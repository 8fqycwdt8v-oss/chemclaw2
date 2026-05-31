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
from mcp_chem_intel.ord_validate import validate_compound as _validate_compound
from mcp_chem_intel.ord_validate import validate_reaction as _validate_reaction
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


@mcp.tool()
def validate_ord_reaction(reaction_json: dict, require_provenance: bool = False) -> dict:
    """Validate a structured ORD Reaction against the Open Reaction Database schema.

    `reaction_json` is the protobuf-JSON form of an ORD `Reaction` (inputs /
    conditions / outcomes / provenance). Returns {valid, errors, warnings,
    num_inputs, num_outcomes}. Recurses into compounds, identifiers/SMILES, and
    amounts/stoichiometry, so one call covers the whole message. A malformed
    structure returns valid=False with the parse error in `errors` (never
    raises) so an ELN-structuring draft can be iterated on. `require_provenance`
    defaults False (early drafts rarely carry it).
    """
    t0 = time.monotonic()
    result = _validate_reaction(reaction_json, require_provenance=require_provenance)
    log.info(
        "ord_reaction_validated",
        extra={"valid": result["valid"], "n_errors": len(result["errors"]),
               "duration_ms": int((time.monotonic() - t0) * 1000)},
    )
    return result


@mcp.tool()
def validate_ord_compound(compound_json: dict) -> dict:
    """Validate a single ORD Compound (identifiers / amount / role) against the schema.

    Granular check before assembling a full reaction. Returns {valid, errors,
    warnings}; malformed input returns valid=False with the parse error rather
    than raising.
    """
    result = _validate_compound(compound_json)
    log.info("ord_compound_validated", extra={"valid": result["valid"], "n_errors": len(result["errors"])})
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
