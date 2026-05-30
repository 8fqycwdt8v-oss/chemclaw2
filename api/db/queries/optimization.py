"""Staged Bayesian-optimisation dispatcher for synthesis-campaign condition
proposals.

Three stages, picked at call time:

    0  Heuristic   — no parameter_spec; falls back to V1 logic in tools.py
    1  BOFIRE LHS  — parameter_spec + BOFIRE installed + completed < threshold
    2  BOFIRE GP+qLogEI — parameter_spec + BOFIRE + completed >= threshold
                          AND botorch importable

The BOFIRE imports live behind try/except so the base chemclaw2-backend
install (no [opt] extras) doesn't pay any dep tax — propose_next_conditions
falls back to stage 0 with a friendly install hint when BOFIRE isn't present.

The "did the user install [opt]?" question is detected by *attempting* the
BOFIRE strategy import. We don't probe at module load — keeps the import
graph cheap when the workload doesn't touch BO.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from api.agent.parameter_spec import ParameterSpec

logger = logging.getLogger(__name__)


# Below this many completed outcomes, GP fit is noise — fall back to LHS
# regardless of whether [optimization] extras are installed. Operators
# can override via env when the parameter space is low-D and a GP fit at
# N=5 makes sense; documented in the Tier-3 plan.
DEFAULT_MIN_DATAPOINTS_FOR_GP = 10


def _min_datapoints_for_gp() -> int:
    raw = os.environ.get("BO_MIN_DATAPOINTS", str(DEFAULT_MIN_DATAPOINTS_FOR_GP))
    try:
        v = int(raw)
    except ValueError:
        logger.warning("invalid BO_MIN_DATAPOINTS=%r — using default", raw)
        return DEFAULT_MIN_DATAPOINTS_FOR_GP
    return max(2, v)


# ── Plan-JSON CRUD ────────────────────────────────────────────────────────────


async def set_campaign_parameter_spec(
    db: AsyncSession,
    campaign_id: str,
    user_id: str,
    spec: ParameterSpec,
) -> bool:
    """Merge a parameter spec into `synthesis_campaigns.plan.parameter_spec`.

    Owner-scoped via `created_by = :uid`. Returns True if a row was
    updated, False if the campaign doesn't exist or isn't owned.
    """
    async with db.begin():
        result = await db.execute(
            text("""
                UPDATE synthesis_campaigns
                   SET plan = COALESCE(plan, '{}'::jsonb)
                              || jsonb_build_object('parameter_spec',
                                                    CAST(:spec AS jsonb)),
                       updated_at = NOW()
                 WHERE id = CAST(:cid AS uuid)
                   AND created_by = :uid
            """),
            {"cid": campaign_id, "uid": user_id,
             "spec": spec.model_dump_json()},
        )
        return result.rowcount > 0  # type: ignore[attr-defined]


async def get_campaign_parameter_spec(
    db: AsyncSession,
    campaign_id: str,
    user_id: str,
) -> ParameterSpec | None:
    """Return the parsed spec, or None if absent / not owned / malformed."""
    result = await db.execute(
        text("""
            SELECT plan -> 'parameter_spec' AS spec
            FROM synthesis_campaigns
            WHERE id = CAST(:cid AS uuid)
              AND created_by = :uid
        """),
        {"cid": campaign_id, "uid": user_id},
    )
    row = result.one_or_none()
    if row is None or row[0] is None:
        return None
    raw = row[0]
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("campaign=%s parameter_spec is unparseable JSON", campaign_id)
            return None
    try:
        return ParameterSpec.model_validate(raw)
    except Exception as e:
        logger.warning("campaign=%s parameter_spec invalid: %s", campaign_id, e)
        return None


# ── Outcome fetch (reaction_outcomes JOIN campaign_steps) ────────────────────


async def load_campaign_experiments(
    db: AsyncSession,
    campaign_id: str,
    spec: ParameterSpec,
) -> list[dict[str, Any]]:
    """Pull completed-step outcomes joined with their input conditions for
    BOFIRE `tell()`. Returns a list of dicts, one per completed step, each
    mapping `spec.input_keys + spec.output_keys` to values.

    Rows with NULLs in any declared output are dropped (BOFIRE GP fit
    can't handle partial observations in V1). Conditions come from
    `reaction_outcomes.conditions_actual` (recorded ground truth) when
    present, falling back to `campaign_steps.conditions` (the planned
    values) otherwise.
    """
    result = await db.execute(
        text("""
            SELECT cs.id::text AS step_id,
                   cs.conditions AS planned_conditions,
                   ro.conditions_actual AS actual_conditions,
                   ro.yield_pct,
                   ro.status,
                   ro.observations
            FROM campaign_steps cs
            LEFT JOIN reaction_outcomes ro
              ON ro.campaign_step_id = cs.id
            WHERE cs.campaign_id = CAST(:cid AS uuid)
              AND cs.status = 'complete'
            ORDER BY cs.step_idx
        """),
        {"cid": campaign_id},
    )
    experiments: list[dict[str, Any]] = []
    output_key_yield = "yield_pct"  # V1: hard-coded mapping
    for row in result:
        d = dict(row._mapping)
        actual = d.get("actual_conditions") or d.get("planned_conditions") or {}
        if isinstance(actual, str):
            try:
                actual = json.loads(actual)
            except json.JSONDecodeError:
                continue
        if not isinstance(actual, dict):
            continue

        # Extract input values, all required.
        experiment: dict[str, Any] = {}
        missing_input = False
        for key in spec.input_keys:
            if key in actual:
                experiment[key] = actual[key]
            else:
                missing_input = True
                break
        if missing_input:
            continue

        # Extract output values. V1 only knows yield_pct as an output key —
        # tool layer rejects specs whose output keys don't match this.
        # Phase D will widen the supported output set.
        missing_output = False
        for key in spec.output_keys:
            if key == output_key_yield and d.get("yield_pct") is not None:
                experiment[key] = float(d["yield_pct"])
            else:
                missing_output = True
                break
        if missing_output:
            continue
        experiments.append(experiment)
    return experiments


# ── BOFIRE adapter (only imports BOFIRE when called) ──────────────────────────


def _to_bofire_domain(spec: ParameterSpec) -> Any:
    """Translate a chemclaw2 ParameterSpec to a BOFIRE Domain. Raises
    ImportError if BOFIRE isn't installed — caller must handle."""
    from bofire.data_models.domain.api import Domain, Inputs, Outputs
    from bofire.data_models.features.api import (
        CategoricalInput,
        ContinuousInput,
        ContinuousOutput,
    )
    from bofire.data_models.objectives.api import (
        MaximizeObjective,
        MinimizeObjective,
    )

    input_features = []
    for i in spec.inputs:
        if i.type == "continuous":
            input_features.append(ContinuousInput(key=i.key, bounds=(i.min, i.max)))
        else:  # categorical
            input_features.append(CategoricalInput(key=i.key, categories=i.categories))

    output_features = []
    for o in spec.outputs:
        obj = (MaximizeObjective(w=1.0) if o.direction == "maximize"
               else MinimizeObjective(w=1.0))
        output_features.append(ContinuousOutput(key=o.key, objective=obj))

    return Domain(inputs=Inputs(features=input_features),
                  outputs=Outputs(features=output_features))


def _bofire_optimization_available() -> bool:
    """Probe whether bofire[optimization] is installed (botorch is the
    canonical dep). Cheap import; no side effects."""
    try:
        import botorch  # noqa: F401
        return True
    except ImportError:
        return False


def propose_via_bofire(
    spec: ParameterSpec,
    experiments: list[dict[str, Any]],
    n_proposals: int,
) -> dict[str, Any]:
    """Return BOFIRE-driven proposals.

    Strategy:
      - len(experiments) < min_for_gp: stage 1 (RandomStrategy / LHS).
      - else: stage 2 (SoboStrategy + qLogEI) when botorch importable,
        else fall back to stage 1 with a warn.

    Raises ImportError when BOFIRE itself isn't installed — caller
    catches and falls back to V1 heuristic. Raises ValueError for
    spec shapes the V1 dispatcher can't handle (e.g. multi-objective).
    Both raise BEFORE importing BOFIRE / pandas so the rejection path
    works even on hosts without [opt] extras.
    """
    if spec.is_multi_objective():
        # MoboStrategy is Tier-3-Phase-D follow-up.
        raise ValueError(
            "multi-objective optimisation not supported in V1; "
            "declare a single output and use a constraint for the rest",
        )

    import pandas as pd
    domain = _to_bofire_domain(spec)
    n_completed = len(experiments)
    min_for_gp = _min_datapoints_for_gp()
    use_gp = n_completed >= min_for_gp and _bofire_optimization_available()

    if use_gp:
        try:
            # bofire >=0.3 renamed the q-Log Expected Improvement acquisition
            # function `qLogExpectedImprovement` -> `qLogEI`.
            from bofire.data_models.acquisition_functions.api import qLogEI
            from bofire.data_models.strategies.api import (
                SoboStrategy as SoboStrategyDataModel,
            )
            from bofire.strategies.api import SoboStrategy
        except ImportError as e:
            logger.warning("bofire[optimization] partial import failure: %s", e)
            use_gp = False

    if use_gp:
        data_model = SoboStrategyDataModel(
            domain=domain,
            acquisition_function=qLogEI(),
        )
        strategy = SoboStrategy(data_model=data_model)
        df = pd.DataFrame(experiments)
        # BOFIRE expects experiment outputs alongside inputs; the column order
        # follows the Domain spec.
        strategy.tell(df)
        proposals_df = strategy.ask(candidate_count=n_proposals)
        strategy_label = "bofire-sobo-qlei"
    else:
        # Stage 1 — Random / LHS sampling. No surrogate fit.
        from bofire.data_models.strategies.api import (
            RandomStrategy as RandomStrategyDataModel,
        )
        from bofire.strategies.api import RandomStrategy
        data_model = RandomStrategyDataModel(domain=domain)
        strategy = RandomStrategy(data_model=data_model)
        proposals_df = strategy.ask(candidate_count=n_proposals)
        strategy_label = (
            "bofire-lhs"
            if n_completed < min_for_gp
            else "bofire-lhs-fallback-no-botorch"
        )

    # Map back to chemclaw2 dict format.
    proposals = []
    for _, row in proposals_df.iterrows():
        conditions = {k: _python_native(row[k]) for k in spec.input_keys if k in row}
        proposals.append({
            "conditions": conditions,
            "rationale": f"BOFIRE {strategy_label} proposal",
        })

    return {
        "strategy": strategy_label,
        "n_experiments_fitted": n_completed,
        "proposals": proposals,
    }


def _python_native(v: Any) -> Any:
    """Convert numpy / pandas scalar to native Python so JSON-ser works."""
    # numpy scalars expose .item() which returns the corresponding native.
    if hasattr(v, "item"):
        try:
            return v.item()
        except Exception:
            pass
    return v
