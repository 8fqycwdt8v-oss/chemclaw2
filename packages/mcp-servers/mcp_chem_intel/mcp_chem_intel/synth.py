"""Synthetic-accessibility scoring.

Adapted from chemclaw2_retrosynthesis (`scoring/sascore.py`). The SAscore
implementation lives in RDKit's ``Contrib/SA_Score`` (BSD). We import it
directly off ``RDConfig.RDContribDir``; if the contrib dir isn't on the
build's path we raise so the caller surfaces a clear error rather than a
silent ``None``.

SAscore runs from 1 (trivial to make) to 10 (very hard). It is a cheap,
deterministic, training-free heuristic — a fast first filter before paying
for a real retrosynthesis search.
"""

from __future__ import annotations

import logging
import os
import sys

logger = logging.getLogger("mcp_chem_intel")

_sascorer = None


def _load_sascorer():
    """Import RDKit's contrib SA_Score module (cached)."""
    global _sascorer
    if _sascorer is not None:
        return _sascorer
    from rdkit.Chem import RDConfig  # noqa: PLC0415

    sa_path = os.path.join(RDConfig.RDContribDir, "SA_Score")
    if sa_path not in sys.path:
        sys.path.append(sa_path)
    import sascorer  # type: ignore[import-not-found]  # noqa: PLC0415

    _sascorer = sascorer
    return sascorer


def _interpret(score: float) -> str:
    if score < 3.0:
        return "easy to synthesize"
    if score < 6.0:
        return "moderate synthetic complexity"
    return "hard to synthesize"


def synthetic_accessibility(smiles: str) -> dict:
    """Compute the SAscore for a molecule SMILES.

    Returns a dict with the raw score, a 1-10 normalized view and a short
    human-readable interpretation.
    """
    if not smiles or not smiles.strip():
        raise ValueError("smiles is required and cannot be empty")

    from rdkit import Chem  # noqa: PLC0415

    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        raise ValueError(f"Invalid SMILES: {smiles!r}")

    try:
        scorer = _load_sascorer()
    except Exception as exc:  # pragma: no cover — depends on RDKit build
        logger.exception("sascore_unavailable")
        raise RuntimeError(
            "RDKit contrib SA_Score is not available in this build"
        ) from exc

    score = float(scorer.calculateScore(mol))
    return {
        "smiles": smiles,
        "sa_score": round(score, 3),
        "scale": "1 (easy) to 10 (hard)",
        "interpretation": _interpret(score),
    }
