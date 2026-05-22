"""HTTP-layer tests for /api/notifications — auth + validation.

Query-level mark-read / count-unread logic is covered by
`test_session_store.py` and direct query tests; this file pins the
route surface. The `/notifications/stream` SSE endpoint is intentionally
not exercised here — its loop sleeps 30s by default and TestClient's
sync wrapper would block.
"""
from __future__ import annotations


# ── GET /api/notifications ───────────────────────────────────────────────────


def test_get_notifications_requires_auth(client):
    resp = client.get("/api/notifications")
    assert resp.status_code == 401


def test_get_notifications_default_unread_only(client, auth_header):
    """Owner-scoped read — caller with no notifications returns empty
    list + zero unread count, not 404."""
    resp = client.get("/api/notifications", headers=auth_header)
    assert resp.status_code == 200
    body = resp.json()
    assert body["notifications"] == []
    assert body["unread_count"] == 0


def test_get_notifications_limit_out_of_range_rejected(client, auth_header):
    """limit is Query(ge=1, le=100) — FastAPI 422s on out-of-range."""
    resp = client.get("/api/notifications?limit=500", headers=auth_header)
    assert resp.status_code == 422


def test_get_notifications_limit_zero_rejected(client, auth_header):
    resp = client.get("/api/notifications?limit=0", headers=auth_header)
    assert resp.status_code == 422


# ── PATCH /api/notifications ─────────────────────────────────────────────────


def test_patch_notifications_requires_auth(client):
    resp = client.patch("/api/notifications", json={"all": True})
    assert resp.status_code == 401


def test_patch_notifications_empty_body_is_noop(client, auth_header):
    """No ids + all=False → marked_read=0 without touching the DB."""
    resp = client.patch(
        "/api/notifications",
        headers=auth_header,
        json={},
    )
    assert resp.status_code == 200
    assert resp.json() == {"marked_read": 0}


def test_patch_notifications_mark_all_owner_scoped(client, auth_header):
    """all=True for a user with no notifications → marked_read=0 (no
    cross-user mark; mark_all_read is owner-scoped)."""
    resp = client.patch(
        "/api/notifications",
        headers=auth_header,
        json={"all": True},
    )
    assert resp.status_code == 200
    assert resp.json() == {"marked_read": 0}


def test_patch_notifications_unknown_ids_is_noop(client, auth_header):
    """Marking ids that don't belong to the caller → marked_read=0
    (owner-scoped UPDATE; no exception)."""
    resp = client.patch(
        "/api/notifications",
        headers=auth_header,
        json={"ids": ["nonexistent-1", "nonexistent-2"]},
    )
    assert resp.status_code == 200
    assert resp.json() == {"marked_read": 0}
