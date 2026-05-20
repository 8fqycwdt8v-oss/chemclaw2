"""ELN payload normalization.

The ELN integration contract is unverified (BACKLOG.md E2 — blocked on
customer). This module owns the mapping from whatever shape the ELN
actually returns into the columns of ``reaction_outcomes``. Keep it
permissive: ignore unknown fields, coerce types defensively, and log
each coercion at warning level so the gap between the stub and the real
contract is visible in production logs.

When the real ELN contract lands, tighten the field aliases here rather
than scattering ``raw.get(...)`` calls across the codebase.
"""
from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

logger = logging.getLogger(__name__)

_STATUS_ALIASES = {
    "success": "success", "succeeded": "success", "passed": "success",
    "ok": "success", "complete": "success", "completed": "success",
    "partial": "partial", "partial_success": "partial",
    "fail": "fail", "failed": "fail", "failure": "fail", "error": "fail",
    "inconclusive": "inconclusive", "unknown": "inconclusive",
    "ambiguous": "inconclusive", "indeterminate": "inconclusive",
}

_YIELD_FIELDS = ("yield", "yield_pct", "yield_percent", "isolated_yield", "yieldPct")
_OBSERVATION_FIELDS = ("observations", "notes", "comments", "remarks")
_FAILURE_FIELDS = ("failure_reason", "failureReason", "error", "fail_reason")
_CONDITIONS_FIELDS = ("conditions", "conditions_actual", "actual_conditions", "run_conditions")


class ElnExperiment(BaseModel):
    """Normalized view of an ELN experiment.

    Pydantic does the type coercion; the call site (``normalize_eln_payload``)
    does the field-alias matching so the model itself stays clean.
    """

    model_config = ConfigDict(extra="ignore")

    status: str = Field(default="inconclusive")
    yield_pct: float | None = None
    conditions_actual: dict[str, Any] | None = None
    observations: str | None = None
    failure_reason: str | None = None

    # status / yield_pct ranges are owned by ``_coerce_status`` and
    # ``_coerce_yield`` below — they drop bad values and log a warning so
    # the rest of the ELN record can still land. The DB has matching
    # CHECK constraints on reaction_outcomes for defense-in-depth.
    @field_validator("status")
    @classmethod
    def _status_must_be_known(cls, v: str) -> str:
        if v not in ("success", "partial", "fail", "inconclusive"):
            raise ValueError(f"unknown status {v!r}")
        return v


def _first_present(payload: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for k in keys:
        if k in payload and payload[k] is not None:
            return payload[k]
    return None


def _coerce_status(raw: Any) -> str:
    if raw is None:
        return "inconclusive"
    key = str(raw).strip().lower().replace("-", "_")
    norm = _STATUS_ALIASES.get(key)
    if norm is None:
        logger.warning("eln_status_unknown raw=%r — defaulting to 'inconclusive'", raw)
        return "inconclusive"
    return norm


def _coerce_yield(raw: Any) -> float | None:
    if raw is None:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        logger.warning("eln_yield_uncoerceable raw=%r — dropping", raw)
        return None
    # Some ELNs report yield as a fraction (0.65) instead of a percentage
    # (65). Normalize so both shapes land in the same column. We bias
    # toward "percent" — anything in [0, 1] gets multiplied by 100 with a
    # warning logged, since a 0.5 % yield is rare enough that it's
    # almost certainly a fraction.
    if 0 < value <= 1:
        logger.warning("eln_yield_fractional raw=%r — assuming fraction, scaling x100", raw)
        value *= 100
    if not 0 <= value <= 100:
        logger.warning("eln_yield_out_of_range raw=%r — dropping", raw)
        return None
    return value


def _coerce_conditions(raw: Any) -> dict[str, Any] | None:
    if raw is None:
        return None
    if isinstance(raw, dict):
        return raw
    logger.warning("eln_conditions_not_dict type=%s — wrapping in {value:...}", type(raw).__name__)
    return {"value": raw}


def normalize_eln_payload(raw: dict[str, Any]) -> ElnExperiment:
    """Map an ELN response into a validated ElnExperiment.

    Raises ``pydantic.ValidationError`` if even the permissive coercions
    fail; the caller logs and surfaces a generic error to the agent.
    """
    if not isinstance(raw, dict):
        raise ValueError(f"ELN payload must be a JSON object, got {type(raw).__name__}")
    status = _coerce_status(_first_present(raw, ("status", "outcome", "result")))
    yield_pct = _coerce_yield(_first_present(raw, _YIELD_FIELDS))
    conditions_actual = _coerce_conditions(_first_present(raw, _CONDITIONS_FIELDS))
    observations_raw = _first_present(raw, _OBSERVATION_FIELDS)
    failure_raw = _first_present(raw, _FAILURE_FIELDS)
    return ElnExperiment(
        status=status,
        yield_pct=yield_pct,
        conditions_actual=conditions_actual,
        observations=str(observations_raw) if observations_raw is not None else None,
        failure_reason=str(failure_raw) if failure_raw is not None else None,
    )
