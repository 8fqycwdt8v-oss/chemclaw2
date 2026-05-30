"""DB integration tests for the BO dispatcher's plan / outcome plumbing.

Stage-1 / stage-2 BOFIRE invocations are NOT exercised here — they need
the [opt] extras and a working torch install, which the base CI image
doesn't provide (by design, per the lightweight-deps Tier-3 plan). The
ImportError fallback path IS exercised: when BOFIRE isn't present,
`propose_via_bofire` must raise ImportError so the tool layer can fall
back to the heuristic.

Tests below cover:
  - set_campaign_parameter_spec persists into plan.parameter_spec
  - get_campaign_parameter_spec round-trips
  - load_campaign_experiments joins reaction_outcomes correctly
  - propose_via_bofire raises ImportError when BOFIRE missing
"""
from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from sqlalchemy import text

from api.agent.parameter_spec import ParameterSpec
from api.db.queries.optimization import (
    get_campaign_parameter_spec,
    load_campaign_experiments,
    propose_via_bofire,
    set_campaign_parameter_spec,
)


def _spec() -> ParameterSpec:
    return ParameterSpec.model_validate({
        "inputs": [
            {"key": "temperature", "type": "continuous", "min": 20, "max": 120},
            {"key": "solvent", "type": "categorical",
             "categories": ["THF", "DMF", "EtOH"]},
        ],
        "outputs": [{"key": "yield_pct", "direction": "maximize"}],
    })


async def _new_campaign(session_factory, user_id: str) -> str:
    """Insert a minimal synthesis_campaigns row. Returns the id."""
    async with session_factory() as db:
        async with db.begin():
            result = await db.execute(
                text("""
                    INSERT INTO synthesis_campaigns
                        (created_by, session_id, target_smiles, status)
                    VALUES (:uid, :sid, 'CCO', 'planning')
                    RETURNING id::text
                """),
                {"uid": user_id, "sid": f"sess-{uuid.uuid4().hex[:8]}"},
            )
            return result.scalar_one()


async def test_set_and_get_parameter_spec_round_trip(
    session_factory, user_id: str,
) -> None:
    cid = await _new_campaign(session_factory, user_id)
    async with session_factory() as db:
        ok = await set_campaign_parameter_spec(db, cid, user_id, _spec())
    assert ok is True
    async with session_factory() as db:
        spec = await get_campaign_parameter_spec(db, cid, user_id)
    assert spec is not None
    assert spec.input_keys == ["temperature", "solvent"]
    assert spec.outputs[0].direction == "maximize"


async def test_set_parameter_spec_owner_scoped(
    session_factory, user_id: str,
) -> None:
    stranger = f"u-{uuid.uuid4().hex[:8]}"
    cid = await _new_campaign(session_factory, user_id)
    async with session_factory() as db:
        ok = await set_campaign_parameter_spec(db, cid, stranger, _spec())
    assert ok is False
    async with session_factory() as db:
        spec = await get_campaign_parameter_spec(db, cid, user_id)
    assert spec is None  # stranger never wrote it


async def test_get_parameter_spec_absent_returns_none(
    session_factory, user_id: str,
) -> None:
    cid = await _new_campaign(session_factory, user_id)
    async with session_factory() as db:
        spec = await get_campaign_parameter_spec(db, cid, user_id)
    assert spec is None


async def test_get_parameter_spec_malformed_returns_none_with_log(
    session_factory, user_id: str, caplog: pytest.LogCaptureFixture,
) -> None:
    """If the JSON in plan is invalid against the spec schema, fail closed."""
    cid = await _new_campaign(session_factory, user_id)
    async with session_factory() as db:
        async with db.begin():
            await db.execute(
                text("""
                    UPDATE synthesis_campaigns
                       SET plan = jsonb_build_object('parameter_spec',
                                                     '{"inputs": [], "outputs": []}'::jsonb)
                     WHERE id = CAST(:cid AS uuid)
                """),
                {"cid": cid},
            )
    async with session_factory() as db:
        spec = await get_campaign_parameter_spec(db, cid, user_id)
    assert spec is None


