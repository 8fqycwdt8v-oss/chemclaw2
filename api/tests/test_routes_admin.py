"""Route-layer tests for `/api/admin/*`.

Covers the auth-gate pattern documented in `api/routes/admin.py`: the
admin dependency runs before the rate-limit dependency, so non-admins
must see 403 and never 429. Also exercises the tool-permissions CRUD
cycle + admin health + the eval-runs reads.
"""
from __future__ import annotations

import uuid


def test_tool_permissions_get_rejects_non_admin(client, auth_header):
    r = client.get("/api/admin/tool-permissions", headers=auth_header)
    assert r.status_code == 403


def test_tool_permissions_get_rejects_anonymous(client):
    r = client.get("/api/admin/tool-permissions")
    assert r.status_code == 401


def test_tool_permissions_post_rejects_non_admin(client, auth_header):
    r = client.post(
        "/api/admin/tool-permissions",
        headers=auth_header,
        json={"scope": "user", "scope_id": "user_test", "tool_name": "web_search", "mode": "allow"},
    )
    assert r.status_code == 403


def test_eval_runs_rejects_non_admin(client, auth_header):
    r = client.get("/api/admin/eval", headers=auth_header)
    assert r.status_code == 403


def test_admin_health_rejects_non_admin(client, auth_header):
    r = client.get("/api/admin/health", headers=auth_header)
    assert r.status_code == 403


def test_admin_health_ok(client, admin_header):
    r = client.get("/api/admin/health", headers=admin_header)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    # Each downstream may return -1 on error; the contract is that the
    # key is present so dashboards don't KeyError.
    for key in ("running_campaigns", "pending_campaign_steps", "wiki_needs_review"):
        assert key in body
        assert isinstance(body[key], int)


def test_tool_permissions_crud_cycle(client, admin_header):
    """End-to-end CRUD: list (empty subset) → create → list (contains) → delete → 404 on re-delete.

    `scope_id` must match the `tool_permissions_scope_shape_chk` constraint
    (migration 0029): scope='user' → scope_id matches `^user_[A-Za-z0-9]+$`.
    """
    scope_id = f"user_test{uuid.uuid4().hex[:12]}"
    create = client.post(
        "/api/admin/tool-permissions",
        headers=admin_header,
        json={"scope": "user", "scope_id": scope_id, "tool_name": "web_search", "mode": "allow"},
    )
    assert create.status_code == 200, create.text
    permission_id = create.json()["id"]
    assert permission_id

    listing = client.get(
        "/api/admin/tool-permissions",
        headers=admin_header,
        params={"scope": "user", "scope_id": scope_id},
    )
    assert listing.status_code == 200
    # The list endpoint serialises uuid columns as their JSON repr (string).
    matches = [p for p in listing.json()["permissions"] if str(p["id"]) == permission_id]
    assert len(matches) == 1
    assert matches[0]["mode"] == "allow"
    assert matches[0]["tool_name"] == "web_search"

    deleted = client.delete(
        f"/api/admin/tool-permissions/{permission_id}",
        headers=admin_header,
    )
    assert deleted.status_code == 200
    assert deleted.json()["ok"] is True

    # Second delete: the row is gone, so the route returns 404.
    again = client.delete(
        f"/api/admin/tool-permissions/{permission_id}",
        headers=admin_header,
    )
    assert again.status_code == 404


def test_tool_permission_rejects_invalid_mode(client, admin_header):
    """Pydantic rejects unknown enum members before the query layer runs."""
    r = client.post(
        "/api/admin/tool-permissions",
        headers=admin_header,
        json={"scope": "user", "scope_id": "u-x", "tool_name": "web_search", "mode": "yolo"},
    )
    assert r.status_code == 422


def test_eval_runs_list_ok(client, admin_header):
    r = client.get("/api/admin/eval", headers=admin_header)
    assert r.status_code == 200
    assert "runs" in r.json()


def test_eval_run_404(client, admin_header):
    r = client.get(f"/api/admin/eval/{uuid.uuid4()}", headers=admin_header)
    assert r.status_code == 404
