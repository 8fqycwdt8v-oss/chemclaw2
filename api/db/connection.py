import logging
import os
from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

logger = logging.getLogger(__name__)


def _int_env(name: str, default: int, min_value: int = 0) -> int:
    """Read an int env var with a sane default and lower bound.

    Falls back to `default` on missing var, non-numeric value, or a value
    below `min_value`. Logs the fallback so misconfigurations don't go
    silently to the default.
    """
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        logger.warning("env %s=%r is not an int, using default %d", name, raw, default)
        return default
    if value < min_value:
        logger.warning("env %s=%d below minimum %d, using default %d", name, value, min_value, default)
        return default
    return value


def _make_engine() -> AsyncEngine:
    url = os.environ["DATABASE_URL"]
    # Drizzle/TypeScript uses postgresql:// — asyncpg needs postgresql+asyncpg://
    if url.startswith("postgresql://") and "+asyncpg" not in url:
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
    # Pool sizing: DB_POOL_SIZE × replicas + DB_POOL_MAX_OVERFLOW headroom
    # must stay under Postgres's `max_connections`. Operators tune via env
    # at deploy time; defaults match the original baseline (5 + 10 = 15).
    pool_size = _int_env("DB_POOL_SIZE", default=5, min_value=1)
    max_overflow = _int_env("DB_POOL_MAX_OVERFLOW", default=10, min_value=0)
    return create_async_engine(
        url,
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_pre_ping=True,
    )


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
