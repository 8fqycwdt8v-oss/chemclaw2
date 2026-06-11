"""Tests for the mcp-chem-intel ORD-validation rule pack.

`validate_ord_reaction` / `validate_ord_compound` wrap ord-schema's own
recursive validator (`ord_schema.validations`). ord-schema ships as a
dependency of the mcp_chem_intel package; tests skip automatically when the
package or ord_schema isn't installed.
"""
from __future__ import annotations

import pytest

# ord-schema rides in the [ord] extra, installed only on the heavy CI lane.
# Mark heavy so the cheap lane deselects (importorskip is the local backstop).

pytest.importorskip("mcp_chem_intel")
pytest.importorskip("ord_schema")

from mcp_chem_intel.ord_validate import validate_compound, validate_reaction  # noqa: E402

# A minimal but schema-valid reaction: one reactant input with a parseable
# SMILES + amount, and one product outcome. require_provenance defaults False.
_VALID_REACTION = {
    "inputs": {
        "m1": {
            "components": [
                {
                    "identifiers": [{"type": "SMILES", "value": "CCO"}],
                    "amount": {"mass": {"value": 1.0, "units": "GRAM"}},
                    "reaction_role": "REACTANT",
                }
            ]
        }
    },
    "outcomes": [
        {"products": [{"identifiers": [{"type": "SMILES", "value": "CC=O"}], "reaction_role": "PRODUCT"}]}
    ],
}


def test_validate_reaction_valid() -> None:
    res = validate_reaction(_VALID_REACTION)
    assert res["valid"] is True
    assert res["errors"] == []
    assert res["num_inputs"] == 1
    assert res["num_outcomes"] == 1


def test_validate_reaction_missing_outcome_is_invalid() -> None:
    rxn = {"inputs": _VALID_REACTION["inputs"]}
    res = validate_reaction(rxn)
    assert res["valid"] is False
    assert any("outcome" in e.lower() for e in res["errors"])
    assert res["num_outcomes"] == 0


def test_validate_reaction_requires_provenance_when_asked() -> None:
    # Same valid reaction, but demanding provenance surfaces the gap as an error.
    res = validate_reaction(_VALID_REACTION, require_provenance=True)
    assert res["valid"] is False
    assert any("provenance" in e.lower() for e in res["errors"])


def test_validate_reaction_malformed_returns_parse_error_not_raise() -> None:
    res = validate_reaction({"inputs": {"m1": {"not_a_field": []}}})
    assert res["valid"] is False
    assert res["errors"] and "parse error" in res["errors"][0].lower()
    assert res["warnings"] == []


def test_validate_compound_bad_smiles_is_invalid() -> None:
    res = validate_compound(
        {"identifiers": [{"type": "SMILES", "value": "this-is-not-smiles"}], "reaction_role": "REACTANT"}
    )
    assert res["valid"] is False
    assert any("smiles" in e.lower() for e in res["errors"])


def test_validate_reaction_rejects_non_dict() -> None:
    with pytest.raises(ValueError, match="JSON object"):
        validate_reaction("not a dict")  # type: ignore[arg-type]
