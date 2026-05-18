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
import json
import logging
import re
import sys
import uuid as _uuid_mod
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

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


async def _call_mcp_tool(server_module: str, tool_name: str, tool_input: dict[str, Any]) -> dict[str, Any]:
    """Call an MCP stdio server tool via subprocess. Safe: module name is a constant, not user input."""
    request = {
        "jsonrpc": "2.0", "id": 1,
        "method": "tools/call",
        "params": {"name": tool_name, "arguments": tool_input},
    }
    # asyncio.create_subprocess_exec does NOT invoke a shell — no injection risk.
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", server_module,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr_bytes = await asyncio.wait_for(
            proc.communicate(json.dumps(request).encode()), timeout=30.0
        )
        if stderr_bytes:
            logger.debug("mcp_stderr server=%s tool=%s: %s", server_module, tool_name,
                         stderr_bytes.decode(errors="replace")[:500])
    except asyncio.TimeoutError:
        try:
            proc.terminate()
        except Exception:
            pass
        try:
            await asyncio.wait_for(proc.wait(), timeout=2.0)
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except Exception:
                pass
        raise RuntimeError(f"MCP call to {server_module}.{tool_name} timed out")

    for line in reversed(stdout.decode().strip().splitlines()):
        if line.strip().startswith('{'):
            resp = json.loads(line)
            for block in resp.get("result", {}).get("content", []):
                if block.get("type") == "text":
                    return json.loads(block["text"])
            raise RuntimeError(f"No text block in MCP response: {resp}")
    raise RuntimeError(f"Could not parse MCP response: {stdout.decode()[:200]}")


async def compute_compound_fingerprints(db: AsyncSession) -> int:
    rows = (await db.execute(
        text("SELECT id::text, smiles FROM compounds WHERE morgan_fp IS NULL LIMIT :n"), {"n": BATCH_SIZE}
    )).fetchall()
    computed = 0
    for row in rows:
        compound_id, smiles = row.id, row.smiles
        lock_key = _stable_lock_key(compound_id)
        if not (await db.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": lock_key})).scalar():
            continue
        try:
            data = await _call_mcp_tool("mcp_molfp.server", "compute_morgan_fp", {"smiles": smiles})
            bits = data.get("fingerprint_bits", "")
            if not _FP_RE.match(bits):
                logger.error("fp_invalid_bits compound=%s", compound_id)
                continue
            await db.execute(text("""
                UPDATE compounds
                SET morgan_fp = :bits::bit(2048), morgan_fp_popcount = :pc, fp_computed_at = now()
                WHERE id = :id::uuid AND morgan_fp IS NULL
            """), {"bits": bits, "pc": bits.count('1'), "id": compound_id})
            await db.commit()
            computed += 1
        except Exception as e:
            logger.warning("fp_compute_failed compound=%s: %s", compound_id, e)
            try:
                await db.rollback()
            except Exception:
                pass
        finally:
            try:
                await db.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": lock_key})
            except Exception as unlock_err:
                logger.warning("fp_advisory_unlock_failed compound=%s: %s", compound_id, unlock_err)
    return computed


async def compute_reaction_fingerprints(db: AsyncSession) -> int:
    rows = (await db.execute(
        text("SELECT id::text, rxn_smiles FROM reactions WHERE drfp IS NULL LIMIT :n"), {"n": BATCH_SIZE}
    )).fetchall()
    computed = 0
    for row in rows:
        reaction_id, rxn_smiles = row.id, row.rxn_smiles
        lock_key = _stable_lock_key(reaction_id)
        if not (await db.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": lock_key})).scalar():
            continue
        try:
            data = await _call_mcp_tool("mcp_rxnfp.server", "compute_drfp", {"rxn_smiles": rxn_smiles})
            bits = data.get("fingerprint_bits", "")
            if not _FP_RE.match(bits):
                logger.error("drfp_invalid_bits reaction=%s", reaction_id)
                continue
            await db.execute(text("""
                UPDATE reactions SET drfp = :bits::bit(2048), fp_computed_at = now()
                WHERE id = :id::uuid AND drfp IS NULL
            """), {"bits": bits, "id": reaction_id})
            await db.commit()
            computed += 1
        except Exception as e:
            logger.warning("drfp_compute_failed reaction=%s: %s", reaction_id, e)
            try:
                await db.rollback()
            except Exception:
                pass
        finally:
            try:
                await db.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": lock_key})
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
    import os
    logging.basicConfig(level=logging.INFO)
    from api.db.connection import init_db, async_session_factory as factory
    init_db()
    if factory is None:
        raise RuntimeError("init_db() failed")
    asyncio.run(run_worker(factory))
