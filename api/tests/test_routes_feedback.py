"""HTTP-layer tests for the feedback routes (POST/GET /api/feedback).

Covers auth gating, Pydantic body validation (score ∈ {1,-1}, turn_index ≥ 0),
the post→read round trip, and owner-scoping of the read.
"""
from __future__ import annotations

import uuid

from fastapi.testclient import TestClient


def test_post_feedback_requires_auth(client: TestClient) -> None:
    resp = client.post(
        "/api/feedback",
        json={"session_id": "s1", "turn_index": 0, "score": 1},
    )
    assert resp.status_code == 401


def test_post_feedback_rejects_invalid_score(client: TestClient, auth_header) -> None:
    resp = client.post(
        "/api/feedback",
        json={"session_id": "s1", "turn_index": 0, "score": 2},
        headers=auth_header,
    )
    assert resp.status_code == 422


def test_post_feedback_rejects_negative_turn(client: TestClient, auth_header) -> None:
    resp = client.post(
        "/api/feedback",
        json={"session_id": "s1", "turn_index": -1, "score": 1},
        headers=auth_header,
    )
    assert resp.status_code == 422


def test_post_then_get_feedback_round_trip(client: TestClient, auth_header) -> None:
    session_id = f"sess-{uuid.uuid4().hex[:8]}"
    post = client.post(
        "/api/feedback",
        json={"session_id": session_id, "turn_index": 0, "score": 1, "reason": "great"},
        headers=auth_header,
    )
    assert post.status_code == 200
    assert "id" in post.json()

    got = client.get(f"/api/feedback/{session_id}", headers=auth_header)
    assert got.status_code == 200
    fb = got.json()["feedback"]
    assert len(fb) == 1
    assert fb[0]["score"] == 1
    assert fb[0]["reason"] == "great"


def test_get_feedback_owner_scoped(client: TestClient, auth_header) -> None:
    session_id = f"sess-{uuid.uuid4().hex[:8]}"
    client.post(
        "/api/feedback",
        json={"session_id": session_id, "turn_index": 0, "score": -1},
        headers=auth_header,
    )
    # A different user must not see another user's feedback for the same session id.
    other = {"Authorization": f"Bearer mock:u-{uuid.uuid4().hex[:8]}"}
    got = client.get(f"/api/feedback/{session_id}", headers=other)
    assert got.status_code == 200
    assert got.json()["feedback"] == []
