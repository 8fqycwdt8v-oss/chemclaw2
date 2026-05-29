"""Smoke tests — import chain + health endpoint."""
import os

import pytest

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:5432/chemclaw2_test")
os.environ.setdefault("ANTHROPIC_API_KEY", "sk-ant-placeholder")
os.environ.setdefault("OPENAI_API_KEY", "sk-placeholder")


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient

    from api.main import create_app
    app = create_app()
    with TestClient(app) as c:
        yield c


def test_health_ok(client):
    """Liveness probe — process up, event loop responsive. No DB."""
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_readiness_ok(client):
    resp = client.get("/api/readiness")
    # 200 when DB is up + backlog under threshold, 503 otherwise.
    assert resp.status_code in (200, 503)
    data = resp.json()
    assert "ready" in data
    assert "db" in data
    assert "fingerprint_backlog" in data


def test_chat_requires_auth(client):
    resp = client.post("/api/chat", json={"prompt": "hello"})
    assert resp.status_code in (401, 422, 503)


def test_search_requires_query(client):
    resp = client.get("/api/search")
    assert resp.status_code == 422