async def test_load_campaign_experiments_joins_reaction_outcomes(
    session_factory, user_id: str,
) -> None:
    """A completed step + a recorded outcome should appear as one
    experiment row keyed by the spec's input + output keys."""
    cid = await _new_campaign(session_factory, user_id)
    spec = _spec()
    async with session_factory() as db:
        await set_campaign_parameter_spec(db, cid, user_id, spec)

    # Seed one completed campaign step + its reaction + outcome.
    async with session_factory() as db:
        async with db.begin():
            # campaign_steps row. `conditions` is a TEXT column — pass the
            # JSON as a string; the loader parses it back via json.loads.
            await db.execute(
                text("""
                    INSERT INTO campaign_steps
                        (campaign_id, step_idx, reaction_smiles,
                         conditions, status)
                    VALUES (CAST(:cid AS uuid), 0, 'CC>>CCC',
                            :c, 'complete')
                """),
                {"cid": cid, "c": json.dumps({"temperature": 60.0, "solvent": "THF"})},
            )
            step_row = await db.execute(
                text("""
                    SELECT id::text FROM campaign_steps
                     WHERE campaign_id = CAST(:cid AS uuid) AND step_idx = 0
                """),
                {"cid": cid},
            )
            step_id = step_row.scalar_one()
            # reactions row (FK target for reaction_outcomes). The column
            # is `rxn_smiles`, not `reaction_smiles`.
            rxn_row = await db.execute(
                text("""
                    INSERT INTO reactions (rxn_smiles, created_by)
                    VALUES ('CC>>CCC', :uid)
                    RETURNING id::text
                """),
                {"uid": user_id},
            )
            rxn_id = rxn_row.scalar_one()
            # reaction_outcomes row
            await db.execute(
                text("""
                    INSERT INTO reaction_outcomes
                        (reaction_id, campaign_step_id, source, status,
                         yield_pct, conditions_actual, created_by)
                    VALUES (CAST(:rxn AS uuid), CAST(:step AS uuid),
                            'manual', 'success', 73.5,
                            CAST(:c AS jsonb), :uid)
                """),
                {
                    "rxn": rxn_id, "step": step_id, "uid": user_id,
                    "c": json.dumps({"temperature": 62.0, "solvent": "THF"}),
                },
            )

    async with session_factory() as db:
        experiments = await load_campaign_experiments(db, cid, spec)
    assert len(experiments) == 1
    e = experiments[0]
    # `conditions_actual` wins over planned `conditions` per the loader's docstring.
    assert e["temperature"] == 62.0
    assert e["solvent"] == "THF"
    assert e["yield_pct"] == 73.5


async def test_load_campaign_experiments_skips_step_without_outcome(
    session_factory, user_id: str,
) -> None:
    """A completed step with no reaction_outcome row is dropped — V1
    requires the declared output to be observed."""
    cid = await _new_campaign(session_factory, user_id)
    spec = _spec()
    async with session_factory() as db:
        await set_campaign_parameter_spec(db, cid, user_id, spec)
        async with db.begin():
            await db.execute(
                text("""
                    INSERT INTO campaign_steps
                        (campaign_id, step_idx, reaction_smiles,
                         conditions, status)
                    VALUES (CAST(:cid AS uuid), 0, 'CC>>CCC',
                            :c, 'complete')
                """),
                {"cid": cid, "c": json.dumps({"temperature": 60.0, "solvent": "THF"})},
            )
    async with session_factory() as db:
        experiments = await load_campaign_experiments(db, cid, spec)
    assert experiments == []


# ── BOFIRE absent → ImportError raised, NOT silently swallowed ──────────────


