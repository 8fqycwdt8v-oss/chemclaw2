from mcp.server.fastmcp import FastMCP
from drfp import DrfpEncoder

mcp = FastMCP("mcp-rxnfp")

# Fixed at 2048 to match the BIT(2048) column in reactions.drfp. DRFP's library
# default is 512; we pin and don't expose the dial.
_NBITS = 2048

# Reaction SMILES are longer than single-compound SMILES (reactants>>products);
# 20k chars is a generous ceiling for realistic chemistry.
MAX_REACTION_SMILES_LEN = 20_000


@mcp.tool()
def compute_drfp(reaction_smiles: str) -> dict:
    """Compute DRFP fingerprint for a reaction SMILES string.

    reaction_smiles format: reactants>>products (e.g. 'CC>>CCC')
    Returns fingerprint_bits (binary string of '0'/'1', length 2048) and n_bits.
    Compatible with Postgres BIT(2048) via $1::bit(2048) parameter cast.
    """
    if len(reaction_smiles) > MAX_REACTION_SMILES_LEN:
        raise ValueError(
            f"reaction_smiles exceeds {MAX_REACTION_SMILES_LEN} chars (got {len(reaction_smiles)})"
        )
    fps = DrfpEncoder.encode([reaction_smiles], n_folded_length=_NBITS)
    bit_arr = fps[0]
    # DrfpEncoder returns a numpy array of 0/1 ints
    bit_str = "".join(str(int(b)) for b in bit_arr)
    if len(bit_str) != _NBITS:
        raise RuntimeError(
            f"DRFP returned {len(bit_str)} bits, expected {_NBITS}. "
            "Verify drfp version supports n_folded_length."
        )
    return {"fingerprint_bits": bit_str, "n_bits": _NBITS}


def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
