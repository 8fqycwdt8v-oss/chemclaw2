"""Agent tools — Python port of packages/agent-tools/src/*.ts.

All tools are registered on an in-process MCP server via the Python Agent SDK's
create_sdk_mcp_server() / @mcp.tool() pattern.
"""
from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import os
import re
import socket
import urllib.parse
import uuid
from typing import Any
from urllib.parse import urlparse

import httpx
from claude_agent_sdk import create_sdk_mcp_server
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

logger = logging.getLogger(__name__)


class _PredictedConditionsPayload(BaseModel):
    """Schema for the `record_predicted_conditions` tool's nested conditions arg."""
    catalysts: list[str] = Field(default_factory=list)
    solvents: list[str] = Field(default_factory=list)
    reagents: list[str] = Field(default_factory=list)
    temperature_c: float | None = None

# ── SSRF protection ───────────────────────────────────────────────────────────

async def _resolve_to_global_ip(hostname: str) -> str:
    """Resolve a hostname and return the first public IP, or raise ValueError.

    Single point of DNS resolution: callers should *use this IP* as the
    connection target (see `_fetch_validated`) rather than passing the
    hostname back through httpx, which would re-resolve at connect time
    and open a DNS-rebinding TOCTOU window. CLAUDE.md §security-5.

    Uses run_in_executor so the blocking getaddrinfo call does not stall
    the event loop. Fails closed on DNS error, unrecognised address
    format, private/loopback/link-local/multicast/etc.
    """
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.run_in_executor(None, socket.getaddrinfo, hostname, None)
    except OSError as e:
        raise ValueError(f"DNS resolution failed for {hostname}: {e}") from e
    chosen: str | None = None
    for info in infos:
        ip_str = info[4][0]
        try:
            addr = ipaddress.ip_address(ip_str)
        except ValueError:
            raise ValueError(f"SSRF blocked: unrecognised address format {ip_str}")
        # is_global treats multicast (224.0.0.0/4, ff00::/8) as globally
        # routed, so check it separately — outbound HTTP must never target
        # a multicast destination.
        if not addr.is_global or addr.is_multicast:
            raise ValueError(f"SSRF blocked: {hostname} resolves to a non-public address ({ip_str})")
        # Prefer IPv4 (first match) — more libraries handle it cleanly,
        # and the SNI extension path is identical for v4/v6.
        if chosen is None or (":" in chosen and ":" not in ip_str):
            chosen = ip_str
    if chosen is None:
        raise ValueError(f"DNS returned no records for {hostname}")
    return chosen


async def _assert_not_private(hostname: str) -> None:
    """Back-compat alias: validates the hostname but discards the IP.

    Prefer `_resolve_to_global_ip` directly so the resolved IP can be
    pinned for the actual connection.
    """
    await _resolve_to_global_ip(hostname)


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
    'ich.org',
    'cactus.nci.nih.gov',  # NCI CACTUS — name↔SMILES↔CAS resolver
]


def _is_allowed_domain(hostname: str) -> bool:
    h = hostname.lower()
    return any(h == d or h.endswith('.' + d) for d in ALLOWED_DOMAINS)


# ── Validated HTTP fetch with IP pinning ──────────────────────────────────────


def _pin_url_to_ip(url: str, ip: str) -> str:
    """Replace the hostname in `url` with `ip`, preserving scheme/port/path/query.

    IPv6 addresses are wrapped in brackets so the resulting URL is valid.
    The caller still passes the original hostname via SNI + Host header
    (see `_fetch_validated`), so TLS verification continues to work.
    """
    parsed = urlparse(url)
    bracketed = f"[{ip}]" if ":" in ip else ip
    netloc = f"{bracketed}:{parsed.port}" if parsed.port is not None else bracketed
    if parsed.username:
        creds = parsed.username + (f":{parsed.password}" if parsed.password else "")
        netloc = f"{creds}@{netloc}"
    return parsed._replace(netloc=netloc).geturl()


class _SSRFError(ValueError):
    """Raised by `_fetch_validated` when an SSRF guard rejects the request."""


async def _fetch_validated(
    url: str,
    *,
    enforce_domain_allowlist: bool,
    max_redirects: int = 5,
    timeout: float = 15.0,
    headers: dict[str, str] | None = None,
    user_agent: str = "chemclaw2/1.0 (research assistant)",
) -> httpx.Response:
    """Fetch `url` with SSRF guards applied at every hop.

    For each URL in the redirect chain:
      1. Validate the hostname against `ALLOWED_DOMAINS` if requested.
      2. Resolve DNS exactly once and reject non-global / multicast IPs.
      3. Rewrite the URL to point at the resolved IP, pass the original
         hostname via SNI + Host header so TLS cert verification still
         matches the certificate's SAN/CN.
      4. Send the request without httpx-level redirect following — this
         function handles redirects explicitly so each new hop is
         re-validated.

    Raises `_SSRFError` (a ValueError subclass) for any guard failure;
    other exceptions (timeout, connection reset, etc.) propagate as
    `httpx.HTTPError` for the caller to handle.
    """
    current = url
    base_headers = {"User-Agent": user_agent, **(headers or {})}
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
        for _ in range(max_redirects + 1):
            parsed = urlparse(current)
            hostname = (parsed.hostname or "").lower()
            if not hostname:
                raise _SSRFError(f"Invalid URL: missing hostname in {current[:100]}")
            if enforce_domain_allowlist and not _is_allowed_domain(hostname):
                raise _SSRFError(f"Domain '{hostname}' is not in the allowed list")
            try:
                ip = await _resolve_to_global_ip(hostname)
            except ValueError as e:
                # Re-raise as _SSRFError so callers' single `except _SSRFError`
                # branch covers both allowlist + DNS rejection paths.
                raise _SSRFError(str(e)) from e
            pinned = _pin_url_to_ip(current, ip)
            # `Host` header preserves the hostname for HTTP routing on the
            # peer; `sni_hostname` makes httpcore use the hostname for both
            # TLS SNI and certificate verification (server_hostname).
            req_headers = {**base_headers, "Host": hostname}
            response = await client.get(
                pinned,
                headers=req_headers,
                extensions={"sni_hostname": hostname},
            )
            if not response.is_redirect:
                return response
            location = response.headers.get("location", "").strip()
            if not location:
                raise _SSRFError("Redirect response with no Location header")
            # Resolve relative redirects against the *original* hostname
            # (not the pinned IP) so the next hop's allowlist check runs
            # against the real authority.
            current = str(httpx.URL(f"{parsed.scheme}://{hostname}").join(location))
        raise _SSRFError("Too many redirects")


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


