"""HTTP-layer tests for /api/notifications — auth + validation.

Pydantic-validation and auth-only tests; success paths (which touch
the DB through the route) live in `test_session_store.py` at the
query layer where teardown is clean (per the asyncpg event-loop
teardown caveat in `test_routes_v2.py`).
"""
from __future__ import annotations


# ── GET /api/notifications ───────────────────────────────────────────────────


def test_get_notifications_requires_auth(client):
    resp = client.get("/api/notifications")
    assert resp.status_code == 401


def test_get_notifications_limit_out_of_range_rejected(client, auth_header):
    """limit is Query(ge=1, le=100) — FastAPI 422s on out-of-range
    before reaching the route body."""
    resp = client.get("/api/notifications?limit=500", headers=auth_header)
    assert resp.status_code == 422


def test_get_notifications_limit_zero_rejected(client, auth_header):
    resp = client.get("/api/notifications?limit=0", headers=auth_header)
    assert resp.status_code == 422


def test_get_notifications_unread_only_non_bool_rejected(client, auth_header):
    """`unread_only` is bool-typed; an explicitly non-bool string is 422."""
    resp = client.get(
        "/api/notifications?unread_only=not-a-bool", headers=auth_header,
    )
    assert resp.status_code == 422


# ── PATCH /api/notifications ─────────────────────────────────────────────────


def test_patch_notifications_requires_auth(client):
    resp = client.patch("/api/notifications", json={"all": True})
    assert resp.status_code == 401


def test_patch_notifications_empty_body_is_noop(client, auth_header):
    """No ids + all=False → marked_read=0 without touching the DB.

    This is the only route-path test in this file that returns 200 from
    the route body — but it's safe because the route's early return
    skips the DB call when both ids and all are empty/None."""
    resp = client.patch(
        "/api/notifications",
        headers=auth_header,
        json={},
    )
    assert resp.status_code == 200
    assert resp.json() == {"marked_read": 0}


def test_patch_notifications_ids_wrong_type_rejected(client, auth_header):
    """`ids` is list[str] | None — a scalar string is 422."""
    resp = client.patch(
        "/api/notifications",
        headers=auth_header,
        json={"ids": "not-a-list"},
    )
    assert resp.status_code == 422
