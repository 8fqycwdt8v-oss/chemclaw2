"""Unit tests for the BO parameter-spec schema.

Pure Pydantic — no BOFIRE, no DB. Locks down the V1 invariants:
≤ 8 categorical levels, range sanity, unique keys, no duplicate
categories.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from api.agent.parameter_spec import ParameterSpec


def _minimal_spec(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "inputs": [
            {"key": "temperature", "type": "continuous", "min": 20, "max": 120},
            {"key": "solvent", "type": "categorical",
             "categories": ["THF", "DMF", "EtOH"]},
        ],
        "outputs": [{"key": "yield_pct", "direction": "maximize"}],
    }
    base.update(overrides)
    return base


def test_minimal_spec_round_trip() -> None:
    spec = ParameterSpec.model_validate(_minimal_spec())
    assert len(spec.inputs) == 2
    assert spec.outputs[0].direction == "maximize"
    assert spec.input_keys == ["temperature", "solvent"]
    assert spec.output_keys == ["yield_pct"]
    assert not spec.is_multi_objective()


def test_multi_output_marked() -> None:
    spec = ParameterSpec.model_validate(
        _minimal_spec(outputs=[
            {"key": "yield_pct", "direction": "maximize"},
            {"key": "purity_pct", "direction": "maximize"},
        ]),
    )
    assert spec.is_multi_objective()


def test_continuous_input_rejects_min_ge_max() -> None:
    with pytest.raises(ValidationError, match="min .* must be < max"):
        ParameterSpec.model_validate(_minimal_spec(inputs=[
            {"key": "T", "type": "continuous", "min": 100, "max": 100},
        ]))
    with pytest.raises(ValidationError, match="min .* must be < max"):
        ParameterSpec.model_validate(_minimal_spec(inputs=[
            {"key": "T", "type": "continuous", "min": 100, "max": 50},
        ]))


def test_categorical_rejects_duplicate_levels() -> None:
    with pytest.raises(ValidationError, match="categories must be unique"):
        ParameterSpec.model_validate(_minimal_spec(inputs=[
            {"key": "S", "type": "categorical", "categories": ["A", "A", "B"]},
        ]))


def test_categorical_caps_at_eight_levels() -> None:
    too_many = ["L" + str(i) for i in range(9)]
    with pytest.raises(ValidationError):
        ParameterSpec.model_validate(_minimal_spec(inputs=[
            {"key": "S", "type": "categorical", "categories": too_many},
        ]))


def test_categorical_requires_at_least_two_levels() -> None:
    with pytest.raises(ValidationError):
        ParameterSpec.model_validate(_minimal_spec(inputs=[
            {"key": "S", "type": "categorical", "categories": ["only"]},
        ]))


def test_duplicate_keys_across_inputs_and_outputs_rejected() -> None:
    with pytest.raises(ValidationError, match="keys must all be unique"):
        ParameterSpec.model_validate({
            "inputs": [
                {"key": "yield_pct", "type": "continuous", "min": 0, "max": 100},
            ],
            "outputs": [{"key": "yield_pct", "direction": "maximize"}],
        })


def test_too_many_inputs_rejected() -> None:
    inputs = [
        {"key": f"x{i}", "type": "continuous", "min": 0, "max": 1}
        for i in range(21)
    ]
    with pytest.raises(ValidationError):
        ParameterSpec.model_validate({"inputs": inputs, "outputs": [
            {"key": "y", "direction": "maximize"},
        ]})


def test_output_direction_required() -> None:
    with pytest.raises(ValidationError):
        ParameterSpec.model_validate(_minimal_spec(outputs=[
            {"key": "yield_pct"},  # missing direction
        ]))


def test_minimize_direction_accepted() -> None:
    spec = ParameterSpec.model_validate(_minimal_spec(outputs=[
        {"key": "yield_pct", "direction": "minimize"},
    ]))
    assert spec.outputs[0].direction == "minimize"


def test_input_discriminator_routes_by_type() -> None:
    """`type` is the discriminator — a continuous-shaped dict with type
    'categorical' should fail validation."""
    with pytest.raises(ValidationError):
        ParameterSpec.model_validate(_minimal_spec(inputs=[
            {"key": "T", "type": "categorical", "min": 0, "max": 100},
        ]))
