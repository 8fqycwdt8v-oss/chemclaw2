"""Wiki + knowledge MCP tools split out of api/agent/tools.py.

Eight tools covering wiki retrieval (`wiki_lookup`), unified knowledge
lookup across wiki / papers / properties / facts (`lookup_knowledge`),
paper ingest + retrieval (`register_paper`, `paper_qa`), external-fact
caching (`record_external_fact`, `verify_citation`), wiki contradiction
audit (`record_contradiction`), and ICH regulatory guidance lookup
(`lookup_regulatory_guidance`).

`build_knowledge_tools(user_id, session_factory)` returns the
`SdkMcpTool` list. The four write tools (`register_paper`,
`record_external_fact`, `record_contradiction`, the regulatory-fetch
upsert in `lookup_regulatory_guidance`) need `user_id` for `created_by`
/ `fetched_by`; the other four are read-only.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from claude_agent_sdk import SdkMcpTool
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.agent.tool_adapter import wrap_tool
from api.agent.tool_helpers import (
    _fetch_validated,
    _html_to_text,
    _ingest_paper_chunks,
    _redact_ssrf_error,
    _SSRFError,
)

logger = logging.getLogger(__name__)


# ICH publishes guidelines under stable topic landing pages (quality-guidelines,
# multidisciplinary-guidelines, etc.) and per-document PDFs whose URLs change
# with each revision (e.g. database.ich.org/sites/default/files/...). The
# PDF URLs are not safe to hard-code here without an offline verification
# step against the current revision set — so we point at the stable topic
# pages and let the `topic` filter + 24h external_facts cache (below) handle
# the substantive matching. See BACKLOG.md Tier F (D2 → deferred).
_ICH_URLS: dict[str, str] = {
    # Quality (Q-series) — all under the stable Quality Guidelines page
    "ICH Q1": "https://www.ich.org/page/quality-guidelines",
    "ICH Q2": "https://www.ich.org/page/quality-guidelines",
    "ICH Q3A": "https://www.ich.org/page/quality-guidelines",
    "ICH Q3B": "https://www.ich.org/page/quality-guidelines",
    "ICH Q3C": "https://www.ich.org/page/quality-guidelines",
    "ICH Q3D": "https://www.ich.org/page/quality-guidelines",
    "ICH Q6A": "https://www.ich.org/page/quality-guidelines",
    "ICH Q6B": "https://www.ich.org/page/quality-guidelines",
    "ICH Q7": "https://www.ich.org/page/quality-guidelines",
    "ICH Q8": "https://www.ich.org/page/quality-guidelines",
    "ICH Q9": "https://www.ich.org/page/quality-guidelines",
    "ICH Q10": "https://www.ich.org/page/quality-guidelines",
    "ICH Q11": "https://www.ich.org/page/quality-guidelines",
    # Multidisciplinary
    "ICH M7": "https://www.ich.org/page/multidisciplinary-guidelines",
}


def build_knowledge_tools(
    user_id: str,
    session_factory: async_sessionmaker[AsyncSession],
) -> list[SdkMcpTool[Any]]:
    """Build the wiki + knowledge tools for the MCP server."""

    async def wiki_lookup(
        query: str | None = None,
        slug: str | None = None,
        mode: str = "hybrid",
        limit: int = 5,
    ) -> dict[str, Any]:
        """Look up wiki knowledge. Provide slug for a direct page fetch, or
        query for search (mode='hybrid' default, 'fts', or 'semantic').

        'hybrid' runs FTS + semantic in parallel and fuses via Reciprocal
        Rank Fusion — preferred for natural-language queries where you
        can't predict whether lexical or semantic recall will win.
        'fts' is faster and good for exact-term queries (SMILES, CAS,
        precise titles). 'semantic' is best for paraphrases.
        """
        from api.db.queries.wiki_read import (
            get_wiki_page,
            get_wiki_page_citations,
            hybrid_search_wiki,
            search_wiki_by_fts,
            semantic_search_wiki,
        )
        async with session_factory() as db:
            if slug:
                page = await get_wiki_page(db, slug)
                if not page:
                    return {"error": f"Wiki page '{slug}' not found"}
                citations = await get_wiki_page_citations(db, page["id"])
                return {"mode": "slug", "results": [{**page, "citations": citations}]}
            if not query or not query.strip():
                return {"error": "Provide either slug or query"}
            if len(query) > 500:
                return {"error": "query too long (max 500 chars)"}
            if mode == "semantic":
                from api.embeddings import embed_texts
                embeddings = await embed_texts([query])
                results = await semantic_search_wiki(db, embeddings[0], limit=limit)
            elif mode == "hybrid":
                from api.embeddings import embed_texts
                embeddings = await embed_texts([query])
                results = await hybrid_search_wiki(db, query, embeddings[0], limit=limit)
            else:
                results = await search_wiki_by_fts(db, query, limit=limit)
        return {"mode": mode, "results": results}

    async def lookup_knowledge(
        query: str,
        sources: list[str] | None = None,
        compound_id: str | None = None,
    ) -> dict[str, Any]:
        """Unified knowledge retrieval across wiki, papers, compound properties, and external facts.

        sources may be any subset of ['wiki', 'papers', 'properties', 'facts'].
        Defaults to all sources.  Use wiki_lookup(slug=...) for direct page access.
        """
        if not query or len(query) > 500:
            return {"error": "query must be 1-500 chars"}
        active = set(sources) if sources else {"wiki", "papers", "properties", "facts"}

        from api.db.queries.knowledge import (
            lookup_compound_properties,
            search_external_facts,
            search_papers,
        )
        from api.db.queries.wiki_read import search_wiki_by_fts

        async with session_factory() as db:
            wiki_results: list[dict] = []
            paper_results: list[dict] = []
            fact_results: list[dict] = []
            prop_results: list[dict] = []

            if "wiki" in active:
                wiki_results = await search_wiki_by_fts(db, query, limit=5)
            if "papers" in active:
                paper_results = await search_papers(db, query, limit=5)
            if "facts" in active:
                fact_results = await search_external_facts(db, query=query, limit=5)
            if "properties" in active and compound_id:
                prop_results = await lookup_compound_properties(db, compound_id=compound_id)

        return {
            "wiki": wiki_results,
            "papers": paper_results,
            "properties": prop_results,
            "facts": fact_results,
        }

    async def register_paper(
        url: str,
        title: str,
        doi: str | None = None,
        abstract: str | None = None,
        content_text: str | None = None,
        ingest_chunks: bool = False,
    ) -> dict[str, Any]:
        """Persist a fetched paper's metadata into the knowledge base.

        When `content_text` is provided and `ingest_chunks=True`, the body is
        sliding-window-chunked (1500 chars, 200 char overlap), embedded with
        OpenAI text-embedding-3-small, and persisted to `paper_chunks` for
        later semantic / hybrid retrieval via `paper_qa`.

        The metadata upsert and the chunk ingest run as two separate
        transactions by design: if chunk ingest fails (embedding API down,
        bad text encoding, etc.) the paper row stays committed because the
        body is recoverable from `papers.content_text` — a later re-call
        with `ingest_chunks=True` re-runs chunking idempotently via the
        ON CONFLICT clause in insert_paper_chunks.

        Returns {id, already_existed, chunks_written}.
        """
        from api.db.queries.knowledge import upsert_paper
        async with session_factory() as db:
            paper_id, already_existed = await upsert_paper(
                db, url, title, doi=doi, abstract=abstract,
                content_text=content_text, created_by=user_id,
            )
        chunks_written = 0
        if ingest_chunks and content_text and content_text.strip():
            chunks_written = await _ingest_paper_chunks(paper_id, content_text, session_factory)
        return {"id": paper_id, "already_existed": already_existed, "chunks_written": chunks_written}

    async def paper_qa(
        query: str,
        max_chunks: int = 8,
        rcs_min_score: int = 6,
        paper_id: str | None = None,
    ) -> dict[str, Any]:
        """Retrieve + rerank paper excerpts with PaperQA2-style RCS.

        1. Hybrid (FTS + semantic via pgvector) retrieval over `paper_chunks`.
        2. For each top-k chunk, an LLM produces a 1-10 relevance score and a
           ≤300-word query-conditioned summary.
        3. Returns chunks at or above `rcs_min_score`, sorted by score, each
           with its paper title / DOI / section so the agent can cite.

        Restrict to one paper via `paper_id`. Without RCS-eligible chunks (no
        ANTHROPIC_API_KEY, no embedding, etc.), falls back to retrieval-only
        results with `rcs_error` set per chunk.
        """
        from api.db.queries.paper_chunks import hybrid_search_paper_chunks
        from api.db.queries.paper_rcs import score_chunks_with_llm
        q = query.strip()
        if not q or len(q) > 500:
            return {"error": "query must be 1-500 chars"}
        if not (1 <= max_chunks <= 30):
            return {"error": "max_chunks must be between 1 and 30"}
        if not (1 <= rcs_min_score <= 10):
            return {"error": "rcs_min_score must be between 1 and 10"}

        from api.embeddings import embed_texts
        try:
            embeddings = await embed_texts([q])
        except Exception:
            logger.exception("paper_qa_embedding_failed")
            return {"error": "Embedding service unavailable"}
        async with session_factory() as db:
            candidates = await hybrid_search_paper_chunks(
                db, q, embeddings[0], limit=max_chunks, paper_id=paper_id,
            )
        if not candidates:
            return {"query": q, "results": [], "total_candidates": 0}
        scored = await score_chunks_with_llm(candidates, q)
        accepted = [
            r for r in scored
            if r.get("relevance_score") is not None and r["relevance_score"] >= rcs_min_score
        ]
        accepted.sort(key=lambda r: r["relevance_score"], reverse=True)
        # When RCS failed for every candidate, fall back to retrieval order
        # so the tool still returns something useful.
        if not accepted and all(r.get("rcs_error") for r in scored):
            accepted = scored
        return {
            "query": q,
            "results": [
                {
                    "paper_id": r.get("paper_id"),
                    "title": r.get("title"),
                    "doi": r.get("doi"),
                    "url": r.get("url"),
                    "section": r.get("section"),
                    "page": r.get("page"),
                    "chunk_idx": r.get("chunk_idx"),
                    "excerpt": (r.get("text") or "")[:1200],
                    "relevance_score": r.get("relevance_score"),
                    "summary": r.get("summary"),
                    "rcs_error": r.get("rcs_error"),
                }
                for r in accepted
            ],
            "total_candidates": len(candidates),
            "total_accepted": len(accepted),
        }

    async def record_external_fact(
        source_type: str,
        source_id: str,
        content_text: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Persist a fetched external fact (ELN data, search result, document excerpt) for later lookup."""
        from api.db.queries.knowledge import upsert_external_fact
        async with session_factory() as db:
            fact_id, _ = await upsert_external_fact(
                db, source_type, source_id, payload or {}, content_text, fetched_by=user_id
            )
        return {"id": fact_id}

    async def verify_citation(citation_id: str) -> dict[str, Any]:
        """Check whether a wiki citation's underlying source still resolves.

        Looks up external_facts by source_id, checks last_seen freshness, and returns
        whether the fact is still current (last_seen within 30 days).
        """
        from datetime import datetime as _dt
        from datetime import timedelta, timezone

        from api.db.queries.knowledge import get_external_fact_by_source_id
        async with session_factory() as db:
            row = await get_external_fact_by_source_id(db, citation_id)
        if not row:
            return {"found": False, "source_type": None, "last_seen": None, "stale": None}
        cutoff = _dt.now(tz=timezone.utc) - timedelta(days=30)
        last_seen = row["last_seen"]
        if last_seen is None:
            is_stale = True
        elif last_seen.tzinfo is None:
            is_stale = last_seen.replace(tzinfo=timezone.utc) < cutoff
        else:
            is_stale = last_seen < cutoff
        return {
            "found": True,
            "source_type": row["source_type"],
            "last_seen": last_seen.isoformat() if hasattr(last_seen, "isoformat") else str(last_seen),
            "stale": is_stale,
        }

    async def record_contradiction(
        page_slug: str,
        citation_a: str,
        citation_b: str,
        proposed_winner: str,
        reason: str,
    ) -> dict[str, Any]:
        """Persist the result of a contradiction-resolver sub-agent invocation.

        Call this after the contradiction-resolver Task tool returns, passing the
        page slug and the sub-agent's WINNER/REASON output.
        proposed_winner must be 'a', 'b', or 'inconclusive'.
        """
        if proposed_winner not in ("a", "b", "inconclusive"):
            return {"error": "proposed_winner must be 'a', 'b', or 'inconclusive'"}
        from api.db.queries.contradictions import create_contradiction
        from api.db.queries.wiki_read import get_wiki_page
        async with session_factory() as db:
            page = await get_wiki_page(db, page_slug)
            if not page:
                return {"error": f"Wiki page '{page_slug}' not found"}
            async with db.begin():
                contradiction_id = await create_contradiction(
                    db, page["id"], citation_a, citation_b, proposed_winner, reason
                )
        return {"id": contradiction_id}

    async def lookup_regulatory_guidance(
        guideline: str,
        topic: str | None = None,
    ) -> dict[str, Any]:
        """Look up ICH or pharmacopoeial guidance.

        Checks external_facts cache first (24h TTL), then fetches from ich.org.
        Returns the guideline page text or a topic-filtered excerpt.
        """
        from datetime import datetime as _dt
        from datetime import timedelta, timezone

        from api.db.queries.knowledge import get_external_fact_by_source_id, upsert_external_fact

        guideline_key = guideline.strip().upper()
        # Normalise common variants: "ICH Q3A(R2)" -> "ICH Q3A"
        guideline_key = re.sub(r'\([^)]*\)', '', guideline_key).strip()

        cache_source_id = f"regulatory:{guideline_key}"
        freshness_cutoff = _dt.now(tz=timezone.utc) - timedelta(hours=24)

        async with session_factory() as db:
            # Look up by source_id (not FTS) so we always get the right guideline's cache entry.
            entry = await get_external_fact_by_source_id(db, cache_source_id)

        if entry:
            last_seen = entry.get("last_seen")
            if last_seen is not None:
                if last_seen.tzinfo is None:
                    last_seen = last_seen.replace(tzinfo=timezone.utc)
            if last_seen and last_seen >= freshness_cutoff:
                text_body = entry.get("content_text", "")
                if topic:
                    idx = text_body.lower().find(topic.lower())
                    if idx >= 0:
                        text_body = text_body[max(0, idx - 100): idx + 2000]
                payload = entry.get("payload") or {}
                if isinstance(payload, str):
                    try:
                        payload = json.loads(payload)
                    except Exception:
                        payload = {}
                return {
                    "guideline": guideline_key,
                    "summary": text_body[:3000],
                    "url": payload.get("url", ""),
                    "cached": True,
                }

        # Fetch from ich.org via the shared SSRF-pinned helper.
        url = _ICH_URLS.get(guideline_key, "https://www.ich.org/page/quality-guidelines")
        try:
            r = await _fetch_validated(url, enforce_domain_allowlist=True)
        except _SSRFError as e:
            return _redact_ssrf_error("regulatory_fetch", e, guideline=guideline_key)
        except Exception as e:
            logger.warning("regulatory_fetch_failed guideline=%s: %s", guideline_key, e)
            return {"error": "Failed to fetch regulatory guidance", "guideline": guideline_key}
        if not r.is_success:
            return {"error": f"ICH fetch returned HTTP {r.status_code}", "guideline": guideline_key}
        raw_text = _html_to_text(r.text)

        excerpt = raw_text[:10_000]
        async with session_factory() as db:
            await upsert_external_fact(
                db, "regulatory", cache_source_id,
                {"url": url, "guideline": guideline_key},
                excerpt,
                fetched_by=user_id,
            )

        result_text = excerpt
        if topic:
            idx = result_text.lower().find(topic.lower())
            if idx >= 0:
                result_text = result_text[max(0, idx - 100): idx + 2000]
        return {
            "guideline": guideline_key,
            "summary": result_text[:3000],
            "url": url,
            "cached": False,
        }

    return [
        wrap_tool("wiki_lookup", wiki_lookup),
        wrap_tool("lookup_knowledge", lookup_knowledge),
        wrap_tool("register_paper", register_paper),
        wrap_tool("paper_qa", paper_qa),
        wrap_tool("record_external_fact", record_external_fact),
        wrap_tool("verify_citation", verify_citation),
        wrap_tool("record_contradiction", record_contradiction),
        wrap_tool("lookup_regulatory_guidance", lookup_regulatory_guidance),
    ]
