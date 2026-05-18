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
os.environ.setdefault("CLERK_SECRET_KEY", "sk_test_placeholder")
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
async def db(db_engine) -> AsyncIterator[AsyncSession]:
    """A bare AsyncSession bound to the test engine.

    We do NOT wrap the session in an outer transaction-rolled-back pattern
    here because most of the queries we're testing wrap their own writes in
    `async with db.begin():` blocks, and SAVEPOINT-around-commit interacts
    badly with that. Tests should write into uniquely-named slugs / ids so
    they don't collide between runs.
    """
    factory = async_sessionmaker(db_engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as session:
        yield session


@pytest_asyncio.fixture
async def session_factory(db_engine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(db_engine, expire_on_commit=False, class_=AsyncSession)


# ── Factories ────────────────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def wiki_page(db: AsyncSession, user_id: str) -> dict[str, Any]:
    """Create a fresh wiki page and yield its row dict."""
    from api.db.queries.wiki_write import upsert_wiki_page

    async def _noop_embed(texts: list[str]) -> list[list[float]]:
        from api.embeddings import EMBED_DIM
        return [[0.0] * EMBED_DIM for _ in texts]

    slug = f"test-page-{uuid.uuid4().hex[:8]}"
    page_id = await upsert_wiki_page(
        db,
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
    from api.db.queries.wiki_read import get_wiki_page
    return await get_wiki_page(db, slug)  # type: ignore[return-value]
