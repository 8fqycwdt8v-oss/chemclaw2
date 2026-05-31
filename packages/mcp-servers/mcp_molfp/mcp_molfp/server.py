import os
import time

from mcp.server.fastmcp import FastMCP
from mcp_chemclaw_shared import configure_logging
from rdkit import Chem
from rdkit.Chem import AllChem, Crippen, Descriptors, Lipinski, rdMolDescriptors

log = configure_logging("mcp-molfp")
mcp = FastMCP("mcp-molfp")

# Hard cap on SMILES input: RDKit happily parses arbitrarily long strings and
# real chemistry SMILES top out well under 1k chars; anything beyond this is
# malformed input or a DoS attempt.
MAX_SMILES_LEN = 10_000


def _check_smiles_len(label: str, smiles: str) -> None:
    if len(smiles) > MAX_SMILES_LEN:
        log.warning("smiles_oversize", extra={"label": label, "length": len(smiles), "max": MAX_SMILES_LEN})
        raise ValueError(f"{label} exceeds {MAX_SMILES_LEN} chars (got {len(smiles)})")


@mcp.tool()
def compute_morgan_fp(smiles: str, radius: int = 2, n_bits: int = 2048) -> dict:
    """Compute Morgan/ECFP fingerprint for a SMILES string.

    Returns fingerprint_bits (binary string of '0'/'1', length = n_bits) and n_bits.
    Compatible with Postgres BIT(n_bits) via $1::bit(n_bits) parameter cast.
    """
    _check_smiles_len("smiles", smiles)
    if not (64 <= n_bits <= 4096):
        log.warning("invalid_n_bits", extra={"n_bits": n_bits})
        raise ValueError(f"n_bits must be between 64 and 4096, got {n_bits}")
    t0 = time.monotonic()
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        log.warning("invalid_smiles", extra={"smiles_len": len(smiles), "tool": "compute_morgan_fp"})
        raise ValueError("Invalid SMILES")
    fp = AllChem.GetMorganFingerprintAsBitVect(mol, radius=radius, nBits=n_bits)
    bit_str = fp.ToBitString()
    log.info(
        "fp_computed",
        extra={"tool": "compute_morgan_fp", "n_bits": n_bits, "radius": radius,
               "smiles_len": len(smiles), "duration_ms": int((time.monotonic() - t0) * 1000)},
    )
    return {"fingerprint_bits": bit_str, "n_bits": n_bits}


@mcp.tool()
def validate_smiles(smiles: str) -> dict:
    """Validate a SMILES string and return its canonical form."""
    _check_smiles_len("smiles", smiles)
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        return {"valid": False, "canonical_smiles": None}
    return {"valid": True, "canonical_smiles": Chem.MolToSmiles(mol)}


@mcp.tool()
def compute_descriptors(smiles: str) -> dict:
    """Compute deterministic molecular descriptors for a SMILES string.

    Returns Crippen logP (MolLogP), exact + average molecular weight, TPSA,
    H-bond donor/acceptor counts, rotatable bond count, aromatic ring count,
    heavy atom count, and a Lipinski Rule-of-Five pass flag.

    All values come from RDKit-implemented algorithms — no ML, no external
    calls, deterministic across versions. Spec §3.5 ("property predictions");
    the calibrated-uncertainty + ML-based predictions (yield, tox) stay
    deferred per the operating principles (no custom embedding models).
    """
    _check_smiles_len("smiles", smiles)
    t0 = time.monotonic()
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        log.warning("invalid_smiles", extra={"smiles_len": len(smiles), "tool": "compute_descriptors"})
        raise ValueError("Invalid SMILES")
    logp = Crippen.MolLogP(mol)
    mw_exact = Descriptors.ExactMolWt(mol)
    mw_avg = Descriptors.MolWt(mol)
    tpsa = Descriptors.TPSA(mol)
    hbd = Lipinski.NumHDonors(mol)
    hba = Lipinski.NumHAcceptors(mol)
    rotatable = Lipinski.NumRotatableBonds(mol)
    aromatic_rings = rdMolDescriptors.CalcNumAromaticRings(mol)
    heavy_atoms = mol.GetNumHeavyAtoms()
    # Lipinski's Rule of Five — quick orally-bioavailable screen.
    lipinski_violations = sum(
        1 for v in (mw_exact > 500, logp > 5, hbd > 5, hba > 10) if v
    )
    log.info(
        "descriptors_computed",
        extra={"tool": "compute_descriptors", "smiles_len": len(smiles),
               "duration_ms": int((time.monotonic() - t0) * 1000)},
    )
    return {
        "logp": round(logp, 3),
        "mw_exact": round(mw_exact, 4),
        "mw_avg": round(mw_avg, 4),
        "tpsa": round(tpsa, 2),
        "h_bond_donors": hbd,
        "h_bond_acceptors": hba,
        "rotatable_bonds": rotatable,
        "aromatic_rings": aromatic_rings,
        "heavy_atoms": heavy_atoms,
        "lipinski_violations": lipinski_violations,
        "lipinski_pass": lipinski_violations == 0,
    }


@mcp.tool()
def substructure_match(smiles: str, smarts: str) -> dict:
    """Test whether a SMILES contains a SMARTS substructure pattern.

    Returns {"match": bool}. Invalid SMILES → match: false. Invalid SMARTS raises.
    """
    _check_smiles_len("smiles", smiles)
    _check_smiles_len("smarts", smarts)
    if not smarts.strip():
        raise ValueError("smarts pattern is required")
    pattern = Chem.MolFromSmarts(smarts)
    if pattern is None:
        log.warning("invalid_smarts", extra={"smarts_len": len(smarts)})
        raise ValueError("Invalid SMARTS")
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        return {"match": False}
    return {"match": mol.HasSubstructMatch(pattern)}


def main():
    log.info("mcp_server_starting", extra={"name": mcp.name, "pid": os.getpid()})
    try:
        mcp.run(transport="stdio")
    except Exception:
        log.exception("mcp_server_crashed")
        raise


if __name__ == "__main__":
    main()
