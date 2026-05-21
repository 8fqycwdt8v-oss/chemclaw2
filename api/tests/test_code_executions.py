"""Integration tests for the code_executions audit log."""
from __future__ import annotations

import uuid

import pytest

from api.db.queries.code_executions import (
    get_execution,
    insert_execution,
    list_executions,
)


async def test_insert_and_list_session_scoped(
    session_factory, user_id: str,
) -> None:
    sid = f"sess-{uuid.uuid4().hex[:8]}"
    async with session_factory() as db:
        eid = await insert_execution(
            db,
            code="print('hi')",
            stdout="hi\n",
            stderr="",
            exit_code=0,
            duration_ms=42,
            status="completed",
            created_by=user_id,
            session_id=sid,
        )
    async with session_factory() as db:
        rows = await list_executions(db, user_id, session_id=sid)
    assert len(rows) == 1
    assert rows[0]["id"] == eid
    assert rows[0]["session_id"] == sid
    assert rows[0]["status"] == "completed"


async def test_insert_requires_anchor(session_factory, user_id: str) -> None:
    """Either investigation_id or session_id must be set (DB CHECK)."""
    async with session_factory() as db:
        with pytest.raises(ValueError, match="at least one"):
            await insert_execution(
                db, code="x", stdout="", stderr="", exit_code=0,
                duration_ms=0, status="completed", created_by=user_id,
            )


async def test_get_execution_owner_scoped(session_factory, user_id: str) -> None:
    sid = f"sess-{uuid.uuid4().hex[:8]}"
    other = f"u-{uuid.uuid4().hex[:8]}"
    async with session_factory() as db:
        eid = await insert_execution(
            db, code="x", stdout="", stderr="", exit_code=0,
            duration_ms=0, status="completed", created_by=user_id,
            session_id=sid,
        )
    async with session_factory() as db:
        mine = await get_execution(db, eid, user_id)
        theirs = await get_execution(db, eid, other)
    assert mine is not None
    assert theirs is None


async def test_insert_rejects_unknown_status(
    session_factory, user_id: str,
) -> None:
    sid = f"sess-{uuid.uuid4().hex[:8]}"
    async with session_factory() as db:
        with pytest.raises(ValueError, match="status must be one of"):
            await insert_execution(
                db, code="x", stdout="", stderr="", exit_code=0,
                duration_ms=0, status="bogus", created_by=user_id,
                session_id=sid,
            )


# ── P1 review-finding: _strip_artifact_payload error paths ───────────────────


def test_strip_artifact_payload_handles_str_json() -> None:
    """JSONB columns can come back as a JSON-encoded string depending on
    the driver. The helper must parse and strip b64."""
    from api.db.queries.code_executions import _strip_artifact_payload
    raw = '[{"filename": "p.png", "mime": "image/png", "size_bytes": 8, "b64": "iVBORw=="}]'
    out = _strip_artifact_payload(raw)
    assert len(out) == 1
    assert out[0]["filename"] == "p.png"
    assert "b64" not in out[0]


def test_strip_artifact_payload_handles_unparseable_string(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Malformed JSON returns [] and logs a warning. Pre-§M-cleanup
    this branch was silent."""
    import logging
    from api.db.queries.code_executions import _strip_artifact_payload
    with caplog.at_level(logging.WARNING):
        out = _strip_artifact_payload("not valid json {")
    assert out == []
    assert any("stringified but not JSON-parseable" in m for m in caplog.messages)


def test_strip_artifact_payload_handles_non_list(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A dict or scalar instead of a list — also fails closed with a warn."""
    import logging
    from api.db.queries.code_executions import _strip_artifact_payload
    with caplog.at_level(logging.WARNING):
        out = _strip_artifact_payload({"not": "a list"})
    assert out == []
    assert any("unexpected type" in m for m in caplog.messages)


def test_strip_artifact_payload_drops_b64_only() -> None:
    """Already-parsed list: drop b64 from each entry, keep all other
    metadata. Multiple artefacts handled."""
    from api.db.queries.code_executions import _strip_artifact_payload
    parsed = [
        {"filename": "a.png", "mime": "image/png", "size_bytes": 100, "b64": "AAA="},
        {"filename": "b.png", "mime": "image/png", "size_bytes": 200, "b64": "BBB="},
    ]
    out = _strip_artifact_payload(parsed)
    assert len(out) == 2
    for entry in out:
        assert "b64" not in entry
        assert "filename" in entry
        assert "size_bytes" in entry


def test_strip_artifact_payload_skips_non_dict_entries() -> None:
    """A list that contains non-dict entries (None, strings, numbers)
    is allowed but the bad entries are quietly dropped — they couldn't
    be artefacts in any case."""
    from api.db.queries.code_executions import _strip_artifact_payload
    out = _strip_artifact_payload([
        {"filename": "good.png", "b64": "x"},
        "not a dict",
        None,
        42,
    ])
    assert len(out) == 1
    assert out[0]["filename"] == "good.png"


def test_strip_artifact_payload_handles_none() -> None:
    """`None` (column default is `'[]'::jsonb` so this shouldn't happen,
    but defend anyway)."""
    from api.db.queries.code_executions import _strip_artifact_payload
    assert _strip_artifact_payload(None) == []


# ── P1 review-finding: cross-investigation ownership rejection ──────────────


async def test_insert_execution_rejects_unowned_investigation(
    session_factory, user_id: str,
) -> None:
    """The EXISTS-gated INSERT must raise ValueError when the
    investigation belongs to a different user. The tool layer relies
    on this so a future caller that forgets the tool-layer owner check
    can't attach an execution to a stranger's investigation."""
    from api.db.queries.investigations import create_investigation
    from api.db.queries.code_executions import insert_execution

    other_user = f"u-{uuid.uuid4().hex[:8]}"
    async with session_factory() as db:
        # Other user owns the investigation.
        iid = await create_investigation(
            db, "Their thread", "Their objective", other_user,
        )

    async with session_factory() as db:
        with pytest.raises(ValueError, match="not found or not owned"):
            await insert_execution(
                db,
                code="print('attempt')",
                stdout="",
                stderr="",
                exit_code=0,
                duration_ms=0,
                status="completed",
                created_by=user_id,            # caller is NOT the owner
                investigation_id=iid,
            )

    # Sanity: the legitimate owner CAN insert against the same id.
    async with session_factory() as db:
        eid = await insert_execution(
            db,
            code="print('ok')",
            stdout="",
            stderr="",
            exit_code=0,
            duration_ms=0,
            status="completed",
            created_by=other_user,
            investigation_id=iid,
        )
    assert eid is not None


async def test_insert_execution_rejects_nonexistent_investigation(
    session_factory, user_id: str,
) -> None:
    """An investigation_id that doesn't exist at all also raises."""
    from api.db.queries.code_executions import insert_execution
    bogus = "00000000-0000-0000-0000-000000000000"
    async with session_factory() as db:
        with pytest.raises(ValueError, match="not found or not owned"):
            await insert_execution(
                db,
                code="x",
                stdout="",
                stderr="",
                exit_code=0,
                duration_ms=0,
                status="completed",
                created_by=user_id,
                investigation_id=bogus,
            )
