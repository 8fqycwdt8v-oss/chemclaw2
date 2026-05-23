"""Tests for `mcp_molfp.server.compute_descriptors`.

The MCP server installs RDKit via the `chem` extras (see
`packages/mcp-servers/mcp_molfp/pyproject.toml`). CI's "Install MCP
servers" step makes RDKit importable; if you're running these locally
you need to `pip install packages/mcp-servers/mcp_molfp` first. The
tests are skipped automatically when rdkit is missing so the rest of
the suite still runs.

We assert against literature values for four well-known compounds with
±0.5 unit tolerance — RDKit's Crippen / TPSA / Lipinski algorithms
are deterministic across versions, but the tolerance keeps the test
robust to RDKit's atom-typing tweaks between releases.
"""
from __future__ import annotations

import pytest

pytest.importorskip("rdkit")
pytest.importorskip("mcp_molfp")

from mcp_molfp.server import compute_descriptors  # noqa: E402

# (smiles, name, expected) with reasonable tolerance bands.
KNOWN_COMPOUNDS = [
    # Ethanol
    (
        "CCO", "ethanol",
        {"logp": (-0.5, 0.5), "mw_exact": (45.5, 46.5), "tpsa": (15.0, 25.0),
         "h_bond_donors": 1, "h_bond_acceptors": 1, "rotatable_bonds": 0,
         "aromatic_rings": 0, "lipinski_pass": True},
    ),
    # Aspirin (acetylsalicylic acid)
    (
        "CC(=O)OC1=CC=CC=C1C(=O)O", "aspirin",
        {"logp": (0.5, 2.0), "mw_exact": (179.5, 180.5), "tpsa": (60.0, 70.0),
         "h_bond_donors": 1, "h_bond_acceptors": 3, "rotatable_bonds": 3,
         "aromatic_rings": 1, "lipinski_pass": True},
    ),
    # Caffeine
    (
        "CN1C=NC2=C1C(=O)N(C(=O)N2C)C", "caffeine",
        {"logp": (-1.0, 1.0), "mw_exact": (193.5, 194.5), "tpsa": (55.0, 65.0),
         "h_bond_donors": 0, "h_bond_acceptors": 3, "rotatable_bonds": 0,
         "aromatic_rings": 2, "lipinski_pass": True},
    ),
    # Paracetamol (acetaminophen)
    (
        "CC(=O)NC1=CC=C(O)C=C1", "paracetamol",
        {"logp": (0.0, 1.5), "mw_exact": (150.5, 151.5), "tpsa": (45.0, 55.0),
         "h_bond_donors": 2, "h_bond_acceptors": 2, "rotatable_bonds": 1,
         "aromatic_rings": 1, "lipinski_pass": True},
    ),
]


@pytest.mark.parametrize("smiles,name,expected", KNOWN_COMPOUNDS)
def test_compute_descriptors_known_compound(
    smiles: str, name: str, expected: dict[str, object]
) -> None:
    out = compute_descriptors(smiles)

    # logP / mw / tpsa — tolerance bands.
    for key in ("logp", "mw_exact", "tpsa"):
        band = expected[key]
        assert isinstance(band, tuple), f"expected band is a tuple for {key}"
        lo, hi = band
        assert lo <= out[key] <= hi, (
            f"{name}: {key}={out[key]} not in [{lo}, {hi}]"
        )

    # Exact-match integer counts + booleans.
    for key in (
        "h_bond_donors", "h_bond_acceptors", "rotatable_bonds",
        "aromatic_rings", "lipinski_pass",
    ):
        assert out[key] == expected[key], (
            f"{name}: {key}={out[key]} != expected {expected[key]}"
        )


def test_compute_descriptors_invalid_smiles() -> None:
    with pytest.raises(ValueError, match="Invalid SMILES"):
        compute_descriptors("not-a-real-smiles-string!")


def test_compute_descriptors_smiles_size_cap() -> None:
    with pytest.raises(ValueError, match="exceeds"):
        compute_descriptors("C" * 20_000)


def test_compute_descriptors_lipinski_violator() -> None:
    """A grossly hydrophobic compound (logP > 5) should fail Lipinski."""
    # Long saturated hydrocarbon, far above the logP=5 threshold.
    out = compute_descriptors("CCCCCCCCCCCCCCCCCCCC")  # C20H42
    assert out["lipinski_pass"] is False
    assert out["lipinski_violations"] >= 1
