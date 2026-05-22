"""ELN integration tools split out of `tools_external.py`.

Three tools covering the ELN read + ingest + manual-outcome paths
that share the `_fetch_eln_raw` SSRF-pinned fetch helper:

  - `eln_fetch_experiment` — read-only ELN experiment fetch
  - `ingest_eln_experiment` — fetch + normalise + persist as a
    `reaction_outcomes` row, idempotent on experiment_id
  - `record_manual_outcome` — user-pasted-data variant; persists
    a `reaction_outcomes` row with `source='manual'`

`build_eln_tools(user_id, session_factory)` returns the `SdkMcpTool`
list. `user_id` is needed for `created_by`/`fetched_by` audit fields;
`session_factory` opens DB sessions for the persistence path.
"""
from __future__ import annotations

import logging
import os
import re
import uuid
from typing import Any

from claude_agent_sdk import SdkMcpTool
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.agent.tool_adapter import wrap_tool
from api.agent.tool_helpers import (
    _fetch_validated,
    _redact_ssrf_error,
    _SSRFError,
)

logger = logging.getLogger(__name__)


def build_eln_tools(
    user_id: str,
    session_factory: async_sessionmaker[AsyncSession],
) -> list[SdkMcpTool[Any]]:
    """Build the ELN + manual-outcome tools."""

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

    return [
        wrap_tool("eln_fetch_experiment", eln_fetch_experiment),
        wrap_tool("ingest_eln_experiment", ingest_eln_experiment),
        wrap_tool("record_manual_outcome", record_manual_outcome),
    ]
