"""HTTP-layer tests for /api/todos/{session_id} — auth + validation.

Query-level logic (owner scoping of `list_todos`, `upsert_todos`,
`mark_todo_done`) is covered by `test_session_store.py` and direct
query tests; this file pins the route validation surface.
"""
from __future__ import annotations

import uuid


# ── GET /api/todos/{session_id} ───────────────────────────────────────────────


def test_get_todos_requires_auth(client):
    resp = client.get("/api/todos/some-session")
    assert resp.status_code == 401


def test_get_todos_returns_empty_envelope_for_unknown_session(
    client, auth_header,
):
    """Unknown session_id (or one not owned by the caller) returns an
    empty list — not 404 — because `list_todos` is owner-scoped and
    returns nothing rather than raising on missing rows."""
    resp = client.get(
        f"/api/todos/sess-{uuid.uuid4().hex[:8]}",
        headers=auth_header,
    )
    assert resp.status_code == 200
    assert resp.json() == {"todos": []}


# ── PUT /api/todos/{session_id} ───────────────────────────────────────────────


def test_put_todos_requires_auth(client):
    resp = client.put(
        "/api/todos/some-session",
        json={"todos": []},
    )
    assert resp.status_code == 401


def test_put_todos_empty_list_is_valid(client, auth_header):
    """Replacing with an empty list is a valid 'clear all' operation."""
    resp = client.put(
        f"/api/todos/sess-{uuid.uuid4().hex[:8]}",
        headers=auth_header,
        json={"todos": []},
    )
    assert resp.status_code == 200


def test_put_todos_empty_text_rejected(client, auth_header):
    """text must be min_length=1 — Pydantic 422s."""
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


# ── PATCH /api/todos/{session_id}/{todo_id} ───────────────────────────────────


def test_patch_todo_requires_auth(client):
    resp = client.patch(
        f"/api/todos/sess-x/{uuid.uuid4()}",
        json={"status": "done"},
    )
    assert resp.status_code == 401


def test_patch_todo_invalid_status_rejected(client, auth_header):
    """The patch route only accepts status='done' (mark complete is
    the only mutation allowed via PATCH)."""
    resp = client.patch(
        f"/api/todos/sess-x/{uuid.uuid4()}",
        headers=auth_header,
        json={"status": "pending"},
    )
    assert resp.status_code == 422


def test_patch_todo_unknown_id_returns_404(client, auth_header):
    """Owner-scoped mark_todo_done returns False for unknown / stranger-
    owned ids → route raises 404."""
    resp = client.patch(
        f"/api/todos/sess-x/{uuid.uuid4()}",
        headers=auth_header,
        json={"status": "done"},
    )
    assert resp.status_code == 404
