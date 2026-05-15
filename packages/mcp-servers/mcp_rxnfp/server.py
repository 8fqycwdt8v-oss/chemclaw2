from mcp.server.fastmcp import FastMCP
from drfp import DrfpEncoder

mcp = FastMCP("mcp-rxnfp")

# CRITICAL: default DRFP is 512-bit; always pass n_folded_length=2048 explicitly
_NBITS = 2048


@mcp.tool()
def compute_drfp(reaction_smiles: str, n_bits: int = _NBITS) -> dict:
    """Compute DRFP fingerprint for a reaction SMILES string.

    reaction_smiles format: reactants>>products (e.g. 'CC>>CCC')
    Returns fingerprint_bits (binary string of '0'/'1', length = n_bits) and n_bits.
    Compatible with Postgres BIT(n_bits) via $1::bit(n_bits) parameter cast.
    n_bits MUST be 2048 to match the database column — do not change.
    """
    fps = DrfpEncoder.encode([reaction_smiles], n_folded_length=n_bits)
    bit_arr = fps[0]
    # DrfpEncoder returns a numpy array of 0/1 ints
    bit_str = "".join(str(int(b)) for b in bit_arr)
    if len(bit_str) != n_bits:
        raise RuntimeError(
            f"DRFP returned {len(bit_str)} bits, expected {n_bits}. "
            "Verify drfp version supports n_folded_length."
        )
    # Return binary string ('010101...') — compatible with Postgres BIT(2048) parameter cast
    return {"fingerprint_bits": bit_str, "n_bits": n_bits}


def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
