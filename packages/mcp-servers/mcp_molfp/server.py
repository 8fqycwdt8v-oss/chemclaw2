from mcp.server.fastmcp import FastMCP
from rdkit import Chem
from rdkit.Chem import AllChem

mcp = FastMCP("mcp-molfp")

# Hard cap on SMILES input: RDKit happily parses arbitrarily long strings and
# real chemistry SMILES top out well under 1k chars; anything beyond this is
# malformed input or a DoS attempt.
MAX_SMILES_LEN = 10_000


def _check_smiles_len(label: str, smiles: str) -> None:
    if len(smiles) > MAX_SMILES_LEN:
        raise ValueError(f"{label} exceeds {MAX_SMILES_LEN} chars (got {len(smiles)})")


@mcp.tool()
def compute_morgan_fp(smiles: str, radius: int = 2, n_bits: int = 2048) -> dict:
    """Compute Morgan/ECFP fingerprint for a SMILES string.

    Returns fingerprint_bits (binary string of '0'/'1', length = n_bits) and n_bits.
    Compatible with Postgres BIT(n_bits) via $1::bit(n_bits) parameter cast.
    """
    _check_smiles_len("smiles", smiles)
    if not (64 <= n_bits <= 4096):
        raise ValueError(f"n_bits must be between 64 and 4096, got {n_bits}")
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        raise ValueError(f"Invalid SMILES: {smiles}")
    fp = AllChem.GetMorganFingerprintAsBitVect(mol, radius=radius, nBits=n_bits)
    # Return binary string ('010101...') — compatible with Postgres BIT(2048) parameter cast
    bit_str = fp.ToBitString()
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
def substructure_match(smiles: str, smarts: str) -> dict:
    """Test whether a SMILES contains a SMARTS substructure pattern.

    Returns {"match": bool}. Invalid SMILES → match: false (not an error,
    so the caller can iterate over a candidate set without aborting on bad data).
    Invalid SMARTS raises — SMARTS errors are programmer bugs, not data issues.
    """
    _check_smiles_len("smiles", smiles)
    if len(smarts) > MAX_SMILES_LEN:
        raise ValueError(f"smarts exceeds {MAX_SMILES_LEN} chars (got {len(smarts)})")
    if not smarts.strip():
        raise ValueError("smarts pattern is required")
    pattern = Chem.MolFromSmarts(smarts)
    if pattern is None:
        raise ValueError(f"Invalid SMARTS: {smarts}")
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        return {"match": False}
    return {"match": mol.HasSubstructMatch(pattern)}


def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
