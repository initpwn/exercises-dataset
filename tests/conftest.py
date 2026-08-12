"""Shared fixtures for exercise API endpoint tests."""

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from exercise_api.catalog import LoadedCatalog, sync_catalog
from exercise_api.config import DatabaseSettings, Settings
from exercise_api.database import Database
from exercise_api.main import create_app
from tests.factories import catalog_record


@pytest.fixture
async def database(tmp_path: Path) -> AsyncIterator[Database]:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'api.db'}")
    await database.create_schema()
    await sync_catalog(
        database.session_factory,
        LoadedCatalog(
            "api-fixture",
            [
                catalog_record("0001", "Dumbbell bench press"),
                catalog_record("0002", "Dumbbell fly"),
                catalog_record(
                    "0003",
                    "Chin up",
                    category="upper arms",
                    body_part="upper arms",
                    equipment="body weight",
                    muscle_group="biceps",
                    target="biceps",
                ),
            ],
        ),
    )
    yield database
    await database.dispose()


@pytest.fixture
async def client(database: Database) -> AsyncIterator[AsyncClient]:
    settings = Settings(database=DatabaseSettings(url="sqlite:///unused.db"))
    app = create_app(settings, lifespan_enabled=False, initialized_database=database)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as api_client:
        yield api_client
