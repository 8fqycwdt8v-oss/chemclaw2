"""Tests for the retrosynthesis disconnection library.

The MCP server installs RDKit via the `chem` extras (see
`packages/mcp-servers/mcp_retrosynth/pyproject.toml`). CI's "Install MCP
servers" step makes RDKit + the package importable; if you're running
these locally you need to `pip install packages/mcp-servers/mcp_retrosynth`
first. Skipped automatically when rdkit or the package is missing.
"""
from __future__ import annotations

import pytest

pytest.importorskip("rdkit")
pytest.importorskip("mcp_retrosynth")

from mcp_retrosynth.disconnect import (  # noqa: E402
    list_transforms,
    propose_disconnections,
)


def test_amide_disconnection_finds_carboxylic_acid_and_amine() -> None:
    """N-methylacetamide: CC(=O)NC should disconnect to AcOH + MeNH2."""
    routes = propose_disconnections("CC(=O)NC", max_routes=10)
    transforms = {r["transform"] for r in routes}
    assert "amide_bond" in transforms

    amide_route = next(r for r in routes if r["transform"] == "amide_bond")
    precursors = set(amide_route["precursors"])
    # Carboxylic acid (acetic acid) + primary amine (methylamine).
    assert "CC(=O)O" in precursors
    assert "CN" in precursors


def test_ester_disconnection_finds_acid_and_alcohol() -> None:
    """Methyl acetate: CC(=O)OC → acetic acid + methanol."""
    routes = propose_disconnections("CC(=O)OC", max_routes=10)
    transforms = {r["transform"] for r in routes}
    assert "ester_bond" in transforms
    ester_route = next(r for r in routes if r["transform"] == "ester_bond")
    precursors = set(ester_route["precursors"])
    assert "CC(=O)O" in precursors
    assert "CO" in precursors


def test_sulfonamide_disconnection() -> None:
    """N-methyl methanesulfonamide: CS(=O)(=O)NC → MsCl + MeNH2."""
    routes = propose_disconnections("CS(=O)(=O)NC", max_routes=10)
    transforms = {r["transform"] for r in routes}
    assert "sulfonamide" in transforms


def test_no_routes_for_unreactive_target() -> None:
    """Pure alkane: no templates should match."""
    routes = propose_disconnections("CCCC", max_routes=5)
    # Butane has no amide / ester / etc., so the route list should be empty.
    assert routes == []


def test_invalid_smiles_raises() -> None:
    with pytest.raises(ValueError, match="Invalid SMILES"):
        propose_disconnections("not-a-smiles", max_routes=5)


def test_empty_smiles_raises() -> None:
    with pytest.raises(ValueError, match="required"):
        propose_disconnections("   ", max_routes=5)


def test_routes_sorted_by_confidence_desc() -> None:
    """When multiple templates match, higher-confidence ones come first."""
    # Acetanilide: CC(=O)Nc1ccccc1 — has an amide bond.
    routes = propose_disconnections("CC(=O)Nc1ccccc1", max_routes=10)
    confidences = [r["confidence"] for r in routes]
    assert confidences == sorted(confidences, reverse=True)


def test_max_routes_caps_output() -> None:
    """Even when many templates match, output is capped at max_routes."""
    # A molecule with several disconnection sites.
    routes = propose_disconnections("CC(=O)NCC(=O)OC", max_routes=2)
    assert len(routes) <= 2


def test_list_transforms_metadata() -> None:
    transforms = list_transforms()
    assert len(transforms) >= 10
    names = {t["name"] for t in transforms}
    # Spot-check core medchem disconnections are present.
    assert {"amide_bond", "ester_bond", "suzuki_biaryl", "reductive_amination"} <= names
    for t in transforms:
        assert 0.0 <= t["confidence"] <= 1.0
        assert t["notes"]
