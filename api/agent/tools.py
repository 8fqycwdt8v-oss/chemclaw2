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

    return mcp
