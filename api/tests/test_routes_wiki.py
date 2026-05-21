"""HTTP-layer tests for wiki routes — validation + auth paths.

Same pattern as test_routes_v2.py: sync TestClient verifies the routes
are wired and their Pydantic / slug / param validation works. Seeded-
data E2E tests are out of scope due to the asyncpg event-loop teardown
issue documented in test_routes_v2.py — the query-level coverage in
test_wiki_queries.py + test_audit_wiki.py already exercises the
seeded paths.
"""
from __future__ import annotations


# ── POST /api/wiki — create page ─────────────────────────────────────────────


def test_create_wiki_invalid_slug_rejected(client, auth_header):
    """Slug must be lowercase letters/numbers/hyphens. Anything else → 400
    from the explicit `_SLUG_RE.match` check before the DB call."""
    resp = client.post(
        "/api/wiki",
        headers=auth_header,
        json={"slug": "Has_Underscores", "title": "x"},
    )
    assert resp.status_code == 400


def test_create_wiki_missing_required_field_rejected(client, auth_header):
    """slug + title are both required by Pydantic — missing one is 422."""
    resp = client.post("/api/wiki", headers=auth_header, json={"slug": "x"})
    assert resp.status_code == 422


def test_create_wiki_requires_auth(client):
    """No Authorization → 401 from get_current_user dep."""
    resp = client.post("/api/wiki", json={"slug": "x", "title": "y"})
    assert resp.status_code == 401


# ── GET /api/wiki — list ─────────────────────────────────────────────────────


def test_list_wiki_invalid_as_of_rejected_on_get_by_slug(client, auth_header):
    """The as_of timestamp on GET /api/wiki/{slug} must be ISO-8601."""
    resp = client.get(
        "/api/wiki/some-slug?as_of=not-a-timestamp",
        headers=auth_header,
    )
    # The bi-temporal route 400s on invalid timestamp BEFORE the page lookup.
    assert resp.status_code == 400


# ── GET /api/wiki/{slug}/revisions/{version} ─────────────────────────────────


def test_get_wiki_revision_404_on_unknown_slug(client, auth_header):
    """Looking up revisions for a page that doesn't exist is 404, not 500.
    Exercises the get_wiki_page(include_archived=True) check before
    reaching the revisions table."""
    resp = client.get(
        "/api/wiki/this-slug-definitely-does-not-exist-12345/revisions",
        headers=auth_header,
    )
    assert resp.status_code == 404


# ── POST /api/wiki/{slug}/contradictions ─────────────────────────────────────


def test_post_contradiction_invalid_winner_rejected(client, auth_header):
    """proposed_winner is Literal['a','b','inconclusive'] — Pydantic 422s."""
    resp = client.post(
        "/api/wiki/some-slug/contradictions",
        headers=auth_header,
        json={
            "citation_a": "cite-1",
            "citation_b": "cite-2",
            "proposed_winner": "neither",  # not in the Literal set
            "reason": "test",
        },
    )
    assert resp.status_code == 422


def test_post_contradiction_requires_auth(client):
    resp = client.post(
        "/api/wiki/some-slug/contradictions",
        json={
            "citation_a": "a", "citation_b": "b",
            "proposed_winner": "a", "reason": "x",
        },
    )
    assert resp.status_code == 401
