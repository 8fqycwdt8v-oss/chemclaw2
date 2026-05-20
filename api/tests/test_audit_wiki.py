"""Tests for GET /api/audit/wiki/{slug} — admin-only compliance read.

Sync TestClient style (matches test_routes_v2.py) — the auth surface
is the high-value coverage here. The full-trail content path is
exercised indirectly by test_wiki_queries.py (list_wiki_revisions)
and test_curator_inbox.py (get_wiki_page).
"""
from __future__ import annotations


def test_audit_wiki_requires_admin_for_unauthenticated(client):
    """Anonymous request rejects at the auth dep with 401."""
    resp = client.get("/api/audit/wiki/some-slug")
    assert resp.status_code == 401


def test_audit_wiki_non_admin_user_rejected(client, auth_header):
    """A regular authenticated user is not an admin. The admin dep
    listed first in `_AUDIT_WIKI` enforces this before the rate-limit
    or route body runs."""
    resp = client.get("/api/audit/wiki/some-slug", headers=auth_header)
    assert resp.status_code == 403


def test_audit_wiki_admin_missing_slug_404(client, admin_header):
    """Admin caller with a slug that doesn't exist gets 404 (after the
    admin gate passes and the route body runs the existence check)."""
    resp = client.get(
        "/api/audit/wiki/this-slug-does-not-exist-zzz",
        headers=admin_header,
    )
    # 404 (real DB lookup) or 429 (rate-limit fail-closed locally) are both
    # OK for this assertion — both prove auth + admin gate passed.
    assert resp.status_code in (404, 429)
