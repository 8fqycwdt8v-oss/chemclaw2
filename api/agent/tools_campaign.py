"""Synthesis-campaign + chem-record + active-learning MCP tools.

Seven tools that didn't fit the chemistry / knowledge / investigation /
external thematic groups:

  - synthesis_campaign lifecycle: start_synthesis_campaign,
    confirm_synthesis_plan
  - record_feedback: thumbs-up/down on a chat turn
  - chem record: register_compound_property, record_predicted_conditions
  - active learning: declare_campaign_parameter_space,
    propose_next_conditions

`build_campaign_tools(user_id, session_id, session_factory)` returns
the `SdkMcpTool` list.
"""
from __future__ import annotations

import asyncio
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
        async with session_factory() as db:
            async with db.begin():
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
        async with session_factory() as db:
            async with db.begin():
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
        if drfp_bits is not None and not is_fingerprint(drfp_bits):
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
        from api.db.queries.campaigns import get_campaign_with_steps
        from api.db.queries.optimization import (
            get_campaign_parameter_spec,
            load_campaign_experiments,
            propose_via_bofire,
        )

        if not (1 <= n_proposals <= 20):
            return {"error": "n_proposals must be between 1 and 20"}

        async with session_factory() as db:
            campaign = await get_campaign_with_steps(db, campaign_id, user_id)
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

    return [
        wrap_tool("start_synthesis_campaign", start_synthesis_campaign),
        wrap_tool("confirm_synthesis_plan", confirm_synthesis_plan),
        wrap_tool("record_feedback", record_feedback),
        wrap_tool("register_compound_property", register_compound_property),
        wrap_tool("record_predicted_conditions", record_predicted_conditions),
        wrap_tool("declare_campaign_parameter_space", declare_campaign_parameter_space),
        wrap_tool("propose_next_conditions", propose_next_conditions),
    ]
