"""Docker-backed parity checks for the supported PostgreSQL runtime."""

import asyncio
from uuid import UUID

import pytest
from sqlalchemy import text
from testcontainers.community.postgres import PostgresContainer

from exercise_api.catalog import LoadedCatalog, sync_catalog
from exercise_api.database import Database, async_database_url
from exercise_api.exercise_repository import ExerciseFilters, ExerciseRepository
from exercise_api.session_repository import SessionRepository
from tests.factories import catalog_record


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_postgres_catalog_filters_and_session_restart() -> None:
    with PostgresContainer("postgres:17-alpine") as postgres:
        url = async_database_url(postgres.get_connection_url())
        database = Database(url)
        await database.create_schema()
        catalog = LoadedCatalog(
            "postgres-fixture",
            [catalog_record("0001", "Press"), catalog_record("0002", "Fly")],
        )
        sync_results = await asyncio.gather(
            sync_catalog(database.session_factory, catalog),
            sync_catalog(database.session_factory, catalog),
        )
        assert sorted(sync_results) == [False, True]

        exercises = ExerciseRepository(database.session_factory)
        page = await exercises.list(
            ExerciseFilters(equipment="DUMB", page=1, limit=20)
        )
        assert page.total == 2
        assert [exercise.name for exercise in page.data] == ["Press", "Fly"]

        changed = LoadedCatalog(
            "postgres-fixture-changed",
            [catalog_record("0001", "Strict press")],
        )
        assert await sync_catalog(database.session_factory, changed) is True
        changed_page = await exercises.list(ExerciseFilters(page=1, limit=20))
        assert changed_page.total == 1
        assert [exercise.name for exercise in changed_page.data] == ["Strict press"]

        sessions = SessionRepository(database.session_factory)
        created = await sessions.create()
        await sessions.append_exchange(
            created.id,
            "hello",
            "response",
            {"intent": "exercise_search"},
        )
        await database.dispose()

        restarted_database = Database(url)
        restarted_sessions = SessionRepository(restarted_database.session_factory)
        restarted = await restarted_sessions.get(created.id)
        assert restarted is not None
        assert [message.text for message in restarted.messages] == [
            "hello",
            "response",
        ]
        assert restarted.messages[1].payload == {"intent": "exercise_search"}
        await restarted_database.dispose()


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_postgres_concurrent_session_appends_are_ordered() -> None:
    with PostgresContainer("postgres:17-alpine") as postgres:
        database = Database(async_database_url(postgres.get_connection_url()))
        await database.create_schema()
        sessions = SessionRepository(database.session_factory)
        created = await sessions.create()

        await asyncio.gather(
            sessions.append_exchange(created.id, "user 1", "assistant 1", None),
            sessions.append_exchange(created.id, "user 2", "assistant 2", None),
        )

        loaded = await sessions.get(created.id)
        assert loaded is not None
        assert [message.position for message in loaded.messages] == [0, 1, 2, 3]
        assert [message.role for message in loaded.messages] == [
            "user",
            "assistant",
            "user",
            "assistant",
        ]
        await database.dispose()


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_postgres_schema_upgrade_initializes_session_counter() -> None:
    with PostgresContainer("postgres:17-alpine") as postgres:
        database = Database(async_database_url(postgres.get_connection_url()))
        async with database.engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    CREATE TABLE conversation_sessions (
                        id VARCHAR(36) PRIMARY KEY,
                        created_at TIMESTAMP WITH TIME ZONE NOT NULL
                    )
                    """
                )
            )
            await connection.execute(
                text(
                    """
                    CREATE TABLE conversation_messages (
                        id SERIAL PRIMARY KEY,
                        session_id VARCHAR(36) NOT NULL,
                        position INTEGER NOT NULL,
                        role VARCHAR(9) NOT NULL,
                        text VARCHAR NOT NULL,
                        payload JSON,
                        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
                        UNIQUE (session_id, position),
                        FOREIGN KEY(session_id) REFERENCES conversation_sessions (id)
                            ON DELETE CASCADE
                    )
                    """
                )
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO conversation_sessions (id, created_at)
                    VALUES ('00000000-0000-0000-0000-000000000001', CURRENT_TIMESTAMP)
                    """
                )
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO conversation_messages
                        (session_id, position, role, text, payload, created_at)
                    VALUES
                        ('00000000-0000-0000-0000-000000000001', 0, 'user',
                         'existing user', NULL, CURRENT_TIMESTAMP),
                        ('00000000-0000-0000-0000-000000000001', 1, 'assistant',
                         'existing assistant', NULL, CURRENT_TIMESTAMP)
                    """
                )
            )

        await database.create_schema()
        await database.create_schema()
        sessions = SessionRepository(database.session_factory)
        loaded = await sessions.get(
            UUID("00000000-0000-0000-0000-000000000001")
        )
        assert loaded is not None
        await sessions.append_exchange(
            loaded.id, "new user", "new assistant", {"intent": "workout"}
        )

        upgraded = await sessions.get(loaded.id)
        assert upgraded is not None
        assert [message.position for message in upgraded.messages] == [0, 1, 2, 3]
        assert upgraded.messages[-1].payload == {"intent": "workout"}
        await database.dispose()
