"""HTTP-layer tests for the campaign read/cancel routes.

`test_routes_v2.py` already covers the step-approval routes. This file
fills in the GET/PATCH surface that wasn't yet under HTTP-level test.
Same convention as the rest of `api/tests/`: validation + auth, no
seeded-data E2E (see test_routes_v2.py docstring on the asyncpg
event-loop teardown issue).
"""
from __future__ import annotations

import uuid

# ── GET /api/campaigns — list ────────────────────────────────────────────────


def test_list_campaigns_requires_auth(client):
    resp = client.get("/api/campaigns")
    assert resp.status_code == 401


def test_list_campaigns_invalid_cursor_rejected(client, auth_header):
    """Cursor is `<iso_timestamp>_<uuid>`; anything else → 400."""
    resp = client.get("/api/campaigns?cursor=not-a-valid-cursor", headers=auth_header)
    assert resp.status_code == 400


def test_list_campaigns_cursor_with_bad_uuid_rejected(client, auth_header):
    """The cursor's UUID half is validated separately from the timestamp."""
    resp = client.get(
        "/api/campaigns?cursor=2026-01-01T00:00:00_not-a-uuid",
        headers=auth_header,
    )
    assert resp.status_code == 400


# ── GET /api/campaigns/{id} ──────────────────────────────────────────────────


def test_get_campaign_requires_auth(client):
    fake_uuid = str(uuid.uuid4())
    resp = client.get(f"/api/campaigns/{fake_uuid}")
    assert resp.status_code == 401


def test_get_campaign_unknown_id_is_404(client, auth_header):
    """A well-formed UUID for a campaign that doesn't exist (or isn't owned
    by the caller) → 404. Exercises the owner-scope predicate in
    get_campaign_with_steps."""
    fake_uuid = str(uuid.uuid4())
    resp = client.get(f"/api/campaigns/{fake_uuid}", headers=auth_header)
    assert resp.status_code == 404


# ── PATCH /api/campaigns/{id} — cancel ───────────────────────────────────────


def test_patch_campaign_requires_auth(client):
    fake_uuid = str(uuid.uuid4())
    resp = client.patch(f"/api/campaigns/{fake_uuid}", json={"status": "failed"})
    assert resp.status_code == 401


def test_patch_campaign_invalid_status_rejected(client, auth_header):
    """status is Literal['failed'] — only 'failed' (cancel) is allowed via PATCH."""
    fake_uuid = str(uuid.uuid4())
    resp = client.patch(
        f"/api/campaigns/{fake_uuid}",
        headers=auth_header,
        json={"status": "running"},
    )
    assert resp.status_code == 422


def test_patch_campaign_unknown_id_is_404(client, auth_header):
    fake_uuid = str(uuid.uuid4())
    resp = client.patch(
        f"/api/campaigns/{fake_uuid}",
        headers=auth_header,
        json={"status": "failed"},
    )
    assert resp.status_code == 404
