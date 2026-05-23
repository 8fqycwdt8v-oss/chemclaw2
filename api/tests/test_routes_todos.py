"""HTTP-layer tests for /api/todos/{session_id} — auth + validation.

Pydantic-validation and auth-only tests; success paths (which touch
the DB through the route and trip the asyncpg event-loop teardown
issue documented in `test_routes_v2.py`) live in
`test_session_store.py` at the query layer.
"""
from __future__ import annotations

import uuid

# ── GET /api/todos/{session_id} ───────────────────────────────────────────────


def test_get_todos_requires_auth(client):
    resp = client.get("/api/todos/some-session")
    assert resp.status_code == 401


# ── PUT /api/todos/{session_id} ───────────────────────────────────────────────


def test_put_todos_requires_auth(client):
    resp = client.put(
        "/api/todos/some-session",
        json={"todos": []},
    )
    assert resp.status_code == 401


def test_put_todos_empty_text_rejected(client, auth_header):
    """text must be min_length=1 — Pydantic 422s before the route body
    runs (no DB)."""
    resp = client.put(
        f"/api/todos/sess-{uuid.uuid4().hex[:8]}",
        headers=auth_header,
        json={
            "todos": [{"text": "", "status": "pending", "position": 0}],
        },
    )
    assert resp.status_code == 422


def test_put_todos_invalid_status_rejected(client, auth_header):
    """status is Literal['pending','done'] — Pydantic 422s."""
    resp = client.put(
        f"/api/todos/sess-{uuid.uuid4().hex[:8]}",
        headers=auth_header,
        json={
            "todos": [{"text": "x", "status": "in_progress", "position": 0}],
        },
    )
    assert resp.status_code == 422


def test_put_todos_negative_position_rejected(client, auth_header):
    """position must be ge=0 — Pydantic 422s."""
    resp = client.put(
        f"/api/todos/sess-{uuid.uuid4().hex[:8]}",
        headers=auth_header,
        json={
            "todos": [{"text": "x", "status": "pending", "position": -1}],
        },
    )
    assert resp.status_code == 422


def test_put_todos_missing_todos_field_rejected(client, auth_header):
    """`todos` is required on TodosPutBody — missing it is 422."""
    resp = client.put(
        f"/api/todos/sess-{uuid.uuid4().hex[:8]}",
        headers=auth_header,
        json={},
    )
    assert resp.status_code == 422


# ── PATCH /api/todos/{session_id}/{todo_id} ───────────────────────────────────


def test_patch_todo_requires_auth(client):
    resp = client.patch(
        f"/api/todos/sess-x/{uuid.uuid4()}",
        json={"status": "done"},
    )
    assert resp.status_code == 401


def test_patch_todo_invalid_status_rejected(client, auth_header):
    """The patch route only accepts status='done' — Pydantic 422s on
    anything else (the validator fires before the route body)."""
    resp = client.patch(
        f"/api/todos/sess-x/{uuid.uuid4()}",
        headers=auth_header,
        json={"status": "pending"},
    )
    assert resp.status_code == 422


def test_patch_todo_missing_status_field_rejected(client, auth_header):
    """`status` is required on TodoPatchBody — missing it is 422."""
    resp = client.patch(
        f"/api/todos/sess-x/{uuid.uuid4()}",
        headers=auth_header,
        json={},
    )
    assert resp.status_code == 422
