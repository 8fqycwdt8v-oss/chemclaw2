"""HTTP-layer tests for /api/budgets/* — auth, validation, ownership.

Same convention as `test_routes_v2.py`: sync TestClient verifies the
routes are wired and their auth + Pydantic validation + ownership
gate work. Query-level budget logic is covered in `test_budgets.py`.

The ownership check in `api/routes/budgets.py::_check_ownership`
requires `project_key == "chemclaw2:{user_id}"`. Any other project_key
returns 403 — that's the per-route owner-scope predicate this file
pins.
"""
from __future__ import annotations


# ── GET /api/budgets/{project_key} ────────────────────────────────────────────


def test_get_budget_requires_auth(client):
    resp = client.get("/api/budgets/chemclaw2:some-user")
    assert resp.status_code == 401


def test_get_budget_rejects_other_users_project(client, auth_header, user_id):
    """A user with id `user_id` may only access `chemclaw2:{user_id}` —
    accessing another user's project_key returns 403. Pins the
    `_check_ownership` predicate."""
    resp = client.get(
        "/api/budgets/chemclaw2:not-this-user",
        headers=auth_header,
    )
    assert resp.status_code == 403


def test_get_budget_owner_returns_empty_envelope_when_absent(
    client, auth_header, user_id,
):
    """The caller's own project_key is allowed; an unconfigured budget
    surfaces {budget: None, spend: None} not 404 (drives UI placeholder
    state)."""
    resp = client.get(
        f"/api/budgets/chemclaw2:{user_id}",
        headers=auth_header,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["budget"] is None
    assert body["spend"] is None


# ── PUT /api/budgets/{project_key} ────────────────────────────────────────────


def test_put_budget_requires_auth(client):
    resp = client.put(
        "/api/budgets/chemclaw2:x",
        json={"period": "day"},
    )
    assert resp.status_code == 401


def test_put_budget_invalid_period_rejected(client, auth_header, user_id):
    """period is Literal['day','week','month'] — Pydantic 422s."""
    resp = client.put(
        f"/api/budgets/chemclaw2:{user_id}",
        headers=auth_header,
        json={"period": "century"},
    )
    assert resp.status_code == 422


def test_put_budget_negative_cap_rejected(client, auth_header, user_id):
    """Caps must be ge=0 — Pydantic enforces it."""
    resp = client.put(
        f"/api/budgets/chemclaw2:{user_id}",
        headers=auth_header,
        json={"period": "day", "tool_calls_cap": -5},
    )
    assert resp.status_code == 422


def test_put_budget_rejects_other_users_project(client, auth_header, user_id):
    """Same ownership gate on writes — can't create a budget for
    someone else's project_key."""
    resp = client.put(
        "/api/budgets/chemclaw2:not-this-user",
        headers=auth_header,
        json={"period": "day", "tool_calls_cap": 100},
    )
    assert resp.status_code == 403


# ── DELETE /api/budgets/{project_key} ─────────────────────────────────────────


def test_delete_budget_requires_auth(client):
    resp = client.delete("/api/budgets/chemclaw2:x")
    assert resp.status_code == 401


def test_delete_budget_rejects_other_users_project(client, auth_header):
    resp = client.delete(
        "/api/budgets/chemclaw2:not-this-user",
        headers=auth_header,
    )
    assert resp.status_code == 403


def test_delete_budget_returns_404_when_absent(client, auth_header, user_id):
    """Owner with no configured budget → 404 (delete_project_budget
    returns False, route raises 404)."""
    resp = client.delete(
        f"/api/budgets/chemclaw2:{user_id}",
        headers=auth_header,
    )
    assert resp.status_code == 404
