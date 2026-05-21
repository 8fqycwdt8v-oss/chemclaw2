"""Agent tools — Python port of packages/agent-tools/src/*.ts.

All tools are registered on an in-process MCP server via the Python Agent SDK's
create_sdk_mcp_server() / @mcp.tool() pattern.

The SSRF guards, HTTP fetch wrapper, HTML→text helper, paper-chunk
ingest, and heuristic conditions proposer live in `tool_helpers.py` and
are imported below. Anything reaching into those internals (tests,
document_enrichment) imports from `api.agent.tool_helpers` directly.
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
from claude_agent_sdk import create_sdk_mcp_server
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.agent.tool_helpers import (
    _fetch_validated,
    _heuristic_propose,
    _html_to_text,
    _is_allowed_domain,
    _PredictedConditionsPayload,
    _redact_ssrf_error,
    _SSRFError,
)
from api.agent.tools_chem import register_chem_tools
from api.agent.tools_knowledge import register_knowledge_tools

logger = logging.getLogger(__name__)


# ── Tool factory ──────────────────────────────────────────────────────────────

def build_chemclaw_mcp_server(
    user_id: str,
    session_id: str | None,
    session_factory: async_sessionmaker[AsyncSession],
) -> Any:
    """Build an in-process MCP server with all chemclaw2 agent tools."""
    mcp = create_sdk_mcp_server("chemclaw2-tools")

    register_chem_tools(mcp, session_factory)
    register_knowledge_tools(mcp, user_id, session_factory)

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

        Figure capture (matplotlib): the sandbox prepends
        `matplotlib.use("Agg")` so any `plt.savefig("foo.png")` call
        lands in the tempdir and is base64-encoded into the response.
        Total cap 1.5 MB across all PNGs per run; single files > 1 MB
        are dropped. Stderr gains a `[sandbox] artifact truncated`
        line when the cap fires.

        `investigation_id`, if provided, anchors the execution to a
        research thread (and is required if no chat session_id is
        available — both can't be NULL). Both ownership is checked.

        Hard caps: CPU 1–300s (default 30), memory 512 MB, stdout 1 MB,
        stderr 256 KB, source ≤200 KB. Returns:
          {execution_id, status, exit_code, duration_ms, stdout, stderr,
           artifacts: [{filename, mime, size_bytes, b64}]}
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
                    artifacts=result.artifacts,
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
            # Full artefact payloads (b64 included). The agent just ran
            # the code; it gets the figures immediately so it can render
            # them. `list_code_executions` strips b64 to keep list
            # responses paginatable.
            "artifacts": result.artifacts,
        }

    @mcp.tool()
    async def get_code_execution(execution_id: str) -> dict[str, Any]:
        """Fetch a single past execution with FULL artefact payloads.

        Owner-scoped via `created_by`. Returns {execution} on success or
        {error} when the id is unknown / not owned. Use to recover the
        b64 PNG payload from an execution that was listed without it.
        """
        from api.db.queries.code_executions import get_execution
        async with session_factory() as db:
            row = await get_execution(db, execution_id, user_id)
        if row is None:
            return {"error": "execution not found or not owned by user"}
        return {"execution": row}

    @mcp.tool()
    async def list_code_executions(
        investigation_id: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        """List recent sandbox executions. Filter by `investigation_id`,
        or omit to list the caller's recent runs across all contexts.
        Owner-scoped on `created_by`.

        Artefacts in the response carry metadata only (filename, mime,
        size_bytes) — no b64 payload, to keep list responses small.
        Fetch the full payload via `get_code_execution(execution_id)`."""
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
    async def declare_campaign_parameter_space(
        campaign_id: str,
        parameter_spec: dict[str, Any],
    ) -> dict[str, Any]:
        """Declare the input/output parameter space for a campaign's BO loop.

        `parameter_spec` JSON schema:
          {
            "inputs": [
              {"key": "temperature", "type": "continuous", "min": 20, "max": 120, "unit": "C"},
              {"key": "solvent", "type": "categorical", "categories": ["THF","DMF","EtOH"]},
              ...
            ],
            "outputs": [
              {"key": "yield_pct", "direction": "maximize", "unit": "%"}
            ]
          }

        V1 constraints: categorical ≤ 8 levels; ≤ 20 inputs; ≤ 4 outputs;
        single-objective only (multiple outputs accepted by schema but
        rejected by `propose_next_conditions` until MoboStrategy lands).
        Output key MUST be `yield_pct` — the only outcome the V1
        dispatcher knows how to feed from `reaction_outcomes` to BOFIRE.

        Once declared, `propose_next_conditions` switches from the V1
        heuristic to BOFIRE-driven proposals (LHS until ≥10 completed
        steps; surrogate-driven GP+qLogEI thereafter when the [opt]
        extras are installed).

        Returns {ok: bool, campaign_id, n_inputs, n_outputs, strategy_hint}.
        """
        from api.agent.parameter_spec import ParameterSpec
        from api.db.queries.optimization import set_campaign_parameter_spec
        try:
            spec = ParameterSpec.model_validate(parameter_spec)
        except Exception as e:
            return {"ok": False, "error": f"invalid parameter_spec: {e}"}
        # V1: only yield_pct is supported as an output key by the
        # outcomes feeder. Reject other names early with a clear message.
        valid_outputs = {"yield_pct"}
        for o in spec.outputs:
            if o.key not in valid_outputs:
                return {
                    "ok": False,
                    "error": (
                        f"output key {o.key!r} not supported in V1 — "
                        f"only {sorted(valid_outputs)} can be fed from "
                        "reaction_outcomes today"
                    ),
                }
        async with session_factory() as db:
            ok = await set_campaign_parameter_spec(db, campaign_id, user_id, spec)
        if not ok:
            return {"ok": False, "error": "campaign not found or not owned by user"}
        return {
            "ok": True,
            "campaign_id": campaign_id,
            "n_inputs": len(spec.inputs),
            "n_outputs": len(spec.outputs),
            "strategy_hint": (
                "BOFIRE LHS until ≥10 completed steps; GP+qLogEI thereafter "
                "if [opt] extras are installed."
            ),
        }

    @mcp.tool()
    async def propose_next_conditions(
        campaign_id: str,
        n_proposals: int = 3,
    ) -> dict[str, Any]:
        """Propose conditions for the next experimental step of a campaign.

        Three-stage dispatch:

          0  Heuristic (no parameter_spec declared): rank completed steps
             by yield, return best + temperature tweak + solvent swap.
          1  BOFIRE LHS (parameter_spec exists, < 10 completed outcomes
             OR botorch not installed): structured Latin-Hypercube
             samples from the declared input space. Better diversity
             than the V1 heuristic; no surrogate fit.
          2  BOFIRE GP+qLogEI (parameter_spec + ≥ 10 completed outcomes
             + botorch installed via [opt] extras): MixedSingleTaskGP
             surrogate + qLogExpectedImprovement acquisition.

        Use `declare_campaign_parameter_space` first to unlock stages 1/2.

        Returns {campaign_id, strategy, proposals, best_so_far?, n_experiments_fitted?}.
        """
        from api.db.queries.campaigns import get_campaign
        from api.db.queries.optimization import (
            get_campaign_parameter_spec,
            load_campaign_experiments,
            propose_via_bofire,
        )

        if not (1 <= n_proposals <= 20):
            return {"error": "n_proposals must be between 1 and 20"}

        async with session_factory() as db:
            campaign = await get_campaign(db, campaign_id, user_id)
            if campaign is None:
                return {"error": "campaign not found or not owned by user"}
            spec = await get_campaign_parameter_spec(db, campaign_id, user_id)

        # ── Stage 1/2 (BOFIRE-driven) when a parameter_spec is declared ───
        if spec is not None:
            async with session_factory() as db:
                experiments = await load_campaign_experiments(db, campaign_id, spec)
            try:
                # GP fit can take multiple seconds — offload to a thread so
                # the event loop stays responsive for other coroutines
                # (concurrent paper_qa, agent streams, etc).
                result = await asyncio.to_thread(
                    propose_via_bofire, spec, experiments, n_proposals,
                )
                return {"campaign_id": campaign_id, **result}
            except ImportError:
                logger.info(
                    "campaign=%s falling back to heuristic — bofire not installed; "
                    "pip install chemclaw2-backend[opt] to enable",
                    campaign_id,
                )
                # Fall through to heuristic; preserve the same response shape
                # the agent expects but flag the install hint in the strategy.
                heuristic = await _heuristic_propose(
                    session_factory, campaign_id, n_proposals,
                )
                heuristic["strategy"] = (
                    "heuristic-v1-bofire-unavailable "
                    "(install chemclaw2-backend[opt] for BO)"
                )
                return heuristic
            except ValueError as e:
                return {"error": str(e)}

        # ── Stage 0 (V1 heuristic) when no parameter_spec ─────────────────
        return await _heuristic_propose(session_factory, campaign_id, n_proposals)

    # ── §H deep retrosynthesis via AiZynthFinder ─────────────────────────────

    @mcp.tool()
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

    return mcp
