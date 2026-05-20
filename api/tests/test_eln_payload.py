"""Tests for ELN payload normalization.

The real ELN contract is unverified (BACKLOG.md E2). These tests pin the
permissive behaviour we ship with — fractional yields get scaled,
unknown status strings fall back to inconclusive, missing fields default,
and the unknown-key tolerance keeps the loader robust to schema drift.
"""
from __future__ import annotations

import pytest

from api.agent.eln_payload import normalize_eln_payload


def test_normalize_full_payload() -> None:
    raw = {
        "status": "succeeded",
        "yield_pct": 78.4,
        "conditions": {"solvent": "DMF", "temp_c": 110, "time_h": 12},
        "observations": "Slight discoloration after 4h",
        "failure_reason": None,
        "extra_field_we_ignore": "ok",
    }
    out = normalize_eln_payload(raw)
    assert out.status == "success"
    assert out.yield_pct == 78.4
    assert out.conditions_actual == {"solvent": "DMF", "temp_c": 110, "time_h": 12}
    assert out.observations == "Slight discoloration after 4h"
    assert out.failure_reason is None


def test_normalize_fractional_yield_is_scaled() -> None:
    out = normalize_eln_payload({"status": "ok", "yield": 0.65})
    assert out.yield_pct == 65.0


def test_normalize_unknown_status_falls_back_to_inconclusive() -> None:
    out = normalize_eln_payload({"status": "weird-state", "yield": 50})
    assert out.status == "inconclusive"
    assert out.yield_pct == 50.0


def test_normalize_missing_status_defaults_to_inconclusive() -> None:
    out = normalize_eln_payload({"yield": 10})
    assert out.status == "inconclusive"


def test_normalize_failure_path() -> None:
    raw = {"outcome": "failed", "failureReason": "Catalyst deactivation", "notes": "Off-spec"}
    out = normalize_eln_payload(raw)
    assert out.status == "fail"
    assert out.failure_reason == "Catalyst deactivation"
    assert out.observations == "Off-spec"
    assert out.yield_pct is None


def test_normalize_drops_out_of_range_yield() -> None:
    out = normalize_eln_payload({"status": "success", "yield_pct": 250})
    assert out.yield_pct is None
    assert out.status == "success"


def test_normalize_rejects_non_object() -> None:
    with pytest.raises(ValueError):
        normalize_eln_payload([1, 2, 3])  # type: ignore[arg-type]


def test_normalize_wraps_non_dict_conditions() -> None:
    out = normalize_eln_payload({"status": "ok", "conditions": "toluene, 80C"})
    assert out.conditions_actual == {"value": "toluene, 80C"}
