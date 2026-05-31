"""ORD (Open Reaction Database) message validation.

Wraps ord-schema's own validation suite (``ord_schema.validations``) — the
off-the-shelf rule set maintained by the ORD project — and exposes it as plain
MCP tools. The ELN-structuring flow uses this to turn a free-text experimental
procedure, once the agent has structured it into an ORD ``Reaction``, into a
*validated* message: the returned errors + warnings feed the agent's
self-repair loop (re-edit the structure, re-validate) without vendoring a
second nested agent runtime.

We deliberately do NOT hand-roll CMP/STR/STO rules. ``validate_message``
recurses (``recurse=True``) through the message tree, so a single Reaction
validation already covers:
  - CMP — compounds (`validate_compound`)
  - STR — compound identifiers / SMILES parseability (`validate_compound_identifier`)
  - STO — amounts, concentrations, stoichiometry (`validate_amount`, …)
  - ORD — the Reaction message itself (outcomes, conditions, provenance)
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("mcp_chem_intel")

# ord-schema ships behind the [ord] extra (it pulls protobuf/pandas/pyarrow);
# the base mcp_chem_intel install stays light. The tools degrade to a clear
# error envelope rather than crashing the agent when the extra is absent —
# same fail-soft contract as mcp_tabular's [tabicl] gate.
_NOT_INSTALLED = {
    "valid": False,
    "errors": ["ord-schema is not installed on this server (pip install 'mcp-chem-intel[ord]')"],
    "warnings": [],
}


def _validate_message(message: Any, *, require_provenance: bool) -> dict:
    """Run ord-schema's recursive validator and shape the output.

    Never raises on ORD-content problems — they come back as the `errors`
    list so the caller's repair loop can act on them.
    """
    from ord_schema import validations  # noqa: PLC0415

    options = validations.ValidationOptions(require_provenance=require_provenance)
    out = validations.validate_message(message, raise_on_error=False, options=options)
    errors = list(out.errors)
    return {"valid": not errors, "errors": errors, "warnings": list(out.warnings)}


def validate_reaction(reaction_json: dict[str, Any], *, require_provenance: bool = False) -> dict:
    """Validate a JSON-shaped ORD ``Reaction`` against the ORD schema.

    `reaction_json` is the protobuf-JSON form of an `ord_schema.proto.Reaction`
    (inputs / conditions / outcomes / provenance). A malformed structure
    (unknown field, wrong type) comes back as `valid=False` with the parse
    error in `errors` rather than raising, so an ELN draft can be iterated on.

    `require_provenance=False` by default — early ELN drafts rarely carry full
    provenance, and flagging it would drown the substantive structure errors.
    """
    if not isinstance(reaction_json, dict):
        raise ValueError("reaction_json must be a JSON object")

    try:
        from google.protobuf import json_format  # noqa: PLC0415
        from ord_schema.proto import reaction_pb2  # noqa: PLC0415
    except ImportError:
        logger.warning("ord_schema_not_installed")
        return dict(_NOT_INSTALLED)

    try:
        message = json_format.ParseDict(reaction_json, reaction_pb2.Reaction())
    except json_format.ParseError as exc:
        return {"valid": False, "errors": [f"ORD parse error: {exc}"], "warnings": []}

    result = _validate_message(message, require_provenance=require_provenance)
    result["num_inputs"] = len(message.inputs)
    result["num_outcomes"] = len(message.outcomes)
    return result


def validate_compound(compound_json: dict[str, Any]) -> dict:
    """Validate a single JSON-shaped ORD ``Compound`` against the ORD schema.

    Granular check for the agent to validate one component (identifiers /
    amount / role) before assembling the full reaction. Same non-raising
    contract as `validate_reaction`.
    """
    if not isinstance(compound_json, dict):
        raise ValueError("compound_json must be a JSON object")

    try:
        from google.protobuf import json_format  # noqa: PLC0415
        from ord_schema.proto import reaction_pb2  # noqa: PLC0415
    except ImportError:
        logger.warning("ord_schema_not_installed")
        return dict(_NOT_INSTALLED)

    try:
        message = json_format.ParseDict(compound_json, reaction_pb2.Compound())
    except json_format.ParseError as exc:
        return {"valid": False, "errors": [f"ORD parse error: {exc}"], "warnings": []}

    return _validate_message(message, require_provenance=False)
