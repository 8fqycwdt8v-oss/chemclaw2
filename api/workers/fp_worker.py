"""Fingerprint worker — asyncio replacement for the pg-boss fp-worker.

Polls for NULL fingerprints every 30 seconds and computes them via
subprocess MCP calls to mcp_molfp / mcp_rxnfp. Uses Postgres advisory
locks to prevent duplicate processing in multi-instance deploys.

Run standalone:
    python -m api.workers.fp_worker

Or mounted as a background task in the FastAPI lifespan.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
import sys
import uuid as _uuid_mod
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.db.queries.fingerprints import (
    fetch_compounds_missing_fp,
    fetch_reactions_missing_fp,
    release_fp_lock,
    try_acquire_fp_lock,
    write_compound_fp,
    write_reaction_fp,
)

logger = logging.getLogger(__name__)

_FP_RE = re.compile(r'^[01]{2048}$')

POLL_INTERVAL_SECONDS = 30
BATCH_SIZE = 50

_in_flight = False


def _stable_lock_key(row_id: str) -> int:
    """Derive a stable, cross-process advisory lock key from a UUID string.

    Python's hash() is randomised per process (PYTHONHASHSEED), making it
    useless for cross-instance locking. Instead, take the lower 63 bits of
    the UUID integer — this is stable, process-independent, and fits in a
    Postgres BIGINT positive range with a collision probability low enough
    for batch sizes ≤50 (~1e-12 at 50 items vs 2^63 key space).
    """
    return _uuid_mod.UUID(row_id).int % (2 ** 63)


# MCP servers reject `tools/call` before the `initialize` handshake completes,
# so every call sends the full three-message sequence on stdin. The tools/call
# id is fixed so the response parser can pick it out from the initialize reply.
_MCP_CALL_ID = 2


async def _converse(proc: Any, tool_name: str, tool_input: dict[str, Any]) -> dict[str, Any]:
    """Drive one initialize → initialized → tools/call exchange on the pipes.

    The server tears the session down at stdin EOF, so the handshake must be
    staged (write, read the initialize reply, then send the call) rather than
    pipelined through a single communicate().
    """
    def _send(msg: dict[str, Any]) -> None:
        proc.stdin.write(json.dumps(msg).encode() + b"\n")

    _send({
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "chemclaw2-fp-worker", "version": "1.0"},
        },
    })
    await proc.stdin.drain()
    await proc.stdout.readline()  # initialize reply (id 1)
    _send({"jsonrpc": "2.0", "method": "notifications/initialized"})
    _send({
        "jsonrpc": "2.0", "id": _MCP_CALL_ID, "method": "tools/call",
        "params": {"name": tool_name, "arguments": tool_input},
    })
    await proc.stdin.drain()
    while True:
        line = await proc.stdout.readline()
        if not line:
            raise RuntimeError("server closed stdout before responding")
        text = line.decode().strip()
        if not text.startswith("{"):
            continue
        try:
            resp = json.loads(text)
        except json.JSONDecodeError as e:
            logger.warning("mcp_response_invalid_json tool=%s err=%s", tool_name, e)
            continue
        # Skip anything that isn't the tools/call response (server-initiated
        # notifications, stray output).
        if resp.get("id") == _MCP_CALL_ID:
            return resp


async def _call_mcp_tool(server_module: str, tool_name: str, tool_input: dict[str, Any]) -> dict[str, Any]:
    """Call an MCP stdio server tool via subprocess. Safe: module name is a constant, not user input."""
    # asyncio.create_subprocess_exec does NOT invoke a shell — no injection risk.
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", server_module,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        try:
            resp = await asyncio.wait_for(
                _converse(proc, tool_name, tool_input), timeout=30.0
            )
        finally:
            # Stdin EOF tells the server to exit; collect remaining output and
            # reap. Guard each step — handles may already be closed.
            with contextlib.suppress(Exception):
                proc.stdin.close()  # type: ignore[union-attr]  # PIPE guarantees stdin
            try:
                _, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=5.0)
                if stderr_bytes:
                    logger.debug("mcp_stderr server=%s tool=%s: %s", server_module, tool_name,
                                 stderr_bytes.decode(errors="replace")[:500])
            except Exception:
                pass
    except TimeoutError:
        with contextlib.suppress(Exception):
            proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=2.0)
        except TimeoutError:
            with contextlib.suppress(Exception):
                proc.kill()
            # Reap the SIGKILL'd child: without a second wait() the exit
            # status is never collected and the stdin/stdout/stderr pipe
            # transports stay open, so repeated timeouts leak zombies + fds.
            # (This mirrors the kill-then-wait pattern in mcp_codesandbox.)
            with contextlib.suppress(Exception):
                await asyncio.wait_for(proc.wait(), timeout=2.0)
        raise RuntimeError(f"MCP call to {server_module}.{tool_name} timed out") from None

    if "error" in resp:
        err = resp["error"]
        raise RuntimeError(
            f"MCP {server_module}.{tool_name} returned error "
            f"{err.get('code')}: {err.get('message')}"
        )
    result = resp.get("result", {})
    if result.get("isError"):
        # Tool-level failure: content is a plain-text message, not JSON.
        texts = " ".join(b.get("text", "") for b in result.get("content", []))
        raise RuntimeError(f"MCP {server_module}.{tool_name} tool error: {texts[:300]}")
    for block in resp.get("result", {}).get("content", []):
        if block.get("type") == "text":
            try:
                return json.loads(block["text"])
            except json.JSONDecodeError as e:
                raise RuntimeError(
                    f"MCP {server_module}.{tool_name} returned invalid JSON in text block: {e}"
                ) from e
    raise RuntimeError(f"No text block in MCP response from {server_module}.{tool_name}")


async def compute_compound_fingerprints(db: AsyncSession) -> int:
    rows = await fetch_compounds_missing_fp(db, BATCH_SIZE)
    computed = 0
    for compound_id, smiles in rows:
        lock_key = _stable_lock_key(compound_id)
        if not await try_acquire_fp_lock(db, lock_key):
            continue
        try:
            data = await _call_mcp_tool("mcp_molfp.server", "compute_morgan_fp", {"smiles": smiles})
            bits = data.get("fingerprint_bits", "")
            if not _FP_RE.match(bits):
                logger.error("fp_invalid_bits compound=%s", compound_id)
                continue
            await write_compound_fp(db, compound_id, bits)
            await db.commit()
            computed += 1
        except Exception as e:
            logger.warning("fp_compute_failed compound=%s: %s", compound_id, e)
            try:
                await db.rollback()
            except Exception as rb_err:
                logger.warning("fp_rollback_failed compound=%s: %s", compound_id, rb_err)
        finally:
            try:
                await release_fp_lock(db, lock_key)
            except Exception as unlock_err:
                logger.warning("fp_advisory_unlock_failed compound=%s: %s", compound_id, unlock_err)
    return computed


async def compute_reaction_fingerprints(db: AsyncSession) -> int:
    rows = await fetch_reactions_missing_fp(db, BATCH_SIZE)
    computed = 0
    for reaction_id, rxn_smiles in rows:
        lock_key = _stable_lock_key(reaction_id)
        if not await try_acquire_fp_lock(db, lock_key):
            continue
        try:
            data = await _call_mcp_tool("mcp_rxnfp.server", "compute_drfp", {"reaction_smiles": rxn_smiles})
            bits = data.get("fingerprint_bits", "")
            if not _FP_RE.match(bits):
                logger.error("drfp_invalid_bits reaction=%s", reaction_id)
                continue
            await write_reaction_fp(db, reaction_id, bits)
            await db.commit()
            computed += 1
        except Exception as e:
            logger.warning("drfp_compute_failed reaction=%s: %s", reaction_id, e)
            try:
                await db.rollback()
            except Exception as rb_err:
                logger.warning("drfp_rollback_failed reaction=%s: %s", reaction_id, rb_err)
        finally:
            try:
                await release_fp_lock(db, lock_key)
            except Exception as unlock_err:
                logger.warning("drfp_advisory_unlock_failed reaction=%s: %s", reaction_id, unlock_err)
    return computed


async def run_worker(session_factory: async_sessionmaker[AsyncSession]) -> None:
    global _in_flight
    logger.info("fp_worker_started")
    _cycle = 0
    try:
        while True:
            await asyncio.sleep(POLL_INTERVAL_SECONDS)
            if _in_flight:
                continue
            _in_flight = True
            try:
                async with session_factory() as db:
                    c = await compute_compound_fingerprints(db)
                    r = await compute_reaction_fingerprints(db)
                if c or r:
                    logger.info("fp_worker_cycle compounds=%d reactions=%d", c, r)
                _cycle += 1
                if _cycle % 10 == 0:
                    logger.info("fp_worker_heartbeat cycle=%d", _cycle)
            except Exception:
                logger.exception("fp_worker_cycle_error")
            finally:
                _in_flight = False
    except asyncio.CancelledError:
        logger.info("fp_worker_shutdown")


if __name__ == "__main__":
    from api.observability.logging import configure_logging
    configure_logging()
    from api.db.connection import async_session_factory as factory
    from api.db.connection import init_db
    init_db()
    if factory is None:
        raise RuntimeError("init_db() failed")
    asyncio.run(run_worker(factory))
