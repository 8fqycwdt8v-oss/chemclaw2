"""Postgres-backed SessionStore for the Python Agent SDK.

Mirrors the TypeScript `postgresSessionStore` in packages/db/src/session-store.ts.
Uses the same `agent_sessions` table with advisory locks for concurrent-safe appends.
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

logger = logging.getLogger(__name__)

MAX_KEY_PART_LEN = 256
MAX_APPEND_ENTRIES = 100
MAX_ENTRY_SERIALIZED_BYTES = 1_000_000


def _assert_key_component(name: str, value: str) -> None:
    if len(value) > MAX_KEY_PART_LEN:
        raise ValueError(f"session-store: {name} exceeds {MAX_KEY_PART_LEN} chars")


class PostgresSessionStore:
    """Implements the claude_agent_sdk.types.SessionStore protocol backed by Postgres."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._factory = session_factory

    async def append(
        self,
        key: dict[str, Any],  # SessionKey TypedDict
        entries: list[dict[str, Any]],
    ) -> None:
        project_key = key["project_key"]
        session_id = key["session_id"]
        subpath = key.get("subpath") or ""
        _assert_key_component("project_key", project_key)
        _assert_key_component("session_id", session_id)
        _assert_key_component("subpath", subpath)

        if len(entries) > MAX_APPEND_ENTRIES:
            raise ValueError(f"session-store: refusing to append {len(entries)} entries (max {MAX_APPEND_ENTRIES})")

        import json
        serialized = json.dumps(entries).encode()
        if len(serialized) > MAX_ENTRY_SERIALIZED_BYTES:
            raise ValueError(
                f"session-store: entries serialize to {len(serialized)} bytes "
                f"(max {MAX_ENTRY_SERIALIZED_BYTES})"
            )

        import time
        mtime = int(time.time() * 1000)

        async with self._factory() as db:
            async with db.begin():
                # Advisory lock: two 32-bit hashes combined → 64-bit lock key.
                # Prevents concurrent appends from interleaving entries.
                await db.execute(text("""
                    SELECT pg_advisory_xact_lock(
                        hashtext(:project_key),
                        hashtext(:session_id || '::' || :subpath)
                    )
                """), {"project_key": project_key, "session_id": session_id, "subpath": subpath})

                await db.execute(text("""
                    INSERT INTO agent_sessions (project_key, session_id, subpath, entries, mtime)
                    VALUES (:project_key, :session_id, :subpath, CAST(:entries AS jsonb), :mtime)
                    ON CONFLICT (project_key, session_id, subpath) DO UPDATE
                        SET entries   = agent_sessions.entries || EXCLUDED.entries,
                            mtime     = EXCLUDED.mtime,
                            insert_seq = DEFAULT
                """), {
                    "project_key": project_key,
                    "session_id": session_id,
                    "subpath": subpath,
                    "entries": serialized.decode(),
                    "mtime": mtime,
                })

    async def load(
        self,
        key: dict[str, Any],
    ) -> list[dict[str, Any]]:
        project_key = key["project_key"]
        session_id = key["session_id"]
        subpath = key.get("subpath") or ""

        async with self._factory() as db:
            result = await db.execute(text("""
                SELECT entries
                FROM agent_sessions
                WHERE project_key = :project_key
                  AND session_id  = :session_id
                  AND subpath     = :subpath
                ORDER BY insert_seq
            """), {"project_key": project_key, "session_id": session_id, "subpath": subpath})
            rows = result.fetchall()
            out = []
            for (entries,) in rows:
                if isinstance(entries, list):
                    out.extend(entries)
            return out

    async def list_sessions(self, project_key: str) -> list[str]:
        async with self._factory() as db:
            result = await db.execute(text("""
                SELECT DISTINCT session_id
                FROM agent_sessions
                WHERE project_key = :project_key AND subpath = ''
                ORDER BY session_id
            """), {"project_key": project_key})
            return [r.session_id for r in result]

    async def delete(self, key: dict[str, Any]) -> None:
        project_key = key["project_key"]
        session_id = key["session_id"]
        subpath = key.get("subpath") or ""
        async with self._factory() as db:
            await db.execute(text("""
                DELETE FROM agent_sessions
                WHERE project_key = :project_key
                  AND session_id  = :session_id
                  AND (:subpath = '' OR subpath = :subpath)
            """), {"project_key": project_key, "session_id": session_id, "subpath": subpath})
            await db.commit()


def scoped_session_store(
    session_factory: async_sessionmaker[AsyncSession],
    project_key: str,
) -> "ScopedPostgresSessionStore":
    """Returns a store that forces every key to use the given project_key.
    Mirrors TypeScript's scopedSessionStore() for multi-tenant isolation.
    """
    return ScopedPostgresSessionStore(session_factory, project_key)


class ScopedPostgresSessionStore(PostgresSessionStore):
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        forced_project_key: str,
    ) -> None:
        super().__init__(session_factory)
        self._forced_project_key = forced_project_key

    def _scoped(self, key: dict[str, Any]) -> dict[str, Any]:
        return {**key, "project_key": self._forced_project_key}

    async def append(self, key: dict[str, Any], entries: list[dict[str, Any]]) -> None:
        await super().append(self._scoped(key), entries)

    async def load(self, key: dict[str, Any]) -> list[dict[str, Any]]:
        return await super().load(self._scoped(key))

    async def delete(self, key: dict[str, Any]) -> None:
        await super().delete(self._scoped(key))

    async def list_sessions(self, project_key: str) -> list[str]:
        return await super().list_sessions(self._forced_project_key)