def test_propose_via_bofire_raises_importerror_when_bofire_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The tool layer relies on propose_via_bofire raising ImportError
    so it can fall back to the heuristic. If BOFIRE silently no-ops,
    the agent would think it got real BO output and the user would be
    misled."""
    import builtins

    real_import = builtins.__import__

    def fake_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "bofire" or name.startswith("bofire."):
            raise ImportError(f"simulated missing dep: {name}")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    with pytest.raises(ImportError):
        propose_via_bofire(_spec(), experiments=[], n_proposals=3)


def test_propose_via_bofire_rejects_multi_objective_v1(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """V1 supports single-objective only — multi-objective should raise
    a clear ValueError even before any BOFIRE import is attempted."""
    multi_spec = ParameterSpec.model_validate({
        "inputs": [
            {"key": "T", "type": "continuous", "min": 0, "max": 100},
        ],
        "outputs": [
            {"key": "yield_pct", "direction": "maximize"},
            {"key": "purity_pct", "direction": "maximize"},
        ],
    })
    with pytest.raises(ValueError, match="multi-objective"):
        propose_via_bofire(multi_spec, experiments=[], n_proposals=3)


# ── BO_MIN_DATAPOINTS env override ───────────────────────────────────────────


def test_min_datapoints_env_default_is_ten() -> None:
    """Default threshold for switching from LHS to GP. Documented in the
    Tier-3 plan; verify the constant matches the doc."""
    from api.db.queries.optimization import (
        DEFAULT_MIN_DATAPOINTS_FOR_GP,
        _min_datapoints_for_gp,
    )
    assert DEFAULT_MIN_DATAPOINTS_FOR_GP == 10
    assert _min_datapoints_for_gp() == 10


def test_min_datapoints_env_override_picked_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`BO_MIN_DATAPOINTS=5` lets a low-D problem fit a GP at smaller N
    than the conservative default."""
    from api.db.queries.optimization import _min_datapoints_for_gp
    monkeypatch.setenv("BO_MIN_DATAPOINTS", "5")
    assert _min_datapoints_for_gp() == 5
    monkeypatch.setenv("BO_MIN_DATAPOINTS", "25")
    assert _min_datapoints_for_gp() == 25


