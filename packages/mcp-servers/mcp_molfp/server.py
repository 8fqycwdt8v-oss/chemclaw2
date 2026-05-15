from mcp.server.fastmcp import FastMCP
from rdkit import Chem
from rdkit.Chem import AllChem, DataStructs

mcp = FastMCP("mcp-molfp")


@mcp.tool()
def compute_morgan_fp(smiles: str, radius: int = 2, n_bits: int = 2048) -> dict:
    """Compute Morgan/ECFP fingerprint for a SMILES string.

    Returns fingerprint_hex (hex-encoded bit vector) and n_bits.
    """
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        raise ValueError(f"Invalid SMILES: {smiles}")
    fp = AllChem.GetMorganFingerprintAsBitVect(mol, radius=radius, nBits=n_bits)
    # Convert to hex: each bit -> bit string -> bytes -> hex
    bit_str = fp.ToBitString()
    # Pack bits into bytes (big-endian)
    n_bytes = (len(bit_str) + 7) // 8
    value = int(bit_str, 2)
    fp_hex = value.to_bytes(n_bytes, byteorder="big").hex()
    return {"fingerprint_hex": fp_hex, "n_bits": n_bits}


@mcp.tool()
def validate_smiles(smiles: str) -> dict:
    """Validate a SMILES string and return its canonical form."""
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        return {"valid": False, "canonical_smiles": None}
    return {"valid": True, "canonical_smiles": Chem.MolToSmiles(mol)}


@mcp.tool()
def tanimoto_similarity(smiles_a: str, smiles_b: str) -> dict:
    """Compute exact Tanimoto similarity between two SMILES strings (Morgan ECFP4)."""
    mol_a = Chem.MolFromSmiles(smiles_a)
    mol_b = Chem.MolFromSmiles(smiles_b)
    if mol_a is None:
        raise ValueError(f"Invalid SMILES: {smiles_a}")
    if mol_b is None:
        raise ValueError(f"Invalid SMILES: {smiles_b}")
    fp_a = AllChem.GetMorganFingerprintAsBitVect(mol_a, radius=2, nBits=2048)
    fp_b = AllChem.GetMorganFingerprintAsBitVect(mol_b, radius=2, nBits=2048)
    return {"tanimoto": DataStructs.TanimotoSimilarity(fp_a, fp_b)}


def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
