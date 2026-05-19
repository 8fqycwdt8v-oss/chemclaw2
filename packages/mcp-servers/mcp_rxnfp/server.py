import json
import logging
import os
import sys
import time
from datetime import datetime, timezone

from drfp import DrfpEncoder
from mcp.server.fastmcp import FastMCP


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "time": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "level": record.levelname.lower(),
            "component": "mcp-rxnfp",
            "event": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        for k, v in record.__dict__.items():
            if k in ("args", "msg", "name", "exc_info", "exc_text", "stack_info",
                     "lineno", "funcName", "created", "msecs", "relativeCreated",
                     "thread", "threadName", "processName", "process", "filename",
                     "module", "pathname", "levelname", "levelno"):
                continue
            payload[k] = v
        return json.dumps(payload)


def _configure_logging() -> logging.Logger:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(os.environ.get("MCP_LOG_LEVEL", "INFO"))
    return logging.getLogger("mcp_rxnfp")


log: logging.Logger = logging.getLogger("mcp_rxnfp")
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
    if not reaction_smiles or not reaction_smiles.strip():
        raise ValueError("reaction_smiles is required and cannot be empty")
    if ">>" not in reaction_smiles:
        raise ValueError("reaction_smiles must contain '>>' separator (reactants>>products)")
    if len(reaction_smiles) > MAX_REACTION_SMILES_LEN:
        log.warning("rxn_smiles_oversize", extra={"length": len(reaction_smiles), "max": MAX_REACTION_SMILES_LEN})
        raise ValueError(
            f"reaction_smiles exceeds {MAX_REACTION_SMILES_LEN} chars (got {len(reaction_smiles)})"
        )
    t0 = time.monotonic()
    try:
        fps = DrfpEncoder.encode([reaction_smiles], n_folded_length=_NBITS)
    except Exception:
        log.exception("drfp_encode_failed", extra={"smiles_len": len(reaction_smiles)})
        raise ValueError("Failed to encode reaction SMILES") from None
    bit_arr = fps[0]
    # DrfpEncoder returns a numpy array of 0/1 ints
    bit_str = "".join(str(b) for b in bit_arr)
    if len(bit_str) != _NBITS:
        log.error(
            "drfp_bit_length_drift",
            extra={"expected": _NBITS, "actual": len(bit_str)},
        )
        raise RuntimeError(
            f"DRFP returned {len(bit_str)} bits, expected {_NBITS}. "
            "Verify drfp version supports n_folded_length."
        )
    log.info(
        "drfp_computed",
        extra={
            "smiles_len": len(reaction_smiles),
            "n_bits": _NBITS,
            "duration_ms": int((time.monotonic() - t0) * 1000),
        },
    )
    return {"fingerprint_bits": bit_str, "n_bits": _NBITS}


def main():
    global log
    log = _configure_logging()
    log.info("mcp_server_starting", extra={"name": mcp.name, "pid": os.getpid()})
    try:
        mcp.run(transport="stdio")
    except Exception:
        log.exception("mcp_server_crashed")
        raise


if __name__ == "__main__":
    main()
