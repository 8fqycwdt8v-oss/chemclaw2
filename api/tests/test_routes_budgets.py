"""HTTP-layer tests for /api/budgets/* — auth, validation, ownership.

Same convention as `test_routes_v2.py`: sync TestClient verifies the
routes are wired and their auth + Pydantic validation + ownership
gate work. Seeded-data / success-path tests are out of scope due to
the asyncpg event-loop teardown issue documented in
`test_routes_v2.py` — query-level budget logic is covered in
`test_budgets.py` at the AsyncSession layer where teardown is clean.

The per-route ownership gate (`api/routes/budgets.py::_check_ownership`)
requires `project_key == "chemclaw2:{user_id}"`. The route raises 403
BEFORE touching the DB, so cross-owner rejection tests are safe to
include here.
"""
from __future__ import annotations


# ── GET /api/budgets/{project_key} ────────────────────────────────────────────


def test_get_budget_requires_auth(client):
    resp = client.get("/api/budgets/chemclaw2:some-user")
    assert resp.status_code == 401


def test_get_budget_rejects_other_users_project(client, auth_header):
    """A user may only access `chemclaw2:{own-user-id}` — accessing
    another user's project_key returns 403. Pins the
    `_check_ownership` predicate. The check fires before any DB I/O."""
    resp = client.get(
        "/api/budgets/chemclaw2:not-this-user",
        headers=auth_header,
    )
    assert resp.status_code == 403


# ── PUT /api/budgets/{project_key} ────────────────────────────────────────────


def test_put_budget_requires_auth(client):
    resp = client.put(
        "/api/budgets/chemclaw2:x",
        json={"period": "day"},
    )
    assert resp.status_code == 401


def test_put_budget_invalid_period_rejected(client, auth_header, user_id):
    """period is Literal['day','week','month'] — Pydantic 422s before
    the route body runs (no DB)."""
    resp = client.put(
        f"/api/budgets/chemclaw2:{user_id}",
        headers=auth_header,
        json={"period": "century"},
    )
    assert resp.status_code == 422


def test_put_budget_negative_cap_rejected(client, auth_header, user_id):
    """Caps must be ge=0 — Pydantic enforces it before the route body."""
    resp = client.put(
        f"/api/budgets/chemclaw2:{user_id}",
        headers=auth_header,
        json={"period": "day", "tool_calls_cap": -5},
    )
    assert resp.status_code == 422


def test_put_budget_rejects_other_users_project(client, auth_header):
    """Same ownership gate on writes — can't create a budget for
    someone else's project_key. _check_ownership 403s pre-DB."""
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
    """_check_ownership 403s pre-DB."""
    resp = client.delete(
        "/api/budgets/chemclaw2:not-this-user",
        headers=auth_header,
    )
    assert resp.status_code == 403
