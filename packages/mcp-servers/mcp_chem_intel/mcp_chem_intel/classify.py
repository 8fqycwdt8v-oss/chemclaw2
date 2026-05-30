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


def _reactants_match(rule: _Rule, reactant_mols: list) -> bool:
    return all(_any_mol_matches(reactant_mols, s) for s in rule.reactant_smarts)


def _products_match(rule: _Rule, product_mols: list) -> bool:
    return all(_any_mol_matches(product_mols, s) for s in rule.product_smarts)


def classify_reaction(reaction_smiles: str) -> dict:
    """Return the best-matching reaction class for a reaction SMILES.

    ``reaction_smiles`` may be ``reactants>>products`` (preferred), a
    ``reactants>agents>products`` form, or a bare reactant set ``a.b``.

    Returns ``{"reaction_class": ..., "matched": bool}``. ``matched`` is True
    only when a rule *fully* fired — for a named class whose rule has a
    product pattern (amide_formation, esterification, Suzuki, …) that means a
    product was supplied and confirmed it. When only reactants are given, a
    product-gated rule can at best *suggest* a class: it is returned with
    ``matched: False`` and a ``note``, never asserted as confirmed. This
    keeps the tool from over-claiming a transformation it cannot see the
    product of — supplying a wrong product already yields ``other``, so a
    missing product must not be treated as more certain than a wrong one.
    """
    if not reaction_smiles or not reaction_smiles.strip():
        raise ValueError("reaction_smiles is required and cannot be empty")

    reactants, product = _split_reaction(reaction_smiles)
    reactant_mols = _smiles_to_mols(reactants)
    if not reactant_mols:
        raise ValueError(f"No parseable reactants in: {reaction_smiles!r}")
    product_mols = _smiles_to_mols(product)

    # A product-gated rule whose reactants matched but which we can't confirm
    # because no product was supplied. Held as a fallback suggestion; the first
    # such rule (in priority order) wins if nothing fully fires.
    candidate: str | None = None
    for rule in _RULES:
        if not _reactants_match(rule, reactant_mols):
            continue
        if not rule.product_smarts:
            # Reactant-only rule — fully determined without a product.
            return {"reaction_class": rule.label, "matched": True}
        if product_mols:
            if _products_match(rule, product_mols):
                return {"reaction_class": rule.label, "matched": True}
        elif candidate is None:
            candidate = rule.label

    if candidate is not None:
        return {
            "reaction_class": candidate,
            "matched": False,
            "note": (
                "candidate inferred from reactants only; supply the product "
                "(reactants>>products) to confirm"
            ),
        }
    return {"reaction_class": CLASS_OTHER, "matched": False}


def supported_classes() -> list[str]:
    """All named classes the rule pack can emit (excluding ``other``)."""
    return sorted({r.label for r in _RULES})
