"""User-facing parameter-spec models for the Bayesian-optimisation tier.

These Pydantic models are deliberately a small mirror of BOFIRE's
`bofire.data_models.features.*` shapes, so the spec the agent declares
via `declare_campaign_parameter_space` can be validated everywhere —
even on hosts where chemclaw2-backend was installed without `[opt]`
extras (no BOFIRE / no torch).

At BO time, `api.db.queries.optimization.to_bofire_domain(spec)` does
the cheap 1:1 translation to a real BOFIRE Domain. That import lives
behind a try/except so this file stays free of the BOFIRE dep.

Constraints kept tight per the Tier 3 plan:
  - Continuous inputs: numeric min/max only
  - Categorical inputs: ≤ 8 levels (one-hot encoding blows up otherwise)
  - Outputs: continuous + maximize/minimize direction only
  - No mixture variables, no descriptor inputs, no LinearInequality constraints
    in V1 (Phase D follow-up)
"""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field, model_validator


_MAX_CATEGORICAL_LEVELS = 8


class ContinuousInputSpec(BaseModel):
    """A real-valued box-constrained input dimension."""
    key: str = Field(min_length=1, max_length=128)
    type: Literal["continuous"]
    min: float
    max: float
    unit: str | None = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def _check_range(self) -> ContinuousInputSpec:
        if not (self.min < self.max):
            raise ValueError(f"min ({self.min}) must be < max ({self.max})")
        return self


class CategoricalInputSpec(BaseModel):
    """A discrete-choice input dimension. ≤ 8 levels per V1 scope."""
    key: str = Field(min_length=1, max_length=128)
    type: Literal["categorical"]
    categories: list[str] = Field(min_length=2, max_length=_MAX_CATEGORICAL_LEVELS)

    @model_validator(mode="after")
    def _check_uniqueness(self) -> CategoricalInputSpec:
        if len(set(self.categories)) != len(self.categories):
            raise ValueError("categories must be unique")
        return self


InputSpec = Annotated[
    ContinuousInputSpec | CategoricalInputSpec,
    Field(discriminator="type"),
]


class ContinuousOutputSpec(BaseModel):
    """A measured / observed output to optimise."""
    key: str = Field(min_length=1, max_length=128)
    type: Literal["continuous"] = "continuous"
    direction: Literal["maximize", "minimize"]
    unit: str | None = Field(default=None, max_length=64)


class ParameterSpec(BaseModel):
    """Top-level campaign parameter spec.

    Persisted into `synthesis_campaigns.plan.parameter_spec` as JSONB.
    Translated to a BOFIRE Domain at BO time. Both single-objective
    (one output) and multi-objective (≥ 2 outputs) are accepted at
    the schema layer, but V1 only ships the single-objective strategy
    — the dispatcher rejects multi-output specs with a `not supported
    in V1` error until MoboStrategy lands as a Phase D follow-up.
    """

    inputs: list[InputSpec] = Field(min_length=1, max_length=20)
    outputs: list[ContinuousOutputSpec] = Field(min_length=1, max_length=4)

    @model_validator(mode="after")
    def _check_unique_keys(self) -> ParameterSpec:
        all_keys = [i.key for i in self.inputs] + [o.key for o in self.outputs]
        if len(set(all_keys)) != len(all_keys):
            raise ValueError("input and output keys must all be unique")
        return self

    @property
    def input_keys(self) -> list[str]:
        return [i.key for i in self.inputs]

    @property
    def output_keys(self) -> list[str]:
        return [o.key for o in self.outputs]

    def is_multi_objective(self) -> bool:
        return len(self.outputs) > 1
