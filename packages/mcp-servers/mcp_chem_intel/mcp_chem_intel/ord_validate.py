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


def _not_installed() -> dict:
    """Fresh error envelope for when the [ord] extra (ord-schema) is absent.

    A new dict (with its own lists) each call — never a shared module constant —
    so a caller that appends to `errors`/`warnings` can't leak into later calls.
    The tools return this instead of raising so the agent's repair loop survives
    a server that wasn't installed with `[ord]`.
    """
    return {
        "valid": False,
        "errors": ["ord-schema is not installed on this server (pip install 'mcp-chem-intel[ord]')"],
        "warnings": [],
    }


def _parse_and_validate(
    payload: dict[str, Any], proto_name: str, *, require_provenance: bool
) -> tuple[dict, Any | None]:
    """Shared spine for both tools: import-guard → ParseDict → validate_message.

    Returns `(result, message)`. `message` is None when the result is a
    not-installed or parse-error envelope (so the caller skips message-derived
    fields). All three ord-schema imports are guarded together here, so a
    partial install can't sneak a later unguarded `ord_schema.validations`
    import past the not-installed envelope.
    """
    try:
        from google.protobuf import json_format  # noqa: PLC0415
        from ord_schema import validations  # noqa: PLC0415
        from ord_schema.proto import reaction_pb2  # noqa: PLC0415
    except ImportError:
        logger.warning("ord_schema_not_installed")
        return _not_installed(), None

    try:
        message = json_format.ParseDict(payload, getattr(reaction_pb2, proto_name)())
    except json_format.ParseError as exc:
        return {"valid": False, "errors": [f"ORD parse error: {exc}"], "warnings": []}, None

    options = validations.ValidationOptions(require_provenance=require_provenance)
    out = validations.validate_message(message, raise_on_error=False, options=options)
    errors = list(out.errors)
    return {"valid": not errors, "errors": errors, "warnings": list(out.warnings)}, message


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

    result, message = _parse_and_validate(reaction_json, "Reaction", require_provenance=require_provenance)
    if message is not None:
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

    result, _ = _parse_and_validate(compound_json, "Compound", require_provenance=False)
    return result
