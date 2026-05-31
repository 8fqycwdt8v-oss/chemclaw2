"""Shared pytest fixtures for api/ tests.

Tests run against the real Postgres container started by CI (see
.github/workflows/*.yml). DATABASE_URL points at it; migrations are
applied before pytest is invoked.

Auth in tests uses the ALLOW_MOCK_AUTH=1 bypass — the conftest sets
ENV=test so validate_auth_config() accepts it. Tokens look like
'Bearer mock:<user_id>'.
"""
from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from typing import Any

# Set test env *before* api modules import, so validate_auth_config()
# accepts ALLOW_MOCK_AUTH=1.
os.environ.setdefault("ENV", "test")
os.environ.setdefault("ALLOW_MOCK_AUTH", "1")
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/chemclaw2_test",
)
os.environ.setdefault("ANTHROPIC_API_KEY", "sk-ant-placeholder")
os.environ.setdefault("OPENAI_API_KEY", "sk-placeholder")
# ADMIN_USER_IDS — populated so admin-route tests can opt in via the
# admin_user_id fixture below.
os.environ.setdefault("ADMIN_USER_IDS", "admin-test-user")

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


def _mock_token(user_id: str) -> str:
    return f"Bearer mock:{user_id}"


@pytest.fixture(scope="session")
def app():
    """The FastAPI app instance.

    NOTE: building the app does NOT run the lifespan — that fires only when
    a TestClient context manager enters. Specifically, validate_auth_config()
    runs in lifespan, so the _admin_user_ids cache stays empty until a test
    enters the `client` fixture. Tests that exercise get_admin_user directly
    (without going through the HTTP layer) should either depend on `client`
    or call api.auth.validate_auth_config() explicitly.
    """
    from api.main import create_app
    return create_app()


@pytest.fixture(scope="session")
def client(app):
    with TestClient(app) as c:
        yield c


@pytest.fixture
def user_id() -> str:
    """A unique mock user id per test so rate-limit buckets don't bleed."""
    return f"u-{uuid.uuid4().hex[:8]}"


@pytest.fixture
def auth_header(user_id: str) -> dict[str, str]:
    return {"Authorization": _mock_token(user_id)}


@pytest.fixture
def admin_user_id() -> str:
    # Matches ADMIN_USER_IDS set above.
    return "admin-test-user"


@pytest.fixture
def admin_header(admin_user_id: str) -> dict[str, str]:
    return {"Authorization": _mock_token(admin_user_id)}


@pytest_asyncio.fixture
async def db_engine():
    """Module-scoped engine pointing at the CI test database."""
    engine = create_async_engine(os.environ["DATABASE_URL"], pool_pre_ping=True)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def session_factory(db_engine) -> async_sessionmaker[AsyncSession]:
    """Session factory mirroring production's `Depends(get_db)` shape.

    Tests that call multiple query functions in sequence should use this
    factory and open a fresh session per call — the same pattern as a
    request handler — so each function's internal `async with db.begin():`
    block sees a clean session.
    """
    return async_sessionmaker(db_engine, expire_on_commit=False, class_=AsyncSession)


@pytest_asyncio.fixture
async def db(session_factory) -> AsyncIterator[AsyncSession]:
    """A single AsyncSession, useful for tests that perform exactly one
    logical DB operation (which itself may begin its own transaction).

    For multi-step tests, prefer `session_factory` and open a fresh session
    per call.
    """
    async with session_factory() as session:
        yield session


# ── Isolation helpers ──────────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def isolate_reactions(session_factory) -> AsyncIterator[None]:
    """TRUNCATE the reactions tables before a test so similarity-search
    assertions over a bounded `limit` aren't perturbed by rows accumulated
    from earlier runs on a reused local DB.

    CI runs against a fresh DB per job, so this is a no-op there; it makes
    tests deterministic when the local test DB is reused across runs. CASCADE
    only reaches `reaction_outcomes` and `reaction_condition_predictions`
    (the two tables with an FK to `reactions`) — both reaction-derived test
    data, nothing else.
    """
    from sqlalchemy import text

    async with session_factory() as db:
        async with db.begin():
            await db.execute(text("TRUNCATE reactions CASCADE"))
    yield


# ── Factories ────────────────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def wiki_page(session_factory, user_id: str) -> dict[str, Any]:
    """Create a fresh wiki page and yield its row dict. Uses separate sessions
    for write + read to mirror production behavior."""
    from api.db.queries.wiki_read import get_wiki_page
    from api.db.queries.wiki_write import upsert_wiki_page
    from api.embeddings import EMBED_DIM

    async def _noop_embed(texts: list[str]) -> list[list[float]]:
        return [[0.0] * EMBED_DIM for _ in texts]

    slug = f"test-page-{uuid.uuid4().hex[:8]}"
    async with session_factory() as s:
        await upsert_wiki_page(
            s,
            slug=slug,
            title=f"Test Page {slug}",
            content={"type": "doc", "content": []},
            content_text=(
                "First paragraph of test content with enough length to "
                "exceed the minimum chunk threshold of fifty characters.\n\n"
                "Second paragraph to give the chunker a second segment to "
                "split into a separate chunk."
            ),
            created_by=user_id,
            citations=[],
            embed_fn=_noop_embed,
        )
    async with session_factory() as s:
        page = await get_wiki_page(s, slug)
    return page  # type: ignore[return-value]
