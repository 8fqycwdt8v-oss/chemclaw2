"""External-fetch MCP tools split out of api/agent/tools.py.

Ten tools that reach outside the chemclaw2 process — web search,
document fetch, ELN integration (read + ingest + manual outcome record),
CACTUS name→structure, PubChem patent coverage, retrosynthesis
(single-step + deep tree). All run through the SSRF-pinned
`_fetch_validated` helper where applicable.

`build_external_tools(user_id, session_factory)` returns the
`SdkMcpTool` list.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import urllib.parse
import uuid
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

logger = logging.getLogger(__name__)


def build_external_tools(
    user_id: str,
    session_factory: async_sessionmaker[AsyncSession],
) -> list[SdkMcpTool[Any]]:
    """Build the web / fetch / ELN / chemistry-lookup tools."""

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
            r = await _fetch_validated(
                f"{eln_base}/api/eln/experiments/{exp_id}",
                enforce_domain_allowlist=False,
                timeout=10.0,
                headers={"Authorization": f"Bearer {eln_key}"},
            )
        except _SSRFError as e:
            return _redact_ssrf_error("eln_fetch", e)
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

    async def eln_fetch_experiment(experiment_id: str) -> dict[str, Any]:
        """Fetch a read-only experiment record from the connected ELN system."""
        return await _fetch_eln_raw(experiment_id)

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
            routes = await asyncio.get_running_loop().run_in_executor(
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

    async def propose_retrosynthesis_deep(
        target_smiles: str,
        max_routes: int = 5,
        max_seconds: int = 300,
    ) -> dict[str, Any]:
        """Multi-step retrosynthesis search via AiZynthFinder.

        Complements `propose_retrosynthesis` (the 11-template single-step
        library). Use for full route discovery on a confirmed target;
        use the fast single-step tool for first-pass disconnection
        enumeration.

        Behaviour:
          - Requires `[retrosynth]` extras (`pip install -e .[retrosynth]`
            on the worker). When absent: returns
            `{"error": "[retrosynth] extras not installed"}` cleanly.
          - First call downloads ~500 MB of demo policy + filter models
            into AiZynthFinder's cache dir. Subsequent calls reuse them.
            Operators can point at the full USPTO bundle via
            `AIZYNTH_CONFIG_PATH`.
          - Wall-cap at `max_seconds` (default 300, 1–600 allowed).
            Tree search is sync; we offload to a thread pool so the
            event loop stays responsive.
          - Result cached in `external_facts` keyed by
            `aizynth:<smiles>` for 30 days.

        Returns:
            {target, routes: [...], total, model, cached: bool} or
            {error}. Each route is a nested AiZynthFinder reaction tree
            (smiles, type, children, in_stock, …).
        """
        from datetime import datetime as _dt
        from datetime import timedelta, timezone

        from api.db.queries.knowledge import (
            get_external_fact_by_source_id, upsert_external_fact,
        )

        s = target_smiles.strip()
        if not s or len(s) > 1000:
            return {"error": "target_smiles must be 1-1000 chars"}
        if not (1 <= max_routes <= 20):
            return {"error": "max_routes must be between 1 and 20"}
        if not (1 <= max_seconds <= 600):
            return {"error": "max_seconds must be between 1 and 600"}

        cache_key = f"aizynth:{s}"
        cutoff = _dt.now(tz=timezone.utc) - timedelta(days=30)
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
                    except json.JSONDecodeError:
                        logger.warning(
                            "external_facts(source_id=%s).payload not "
                            "JSON-parseable; re-running aizynth search",
                            cache_key,
                        )
                        payload = {}
                if isinstance(payload, dict) and "routes" in payload:
                    return {**payload, "cached": True}

        try:
            from api.agent.retrosynth_deep import run_deep_retrosynthesis
        except ImportError:
            return {
                "error": (
                    "[retrosynth] extras not installed — run "
                    "`pip install chemclaw2-backend[retrosynth]` "
                    "on this worker to enable deep retrosynthesis"
                ),
            }

        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(run_deep_retrosynthesis, s, max_routes),
                timeout=float(max_seconds),
            )
        except asyncio.TimeoutError:
            return {
                "error": f"aizynthfinder timed out after {max_seconds}s",
                "target": s,
            }
        except ValueError as e:
            return {"error": str(e)}
        except Exception:
            logger.exception("aizynthfinder run failed smiles_len=%d", len(s))
            return {"error": "deep retrosynthesis failed; see worker logs"}

        async with session_factory() as db:
            await upsert_external_fact(
                db, "aizynth", cache_key,
                result,
                f"deep retrosynthesis for {s} ({result.get('total', 0)} routes)",
                fetched_by=user_id,
            )
        return {**result, "cached": False}

    return [
        wrap_tool("web_search", web_search),
        wrap_tool("fetch_document", fetch_document),
        wrap_tool("eln_fetch_experiment", eln_fetch_experiment),
        wrap_tool("ingest_eln_experiment", ingest_eln_experiment),
        wrap_tool("record_manual_outcome", record_manual_outcome),
        wrap_tool("name_to_structure", name_to_structure),
        wrap_tool("patent_coverage", patent_coverage),
        wrap_tool("propose_retrosynthesis", propose_retrosynthesis),
        wrap_tool("propose_retrosynthesis_deep", propose_retrosynthesis_deep),
    ]