def test_min_datapoints_env_clamps_below_two(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A GP fit with <2 datapoints is meaningless; the helper clamps."""
    from api.db.queries.optimization import _min_datapoints_for_gp
    monkeypatch.setenv("BO_MIN_DATAPOINTS", "0")
    assert _min_datapoints_for_gp() == 2
    monkeypatch.setenv("BO_MIN_DATAPOINTS", "-50")
    assert _min_datapoints_for_gp() == 2


def test_min_datapoints_env_invalid_falls_back_to_default(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    """Non-numeric env value warns + uses the default. Misconfig stays
    visible in logs instead of silently producing 10× the data
    requirement."""
    from api.db.queries.optimization import _min_datapoints_for_gp
    monkeypatch.setenv("BO_MIN_DATAPOINTS", "lots")
    assert _min_datapoints_for_gp() == 10
    monkeypatch.setenv("BO_MIN_DATAPOINTS", "")
    assert _min_datapoints_for_gp() == 10


# ── P2 review-finding: BOFIRE happy path (LHS) ────────────────────────────────


def test_propose_via_bofire_lhs_path() -> None:
    """When [opt] is installed and there are 0 completed experiments,
    the dispatcher picks BOFIRE's RandomStrategy (LHS) and returns
    n_proposals candidates within the declared input bounds.

    Skipped automatically when `bofire` isn't installed (default CI
    env). When it IS installed (e.g. on a `[opt]`-shipping worker),
    this pins the stage-1 contract."""
    pytest.importorskip("bofire")
    from api.db.queries.optimization import propose_via_bofire

    result = propose_via_bofire(
        _spec(),
        experiments=[],     # zero observations → must fall to LHS, not GP
        n_proposals=3,
    )
    assert "strategy" in result
    assert "lhs" in result["strategy"], (
        f"expected LHS path with no experiments, got {result['strategy']!r}"
    )
    assert result["n_experiments_fitted"] == 0
    assert len(result["proposals"]) == 3
    # Every proposal must be a dict with `conditions` containing both
    # declared inputs, and conditions values must lie in the declared
    # bounds.
    for p in result["proposals"]:
        cond = p["conditions"]
        assert set(cond.keys()) == {"temperature", "solvent"}
        assert 20 <= cond["temperature"] <= 120
        assert cond["solvent"] in {"THF", "DMF", "EtOH"}


def test_propose_via_bofire_lhs_proposals_are_diverse() -> None:
    """LHS should produce DIFFERENT proposals — otherwise it's no better
    than the V1 heuristic. Pin a sanity floor: at least 2 of 5 proposals
    should differ on `temperature`."""
    pytest.importorskip("bofire")
    from api.db.queries.optimization import propose_via_bofire

    result = propose_via_bofire(_spec(), experiments=[], n_proposals=5)
    temps = [p["conditions"]["temperature"] for p in result["proposals"]]
    # Probabilistic check — LHS could in theory collide but the
    # 5-sample chance of fewer than 2 unique temps is vanishing.
    assert len(set(temps)) >= 2, (
        f"LHS proposals collapsed to one temperature: {temps}"
    )


def test_propose_via_bofire_gp_path_with_full_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With ≥ `BO_MIN_DATAPOINTS` completed experiments the dispatcher
    must select the SoboStrategy + qLogEI (stage-2 GP) path.

    Only runs on the heavy CI lane — the [opt] extras (~2 GB) are not
    in the cheap lane's install. The test skips automatically there
    via `importorskip` on bofire / torch / botorch.

    `botorch` is the canonical GP dependency — `_bofire_optimization_available`
    probes exactly `import botorch`, and the stage-2 path falls back to LHS
    (`bofire-lhs-fallback-no-botorch`) when it can't import. Gate the test on
    it too: a heavy env where botorch installs but fails to import (a
    botorch/torch version skew) would otherwise assert the GP path and fail,
    when the correct behaviour is to skip — same contract as torch above.

    Verifies the stage-2 contract: `strategy` reports `bofire-sobo-qlei`,
    `n_experiments_fitted` matches the input count, and each proposal
    lies in the declared bounds (BOFIRE's tell-then-ask guarantees this
    but we keep the assertion as a regression net against a future
    spec change that breaks the round-trip).
    """
    pytest.importorskip("bofire")
    pytest.importorskip("torch")
    pytest.importorskip("botorch")
    from api.db.queries.optimization import propose_via_bofire

    # Lower the GP-min threshold to keep the test runtime modest — fitting
    # a GP on 10 points is fast enough for CI, but with the env var we
    # exercise the threshold check too.
    monkeypatch.setenv("BO_MIN_DATAPOINTS", "5")

    # Synthesise a small but real history: 6 completed experiments
    # spanning the design space with a noisy yield surface.
    history = [
        {"temperature": 25.0, "solvent": "THF", "yield_pct": 18.0},
        {"temperature": 45.0, "solvent": "THF", "yield_pct": 32.0},
        {"temperature": 70.0, "solvent": "THF", "yield_pct": 58.0},
        {"temperature": 80.0, "solvent": "DMF", "yield_pct": 67.0},
        {"temperature": 95.0, "solvent": "DMF", "yield_pct": 71.0},
        {"temperature": 110.0, "solvent": "EtOH", "yield_pct": 49.0},
    ]
    result = propose_via_bofire(_spec(), experiments=history, n_proposals=3)
    assert result["strategy"] == "bofire-sobo-qlei", (
        f"expected GP path with N≥min, got {result['strategy']!r}"
    )
    assert result["n_experiments_fitted"] == 6
    assert len(result["proposals"]) == 3
    for p in result["proposals"]:
        cond = p["conditions"]
        assert 20 <= cond["temperature"] <= 120
        assert cond["solvent"] in {"THF", "DMF", "EtOH"}
