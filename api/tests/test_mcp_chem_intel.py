"""Tests for the mcp-chem-intel primitives.

Merged from the forward / retrosynthesis / eln meta-model repos as
dependency-light, always-on tools. The synthesizability + classification
cores need RDKit (installed via CI's "Install MCP servers" step, or
`pip install packages/mcp-servers/mcp_chem_intel` locally); the abbreviation
table is pure-Python. Tests skip automatically when rdkit or the package
is missing.
"""
from __future__ import annotations

import pytest

pytest.importorskip("mcp_chem_intel")

# Abbreviation lookup is pure-Python (no RDKit) — always importable.
from mcp_chem_intel.abbreviations import expand_abbreviation  # noqa: E402


def test_expand_abbreviation_solvent_with_bp() -> None:
    res = expand_abbreviation("DCM")
    assert res["found"] is True
    assert "dichloromethane" in res["expansion"]
    assert res["bp_celsius"] == 40.0


def test_expand_abbreviation_non_solvent() -> None:
    res = expand_abbreviation("o.n.")
    assert res["found"] is True
    assert "overnight" in res["expansion"]
    assert res["bp_celsius"] is None


def test_expand_abbreviation_unknown() -> None:
    res = expand_abbreviation("zzz")
    assert res["found"] is False
    assert res["expansion"] is None
    assert res["bp_celsius"] is None


def test_expand_abbreviation_empty_raises() -> None:
    with pytest.raises(ValueError, match="required"):
        expand_abbreviation("  ")


# ── RDKit-backed cores ──────────────────────────────────────────────────────
pytest.importorskip("rdkit")

from mcp_chem_intel.classify import (  # noqa: E402
    CLASS_AMIDE_FORMATION,
    CLASS_ESTERIFICATION,
    CLASS_OTHER,
    classify_reaction,
    supported_classes,
)
from mcp_chem_intel.synth import synthetic_accessibility  # noqa: E402


def test_classify_amide_formation_from_acid_chloride() -> None:
    # Acetyl chloride + aniline -> acetanilide.
    res = classify_reaction("CC(=O)Cl.Nc1ccccc1>>CC(=O)Nc1ccccc1")
    assert res["reaction_class"] == CLASS_AMIDE_FORMATION
    assert res["matched"] is True


def test_classify_esterification() -> None:
    # Acetic acid + methanol -> methyl acetate.
    res = classify_reaction("CC(=O)O.CO>>CC(=O)OC")
    assert res["reaction_class"] == CLASS_ESTERIFICATION
    assert res["matched"] is True


def test_classify_unmatched_returns_other() -> None:
    res = classify_reaction("CCCC>>CCCC")
    assert res["reaction_class"] == CLASS_OTHER
    assert res["matched"] is False


def test_classify_bare_reactants_without_product() -> None:
    # No '>' separator — treated as reactants only; the no-product-gate
    # rules can still fire, but the SMILES below matches nothing.
    res = classify_reaction("CCCC")
    assert res["reaction_class"] == CLASS_OTHER


def test_classify_product_gated_rule_unconfirmed_without_product() -> None:
    # Acid chloride + amine *could* form an amide, but with no product
    # supplied the tool must NOT assert matched=True — it returns the class
    # as an unconfirmed candidate (matched=False + note). Over-claiming here
    # would mislead an agent that trusts `matched`.
    res = classify_reaction("CC(=O)Cl.NCC")
    assert res["reaction_class"] == CLASS_AMIDE_FORMATION
    assert res["matched"] is False
    assert "note" in res


def test_classify_wrong_product_is_not_amide() -> None:
    # Supplying a product that doesn't contain an amide must fall through to
    # 'other' — consistent with (never weaker than) the no-product case.
    res = classify_reaction("CC(=O)Cl.NCC>>CCCCCCCC")
    assert res["reaction_class"] == CLASS_OTHER
    assert res["matched"] is False


def test_classify_empty_raises() -> None:
    with pytest.raises(ValueError, match="required"):
        classify_reaction("   ")


def test_supported_classes_nonempty() -> None:
    classes = supported_classes()
    assert CLASS_AMIDE_FORMATION in classes
    assert CLASS_ESTERIFICATION in classes
    assert CLASS_OTHER not in classes  # 'other' is the fallback, not a rule label


def test_synthesizability_easy_vs_hard() -> None:
    easy = synthetic_accessibility("CCO")  # ethanol
    assert 1.0 <= easy["sa_score"] <= 10.0
    assert easy["smiles"] == "CCO"
    # A strained polycyclic should score harder than ethanol.
    hard = synthetic_accessibility("C1CC2CCC1C2")
    assert hard["sa_score"] > easy["sa_score"]


def test_synthesizability_invalid_smiles_raises() -> None:
    with pytest.raises(ValueError, match="Invalid SMILES"):
        synthetic_accessibility("not-a-smiles")


def test_synthesizability_empty_raises() -> None:
    with pytest.raises(ValueError, match="required"):
        synthetic_accessibility("")
