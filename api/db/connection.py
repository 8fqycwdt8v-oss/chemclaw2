import os
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


def _make_engine() -> AsyncEngine:
    url = os.environ["DATABASE_URL"]
    # Drizzle/TypeScript uses postgresql:// — asyncpg needs postgresql+asyncpg://
    if url.startswith("postgresql://") and "+asyncpg" not in url:
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return create_async_engine(url, pool_size=5, max_overflow=10, pool_pre_ping=True)


# Module-level singletons — created once at import time (or re-created on
# first use after startup via lifespan). Tests can monkey-patch these.
engine: AsyncEngine | None = None
async_session_factory: async_sessionmaker[AsyncSession] | None = None


def init_db(database_url: str | None = None) -> None:
    global engine, async_session_factory
    if database_url:
        os.environ["DATABASE_URL"] = database_url
    engine = _make_engine()
    async_session_factory = async_sessionmaker(engine, expire_on_commit=False)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    if async_session_factory is None:
        raise RuntimeError("Database not initialised — call init_db() in app lifespan")
    async with async_session_factory() as session:
        yield session
