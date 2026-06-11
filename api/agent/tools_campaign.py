"""Synthesis-campaign + chem-record + active-learning MCP tools.

Seven tools that didn't fit the chemistry / knowledge / investigation /
external thematic groups:

  - synthesis_campaign lifecycle: start_synthesis_campaign,
    confirm_synthesis_plan
  - record_feedback: thumbs-up/down on a chat turn
  - chem record: register_compound_property, record_predicted_conditions
    propose_next_conditions

`build_campaign_tools(user_id, session_id, session_factory)` returns
the `SdkMcpTool` list.
"""
from __future__ import annotations

import logging
from typing import Any

from claude_agent_sdk import SdkMcpTool
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.agent.tool_adapter import wrap_tool
from api.agent.tool_helpers import _heuristic_propose, _PredictedConditionsPayload
from api.agent.tool_validation import is_fingerprint

logger = logging.getLogger(__name__)


def build_campaign_tools(
    user_id: str,
    session_id: str | None,
    session_factory: async_sessionmaker[AsyncSession],
) -> list[SdkMcpTool[Any]]:
    """Build the campaign + chem-record + active-learning tools."""

    async def start_synthesis_campaign(
        target_smiles: str | None = None,
    ) -> dict[str, Any]:
        """Create a new synthesis campaign for the current session."""
        from api.db.queries.campaigns import create_campaign
        if not session_id:
            return {"error": "No session_id — cannot create campaign"}
        async with session_factory() as db, db.begin():
            campaign_id = await create_campaign(db, session_id, user_id, target_smiles)
        return {"campaign_id": campaign_id, "status": "planning"}

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
        async with session_factory() as db, db.begin():
            # update_campaign_status is owner-scoped. add_campaign_step is
            # NOT (it trusts the caller-supplied campaign_id), so we MUST
            # fail closed here when the owner/state check finds no row —
            # otherwise a forged campaign_id would let one user inject steps
            # into another user's campaign (FK ≠ access control).
            advanced = await update_campaign_status(
                db, campaign_id, user_id, "running", plan=plan
            )
            if not advanced:
                return {
                    "error": (
                        "Campaign not found, not owned by you, or not in a "
                        "state that accepts a plan."
                    )
                }
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

        Call this after `suggest_conditions_from_neighbors` returns, so the
        next turn (and future campaigns over the same reaction) hit the
        cache instead of recomputing.

        `conditions` must be an object shaped like:
          {catalysts: [str], solvents: [str], reagents: [str],
           temperature_c: float|null}
        `model` should identify both backend and version, e.g.
          'neighbor-aggregation:v1'.
        `source` is the high-level origin: 'neighbor_aggregation' | 'manual'.
        """
        try:
            payload = _PredictedConditionsPayload.model_validate(conditions)
        except ValidationError as e:
            return {"error": f"invalid conditions payload: {e.errors()[0]['msg']}"}

        if not rxn_smiles or ">>" not in rxn_smiles:
            return {"error": "rxn_smiles must contain '>>' separator"}
        if drfp_bits is not None and not is_fingerprint(drfp_bits):
            return {"error": "drfp_bits must be exactly 2048 binary digits if provided"}

        from api.db.queries.reaction_conditions import insert_prediction
        async with session_factory() as db, db.begin():
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

    async def propose_next_conditions(
        campaign_id: str,
        n_proposals: int = 3,
    ) -> dict[str, Any]:
        """Propose conditions for the next experimental step of a campaign.

        Heuristic: ranks the campaign's completed steps by yield and
        returns the best conditions plus a temperature tweak and a
        solvent swap. Deterministic, dependency-free.

        Returns {campaign_id, strategy, proposals, best_so_far?}.
        """
        from api.db.queries.campaigns import get_campaign_with_steps

        if not (1 <= n_proposals <= 20):
            return {"error": "n_proposals must be between 1 and 20"}

        async with session_factory() as db:
            campaign = await get_campaign_with_steps(db, campaign_id, user_id)
            if campaign is None:
                return {"error": "campaign not found or not owned by user"}

        return await _heuristic_propose(session_factory, campaign_id, n_proposals)

    return [
        wrap_tool("start_synthesis_campaign", start_synthesis_campaign),
        wrap_tool("confirm_synthesis_plan", confirm_synthesis_plan),
        wrap_tool("record_feedback", record_feedback),
        wrap_tool("register_compound_property", register_compound_property),
        wrap_tool("record_predicted_conditions", record_predicted_conditions),
        wrap_tool("propose_next_conditions", propose_next_conditions),
    ]
