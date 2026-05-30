"""HTTP-layer tests for the audit / compliance routes.

Covers the admin gate (overrides + redactions + wiki are admin-only, and the
admin check runs before the rate limit so a non-admin gets 403 not 429), the
user-scoped session-replay endpoint, and the wiki audit-trail happy path +
404.
"""
from __future__ import annotations

import uuid

from fastapi.testclient import TestClient


def test_audit_overrides_requires_auth(client: TestClient) -> None:
    assert client.get("/api/audit/overrides").status_code == 401


def test_audit_overrides_forbidden_for_non_admin(client: TestClient, auth_header) -> None:
    assert client.get("/api/audit/overrides", headers=auth_header).status_code == 403


def test_audit_overrides_admin_ok(client: TestClient, admin_header) -> None:
    resp = client.get("/api/audit/overrides", headers=admin_header)
    assert resp.status_code == 200
    assert "overrides" in resp.json()


def test_audit_redactions_admin_ok(client: TestClient, admin_header) -> None:
    resp = client.get("/api/audit/redactions", headers=admin_header)
    assert resp.status_code == 200
    assert "redactions" in resp.json()


def test_audit_session_replay_unknown_session_is_empty(client: TestClient, auth_header) -> None:
    sid = f"sess-{uuid.uuid4().hex[:8]}"
    resp = client.get(f"/api/audit/sessions/{sid}", headers=auth_header)
    assert resp.status_code == 200
    assert resp.json()["entries"] == []


def test_audit_wiki_missing_page_404(client: TestClient, admin_header) -> None:
    resp = client.get(f"/api/audit/wiki/nope-{uuid.uuid4().hex[:8]}", headers=admin_header)
    assert resp.status_code == 404


def test_audit_wiki_requires_admin(client: TestClient, auth_header, wiki_page) -> None:
    resp = client.get(f"/api/audit/wiki/{wiki_page['slug']}", headers=auth_header)
    assert resp.status_code == 403


def test_audit_wiki_admin_returns_trail(client: TestClient, admin_header, wiki_page) -> None:
    resp = client.get(f"/api/audit/wiki/{wiki_page['slug']}", headers=admin_header)
    assert resp.status_code == 200
    body = resp.json()
    assert body["page"]["slug"] == wiki_page["slug"]
    assert body["page"]["version"] == wiki_page["version"]
    assert body["revision_count"] == len(body["revisions"])
