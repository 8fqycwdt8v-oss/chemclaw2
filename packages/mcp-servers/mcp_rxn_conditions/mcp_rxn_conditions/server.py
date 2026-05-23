import json
import logging
import os
import sys
import time
from datetime import UTC, datetime
from typing import Any

from mcp.server.fastmcp import FastMCP


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "time": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname.lower(),
            "component": "mcp-rxn-conditions",
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
    return logging.getLogger("mcp_rxn_conditions")


log: logging.Logger = logging.getLogger("mcp_rxn_conditions")
mcp = FastMCP("mcp-rxn-conditions")

# Reaction SMILES ceiling matches mcp_rxnfp — reactants>>products can be long
# but 20k chars is a generous bound for realistic chemistry.
MAX_REACTION_SMILES_LEN = 20_000

# IBM's hosted predictor can queue under load; we rely on the SDK's underlying
# httpx timeout to bound a single call. If that proves insufficient, wrap the
# call in asyncio.wait_for via an async tool variant.

# Module-level singleton; assigned in main() once env vars are read.
_wrapper: Any | None = None
_model_version: str | None = None


def _split_csv_list(value: str | None) -> list[str]:
    """Parse a comma-or-dot-separated SMILES list returned by RXN into atoms."""
    if not value:
        return []
    # RXN's reagent output is typically a SMILES of disconnected fragments
    # separated by '.', e.g. 'CC(=O)O.[Na+]'. Split on '.' for round-tripping.
    return [piece.strip() for piece in value.split(".") if piece.strip()]


def _normalize_prediction(raw: dict[str, Any]) -> dict[str, Any]:
    """Coerce the RXN4Chemistry response into our canonical shape.

    IBM's response shape varies by endpoint and SDK version; we accept
    several known field names and return a stable contract to the agent.
    """
    catalysts = raw.get("catalyst") or raw.get("catalysts") or ""
    solvents = raw.get("solvent") or raw.get("solvents") or ""
    reagents = raw.get("reagent") or raw.get("reagents") or ""
    temperature = raw.get("temperature") or raw.get("temperature_c")
    confidence = raw.get("confidence") or raw.get("score")

    temp_c: float | None
    try:
        temp_c = float(temperature) if temperature is not None else None
    except (TypeError, ValueError):
        temp_c = None

    conf: float | None
    try:
        conf = float(confidence) if confidence is not None else None
    except (TypeError, ValueError):
        conf = None

    return {
        "catalysts": _split_csv_list(catalysts) if isinstance(catalysts, str) else list(catalysts or []),
        "solvents": _split_csv_list(solvents) if isinstance(solvents, str) else list(solvents or []),
        "reagents": _split_csv_list(reagents) if isinstance(reagents, str) else list(reagents or []),
        "temperature_c": temp_c,
        "confidence": conf,
    }


@mcp.tool()
def predict_conditions(reaction_smiles: str) -> dict:
    """Predict reaction conditions for a reaction SMILES via IBM RXN4Chemistry.

    reaction_smiles format: reactants>>products (e.g. 'CC=O.NC>>CC=NC').
    Returns either:
      {catalysts, solvents, reagents, temperature_c, confidence,
       model_version, source: "rxn4chemistry"}
    or, on any failure:
      {error: "<message>", source: "rxn4chemistry"}

    Failure is fail-open with a logged error — callers should fall back
    to `suggest_conditions_from_neighbors` (Phase A) for resilience.
    """
    if not reaction_smiles or not reaction_smiles.strip():
        return {"error": "reaction_smiles is required", "source": "rxn4chemistry"}
    if ">>" not in reaction_smiles:
        return {"error": "reaction_smiles must contain '>>' separator", "source": "rxn4chemistry"}
    if len(reaction_smiles) > MAX_REACTION_SMILES_LEN:
        log.warning("rxn_smiles_oversize", extra={"length": len(reaction_smiles), "max": MAX_REACTION_SMILES_LEN})
        return {"error": "reaction_smiles too long", "source": "rxn4chemistry"}

    if _wrapper is None:
        log.warning("predictor_uninitialized")
        return {"error": "predictor unavailable: RXN_API_KEY not configured", "source": "rxn4chemistry"}

    t0 = time.monotonic()
    try:
        # The wrapper exposes condition / reagent prediction under varying
        # method names across SDK versions. Try the documented call sites
        # in order. Any AttributeError indicates an older SDK.
        if hasattr(_wrapper, "predict_reagents"):
            raw = _wrapper.predict_reagents(reaction=reaction_smiles)
        elif hasattr(_wrapper, "predict_reaction_properties"):
            raw = _wrapper.predict_reaction_properties(reactions=[reaction_smiles])
        else:
            log.error("predictor_method_missing")
            return {"error": "predictor method not available in installed SDK", "source": "rxn4chemistry"}
    except Exception:
        # Don't leak internal error detail to the agent (CLAUDE.md security-4);
        # log the real error server-side, return a generic message.
        log.exception("predict_failed", extra={"smiles_len": len(reaction_smiles)})
        return {"error": "predictor request failed", "source": "rxn4chemistry"}

    if not isinstance(raw, dict):
        log.error("predict_response_shape", extra={"type": type(raw).__name__})
        return {"error": "predictor returned unexpected response", "source": "rxn4chemistry"}

    normalized = _normalize_prediction(raw)
    log.info(
        "conditions_predicted",
        extra={
            "smiles_len": len(reaction_smiles),
            "duration_ms": int((time.monotonic() - t0) * 1000),
            "has_temperature": normalized["temperature_c"] is not None,
        },
    )
    return {
        **normalized,
        "model_version": _model_version or "rxn4chemistry:unknown",
        "source": "rxn4chemistry",
    }


def _init_wrapper() -> tuple[Any | None, str | None]:
    """Read env vars and instantiate the SDK wrapper. Env vars are read
    here (not at module import) per CLAUDE.md: a missing key must not
    kill the MCP server — predict_conditions returns an error envelope
    instead, and the agent falls back to neighbor lookup.
    """
    api_key = os.environ.get("RXN_API_KEY")
    if not api_key:
        log.warning("rxn_api_key_missing")
        return None, None
    try:
        from rxn4chemistry import RXN4ChemistryWrapper
    except ImportError:
        log.exception("rxn4chemistry_import_failed")
        return None, None
    try:
        wrapper = RXN4ChemistryWrapper(api_key=api_key)
        project_id = os.environ.get("RXN_PROJECT_ID")
        if project_id and hasattr(wrapper, "set_project"):
            wrapper.set_project(project_id)
    except Exception:
        log.exception("rxn_wrapper_init_failed")
        return None, None
    version = os.environ.get("RXN_MODEL_VERSION", "rxn4chemistry:latest")
    return wrapper, version


def main():
    global log, _wrapper, _model_version
    log = _configure_logging()
    log.info("mcp_server_starting", extra={"name": mcp.name, "pid": os.getpid()})
    _wrapper, _model_version = _init_wrapper()
    if _wrapper is None:
        log.warning("predictor_disabled_starting_anyway")
    try:
        mcp.run(transport="stdio")
    except Exception:
        log.exception("mcp_server_crashed")
        raise


if __name__ == "__main__":
    main()
