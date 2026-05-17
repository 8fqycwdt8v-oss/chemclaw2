import asyncio
import os
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.db.connection import init_db
from api.routes.chat import router as chat_router
from api.routes.health import router as health_router
from api.routes.search import router as search_router
from api.routes.wiki import router as wiki_router


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    init_db()
    # Optional: run fingerprint worker in-process (controlled by env var).
    # For production, prefer running `python -m api.workers.fp_worker` as a
    # separate process so its restart cycle doesn't affect the web server.
    worker_task: asyncio.Task | None = None
    if os.environ.get("RUN_WORKER_IN_PROCESS") == "1":
        from api.db.connection import async_session_factory
        from api.workers.fp_worker import run_worker
        if async_session_factory is not None:
            worker_task = asyncio.create_task(run_worker(async_session_factory))
    yield
    if worker_task is not None:
        worker_task.cancel()
    from api.db.connection import engine
    if engine is not None:
        await engine.dispose()


def create_app() -> FastAPI:
    app = FastAPI(title="chemclaw2", lifespan=lifespan)

    # Auth is Bearer-token only (no cookies), so allow_credentials is not needed.
    # Set CORS_ALLOWED_ORIGINS to a comma-separated list in production
    # (e.g. "https://app.chemclaw.com,https://staging.chemclaw.com").
    raw_origins = os.environ.get("CORS_ALLOWED_ORIGINS", "")
    allowed_origins = [o.strip() for o in raw_origins.split(",") if o.strip()] or [
        "http://localhost:3000",
        "http://localhost:5173",
    ]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health_router)
    app.include_router(chat_router)
    app.include_router(search_router)
    app.include_router(wiki_router)

    return app


app = create_app()
