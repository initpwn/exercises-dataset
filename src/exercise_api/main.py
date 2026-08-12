"""FastAPI application factory."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from exercise_api.config import Settings
from exercise_api.database import Database
from exercise_api.llm_gateway import LLMGateway
from exercise_api.routes.chat import router as chat_router
from exercise_api.routes.exercises import router as exercise_router
from exercise_api.routes.sessions import router as session_router


def create_app(settings: Settings, lifespan_enabled: bool = True) -> FastAPI:
    """Create an exercise API application for the supplied settings."""
    database = Database(settings.database.url)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        await database.create_schema()
        try:
            yield
        finally:
            await database.dispose()

    app = FastAPI(lifespan=lifespan if lifespan_enabled else None)
    app.state.database = database
    app.state.settings = settings
    app.state.llm_gateway = LLMGateway(settings.llm)
    app.include_router(chat_router)
    app.include_router(exercise_router)
    app.include_router(session_router)
    return app
