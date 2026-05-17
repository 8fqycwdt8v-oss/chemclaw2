"""Agent tools — Python port of packages/agent-tools/src/*.ts.

All tools are registered on an in-process MCP server via the Python Agent SDK's
create_sdk_mcp_server() / @mcp.tool() pattern.
"""
from __future__ import annotations

import asyncio
import ipaddress
import logging
import os
import re
import socket
from typing import Any
from urllib.parse import urlparse

import httpx
from claude_agent_sdk import create_sdk_mcp_server
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

logger = logging.getLogger(__name__)

# ── SSRF protection ───────────────────────────────────────────────────────────

async def _assert_not_private(hostname: str) -> None:
    """Raise if hostname resolves to a private/loopback/non-global IP.

    Uses run_in_executor so the blocking getaddrinfo call does not stall
    the event loop. Fails closed: a DNS resolution failure raises ValueError.
    """
    loop = asyncio.get_event_loop()
    try:
        infos = await loop.run_in_executor(None, socket.getaddrinfo, hostname, None)
    except OSError as e:
        raise ValueError(f"DNS resolution failed for {hostname}: {e}") from e
    for info in infos:
        ip_str = info[4][0]
        try:
            addr = ipaddress.ip_address(ip_str)
        except ValueError:
            raise ValueError(f"SSRF blocked: unrecognised address format {ip_str}")
        if not addr.is_global:
            raise ValueError(f"SSRF blocked: {hostname} resolves to a non-public address ({ip_str})")


# ── Allowed domains ───────────────────────────────────────────────────────────

ALLOWED_DOMAINS = [
    'pubchem.ncbi.nlm.nih.gov',
    'pubmed.ncbi.nlm.nih.gov',
    'doi.org',
    'crossref.org',
    'chemrxiv.org',
    'rsc.org',
    'acs.org',
    'nature.com',
    'sciencedirect.com',
    'elsevier.com',
]


def _is_allowed_domain(hostname: str) -> bool:
    h = hostname.lower()
    return any(h == d or h.endswith('.' + d) for d in ALLOWED_DOMAINS)


# ── HTML → text ───────────────────────────────────────────────────────────────

