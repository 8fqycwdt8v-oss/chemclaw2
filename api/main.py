import os
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.db.connection import init_db
from api.routes.health import router as health_router


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    init_db()
    yield
    # Engine disposal happens automatically when the process exits;
    # explicit disposal here supports clean test teardown.
    from api.db.connection import engine
    if engine is not None:
        await engine.dispose()


def create_app() -> FastAPI:
    app = FastAPI(title="chemclaw2", lifespan=lifespan)

    # Allow all origins — frontend lives on a separate origin (chemclaw2_gui).
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health_router)

    return app


app = create_app()
