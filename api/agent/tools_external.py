"""External-fetch MCP tools — web search, document fetch, chemistry lookups.

Four tools that reach outside the chemclaw2 process via SSRF-pinned
`_fetch_validated`:

  - `web_search` — Brave Search API, domain-allowlisted site filter
  - `fetch_document` — generic HTTP fetch with markdown / bytes modes
  - `name_to_structure` — NCI CACTUS name→SMILES/CAS/IUPAC, 7-day cache
  - `patent_coverage` — PubChem SMILES → CID → patent xref count

ELN integration (`build_eln_tools`) and retrosynthesis
(`build_retrosynth_tools`) used to live here too — they're now in
`tools_eln.py` and `tools_retrosynth.py` respectively. The combined
`build_external_tools` below composes all three for the existing
single-import call sites.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import urllib.parse
from typing import Any

import httpx
from claude_agent_sdk import SdkMcpTool
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.agent.tool_adapter import wrap_tool
from api.agent.tool_helpers import (
    _fetch_validated,
    _html_to_text,
    _is_allowed_domain,
    _redact_ssrf_error,
    _SSRFError,
)
from api.agent.tools_eln import build_eln_tools
from api.agent.tools_retrosynth import build_retrosynth_tools

logger = logging.getLogger(__name__)


def build_external_tools(
    user_id: str,
    session_factory: async_sessionmaker[AsyncSession],
) -> list[SdkMcpTool[Any]]:
    """Build the external-fetch tools — composes web/fetch + ELN + retrosynth.

    Web tools live in this file (web_search, fetch_document,
    name_to_structure, patent_coverage). ELN and retrosynthesis tools
    delegate to `build_eln_tools` / `build_retrosynth_tools` in their
    dedicated modules so this file stays under the ~400-line guideline.
    """

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

    async def fetch_document(
        url: str,
        format: str = "markdown",
    ) -> dict[str, Any]:
        """Fetch a scientific document from an allowed domain."""
        MAX_BYTES = 500_000
        try:
            r = await _fetch_validated(url, enforce_domain_allowlist=True)
        except _SSRFError as e:
            return _redact_ssrf_error("fetch_document", e)
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
                # SSRF guard is fatal for the whole call. Log the (IP-bearing)
                # detail and surface only a generic message — same redaction
                # policy as _redact_ssrf_error (CLAUDE.md §security-4).
                logger.warning("cactus_ssrf_blocked field=%s name=%s: %s", field, q[:50], e)
                return field, None, "URL rejected by SSRF guard"
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
            return _redact_ssrf_error("pubchem_cid_lookup", e, cid=None)
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
            return _redact_ssrf_error("pubchem_patent_lookup", e, cid=cid)
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

    return [
        wrap_tool("web_search", web_search),
        wrap_tool("fetch_document", fetch_document),
        wrap_tool("name_to_structure", name_to_structure),
        wrap_tool("patent_coverage", patent_coverage),
        *build_eln_tools(user_id, session_factory),
        *build_retrosynth_tools(user_id, session_factory),
    ]