def _html_to_text(html: str) -> str:
    """Very lightweight HTML → text. Strips tags, decodes common entities."""
    import html as html_lib
    text = re.sub(r'<style[^>]*>.*?</style>', '', html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<script[^>]*>.*?</script>', '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<[^>]+>', ' ', text)
    text = html_lib.unescape(text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


# ── Tool factory ──────────────────────────────────────────────────────────────

def build_chemclaw_mcp_server(
    user_id: str,
    session_id: str | None,
    session_factory: async_sessionmaker[AsyncSession],
) -> Any:
    """Build an in-process MCP server with all chemclaw2 agent tools."""
    mcp = create_sdk_mcp_server("chemclaw2-tools")

    # ── compound similarity search ───────────────────────────────────────────
    @mcp.tool()
    async def compound_similarity_search(
        fingerprint_bits: str,
        limit: int = 20,
        min_tanimoto: float = 0.4,
        created_after: str | None = None,
        has_cas: bool = False,
    ) -> dict[str, Any]:
        """Search the compound registry by Morgan fingerprint similarity (Tanimoto ≥ threshold)."""
        from api.db.queries.compounds import find_similar_compounds
        if not re.match(r'^[01]{2048}$', fingerprint_bits):
            return {"error": "fingerprint_bits must be exactly 2048 binary digits"}
        async with session_factory() as db:
            results = await find_similar_compounds(
                db, fingerprint_bits, limit, min_tanimoto, created_after, has_cas
            )
        return {"type": "compound_similarity", "results": results}

    # ── reaction similarity search ────────────────────────────────────────────
    @mcp.tool()
    async def reaction_similarity_search(
        rxn_fingerprint_bits: str,
        limit: int = 20,
        min_similarity: float = 0.4,
    ) -> dict[str, Any]:
        """Search the reaction database by DRFP fingerprint similarity."""
        from api.db.queries.reactions import find_similar_reactions
        if not re.match(r'^[01]{2048}$', rxn_fingerprint_bits):
            return {"error": "rxn_fingerprint_bits must be exactly 2048 binary digits"}
        async with session_factory() as db:
            results = await find_similar_reactions(db, rxn_fingerprint_bits, limit, min_similarity)
        return {"type": "reaction_similarity", "results": results}

    # ── substructure search ───────────────────────────────────────────────────
    @mcp.tool()
    async def substructure_search(
        smarts: str,
        max_candidates: int = 500,
    ) -> dict[str, Any]:
        """Return compound candidates for substructure SMARTS matching (caller runs RDKit match)."""
        from api.db.queries.compounds import list_compounds_for_substructure
        async with session_factory() as db:
            candidates = await list_compounds_for_substructure(db, max_candidates)
        return {"smarts": smarts, "candidates": candidates}

    # ── wiki lookup ───────────────────────────────────────────────────────────
    @mcp.tool()
    async def wiki_lookup(
        query: str | None = None,
        slug: str | None = None,
        mode: str = "fts",
        limit: int = 5,
    ) -> dict[str, Any]:
        """Look up wiki knowledge. Provide slug for a direct page fetch, or
        query for FTS/semantic search (mode='fts' or mode='semantic')."""
        from api.db.queries.wiki import (
            get_wiki_page,
            get_wiki_page_citations,
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
                from api.routes.wiki import embed_texts
                embeddings = await embed_texts([query])
                results = await semantic_search_wiki(db, embeddings[0], limit=limit)
            else:
                results = await search_wiki_by_fts(db, query, limit=limit)
        return {"mode": mode, "results": results}

    # ── web search ────────────────────────────────────────────────────────────
    @mcp.tool()
    async def web_search(
        query: str,
        site_filter: str | None = None,
    ) -> dict[str, Any]:
        """Search the web for scientific literature. site_filter must be an approved domain."""
        q = query.strip()
        if not q or len(q) > 500:
            return {"results": [], "error": "query must be 1-500 chars"}
        api_key = os.environ.get("BRAVE_SEARCH_API_KEY", "")
        if not api_key:
            return {"results": [], "error": "BRAVE_SEARCH_API_KEY not configured"}
        if site_filter:
            sf = site_filter.strip().lower()
            if not re.match(r'^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$', sf):
                return {"results": [], "error": "Invalid site_filter"}
            if not _is_allowed_domain(sf):
                return {"results": [], "error": f"site_filter '{sf}' is not in the approved domain list"}
            q = f"site:{sf} {q}"
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                r = await client.get(
                    "https://api.search.brave.com/res/v1/web/search",
                    params={"q": q, "count": 5},
                    headers={"X-Subscription-Token": api_key, "Accept": "application/json"},
                )
            if not r.is_success:
                return {"results": [], "error": f"Brave API error: {r.status_code}"}
            data = r.json()
            results = [
                {"title": item["title"], "url": item["url"], "snippet": item.get("description", "")}
                for item in (data.get("web", {}).get("results") or [])
            ]
            return {"results": results}
        except Exception as e:
            logger.error("web_search_failed: %s", e)
            return {"results": [], "error": "Web search failed"}

    # ── document fetch ────────────────────────────────────────────────────────
    @mcp.tool()
    async def fetch_document(
        url: str,
        format: str = "markdown",
    ) -> dict[str, Any]:
        """Fetch a scientific document from an allowed domain."""
        try:
            parsed = urlparse(url)
            hostname = parsed.hostname or ""
        except Exception:
            return {"error": "Invalid URL"}
        if not _is_allowed_domain(hostname):
            return {"error": f"Domain '{hostname}' is not in the allowed list"}
        try:
            await _assert_not_private(hostname)
        except ValueError as e:
            return {"error": str(e)}
        MAX_BYTES = 500_000
        # Follow redirects manually: re-validate each hop's domain and IP.
        current_url = url
        for _ in range(5):
            try:
                async with httpx.AsyncClient(
                    timeout=15.0,
                    follow_redirects=False,
                    headers={"User-Agent": "chemclaw2/1.0 (research assistant)"},
                ) as client:
                    r = await client.get(current_url)
            except Exception:
                logger.warning("fetch_document_failed url=%s", current_url[:100])
                return {"error": "Fetch failed"}
            if r.is_redirect:
                redir_loc = r.headers.get("location", "")
                if not redir_loc:
                    return {"error": "Redirect with no Location header"}
                try:
                    redir_parsed = urlparse(redir_loc)
                    redir_hostname = redir_parsed.hostname or ""
                except Exception:
                    return {"error": "Invalid redirect URL"}
                if not _is_allowed_domain(redir_hostname):
                    return {"error": f"Redirect to blocked domain: '{redir_hostname}'"}
                try:
                    await _assert_not_private(redir_hostname)
                except ValueError as e:
                    return {"error": str(e)}
                current_url = redir_loc
                continue
            if not r.is_success:
                return {"error": f"HTTP {r.status_code}"}
            content_type = r.headers.get("content-type", "")
            body = r.content[:MAX_BYTES]
            if format == "bytes":
                import base64
                return {"content_type": content_type, "data": base64.b64encode(body).decode()}
            if not content_type.startswith("text/"):
                return {"error": f"Unsupported content-type for {format}: {content_type.split(';')[0].strip()}"}
            text = body.decode("utf-8", errors="replace")
            if format == "markdown":
                text = _html_to_text(text)
            # 10 K char limit matches TypeScript doc-fetch — keeps context window manageable.
            return {"content": text[:10_000], "truncated": len(r.content) > MAX_BYTES or len(text) > 10_000}
        return {"error": "Too many redirects"}

    # ── ELN experiment fetch ──────────────────────────────────────────────────
    @mcp.tool()
    async def eln_fetch_experiment(experiment_id: str) -> dict[str, Any]:
        """Fetch a read-only experiment record from the connected ELN system."""
        eln_base = os.environ.get("ELN_API_BASE_URL", "").rstrip("/")
        if not eln_base:
            return {"error": "ELN_API_BASE_URL not configured"}
        try:
            parsed = urlparse(eln_base)
            hostname = parsed.hostname or ""
            await _assert_not_private(hostname)
        except ValueError as e:
            return {"error": str(e)}
        eln_key = os.environ.get("ELN_API_KEY", "")
        exp_id = experiment_id.strip()
        if not re.match(r'^[A-Za-z0-9_-]{1,64}$', exp_id):
            return {"error": "Invalid experiment_id format"}
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                # Path: TypeScript used /experiments/{id}; Python uses /api/eln/experiments/{id}.
                # Verify this against the actual ELN API contract before deploying.
                r = await client.get(
                    f"{eln_base}/api/eln/experiments/{exp_id}",
                    headers={"Authorization": f"Bearer {eln_key}"},
                )
            if r.status_code == 404:
                return {"error": f"Experiment {exp_id} not found"}
            if not r.is_success:
                return {"error": f"ELN API error: {r.status_code}"}
            return r.json()
        except Exception as e:
            logger.warning("eln_fetch_failed exp=%s: %s", exp_id, e)
            return {"error": "ELN fetch failed"}

    # ── synthesis campaign tools ──────────────────────────────────────────────
    @mcp.tool()
    async def start_synthesis_campaign(
        target_smiles: str | None = None,
    ) -> dict[str, Any]:
        """Create a new synthesis campaign for the current session."""
        from api.db.queries.campaigns import create_campaign
        if not session_id:
            return {"error": "No session_id — cannot create campaign"}
        async with session_factory() as db:
            async with db.begin():
                campaign_id = await create_campaign(db, session_id, user_id, target_smiles)
        return {"campaign_id": campaign_id, "status": "planning"}

    @mcp.tool()
    async def confirm_synthesis_plan(
        campaign_id: str,
        plan: dict[str, Any],
        steps: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Confirm a synthesis plan and add steps to the campaign."""
        from api.db.queries.campaigns import update_campaign_status, add_campaign_step
        # Single transaction: status flip + step inserts are atomic.
        # If any step insert fails the whole operation rolls back.
        async with session_factory() as db:
            async with db.begin():
                await update_campaign_status(db, campaign_id, user_id, "running", plan=plan)
                for step in steps:
                    await add_campaign_step(
                        db,
                        campaign_id,
                        int(step.get("step_idx", 0)),
                        step.get("reaction_smiles"),
                        step.get("conditions"),
                    )
        return {"campaign_id": campaign_id, "status": "running", "steps_added": len(steps)}

    # ── record feedback ───────────────────────────────────────────────────────
    @mcp.tool()
    async def record_feedback(
        session_id_: str,
        turn_index: int,
        score: int,
        reason: str | None = None,
    ) -> dict[str, Any]:
        """Record thumbs-up (score=1) or thumbs-down (score=-1) for a conversation turn."""
        if score not in (1, -1):
            return {"ok": False, "error": "score must be 1 or -1"}
        from api.db.queries.feedback import record_feedback as _record_feedback
        async with session_factory() as db:
            feedback_id = await _record_feedback(db, session_id_, turn_index, score, user_id, reason)
        return {"ok": True, "id": feedback_id}

    # ── lookup_knowledge ──────────────────────────────────────────────────────
    @mcp.tool()
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

        from api.db.queries.wiki import search_wiki_by_fts
        from api.db.queries.knowledge import (
            search_papers,
            search_external_facts,
            lookup_compound_properties,
        )

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

    # ── register_paper ────────────────────────────────────────────────────────
    @mcp.tool()
    async def register_paper(
        url: str,
        title: str,
        doi: str | None = None,
        abstract: str | None = None,
    ) -> dict[str, Any]:
        """Persist a fetched paper's metadata into the knowledge base for future retrieval."""
        from api.db.queries.knowledge import upsert_paper
        async with session_factory() as db:
            paper_id, already_existed = await upsert_paper(
                db, url, title, doi=doi, abstract=abstract, created_by=user_id
            )
        return {"id": paper_id, "already_existed": already_existed}

    # ── record_external_fact ──────────────────────────────────────────────────
    @mcp.tool()
    async def record_external_fact(
        source_type: str,
        source_id_: str,
        content_text: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Persist a fetched external fact (ELN data, search result, document excerpt) for later lookup."""
        from api.db.queries.knowledge import upsert_external_fact
        async with session_factory() as db:
            fact_id, _ = await upsert_external_fact(
                db, source_type, source_id_, payload or {}, content_text, fetched_by=user_id
            )
        return {"id": fact_id}

    # ── register_compound_property ────────────────────────────────────────────
    @mcp.tool()
    async def register_compound_property(
        compound_id: str,
        name: str,
        value_num: float | None = None,
        value_text: str | None = None,
        unit: str | None = None,
        method: str | None = None,
        source_citation_id: str | None = None,
    ) -> dict[str, Any]:
        """Record a measured or calculated property for a compound."""
        if value_num is None and value_text is None:
            return {"error": "Provide at least one of value_num or value_text"}
        from api.db.queries.knowledge import insert_compound_property
        async with session_factory() as db:
            prop_id = await insert_compound_property(
                db, compound_id, name, user_id,
                value_num=value_num, value_text=value_text,
                unit=unit, method=method,
                source_citation_id=source_citation_id,
            )
        return {"id": prop_id}

    # ── record_contradiction ──────────────────────────────────────────────────
    @mcp.tool()
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
        from api.db.queries.wiki import get_wiki_page
        from api.db.queries.contradictions import create_contradiction
        async with session_factory() as db:
            page = await get_wiki_page(db, page_slug)
            if not page:
                return {"error": f"Wiki page '{page_slug}' not found"}
            contradiction_id = await create_contradiction(
                db, page["id"], citation_a, citation_b, proposed_winner, reason
            )
        return {"id": contradiction_id}

    # ── lookup_regulatory_guidance ────────────────────────────────────────────
    _ICH_URLS: dict[str, str] = {
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
        "ICH M7": "https://www.ich.org/page/multidisciplinary-guidelines",
    }

    ALLOWED_DOMAINS.append("ich.org")

    @mcp.tool()
    async def lookup_regulatory_guidance(
        guideline: str,
        topic: str | None = None,
    ) -> dict[str, Any]:
        """Look up ICH or pharmacopoeial guidance.

        Checks external_facts cache first (24h TTL), then fetches from ich.org.
        Returns the guideline page text or a topic-filtered excerpt.
        """
        from api.db.queries.knowledge import search_external_facts, upsert_external_fact
        import time
        from datetime import timezone, timedelta

        guideline_key = guideline.strip().upper()
        # Normalise common variants: "ICH Q3A(R2)" -> "ICH Q3A"
        guideline_key = re.sub(r'\([^)]*\)', '', guideline_key).strip()

        cache_source_id = f"regulatory:{guideline_key}"
        freshness_cutoff = datetime.now(tz=timezone.utc) - timedelta(hours=24)

        async with session_factory() as db:
            cached = await search_external_facts(db, source_type="regulatory", query=guideline_key, limit=1)

        if cached:
            entry = cached[0]
            last_seen = entry.get("last_seen") or entry.get("fetched_at")
            if last_seen and (
                (hasattr(last_seen, "tzinfo") and last_seen >= freshness_cutoff)
                or (isinstance(last_seen, str) and last_seen >= freshness_cutoff.isoformat())
            ):
                text_body = entry.get("content_text", "")
                if topic:
                    idx = text_body.lower().find(topic.lower())
                    if idx >= 0:
                        text_body = text_body[max(0, idx - 100): idx + 2000]
                return {
                    "guideline": guideline_key,
                    "summary": text_body[:3000],
                    "url": entry.get("payload", {}).get("url", ""),
                    "cached": True,
                }

        # Fetch from ich.org
        url = _ICH_URLS.get(guideline_key, "https://www.ich.org/page/quality-guidelines")
        try:
            parsed = urlparse(url)
            hostname = parsed.hostname or ""
            await _assert_not_private(hostname)
            async with httpx.AsyncClient(
                timeout=15.0,
                follow_redirects=True,
                headers={"User-Agent": "chemclaw2/1.0 (research assistant)"},
            ) as client:
                r = await client.get(url)
            if not r.is_success:
                return {"error": f"ICH fetch returned HTTP {r.status_code}", "guideline": guideline_key}
            raw_text = _html_to_text(r.text)
        except Exception as e:
            logger.warning("regulatory_fetch_failed guideline=%s: %s", guideline_key, e)
            return {"error": "Failed to fetch regulatory guidance", "guideline": guideline_key}

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

    return mcp
