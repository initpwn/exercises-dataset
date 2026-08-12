"""FastAPI application factory."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Request, status
from fastapi.responses import JSONResponse

from exercise_api.catalog import load_catalog, sync_catalog
from exercise_api.config import Settings, load_settings
from exercise_api.database import Database
from exercise_api.dependencies import require_ready
from exercise_api.llm_gateway import (
    LLMGateway,
    LLMInvalidResponseError,
    LLMUnavailableError,
)
from exercise_api.routes.chat import router as chat_router
from exercise_api.routes.exercises import router as exercise_router
from exercise_api.routes.health import router as health_router
from exercise_api.routes.sessions import router as session_router


def create_app(
    settings: Settings | None = None,
    lifespan_enabled: bool = True,
    *,
    initialized_database: Database | None = None,
    settings_path: Path = Path("config.toml"),
    catalog_path: Path = Path("data/exercises.json"),
    schema_path: Path = Path("data/exercises.schema.json"),
) -> FastAPI:
    """Create an app with deferred production startup or explicit test state."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        database = initialized_database
        owns_database = database is None
        app.state.readiness.update(database="pending", catalog="pending")
        try:
            try:
                resolved_settings = settings or load_settings(app.state.settings_path)
                if database is None:
                    database = Database(resolved_settings.database.url)
                app.state.settings = resolved_settings
                app.state.database = database
                app.state.llm_gateway = LLMGateway(resolved_settings.llm)
                await database.create_schema()
            except Exception:  # noqa: BLE001 - health must survive startup failures.
                app.state.readiness.update(database="failed", catalog="failed")
            else:
                app.state.readiness["database"] = "ready"
                try:
                    catalog = load_catalog(
                        app.state.catalog_path, app.state.schema_path
                    )
                    await sync_catalog(database.session_factory, catalog)
                except Exception:  # noqa: BLE001 - health must report sync failures.
                    app.state.readiness["catalog"] = "failed"
                else:
                    app.state.readiness["catalog"] = "ready"
            yield
        finally:
            if owns_database and database is not None:
                await database.dispose()

    app = FastAPI(lifespan=lifespan if lifespan_enabled else None)
    app.state.database = initialized_database
    app.state.settings = settings
    app.state.llm_gateway = LLMGateway(settings.llm) if settings is not None else None
    app.state.settings_path = settings_path
    app.state.catalog_path = catalog_path
    app.state.schema_path = schema_path
    initialized = (
        not lifespan_enabled
        and settings is not None
        and initialized_database is not None
    )
    initial_state = "ready" if initialized else "pending"
    app.state.readiness = {
        "database": initial_state,
        "catalog": initial_state,
    }

    @app.exception_handler(LLMUnavailableError)
    async def llm_unavailable(_: Request, __: LLMUnavailableError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"detail": "Model service is unavailable"},
        )

    @app.exception_handler(LLMInvalidResponseError)
    async def llm_invalid_response(
        _: Request, __: LLMInvalidResponseError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_502_BAD_GATEWAY,
            content={"detail": "Model returned an invalid grounded response"},
        )

    readiness_dependencies = [Depends(require_ready)]
    app.include_router(health_router)
    app.include_router(chat_router, dependencies=readiness_dependencies)
    app.include_router(exercise_router, dependencies=readiness_dependencies)
    app.include_router(session_router, dependencies=readiness_dependencies)
    return app


app = create_app()
