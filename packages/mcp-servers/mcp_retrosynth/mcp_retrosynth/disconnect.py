"""Curated retrosynthetic disconnection library.

Each transform is a reaction SMARTS where the LHS is the product and the
RHS is the precursor set. RDKit `RunReactants` is applied to the parsed
target; matches that produce a valid disconnection are returned.

This is deliberately a small, opinionated set — the dozen-or-so
high-frequency disconnections used by working medicinal chemists, not
an exhaustive template library. For broader coverage call a hosted
service (AiZynthFinder / ASKCOS / IBM RXN) via a separate tool.

Each entry has:
- name: short label shown in agent output.
- rxn_smarts: product>>reactants reaction SMARTS.
- confidence: a heuristic 0-1 score reflecting how reliably the
  forward reaction works in practice. Pure prior, not learned.
- notes: short rationale shown to the agent for sanity-checking.
"""
from __future__ import annotations

from typing import Any

from rdkit import Chem, RDLogger
from rdkit.Chem import AllChem

# Retrosynthesis templates intentionally introduce unmapped atoms on the
# precursor side (Cl, Br, B(OH)2, etc.), so RDKit's "unmapped atoms in
# reactants" warning fires every RunReactants call. Suppress it at module
# load so the worker logs aren't drowned in noise.
RDLogger.DisableLog("rdApp.warning")


# (name, rxn_smarts, confidence, notes)
_TEMPLATES: list[tuple[str, str, float, str]] = [
    (
        "amide_bond",
        "[C:1](=[O:2])[N:3]>>[C:1](=[O:2])[O;H1].[N:3]",
        0.95,
        "Carboxylic acid + amine → amide (HATU / EDC / T3P).",
    ),
    (
        "ester_bond",
        "[C:1](=[O:2])[O:3][C:4]>>[C:1](=[O:2])[O;H1].[O;H1][C:4]",
        0.9,
        "Carboxylic acid + alcohol → ester (Fischer / Steglich).",
    ),
    (
        "suzuki_biaryl",
        "[c:1]-[c:2]>>[c:1][Br].[c:2][B](O)O",
        0.85,
        "Aryl–aryl Suzuki–Miyaura coupling (Pd catalyst, boronic acid + halide).",
    ),
    (
        "buchwald_amination",
        "[c:1][N:2]([H])>>[c:1][Br].[N:2]([H])([H])",
        0.8,
        "Aryl halide + primary/secondary amine → aryl amine (Buchwald–Hartwig).",
    ),
    (
        "reductive_amination",
        "[N:1][C:2]([H])[C:3]>>[N:1][H].[C:2](=O)[C:3]",
        0.85,
        "Aldehyde/ketone + amine → secondary amine (NaBH(OAc)3, STAB).",
    ),
    (
        "williamson_ether",
        "[O:1][C:2]([H])([H])[C:3]>>[O:1][H].[Br][C:2]([H])([H])[C:3]",
        0.8,
        "Alcohol + alkyl halide → ether (base-mediated alkylation).",
    ),
    (
        "sn2_alkylation",
        "[N:1]([H])[C:2][C:3]>>[N:1]([H])[H].[Br][C:2][C:3]",
        0.7,
        "Amine + alkyl halide → SN2 N-alkylation.",
    ),
    (
        "sulfonamide",
        "[S:1](=[O:2])(=[O:3])[N:4]>>[S:1](=[O:2])(=[O:3])[Cl].[N:4][H]",
        0.9,
        "Sulfonyl chloride + amine → sulfonamide.",
    ),
    (
        "urea",
        "[N:1][C:2](=[O:3])[N:4]>>[N:1][H].[O:3]=[C:2]=[N:4]",
        0.85,
        "Amine + isocyanate → urea.",
    ),
    (
        "carbamate",
        "[O:1][C:2](=[O:3])[N:4]>>[O:1][H].[O:3]=[C:2]=[N:4]",
        0.85,
        "Alcohol + isocyanate → carbamate (or Boc-protection variant).",
    ),
    (
        "click_triazole",
        "[c:1]1[n:2][n:3][n:4][c:5]1>>[C:1]#[C:5].[N:2]=[N:3]=[N:4]",
        0.9,
        "Azide + alkyne → 1,2,3-triazole (CuAAC click).",
    ),
]


def propose_disconnections(target_smiles: str, max_routes: int = 5) -> list[dict[str, Any]]:
    """Apply each template to the target; return up to `max_routes` precursor sets.

    Raises ValueError on invalid SMILES. Returns a list of dicts:
        {transform, precursors (list of SMILES), confidence, notes}
    sorted by confidence descending, deduplicated on (transform, precursors).
    """
    if not target_smiles or not target_smiles.strip():
        raise ValueError("target_smiles is required")
    mol = Chem.MolFromSmiles(target_smiles)
    if mol is None:
        raise ValueError("Invalid SMILES")

    seen: set[tuple[str, tuple[str, ...]]] = set()
    routes: list[dict[str, Any]] = []
    for name, smarts, conf, notes in _TEMPLATES:
        try:
            rxn = AllChem.ReactionFromSmarts(smarts)
        except Exception:
            continue
        if rxn is None:
            continue
        try:
            products = rxn.RunReactants((mol,))
        except Exception:
            continue
        for precursor_tuple in products:
            try:
                smis = tuple(sorted(Chem.MolToSmiles(p) for p in precursor_tuple if p is not None))
            except Exception:
                continue
            if not smis:
                continue
            key = (name, smis)
            if key in seen:
                continue
            seen.add(key)
            routes.append({
                "transform": name,
                "precursors": list(smis),
                "confidence": conf,
                "notes": notes,
            })

    routes.sort(key=lambda r: r["confidence"], reverse=True)
    return routes[:max_routes]


def list_transforms() -> list[dict[str, Any]]:
    """Return metadata for every supported retrosynthetic transform."""
    return [
        {"name": name, "confidence": conf, "notes": notes}
        for name, _smarts, conf, notes in _TEMPLATES
    ]
