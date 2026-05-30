"""Local chemistry-abbreviation and solvent reference tables.

Adapted from eln_structurer (`solvents.py` + `tools/expand_abbreviation.py`).
Pure dictionary lookup — no network, no fuzzy matching. Lets the agent resolve
ambiguous shorthand in literature / ELN procedures ("o.n.", "DCM", "sat.")
deterministically instead of guessing.
"""

from __future__ import annotations

# Solvent NAME (lowercased) -> atmospheric boiling point in °C.
SOLVENT_BP_CELSIUS: dict[str, float] = {
    "pentane": 36.0,
    "hexane": 69.0,
    "hexanes": 69.0,
    "heptane": 98.0,
    "cyclohexane": 81.0,
    "benzene": 80.0,
    "toluene": 111.0,
    "xylene": 138.0,
    "dichloromethane": 40.0,
    "dcm": 40.0,
    "ch2cl2": 40.0,
    "chloroform": 61.0,
    "chcl3": 61.0,
    "carbon tetrachloride": 77.0,
    "1,2-dichloroethane": 84.0,
    "diethyl ether": 35.0,
    "ether": 35.0,
    "et2o": 35.0,
    "thf": 66.0,
    "tetrahydrofuran": 66.0,
    "2-methyltetrahydrofuran": 80.0,
    "1,4-dioxane": 101.0,
    "dioxane": 101.0,
    "dme": 85.0,
    "1,2-dimethoxyethane": 85.0,
    "mtbe": 55.0,
    "tert-butyl methyl ether": 55.0,
    "methanol": 65.0,
    "meoh": 65.0,
    "ethanol": 78.0,
    "etoh": 78.0,
    "isopropanol": 82.0,
    "ipa": 82.0,
    "2-propanol": 82.0,
    "n-butanol": 117.0,
    "t-butanol": 82.0,
    "tert-butanol": 82.0,
    "dmf": 153.0,
    "dimethylformamide": 153.0,
    "dmac": 165.0,
    "dimethylacetamide": 165.0,
    "dmso": 189.0,
    "dimethyl sulfoxide": 189.0,
    "nmp": 202.0,
    "n-methylpyrrolidone": 202.0,
    "n-methyl-2-pyrrolidone": 202.0,
    "hmpa": 233.0,
    "acetonitrile": 82.0,
    "mecn": 82.0,
    "acn": 82.0,
    "ethyl acetate": 77.0,
    "etoac": 77.0,
    "acetone": 56.0,
    "acetic acid": 118.0,
    "formic acid": 100.0,
    "water": 100.0,
    "h2o": 100.0,
}


_ABBREVIATIONS: dict[str, str] = {
    "rt": "room temperature (≈ 20-25 °C, ambient)",
    "r.t.": "room temperature (≈ 20-25 °C, ambient)",
    "o.n.": "overnight (≈ 12-16 hours)",
    "o/n": "overnight (≈ 12-16 hours)",
    "aq.": "aqueous",
    "sat.": "saturated",
    "satd.": "saturated",
    "conc.": "concentrated",
    "anhyd.": "anhydrous",
    "abs.": "absolute (e.g. abs. ethanol = anhydrous ethanol)",
    "dr": "diastereomeric ratio",
    "ee": "enantiomeric excess",
    "de": "diastereomeric excess",
    "tlc": "thin-layer chromatography",
    "nmr": "nuclear magnetic resonance",
    "hplc": "high-performance liquid chromatography",
    "mw": "microwave irradiation",
    "uv": "ultraviolet light",
    "etoac": "ethyl acetate (SMILES CCOC(C)=O)",
    "thf": "tetrahydrofuran (SMILES C1CCOC1, bp 66 °C)",
    "dmf": "N,N-dimethylformamide (SMILES CN(C)C=O, bp 153 °C)",
    "dmso": "dimethyl sulfoxide (SMILES CS(=O)C, bp 189 °C)",
    "dcm": "dichloromethane (SMILES ClCCl, bp 40 °C)",
    "meoh": "methanol (SMILES CO, bp 65 °C)",
    "etoh": "ethanol (SMILES CCO, bp 78 °C)",
    "ipa": "isopropanol (SMILES CC(C)O, bp 82 °C)",
    "mecn": "acetonitrile (SMILES CC#N, bp 82 °C)",
    "et2o": "diethyl ether (SMILES CCOCC, bp 35 °C)",
    "mtbe": "tert-butyl methyl ether (SMILES CC(C)(C)OC, bp 55 °C)",
    "ndp": "non-degassed (atmosphere not specified)",
    "tba": "tert-butyl alcohol",
    "h2o": "water (SMILES O, bp 100 °C)",
    "n2": "nitrogen atmosphere",
    "ar": "argon atmosphere",
}


def expand_abbreviation(token: str) -> dict:
    """Look up a chemistry abbreviation or solvent name (case-insensitive).

    Returns the canonical expansion and, when the token names a known
    solvent, its atmospheric boiling point. ``found`` is False when neither
    table has an entry — the caller should fall back to prior knowledge
    rather than invent a meaning.
    """
    if not token or not token.strip():
        raise ValueError("token is required and cannot be empty")
    key = token.strip().lower()
    expansion = _ABBREVIATIONS.get(key)
    bp = SOLVENT_BP_CELSIUS.get(key)
    return {
        "token": token,
        "expansion": expansion,
        "bp_celsius": bp,
        "found": expansion is not None or bp is not None,
    }
