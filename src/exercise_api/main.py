"""FastAPI application factory."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from exercise_api.config import Settings
from exercise_api.database import Database
from exercise_api.routes.exercises import router as exercise_router


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
    app.include_router(exercise_router)
    return app