# ── paper-chunk ingest ────────────────────────────────────────────────────────

def _resolve_chunk_params() -> tuple[int, int]:
    """Read PAPER_CHUNK_SIZE / PAPER_CHUNK_OVERLAP env vars with defaults
    (1500 / 200) and validate. Misconfigured values log a warning and
    fall back to defaults — never silently land 50-char chunks."""
    default_size, default_overlap = 1500, 200
    try:
        size = int(os.environ.get("PAPER_CHUNK_SIZE", str(default_size)))
        overlap = int(os.environ.get("PAPER_CHUNK_OVERLAP", str(default_overlap)))
    except ValueError:
        logger.warning("invalid PAPER_CHUNK_* env vars (non-numeric); using defaults")
        return default_size, default_overlap
    if not (200 <= size <= 5000) or not (0 <= overlap < size):
        logger.warning(
            "invalid PAPER_CHUNK_* env vars (size=%d overlap=%d); using defaults",
            size, overlap,
        )
        return default_size, default_overlap
    return size, overlap


async def _ingest_paper_chunks(
    paper_id: str,
    content_text: str,
    session_factory: async_sessionmaker[AsyncSession],
) -> int:
    """Chunk + embed + persist a paper body. Returns the count written.

    Chunk size + overlap come from PAPER_CHUNK_SIZE / PAPER_CHUNK_OVERLAP
    env vars (defaults 1500 / 200, validated by `_resolve_chunk_params`).
    Embedding failure is non-fatal: chunks land with embedding=NULL so FTS
    retrieval still works; semantic retrieval will simply skip them.
    """
    from api.db.queries.papers import chunk_paper_text, insert_paper_chunks
    chunk_size, overlap = _resolve_chunk_params()
    parts = chunk_paper_text(content_text, chunk_size=chunk_size, overlap=overlap)
    if not parts:
        return 0
    # Embed in one batch — text-embedding-3-small is happy with arrays.
    embeddings: list[list[float] | None]
    try:
        from api.embeddings import embed_texts
        raw = await embed_texts([p[2] for p in parts])
        embeddings = list(raw)
    except Exception:
        logger.exception("paper_chunk_embed_failed paper_id=%s", paper_id)
        embeddings = [None] * len(parts)
    chunks = [
        {
            "chunk_idx": idx,
            "section": section,
            "page": None,
            "text": txt,
            "embedding": emb,
        }
        for (idx, section, txt), emb in zip(parts, embeddings)
    ]
    # insert_paper_chunks manages its own transaction; we just provide a session.
    async with session_factory() as db:
        return await insert_paper_chunks(db, paper_id, chunks)


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
        include_outcomes: bool = False,
    ) -> dict[str, Any]:
        """Search the reaction database by DRFP fingerprint similarity.

        Set ``include_outcomes=True`` to attach experimental results
        (yield, status, conditions actually run, failure reasons) to each
        hit — needed by the process-gap-analyst sub-agent when proposing
        what to investigate next for a reaction step.
        """
        from api.db.queries.reactions import find_similar_reactions
        if not re.match(r'^[01]{2048}$', rxn_fingerprint_bits):
            return {"error": "rxn_fingerprint_bits must be exactly 2048 binary digits"}
        async with session_factory() as db:
            results = await find_similar_reactions(
                db, rxn_fingerprint_bits, limit, min_similarity,
                include_outcomes=include_outcomes,
            )
        return {"type": "reaction_similarity", "results": results}

    # ── condition precedent from neighbors ────────────────────────────────────
    @mcp.tool()
    async def suggest_conditions_from_neighbors(
        rxn_fingerprint_bits: str,
        limit: int = 10,
        min_similarity: float = 0.4,
    ) -> dict[str, Any]:
        """Aggregate free-text conditions from top-K DRFP neighbors.

        Call this BEFORE invoking a predictor — it is cheaper, grounded in
        the registry's actual reactions, and the returned reaction ids can
        be cited. Compute the DRFP bits with mcp-rxnfp.compute_drfp first.
        """
        from api.db.queries.reactions import find_neighbor_conditions
        if not re.match(r'^[01]{2048}$', rxn_fingerprint_bits):
            return {"error": "rxn_fingerprint_bits must be exactly 2048 binary digits"}
        async with session_factory() as db:
            neighbors = await find_neighbor_conditions(
                db, rxn_fingerprint_bits, limit, min_similarity
            )
        return {"type": "neighbor_conditions", "neighbors": neighbors}

    # ── reaction outcomes lookup ──────────────────────────────────────────────
    @mcp.tool()
    async def list_reaction_outcomes(
        reaction_id: str,
        limit: int = 50,
    ) -> dict[str, Any]:
        """List recorded experimental outcomes for a registered reaction.

        Returns the per-attempt history (yield, status, actual conditions,
        observations, failure reason) newest first, sourced from
        ``reaction_outcomes``. Use this when you have a specific reaction
        in the registry and want to see what's already been tried.
        """
        try:
            rid = str(uuid.UUID(reaction_id.strip()))
        except (ValueError, AttributeError):
            return {"error": "reaction_id must be a UUID"}
        from api.db.queries.reaction_outcomes import list_outcomes_for_reaction
        async with session_factory() as db:
            outcomes = await list_outcomes_for_reaction(db, rid, limit=limit)
        return {"reaction_id": rid, "outcomes": outcomes}

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
        MAX_BYTES = 500_000
        try:
            r = await _fetch_validated(url, enforce_domain_allowlist=True)
        except _SSRFError as e:
            return {"error": str(e)}
        except Exception:
            logger.warning("fetch_document_failed url=%s", url[:100])
            return {"error": "Fetch failed"}
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

    # ── ELN experiment fetch ──────────────────────────────────────────────────
    async def _fetch_eln_raw(experiment_id: str) -> dict[str, Any]:
        """Shared ELN fetch path used by both the read-through tool and ingest.

        Returns the raw ELN payload on HTTP 200, or ``{"error": ...}`` on
        any failure (missing config, SSRF block, 404, non-2xx, network).
        """
        eln_base = os.environ.get("ELN_API_BASE_URL", "").rstrip("/")
        if not eln_base:
            return {"error": "ELN_API_BASE_URL not configured"}
        eln_key = os.environ.get("ELN_API_KEY", "")
        exp_id = experiment_id.strip()
        if not re.match(r'^[A-Za-z0-9_-]{1,64}$', exp_id):
            return {"error": "Invalid experiment_id format"}
        try:
            # Path: TypeScript used /experiments/{id}; Python uses /api/eln/experiments/{id}.
            # Verify this against the actual ELN API contract before deploying.
            r = await _fetch_validated(
                f"{eln_base}/api/eln/experiments/{exp_id}",
                enforce_domain_allowlist=False,
                timeout=10.0,
                headers={"Authorization": f"Bearer {eln_key}"},
            )
        except _SSRFError as e:
            return {"error": str(e)}
        except Exception as e:
            logger.warning("eln_fetch_failed exp=%s: %s", exp_id, e)
            return {"error": "ELN fetch failed"}
        if r.status_code == 404:
            return {"error": f"Experiment {exp_id} not found"}
        if not r.is_success:
            return {"error": f"ELN API error: {r.status_code}"}
        try:
            return r.json()
        except Exception as e:
            logger.warning("eln_fetch_parse_failed exp=%s: %s", exp_id, e)
            return {"error": "ELN response is not valid JSON"}

    @mcp.tool()
    async def eln_fetch_experiment(experiment_id: str) -> dict[str, Any]:
        """Fetch a read-only experiment record from the connected ELN system."""
        return await _fetch_eln_raw(experiment_id)

    # ── ELN experiment ingest (persist outcome) ───────────────────────────────
    @mcp.tool()
    async def ingest_eln_experiment(
        experiment_id: str,
        reaction_id: str,
        campaign_step_id: str | None = None,
    ) -> dict[str, Any]:
        """Fetch an ELN experiment and persist it as a reaction outcome.

        Idempotent on ``experiment_id``: re-calling with the same id
        returns the existing outcome row (``already_existed=True``)
        without duplicating. The ELN payload is normalized via the
        ElnExperiment Pydantic model — fields outside the contract are
        ignored, missing fields fall back to defaults (status='inconclusive'
        when the ELN doesn't tell us). When the real ELN contract lands
        (BACKLOG.md E2), extend ElnExperiment in api/agent/eln_payload.py.
        """
        from api.agent.eln_payload import ElnExperiment, normalize_eln_payload
        from api.db.queries.reaction_outcomes import insert_outcome

        try:
            rid = str(uuid.UUID(reaction_id.strip()))
        except (ValueError, AttributeError):
            return {"ok": False, "error": "reaction_id must be a UUID"}
        csid: str | None
        if campaign_step_id is not None:
            try:
                csid = str(uuid.UUID(campaign_step_id.strip()))
            except (ValueError, AttributeError):
                return {"ok": False, "error": "campaign_step_id must be a UUID"}
        else:
            csid = None

        raw = await _fetch_eln_raw(experiment_id)
        if raw.get("error"):
            return {"ok": False, "error": raw["error"]}

        try:
            normalized: ElnExperiment = normalize_eln_payload(raw)
        except Exception as e:
            # Pydantic ValidationError or upstream payload mangled —
            # surface a generic message; the real exception is logged
            # inside normalize_eln_payload.
            logger.warning(
                "eln_normalize_failed exp=%s reaction=%s: %s",
                experiment_id[:64], rid, e,
            )
            return {"ok": False, "error": "ELN payload could not be normalized"}

        try:
            async with session_factory() as db:
                async with db.begin():
                    outcome_id, already_existed = await insert_outcome(
                        db,
                        reaction_id=rid,
                        source="eln",
                        status=normalized.status,
                        created_by=user_id,
                        campaign_step_id=csid,
                        eln_experiment_id=experiment_id.strip(),
                        yield_pct=normalized.yield_pct,
                        conditions_actual=normalized.conditions_actual,
                        observations=normalized.observations,
                        failure_reason=normalized.failure_reason,
                    )
        except Exception:
            logger.exception(
                "eln_ingest_persist_failed exp=%s reaction=%s",
                experiment_id[:64], rid,
            )
            return {"ok": False, "error": "Failed to persist ELN outcome"}
        return {
            "ok": True,
            "outcome_id": outcome_id,
            "already_existed": already_existed,
            "status": normalized.status,
        }

    # ── manual outcome record (user-pasted data) ──────────────────────────────
    @mcp.tool()
    async def record_manual_outcome(
        reaction_id: str,
        status: str,
        yield_pct: float | None = None,
        conditions_actual: dict[str, Any] | None = None,
        observations: str | None = None,
        failure_reason: str | None = None,
        campaign_step_id: str | None = None,
    ) -> dict[str, Any]:
        """Persist an experimental outcome the user described in chat.

        Use this when the user pastes lab data inline rather than pointing
        at an ELN experiment. ``status`` must be one of 'success',
        'partial', 'fail', 'inconclusive'. The outcome lands with
        ``source='manual'`` so it can be distinguished from ELN-sourced
        rows downstream.
        """
        from api.db.queries.reaction_outcomes import insert_outcome

        try:
            rid = str(uuid.UUID(reaction_id.strip()))
        except (ValueError, AttributeError):
            return {"ok": False, "error": "reaction_id must be a UUID"}
        csid: str | None
        if campaign_step_id is not None:
            try:
                csid = str(uuid.UUID(campaign_step_id.strip()))
            except (ValueError, AttributeError):
                return {"ok": False, "error": "campaign_step_id must be a UUID"}
        else:
            csid = None

        try:
            async with session_factory() as db:
                async with db.begin():
                    outcome_id, _ = await insert_outcome(
                        db,
                        reaction_id=rid,
                        source="manual",
                        status=status,
                        created_by=user_id,
                        campaign_step_id=csid,
                        yield_pct=yield_pct,
                        conditions_actual=conditions_actual,
                        observations=observations,
                        failure_reason=failure_reason,
                    )
        except ValueError as e:
            # CLAUDE.md observability rule 3: log denials at info before returning.
            logger.info("manual_outcome_rejected reaction=%s reason=%s", rid, e)
            return {"ok": False, "error": str(e)}
        except Exception:
            logger.exception("manual_outcome_persist_failed reaction=%s", rid)
            return {"ok": False, "error": "Failed to persist outcome"}
        return {"ok": True, "outcome_id": outcome_id}

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
        """Confirm a synthesis plan and add steps to the campaign.

        Each step in `steps` may include a `requires_approval: bool` flag
        (default False). When True, the step is inserted with
        status='pending_approval' — the worker will skip it until the user
        approves via POST /api/campaigns/{cid}/steps/{idx}/approve. Use
        for steps that are high-risk, ambiguous, or where the agent's
        confidence is low and a human should review before commitment.
        """
        from api.db.queries.campaigns import add_campaign_step, update_campaign_status
        # Single transaction: status flip + step inserts are atomic.
        # If any step insert fails the whole operation rolls back.
        async with session_factory() as db:
            async with db.begin():
                await update_campaign_status(db, campaign_id, user_id, "running", plan=plan)
                pending_approval = 0
                for step in steps:
                    requires_approval = bool(step.get("requires_approval", False))
                    if requires_approval:
                        pending_approval += 1
                    await add_campaign_step(
                        db,
                        campaign_id,
                        int(step.get("step_idx", 0)),
                        step.get("reaction_smiles"),
                        step.get("conditions"),
                        status="pending_approval" if requires_approval else "pending",
                    )
        return {
            "campaign_id": campaign_id,
            "status": "running",
            "steps_added": len(steps),
            "steps_awaiting_approval": pending_approval,
        }

    # ── record feedback ───────────────────────────────────────────────────────
    @mcp.tool()
    async def record_feedback(
        turn_index: int,
        score: int,
        reason: str | None = None,
    ) -> dict[str, Any]:
        """Record thumbs-up (score=1) or thumbs-down (score=-1) for a conversation turn.

        Always records feedback for the current session — session_id is bound at
        tool-factory time to prevent IDOR via caller-supplied session identifiers.
        """
        if score not in (1, -1):
            return {"ok": False, "error": "score must be 1 or -1"}
        if not session_id:
            return {"ok": False, "error": "No active session to record feedback for"}
        from api.db.queries.feedback import record_feedback as _record_feedback
        async with session_factory() as db:
            feedback_id = await _record_feedback(db, session_id, turn_index, score, user_id, reason)
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

    # ── register_paper ────────────────────────────────────────────────────────
    @mcp.tool()
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

    # ── paper_qa (PaperQA2-style) ────────────────────────────────────────────
    @mcp.tool()
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
        from api.db.queries.papers import (
            hybrid_search_paper_chunks,
            score_chunks_with_llm,
        )
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

    # ── record_external_fact ──────────────────────────────────────────────────
    @mcp.tool()
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

    # ── record_predicted_conditions ───────────────────────────────────────────
    @mcp.tool()
    async def record_predicted_conditions(
        rxn_smiles: str,
        conditions: dict[str, Any],
        model: str,
        source: str,
        confidence: float | None = None,
        reaction_id: str | None = None,
        drfp_bits: str | None = None,
    ) -> dict[str, Any]:
        """Persist a reaction condition prediction for caching and feedback.

        Call this after `mcp-rxn-conditions.predict_conditions` or
        `suggest_conditions_from_neighbors` returns, so the next turn (and
        future campaigns over the same reaction) hit the cache instead of
        re-paying the predictor API.

        `conditions` must be an object shaped like:
          {catalysts: [str], solvents: [str], reagents: [str],
           temperature_c: float|null}
        `model` should identify both backend and version, e.g.
          'rxn4chemistry:v2025-04' or 'neighbor-aggregation:v1'.
        `source` is the high-level origin: 'rxn4chemistry' |
          'neighbor_aggregation' | 'manual'.
        """
        try:
            payload = _PredictedConditionsPayload.model_validate(conditions)
        except ValidationError as e:
            return {"error": f"invalid conditions payload: {e.errors()[0]['msg']}"}

        if not rxn_smiles or ">>" not in rxn_smiles:
            return {"error": "rxn_smiles must contain '>>' separator"}
        if drfp_bits is not None and not re.match(r'^[01]{2048}$', drfp_bits):
            return {"error": "drfp_bits must be exactly 2048 binary digits if provided"}

        from api.db.queries.reaction_conditions import insert_prediction
        async with session_factory() as db:
            async with db.begin():
                prediction_id = await insert_prediction(
                    db,
                    rxn_smiles=rxn_smiles,
                    conditions=payload.model_dump(),
                    model=model,
                    source=source,
                    created_by=user_id,
                    confidence=confidence,
                    reaction_id=reaction_id,
                    drfp_bits=drfp_bits,
                )
        return {"id": prediction_id}

    # ── verify_citation ───────────────────────────────────────────────────────
    @mcp.tool()
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

    # ── lookup_regulatory_guidance ────────────────────────────────────────────
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

    @mcp.tool()
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
            return {"error": str(e), "guideline": guideline_key}
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

    # ── name → structure (CACTUS) ────────────────────────────────────────────
    @mcp.tool()
    async def name_to_structure(name: str) -> dict[str, Any]:
        """Resolve a chemical name to SMILES + CAS via the NCI CACTUS service.

        Issues the three lookups (SMILES / CAS / IUPAC) in parallel.
        Cached in external_facts under source_id=cactus:<name-normalised> with
        7-day TTL — CACTUS is rate-throttled (~20 req/s sustained) so cold
        lookups should stay rare. Returns {smiles, cas, iupac_name, cached}
        or {error, name} on failure.
        """
        from datetime import datetime as _dt
        from datetime import timedelta, timezone

        from api.db.queries.knowledge import get_external_fact_by_source_id, upsert_external_fact

        q = name.strip()
        if not q or len(q) > 200:
            return {"error": "name must be 1-200 chars"}
        # Normalise for caching: lowercase, collapse whitespace. The cache_key
        # is bounded by the 200-char `name` cap above so it can't be used as
        # an arbitrary-length write into external_facts.source_id.
        norm = re.sub(r"\s+", " ", q.lower())
        cache_key = f"cactus:{norm}"
        cutoff = _dt.now(tz=timezone.utc) - timedelta(days=7)
        async with session_factory() as db:
            cached = await get_external_fact_by_source_id(db, cache_key)
        if cached:
            last_seen = cached.get("last_seen")
            if last_seen is not None and last_seen.tzinfo is None:
                last_seen = last_seen.replace(tzinfo=timezone.utc)
            if last_seen and last_seen >= cutoff:
                payload = cached.get("payload") or {}
                if isinstance(payload, str):
                    try:
                        payload = json.loads(payload)
                    except Exception:
                        payload = {}
                return {**payload, "cached": True}

        # CACTUS endpoints return plain text (one value per request) or 404.
        encoded = urllib.parse.quote(q, safe="")
        fields = ("smiles", "cas", "iupac_name")
        urls = [
            f"https://cactus.nci.nih.gov/chemical/structure/{encoded}/{f}"
            for f in fields
        ]

        async def _fetch_one(url: str, field: str) -> tuple[str, str | None, str | None]:
            """Return (field, value-or-None, fatal-error-or-None)."""
            try:
                r = await _fetch_validated(url, enforce_domain_allowlist=True, timeout=10.0)
            except _SSRFError as e:
                return field, None, str(e)  # SSRF guard is fatal for the whole call.
            except Exception as e:
                logger.warning("cactus_fetch_failed field=%s name=%s: %s", field, q[:50], e)
                return field, None, None
            if r.status_code == 404 or not r.is_success:
                return field, None, None
            # CACTUS may return multiple newline-separated values; first wins.
            text_body = r.text.strip()
            first = next((ln.strip() for ln in text_body.splitlines() if ln.strip()), None)
            return field, first, None

        responses = await asyncio.gather(*(
            _fetch_one(url, field) for url, field in zip(urls, fields)
        ))
        result: dict[str, Any] = {"name": q}
        for field, value, fatal in responses:
            if fatal is not None:
                return {"error": fatal, "name": q}
            result[field] = value

        if result.get("smiles") is None and result.get("cas") is None:
            return {"error": f"CACTUS could not resolve '{q}'", "name": q}

        # Cache success for 7 days; cache key encodes the normalised name.
        async with session_factory() as db:
            await upsert_external_fact(
                db, "cactus", cache_key,
                result, f"name={q} smiles={result.get('smiles')} cas={result.get('cas')}",
                fetched_by=user_id,
            )
        return {**result, "cached": False}

    # ── patent coverage (PubChem) ────────────────────────────────────────────
    @mcp.tool()
    async def patent_coverage(smiles: str) -> dict[str, Any]:
        """Count patents referencing a compound via PubChem.

        Resolves SMILES → CID, then queries the PubChem PatentID xref endpoint.
        Returns either {cid, patent_count, sample_patent_ids} on success or
        {cid, error} on failure — `cid` is always present (None when SMILES
        couldn't be resolved at all) so the agent can branch on shape
        without worrying about which step failed.

        High patent count signals a chemotype is well-explored commercially;
        zero may mean open IP space, or simply that the compound is too
        obscure to have been indexed yet.
        """
        s = smiles.strip()
        if not s or len(s) > 1000:
            return {"cid": None, "error": "smiles must be 1-1000 chars"}
        encoded_smiles = urllib.parse.quote(s, safe="")
        try:
            r = await _fetch_validated(
                f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/smiles/{encoded_smiles}/cids/JSON",
                enforce_domain_allowlist=True, timeout=15.0,
            )
        except _SSRFError as e:
            return {"cid": None, "error": str(e)}
        except Exception as e:
            logger.warning("pubchem_cid_lookup_failed smiles_len=%d: %s", len(s), e)
            return {"cid": None, "error": "PubChem CID lookup failed"}
        if r.status_code == 404:
            return {"cid": None, "error": "SMILES not found in PubChem", "smiles": s}
        if not r.is_success:
            return {"cid": None, "error": f"PubChem returned HTTP {r.status_code}"}
        try:
            cids = (r.json().get("IdentifierList") or {}).get("CID") or []
        except Exception:
            return {"cid": None, "error": "PubChem response could not be parsed"}
        if not cids:
            return {"cid": None, "patent_count": 0, "sample_patent_ids": []}
        cid = cids[0]
        try:
            pr = await _fetch_validated(
                f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{cid}/xrefs/PatentID/JSON",
                enforce_domain_allowlist=True, timeout=20.0,
            )
        except _SSRFError as e:
            return {"cid": cid, "error": str(e)}
        except Exception as e:
            logger.warning("pubchem_patent_lookup_failed cid=%s: %s", cid, e)
            return {"cid": cid, "error": "Patent xref fetch failed"}
        if pr.status_code == 404:
            return {"cid": cid, "patent_count": 0, "sample_patent_ids": []}
        if not pr.is_success:
            return {"cid": cid, "error": f"PubChem patent lookup returned HTTP {pr.status_code}"}
        try:
            info_list = (pr.json().get("InformationList") or {}).get("Information") or []
        except Exception:
            return {"cid": cid, "error": "Patent response could not be parsed"}
        patent_ids: list[str] = []
        for entry in info_list:
            pid_list = entry.get("PatentID") or []
            if isinstance(pid_list, list):
                patent_ids.extend(p for p in pid_list if isinstance(p, str))
        return {
            "cid": cid,
            "patent_count": len(patent_ids),
            "sample_patent_ids": patent_ids[:10],
        }

    # ── retrosynthesis disconnection proposals ───────────────────────────────
    @mcp.tool()
    async def propose_retrosynthesis(
        target_smiles: str,
        max_routes: int = 5,
    ) -> dict[str, Any]:
        """Propose one-step retrosynthetic disconnections for a target SMILES.

        Calls the mcp-retrosynth subprocess (RDKit + curated reaction-template
        library) and returns precursor sets keyed by transform name. Use the
        output to seed `confirm_synthesis_plan` or for further analog work.
        Returns {target, routes: [{transform, precursors, confidence}], total}.
        """
        # Delegate to the standalone MCP server via subprocess JSON-RPC. Doing
        # the call here (rather than in the MCP server directly) keeps the
        # agent-visible tool surface uniform and lets us reuse caching /
        # logging at the api layer.
        import asyncio as _asyncio

        s = target_smiles.strip()
        if not s or len(s) > 1000:
            return {"error": "target_smiles must be 1-1000 chars"}
        if max_routes < 1 or max_routes > 20:
            return {"error": "max_routes must be between 1 and 20"}

        # Use the in-process retrosynthesis library directly when available —
        # the same code the stdio MCP server runs. Avoids subprocess overhead
        # for what is a pure CPU + RDKit call.
        try:
            from mcp_retrosynth.disconnect import propose_disconnections
        except ImportError:
            return {"error": "Retrosynthesis backend not installed (mcp_retrosynth)"}
        try:
            routes = await _asyncio.get_running_loop().run_in_executor(
                None, propose_disconnections, s, max_routes,
            )
        except ValueError as e:
            return {"error": str(e)}
        except Exception:
            logger.exception("retrosynth_failed smiles_len=%d", len(s))
            return {"error": "Retrosynthesis proposal failed"}
        return {
            "target": s,
            "routes": routes,
            "total": len(routes),
        }

    # ── Phase B: investigations + world model + hypotheses ───────────────────

    @mcp.tool()
    async def start_investigation(
        title: str,
        objective: str,
    ) -> dict[str, Any]:
        """Open a long-horizon research thread.

        An investigation groups world-model entries + hypotheses under one
        open-ended objective and outlives the chat session. Use this when
        starting work on a topic you expect to revisit across multiple
        sessions ("explore selective JAK1 inhibitors with reduced JAK2
        liability"). Returns {id, status}.
        """
        t = title.strip()
        o = objective.strip()
        if not t or len(t) > 500:
            return {"error": "title must be 1-500 chars"}
        if not o or len(o) > 4000:
            return {"error": "objective must be 1-4000 chars"}
        from api.db.queries.investigations import create_investigation
        async with session_factory() as db:
            iid = await create_investigation(
                db, t, o, created_by=user_id, session_id=session_id,
            )
        return {"id": iid, "status": "active"}

    @mcp.tool()
    async def list_investigations_tool(
        status: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        """List the caller's investigations newest-touched first.

        `status` filters to one of: active, paused, complete. Default
        returns all states. Use to resume work from a prior session.
        """
        from api.db.queries.investigations import list_investigations
        if not (1 <= limit <= 100):
            return {"error": "limit must be between 1 and 100"}
        async with session_factory() as db:
            try:
                rows = await list_investigations(db, user_id, status=status, limit=limit)
            except ValueError as e:
                return {"error": str(e)}
        return {"investigations": rows}

    @mcp.tool()
    async def update_investigation_status_tool(
        investigation_id: str,
        status: str,
    ) -> dict[str, Any]:
        """Move an investigation to active / paused / complete."""
        from api.db.queries.investigations import update_investigation_status
        async with session_factory() as db:
            try:
                ok = await update_investigation_status(
                    db, investigation_id, user_id, status,
                )
            except ValueError as e:
                return {"error": str(e)}
        if not ok:
            return {"error": "investigation not found or not owned by user"}
        return {"id": investigation_id, "status": status}

    @mcp.tool()
    async def world_model_add(
        investigation_id: str,
        kind: str,
        content: str,
        payload: dict[str, Any] | None = None,
        confidence: float | None = None,
    ) -> dict[str, Any]:
        """Persist one atomic claim into the investigation's world model.

        `kind` is one of:
          - 'fact' — something the agent now believes is true,
          - 'assumption' — a working premise that may need revisiting,
          - 'open_question' — a gap to investigate,
          - 'evidence' — a citation/observation supporting other entries.

        `confidence` is an optional 0–1 self-reported score. `payload` is a
        JSONB escape hatch (e.g. {citation_id: ..., compound_id: ...}).

        Use frequently during long investigations so you don't lose
        context across rollouts — the world model is queryable; the
        rolling chat context is not.
        """
        from api.db.queries.investigations import get_investigation
        from api.db.queries.world_model import add_world_model_entry
        c = content.strip()
        if not c or len(c) > 4000:
            return {"error": "content must be 1-4000 chars"}
        async with session_factory() as db:
            # Cross-table ownership check before delegating to the queries fn,
            # which only predicates on its own table's created_by.
            inv = await get_investigation(db, investigation_id, user_id)
            if inv is None:
                return {"error": "investigation not found or not owned by user"}
            try:
                eid = await add_world_model_entry(
                    db, investigation_id, user_id, kind, c,
                    payload=payload, confidence=confidence,
                )
            except ValueError as e:
                return {"error": str(e)}
        return {"id": eid, "kind": kind}

    @mcp.tool()
    async def world_model_query(
        investigation_id: str,
        kind: str | None = None,
        status: str | None = None,
        query: str | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        """Read the world model — list by kind/status or FTS-search.

        Pass `query` for a full-text search over entry content (overrides
        kind+status filters when set). Otherwise pass `kind` and/or
        `status` to narrow. Returns entries newest-touched first by
        default; FTS path orders by relevance.
        """
        from api.db.queries.investigations import get_investigation
        from api.db.queries.world_model import (
            list_world_model_entries,
            search_world_model_entries,
        )
        if not (1 <= limit <= 200):
            return {"error": "limit must be between 1 and 200"}
        async with session_factory() as db:
            inv = await get_investigation(db, investigation_id, user_id)
            if inv is None:
                return {"error": "investigation not found or not owned by user"}
            try:
                if query and query.strip():
                    if len(query) > 500:
                        return {"error": "query must be 1-500 chars"}
                    entries = await search_world_model_entries(
                        db, investigation_id, user_id, query.strip(), limit=limit,
                    )
                else:
                    entries = await list_world_model_entries(
                        db, investigation_id, user_id,
                        kind=kind, status=status, limit=limit,
                    )
            except ValueError as e:
                return {"error": str(e)}
        return {"investigation_id": investigation_id, "entries": entries}

    @mcp.tool()
    async def world_model_supersede(
        entry_id: str,
        new_status: str = "superseded",
    ) -> dict[str, Any]:
        """Mark a world-model entry superseded (refined by new evidence) or
        closed (the question is answered / the assumption was wrong).
        `new_status` must be 'superseded' or 'closed'."""
        from api.db.queries.world_model import update_world_model_entry_status
        if new_status not in ("superseded", "closed"):
            return {"error": "new_status must be 'superseded' or 'closed'"}
        async with session_factory() as db:
            try:
                ok = await update_world_model_entry_status(
                    db, entry_id, user_id, new_status,
                )
            except ValueError as e:
                return {"error": str(e)}
        if not ok:
            return {"error": "entry not found or not owned by user"}
        return {"id": entry_id, "status": new_status}

    @mcp.tool()
    async def propose_hypothesis(
        investigation_id: str,
        statement: str,
        rationale: str | None = None,
        parent_id: str | None = None,
    ) -> dict[str, Any]:
        """Add a hypothesis to an investigation.

        `parent_id`, if set, marks this as an evolved child of an existing
        hypothesis (Google AI Co-Scientist's Evolution agent pattern) and
        must belong to the same user. The new hypothesis starts at the
        default Elo rating (1000) until it's compared via `rank_hypotheses`.
        """
        from api.db.queries.hypotheses import create_hypothesis
        from api.db.queries.investigations import get_investigation
        s = statement.strip()
        if not s or len(s) > 4000:
            return {"error": "statement must be 1-4000 chars"}
        if rationale is not None and len(rationale) > 4000:
            return {"error": "rationale must be ≤4000 chars"}
        async with session_factory() as db:
            inv = await get_investigation(db, investigation_id, user_id)
            if inv is None:
                return {"error": "investigation not found or not owned by user"}
            try:
                hid = await create_hypothesis(
                    db, investigation_id, user_id, s,
                    rationale=rationale, parent_id=parent_id,
                )
            except ValueError as e:
                return {"error": str(e)}
        return {"id": hid, "parent_id": parent_id, "elo_rating": 1000.0}

    @mcp.tool()
    async def list_hypotheses_tool(
        investigation_id: str,
        status: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        """List hypotheses for an investigation, Elo-desc.

        `status` filters to proposed / ranked / refined / retired.
        """
        from api.db.queries.hypotheses import list_hypotheses
        from api.db.queries.investigations import get_investigation
        if not (1 <= limit <= 100):
            return {"error": "limit must be between 1 and 100"}
        async with session_factory() as db:
            inv = await get_investigation(db, investigation_id, user_id)
            if inv is None:
                return {"error": "investigation not found or not owned by user"}
            try:
                rows = await list_hypotheses(
                    db, investigation_id, user_id, status=status, limit=limit,
                )
            except ValueError as e:
                return {"error": str(e)}
        return {"investigation_id": investigation_id, "hypotheses": rows}

    @mcp.tool()
    async def rank_hypotheses(
        investigation_id: str,
        hypothesis_a_id: str,
        hypothesis_b_id: str,
        winner: str,
        reason: str | None = None,
        decided_by: str | None = None,
    ) -> dict[str, Any]:
        """Record a pairwise judgment + update both Elo ratings.

        `winner` must be 'a', 'b', or 'tie'. `decided_by` defaults to the
        calling user id but can be set to 'agent:reflection' or
        'agent:debate' when the agent itself is doing the judging (per
        Google Co-Scientist's self-play debate pattern).

        Returns the new Elo ratings; first-time-ranked hypotheses are also
        moved out of the 'proposed' state.
        """
        from api.db.queries.hypotheses import record_pairwise_ranking
        decided = decided_by or user_id
        async with session_factory() as db:
            try:
                result = await record_pairwise_ranking(
                    db, investigation_id, user_id,
                    hypothesis_a_id, hypothesis_b_id,
                    winner, reason, decided,
                )
            except ValueError as e:
                return {"error": str(e)}
        return result

    @mcp.tool()
    async def retire_hypothesis_tool(
        hypothesis_id: str,
    ) -> dict[str, Any]:
        """Move a hypothesis to 'retired'. Idempotent — re-retiring a
        retired hypothesis returns ok=False with no other side effect."""
        from api.db.queries.hypotheses import retire_hypothesis
        async with session_factory() as db:
            ok = await retire_hypothesis(db, hypothesis_id, user_id)
        return {"id": hypothesis_id, "ok": ok}

    # ── Phase C: code sandbox + active-learning + lead orchestrator ──────────

    @mcp.tool()
    async def run_code(
        code: str,
        investigation_id: str | None = None,
        cpu_seconds: int = 30,
    ) -> dict[str, Any]:
        """Run a Python snippet in the resource-limited sandbox.

        The sandbox enforces CPU + memory + wall-clock + output caps and
        runs in a fresh temp dir with a stripped env. Use for exploratory
        analyses (descriptive stats, RDKit batch ops, fitting a small
        regression) that need real computation rather than chat
        reasoning. Every invocation is persisted to `code_executions`
        and is later retrievable via `list_code_executions`.

        `investigation_id`, if provided, anchors the execution to a
        research thread (and is required if no chat session_id is
        available — both can't be NULL). Both ownership is checked.

        Hard caps: CPU 1–300s (default 30), memory 512 MB, stdout 1 MB,
        stderr 256 KB, source ≤200 KB. Returns:
          {execution_id, status, exit_code, duration_ms, stdout, stderr}
        """
        from api.db.queries.code_executions import insert_execution
        from api.db.queries.investigations import get_investigation
        try:
            from mcp_codesandbox.sandbox import run_python
        except ImportError:
            return {"error": "Sandbox backend not installed (mcp_codesandbox)"}

        if investigation_id is None and session_id is None:
            return {"error": "either investigation_id or active session_id required"}
        if investigation_id is not None:
            async with session_factory() as db:
                inv = await get_investigation(db, investigation_id, user_id)
            if inv is None:
                return {"error": "investigation not found or not owned by user"}

        result = await run_python(code, cpu_seconds=cpu_seconds)
        async with session_factory() as db:
            try:
                exec_id = await insert_execution(
                    db,
                    code=code,
                    stdout=result.stdout,
                    stderr=result.stderr,
                    exit_code=result.exit_code,
                    duration_ms=result.duration_ms,
                    status=result.status,
                    created_by=user_id,
                    investigation_id=investigation_id,
                    session_id=session_id,
                )
            except ValueError as e:
                return {"error": str(e)}
        return {
            "execution_id": exec_id,
            "status": result.status,
            "exit_code": result.exit_code,
            "duration_ms": result.duration_ms,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }

    @mcp.tool()
    async def list_code_executions(
        investigation_id: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        """List recent sandbox executions. Filter by `investigation_id`,
        or omit to list the caller's recent runs across all contexts.
        Owner-scoped on `created_by`."""
        from api.db.queries.code_executions import list_executions
        if not (1 <= limit <= 100):
            return {"error": "limit must be between 1 and 100"}
        async with session_factory() as db:
            rows = await list_executions(
                db, user_id,
                investigation_id=investigation_id,
                session_id=session_id if investigation_id is None else None,
                limit=limit,
            )
        return {"executions": rows}

    # ── V1 active-learning: propose next reaction conditions ─────────────────

    @mcp.tool()
    async def propose_next_conditions(
        campaign_id: str,
        n_proposals: int = 3,
    ) -> dict[str, Any]:
        """Propose conditions for the next experimental step of a campaign.

        V1 strategy (Latin Hypercube-ish): rank completed steps by yield,
        take the best two, and return them plus one *exploitative* tweak
        (small perturbation of the best) and one *exploratory* tweak
        (substituted solvent / temperature ± step). Pure heuristic — the
        proper Gaussian-Process posterior + Expected-Improvement
        acquisition is filed under BACKLOG ("Phase C: real BO"), pending
        actual outcome data to fit against.

        Returns {campaign_id, best_so_far, proposals: [{conditions,
        rationale}], strategy} or {error}.
        """
        from api.db.queries.campaign_steps import list_campaign_steps
        from api.db.queries.campaigns import get_campaign

        async with session_factory() as db:
            campaign = await get_campaign(db, campaign_id, user_id)
            if campaign is None:
                return {"error": "campaign not found or not owned by user"}
            steps = await list_campaign_steps(db, campaign_id)

        completed = [s for s in steps if (s.get("status") == "complete"
                                          and s.get("result") is not None)]
        if not completed:
            return {
                "campaign_id": campaign_id,
                "best_so_far": None,
                "proposals": [],
                "strategy": "no completed steps yet — record at least one outcome before proposing",
            }

        # Extract yield-like numeric from result JSON if present.
        def _yield(step: dict[str, Any]) -> float:
            res = step.get("result") or {}
            if not isinstance(res, dict):
                return float("-inf")
            for key in ("yield", "yield_percent", "yield_pct"):
                v = res.get(key)
                if isinstance(v, (int, float)):
                    return float(v)
            return float("-inf")

        completed.sort(key=_yield, reverse=True)
        best = completed[0]
        best_conditions = best.get("conditions") or {}
        best_yield = _yield(best)

        proposals: list[dict[str, Any]] = []
        # 1. Exploit: take the best as-is.
        proposals.append({
            "conditions": dict(best_conditions) if isinstance(best_conditions, dict) else {},
            "rationale": (
                f"Exploit — reproduce best-seen ({best_yield:.1f}% yield) "
                "before perturbing."
            ),
        })
        # 2. Mild perturb on temperature if present.
        if isinstance(best_conditions, dict) and "temperature" in best_conditions:
            try:
                t = float(best_conditions["temperature"])
                tweaked = {**best_conditions, "temperature": round(t + 10.0, 1)}
                proposals.append({
                    "conditions": tweaked,
                    "rationale": "Exploit/tweak — best conditions with temperature +10°C.",
                })
            except (TypeError, ValueError):
                pass
        # 3. Explore via solvent swap if multiple completed runs used different solvents.
        seen_solvents = {
            s.get("conditions", {}).get("solvent")
            for s in completed
            if isinstance(s.get("conditions"), dict) and s["conditions"].get("solvent")
        }
        seen_solvents.discard(None)
        if isinstance(best_conditions, dict) and best_conditions.get("solvent"):
            for solvent in sorted(seen_solvents):
                if solvent != best_conditions["solvent"]:
                    proposals.append({
                        "conditions": {**best_conditions, "solvent": solvent},
                        "rationale": f"Explore — swap solvent to {solvent}.",
                    })
                    break

        return {
            "campaign_id": campaign_id,
            "best_so_far": {"conditions": best_conditions, "yield": best_yield},
            "proposals": proposals[: max(1, n_proposals)],
            "strategy": "heuristic-v1",
        }

    return mcp
