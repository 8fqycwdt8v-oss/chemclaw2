"""HTTP-layer test for GET /api/curator/inbox.

Covers auth gating and the aggregated response shape. Owner-scoped buckets
(wiki_needs_review, step_approvals) are empty for a fresh user; the
collaborative contradictions bucket may be non-empty from other tests, so we
assert total_pending stays consistent with the three buckets rather than
pinning an absolute count.
"""
from __future__ import annotations

from fastapi.testclient import TestClient


def test_curator_inbox_requires_auth(client: TestClient) -> None:
    assert client.get("/api/curator/inbox").status_code == 401


def test_curator_inbox_shape_for_fresh_user(client: TestClient, auth_header) -> None:
    resp = client.get("/api/curator/inbox", headers=auth_header)
    assert resp.status_code == 200
    body = resp.json()
    assert {"wiki_needs_review", "step_approvals", "contradictions", "total_pending"} <= set(body)
    # Owner-scoped buckets are empty for a brand-new user id.
    assert body["wiki_needs_review"] == []
    assert body["step_approvals"] == []
    assert body["total_pending"] == (
        len(body["wiki_needs_review"])
        + len(body["step_approvals"])
        + len(body["contradictions"])
    )
