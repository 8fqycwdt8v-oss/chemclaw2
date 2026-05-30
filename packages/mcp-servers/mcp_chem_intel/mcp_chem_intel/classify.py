"""Lightweight SMARTS-based reaction classifier.

Adapted from chemclaw2_forward (`meta/classifier.py`). Assigns a single
coarse, named reaction class to a reaction by matching SMARTS rules against
the reactants (and, when supplied, the product). Pure RDKit — no model
weights, no network. Complements mcp-rxnfp (which produces an opaque DRFP
fingerprint) by giving the agent a *human-readable name* for a reaction.

For richer, learned classification, route to an external Rxn-INSIGHT /
rxnfp meta-model service when one is deployed (see BACKLOG).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger("mcp_chem_intel")

# Stable class labels.
CLASS_AMIDE_FORMATION = "amide_formation"
CLASS_ESTERIFICATION = "esterification"
CLASS_SUZUKI = "suzuki_coupling"
CLASS_REDUCTION = "carbonyl_reduction"
CLASS_OXIDATION = "alcohol_oxidation"
CLASS_NUCLEOPHILIC_SUBSTITUTION = "nucleophilic_substitution"
CLASS_NITRATION = "aromatic_nitration"
CLASS_HALOGENATION = "aromatic_halogenation"
CLASS_HYDROLYSIS = "hydrolysis"
CLASS_OTHER = "other"


@dataclass(frozen=True)
class _Rule:
    """A reaction-class rule.

    All ``reactant_smarts`` patterns must match at least one reactant.
    When ``product_smarts`` is non-empty AND a product is supplied, each
    pattern must additionally match the product.
    """

    label: str
    reactant_smarts: tuple[str, ...]
    product_smarts: tuple[str, ...] = ()


_RULES: tuple[_Rule, ...] = (
    _Rule(
        label=CLASS_AMIDE_FORMATION,
        reactant_smarts=("[CX3](=O)[Cl,Br,F,I]", "[NX3;H2,H1;!$(NC=O)]"),
        product_smarts=("[NX3][CX3]=O",),
    ),
    _Rule(
        label=CLASS_AMIDE_FORMATION,
        reactant_smarts=("[CX3](=O)[OX2H]", "[NX3;H2,H1;!$(NC=O)]"),
        product_smarts=("[NX3][CX3]=O",),
    ),
    _Rule(
        label=CLASS_ESTERIFICATION,
        reactant_smarts=("[CX3](=O)[OX2H]", "[OX2H][CX4]"),
        product_smarts=("[CX3](=O)[OX2][CX4]",),
    ),
    _Rule(
        label=CLASS_SUZUKI,
        reactant_smarts=("[c,C][Br,I,Cl]", "[B]([OH])([OH])[c,C]"),
        product_smarts=("[c,C]-[c,C]",),
    ),
    _Rule(
        label=CLASS_REDUCTION,
        reactant_smarts=("[CX3]=[OX1]", "[BH4-,AlH4-]"),
        product_smarts=("[CX4][OX2H]",),
    ),
    _Rule(
        label=CLASS_OXIDATION,
        reactant_smarts=("[CX4][OX2H]", "[Cr,Mn]"),
        product_smarts=("[CX3]=[OX1]",),
    ),
    _Rule(
        label=CLASS_NITRATION,
        reactant_smarts=("c1ccccc1", "O=[N+]([O-])O"),
        product_smarts=("c[N+](=O)[O-]",),
    ),
    _Rule(
        label=CLASS_HALOGENATION,
        reactant_smarts=("c1ccccc1", "[Cl,Br][Cl,Br]"),
        product_smarts=("c[Cl,Br]",),
    ),
    _Rule(
        label=CLASS_HYDROLYSIS,
        reactant_smarts=(
            "[$([CX3](=O)[OX2][CX4]),$([CX3](=O)[NX3]),$([CX2]#[NX1])]",
            "[OX2H2]",
        ),
    ),
    # Generic nucleophilic substitution. No product gate and matches broadly,
    # so it is placed LAST — only fires when no named reaction above matched.
    _Rule(
        label=CLASS_NUCLEOPHILIC_SUBSTITUTION,
        reactant_smarts=("[CX4][Cl,Br,I]", "[N-,O-,S-]"),
    ),
)


def _split_reaction(reaction_smiles: str) -> tuple[str, str | None]:
    """Split ``a.b>>c`` or ``a.b>agent>c`` into (reactants, product)."""
    if ">" not in reaction_smiles:
        # Treat the whole string as reactants (no product gate available).
        return reaction_smiles, None
    parts = reaction_smiles.split(">")
    reactants = parts[0]
    product = parts[-1] if len(parts) >= 2 and parts[-1].strip() else None
    return reactants, product


def _smiles_to_mols(s: str | None) -> list:
    if not s:
        return []
    from rdkit import Chem  # noqa: PLC0415

    out = []
    for part in s.split("."):
        part = part.strip()
        if not part:
            continue
        mol = Chem.MolFromSmiles(part)
        if mol is not None:
            out.append(mol)
    return out


def _any_mol_matches(mols: list, smarts: str) -> bool:
    from rdkit import Chem  # noqa: PLC0415

    patt = Chem.MolFromSmarts(smarts)
    if patt is None:
        logger.warning("invalid_smarts", extra={"smarts": smarts})
        return False
    return any(m.HasSubstructMatch(patt) for m in mols)


def _rule_matches(rule: _Rule, reactant_mols: list, product_mols: list) -> bool:
    for smarts in rule.reactant_smarts:
        if not _any_mol_matches(reactant_mols, smarts):
            return False
    if rule.product_smarts and product_mols:
        for smarts in rule.product_smarts:
            if not _any_mol_matches(product_mols, smarts):
                return False
    return True


def classify_reaction(reaction_smiles: str) -> dict:
    """Return the best-matching reaction class for a reaction SMILES.

    ``reaction_smiles`` may be ``reactants>>products`` (preferred — enables
    the product-side gate), ``reactants>agents>products``, or a bare
    reactant set ``a.b``. Returns ``{"reaction_class": ..., "matched": bool}``
    where ``matched`` is False when no rule fired (class ``"other"``).
    """
    if not reaction_smiles or not reaction_smiles.strip():
        raise ValueError("reaction_smiles is required and cannot be empty")

    reactants, product = _split_reaction(reaction_smiles)
    reactant_mols = _smiles_to_mols(reactants)
    if not reactant_mols:
        raise ValueError(f"No parseable reactants in: {reaction_smiles!r}")
    product_mols = _smiles_to_mols(product)

    for rule in _RULES:
        if _rule_matches(rule, reactant_mols, product_mols):
            return {"reaction_class": rule.label, "matched": True}
    return {"reaction_class": CLASS_OTHER, "matched": False}


def supported_classes() -> list[str]:
    """All named classes the rule pack can emit (excluding ``other``)."""
    return sorted({r.label for r in _RULES})
