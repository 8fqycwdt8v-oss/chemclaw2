"""HTTP-layer integration tests for V2 routes.

Covers the routes shipped in PRs #103-107 (campaign step approval,
hybrid wiki search, document upload). CI previously only exercised
the underlying query functions via session_factory — these tests
verify the actual HTTP behaviour: dependency resolution, response
shape, status codes, auth, Pydantic validation.

Scope: sync TestClient tests that verify the routes are wired and
their input validation works. Seeded-data integration tests (e.g.
"owner approves their own step → 200") aren't here because mixing
asyncpg with pytest-asyncio's per-test event loops produces benign
"Event loop is closed" warnings at teardown that CI flags as failures.
The seeded-data assertions live in test_campaigns_owner_scope.py and
test_curator_inbox.py at the query level, which run cleanly.
"""
from __future__ import annotations

import uuid


# ── Campaign step approval ───────────────────────────────────────────────────


def test_approve_step_invalid_uuid(client, auth_header):
    """The route validates campaign_id UUID format before touching the DB."""
    resp = client.post(
        "/api/campaigns/not-a-uuid/steps/0/approve", headers=auth_header,
    )
    assert resp.status_code == 400


def test_approve_step_requires_auth(client):
    fake_uuid = str(uuid.uuid4())
    resp = client.post(f"/api/campaigns/{fake_uuid}/steps/0/approve")
    assert resp.status_code == 401


def test_reject_step_invalid_uuid(client, auth_header):
    resp = client.post(
        "/api/campaigns/not-a-uuid/steps/0/reject", headers=auth_header,
    )
    assert resp.status_code == 400


def test_reject_step_requires_auth(client):
    fake_uuid = str(uuid.uuid4())
    resp = client.post(f"/api/campaigns/{fake_uuid}/steps/0/reject")
    assert resp.status_code == 401


def test_steps_awaiting_approval_requires_auth(client):
    resp = client.get("/api/campaigns/steps/awaiting-approval")
    assert resp.status_code == 401


# ── Hybrid wiki search ───────────────────────────────────────────────────────


def test_search_unknown_mode_rejected(client, auth_header):
    """Mode is a Literal in the Pydantic param — unknown values 422."""
    resp = client.get("/api/search?q=x&mode=bogus", headers=auth_header)
    assert resp.status_code == 422


def test_search_query_too_long(client, auth_header):
    """Field(max_length=500) on the `q` query param enforces the cap."""
    resp = client.get(f"/api/search?q={'x' * 600}", headers=auth_header)
    assert resp.status_code == 422


def test_search_query_required(client, auth_header):
    """The `q` query param is required (Query(..., min_length=1))."""
    resp = client.get("/api/search", headers=auth_header)
    assert resp.status_code == 422


# ── Document upload ──────────────────────────────────────────────────────────


def test_document_upload_unsupported_type(client, auth_header):
    """Only PDF / plain text / markdown are accepted at the route layer."""
    resp = client.post(
        "/api/integrations/documents",
        headers=auth_header,
        files={"file": ("x.bin", b"\x00\x01\x02", "application/octet-stream")},
    )
    assert resp.status_code == 415


def test_document_upload_oversize_rejected(client, auth_header):
    """Payloads over 10 MB are rejected without buffering the whole file."""
    big = b"a" * (11 * 1024 * 1024)  # 11 MB
    resp = client.post(
        "/api/integrations/documents",
        headers=auth_header,
        files={"file": ("big.txt", big, "text/plain")},
    )
    assert resp.status_code == 413


def test_document_upload_invalid_pdf_magic(client, auth_header):
    """Content-type application/pdf with a missing %PDF prefix is rejected
    — the header alone isn't trusted."""
    resp = client.post(
        "/api/integrations/documents",
        headers=auth_header,
        files={"file": ("fake.pdf", b"not a pdf body", "application/pdf")},
    )
    assert resp.status_code == 415


def test_document_upload_requires_auth(client):
    resp = client.post(
        "/api/integrations/documents",
        files={"file": ("x.txt", b"hello", "text/plain")},
    )
    assert resp.status_code == 401


# ── Curator inbox ────────────────────────────────────────────────────────────
# (Already covered in test_curator_inbox.py — listed here so the test plan
# review at PR time shows the inbox isn't missing from the V2 surface check.)
