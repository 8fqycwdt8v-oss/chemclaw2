"""mcp-chem-intel — lightweight chemistry-intelligence primitives.

Merged from three sibling meta-model repos, keeping only the always-on,
dependency-light tools that add capability ChemClaw2 lacked:

  - score_synthesizability   (chemclaw2_retrosynthesis: SAscore)
  - classify_reaction        (chemclaw2_forward: SMARTS reaction classifier)
  - expand_abbreviation      (eln_structurer: abbreviation / solvent lookup)

The heavy ML ensembles in those repos (forward/condition prediction, learned
single- and multi-step retrosynthesis) need GPUs + model checkpoints and are
meant to run as separate HTTP MCP services — see BACKLOG for that wiring.
"""

import logging
import os
import time

from mcp.server.fastmcp import FastMCP
from mcp_chemclaw_shared import configure_logging

from mcp_chem_intel.abbreviations import expand_abbreviation as _expand_abbreviation
from mcp_chem_intel.classify import classify_reaction as _classify_reaction
from mcp_chem_intel.classify import supported_classes
from mcp_chem_intel.synth import synthetic_accessibility

log: logging.Logger = logging.getLogger("mcp_chem_intel")
mcp = FastMCP("mcp-chem-intel")

MAX_SMILES_LEN = 10_000
MAX_TOKEN_LEN = 200


@mcp.tool()
def score_synthesizability(smiles: str) -> dict:
    """Estimate how hard a molecule is to synthesize (SAscore).

    Returns sa_score on a 1 (trivial) to 10 (very hard) scale plus a short
    interpretation. A fast, deterministic, training-free first filter before
    paying for a full retrosynthesis search. Input is a single molecule SMILES.
    """
    if len(smiles) > MAX_SMILES_LEN:
        raise ValueError(f"smiles exceeds {MAX_SMILES_LEN} chars")
    t0 = time.monotonic()
    result = synthetic_accessibility(smiles)
    log.info(
        "synthesizability_scored",
        extra={"sa_score": result["sa_score"],
               "duration_ms": int((time.monotonic() - t0) * 1000)},
    )
    return result


@mcp.tool()
def classify_reaction(reaction_smiles: str) -> dict:
    """Assign a named reaction class to a reaction SMILES (SMARTS rules).

    Accepts 'reactants>>products' (preferred), 'reactants>agents>products',
    or a bare reactant set 'a.b'. Returns reaction_class (e.g. amide_formation,
    suzuki_coupling, esterification) and matched. matched is True only when a
    rule fully fired; named classes that need the product to confirm come back
    matched=False plus a 'note' (a suggestion, not a confirmation) when only
    reactants are given. Complements mcp-rxnfp's opaque DRFP fingerprint with a
    human-readable name.
    """
    if len(reaction_smiles) > MAX_SMILES_LEN:
        raise ValueError(f"reaction_smiles exceeds {MAX_SMILES_LEN} chars")
    t0 = time.monotonic()
    result = _classify_reaction(reaction_smiles)
    log.info(
        "reaction_classified",
        extra={"reaction_class": result["reaction_class"],
               "matched": result["matched"],
               "duration_ms": int((time.monotonic() - t0) * 1000)},
    )
    return result


@mcp.tool()
def list_reaction_classes() -> dict:
    """List every named reaction class the SMARTS classifier can emit."""
    return {"classes": supported_classes()}


@mcp.tool()
def expand_abbreviation(token: str) -> dict:
    """Resolve a chemistry abbreviation or solvent name from a local table.

    Returns the canonical expansion and, for known solvents, the atmospheric
    boiling point (°C). found=False means no entry — fall back to prior
    knowledge, don't invent. Examples: 'o.n.', 'sat.', 'DCM', 'THF', 'rt'.
    """
    if len(token) > MAX_TOKEN_LEN:
        raise ValueError(f"token exceeds {MAX_TOKEN_LEN} chars")
    result = _expand_abbreviation(token)
    log.info("abbreviation_expanded", extra={"token": token, "found": result["found"]})
    return result


def main():
    global log
    log = configure_logging("mcp-chem-intel")
    log.info("mcp_server_starting", extra={"name": mcp.name, "pid": os.getpid()})
    try:
        mcp.run(transport="stdio")
    except Exception:
        log.exception("mcp_server_crashed")
        raise


if __name__ == "__main__":
    main()
