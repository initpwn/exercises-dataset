"""Persistence and HTTP contract tests for conversation sessions."""

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC
from pathlib import Path
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, func, inspect, select, text
from sqlalchemy.orm import Session as SyncSession

from exercise_api.config import DatabaseSettings, Settings
from exercise_api.database import Database
from exercise_api.db_models import MessageRow, SessionRow
from exercise_api.main import create_app
from exercise_api.session_repository import SessionNotFoundError, SessionRepository


@pytest.fixture
async def client(tmp_path: Path) -> AsyncIterator[AsyncClient]:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'sessions-api.db'}")
    await database.create_schema()
    app = create_app(
        Settings(database=DatabaseSettings(url="sqlite:///unused.db")),
        lifespan_enabled=False,
        initialized_database=database,
        initial_readiness={"database": "ready", "catalog": "ready"},
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as api_client:
        yield api_client
    await database.dispose()


@pytest.mark.asyncio
async def test_session_survives_database_reopen(tmp_path: Path) -> None:
    url = f"sqlite+aiosqlite:///{tmp_path / 'sessions.db'}"
    first_db = Database(url)
    await first_db.create_schema()
    first = SessionRepository(first_db.session_factory)
    session = await first.create()
    await first.append_exchange(
        session.id, "chest workout", "Here it is", {"intent": "workout"}
    )
    await first_db.dispose()

    second_db = Database(url)
    second = SessionRepository(second_db.session_factory)
    loaded = await second.get(session.id)

    assert loaded is not None
    assert [message.text for message in loaded.messages] == [
        "chest workout",
        "Here it is",
    ]
    assert [message.role for message in loaded.messages] == ["user", "assistant"]
    assert [message.payload for message in loaded.messages] == [
        None,
        {"intent": "workout"},
    ]
    assert loaded.created_at.tzinfo is UTC
    assert loaded.updated_at.tzinfo is UTC
    assert all(message.created_at.tzinfo is UTC for message in loaded.messages)
    await second_db.dispose()


@pytest.mark.asyncio
async def test_sqlite_enables_foreign_keys_on_every_connection(tmp_path: Path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'foreign-keys.db'}")

    async with (
        database.engine.connect() as first,
        database.engine.connect() as second,
    ):
        assert await first.scalar(text("PRAGMA foreign_keys")) == 1
        assert await second.scalar(text("PRAGMA foreign_keys")) == 1

    await database.dispose()


@pytest.mark.asyncio
async def test_recent_messages_returns_newest_limit_chronologically(
    tmp_path: Path,
) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'recent.db'}")
    await database.create_schema()
    repository = SessionRepository(database.session_factory)
    session = await repository.create()
    await repository.append_exchange(session.id, "user 1", "assistant 1", None)
    await repository.append_exchange(session.id, "user 2", "assistant 2", None)

    messages = await repository.recent_messages(session.id, 3)

    assert [message.text for message in messages] == [
        "assistant 1",
        "user 2",
        "assistant 2",
    ]
    assert await repository.delete(session.id)
    assert await repository.recent_messages(session.id, 10) == []
    await database.dispose()


@pytest.mark.asyncio
async def test_concurrent_appends_reserve_unique_ordered_positions(
    tmp_path: Path,
) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'concurrent.db'}")
    await database.create_schema()
    repository = SessionRepository(database.session_factory)
    session = await repository.create()

    await asyncio.gather(
        repository.append_exchange(session.id, "user 1", "assistant 1", None),
        repository.append_exchange(session.id, "user 2", "assistant 2", None),
    )

    loaded = await repository.get(session.id)
    assert loaded is not None
    assert [message.position for message in loaded.messages] == [0, 1, 2, 3]
    assert [message.role for message in loaded.messages] == [
        "user",
        "assistant",
        "user",
        "assistant",
    ]
    assert [message.text for message in loaded.messages] in (
        ["user 1", "assistant 1", "user 2", "assistant 2"],
        ["user 2", "assistant 2", "user 1", "assistant 1"],
    )
    await database.dispose()


@pytest.mark.asyncio
async def test_delete_racing_with_append_never_leaves_orphan_messages(
    tmp_path: Path,
) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'delete-append.db'}")
    await database.create_schema()
    repository = SessionRepository(database.session_factory)
    created = await repository.create()

    results = await asyncio.gather(
        repository.delete(created.id),
        repository.append_exchange(
            created.id, "late user", "late assistant", {"intent": "workout"}
        ),
        return_exceptions=True,
    )

    assert results[0] is True
    assert results[1] is None or isinstance(results[1], SessionNotFoundError)
    async with database.session_factory() as checking:
        parent = await checking.get(SessionRow, str(created.id))
        orphan_count = await checking.scalar(
            select(func.count())
            .select_from(MessageRow)
            .where(MessageRow.session_id == str(created.id))
        )
    assert parent is None
    assert orphan_count == 0
    await database.dispose()


@pytest.mark.asyncio
async def test_database_cascade_catches_append_between_delete_load_and_commit(
    tmp_path: Path,
) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'delete-interleave.db'}")
    await database.create_schema()
    repository = SessionRepository(database.session_factory)
    created = await repository.create()

    async with database.session_factory() as deleting:
        row = await deleting.get(SessionRow, str(created.id))
        assert row is not None
        await deleting.delete(row)
        await repository.append_exchange(
            created.id, "late user", "late assistant", None
        )
        await deleting.commit()

    async with database.session_factory() as checking:
        assert await checking.get(SessionRow, str(created.id)) is None
        orphan_count = await checking.scalar(
            select(func.count())
            .select_from(MessageRow)
            .where(MessageRow.session_id == str(created.id))
        )
    assert orphan_count == 0
    await database.dispose()


@pytest.mark.asyncio
async def test_append_after_committed_delete_is_not_found(tmp_path: Path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'append-after-delete.db'}")
    await database.create_schema()
    repository = SessionRepository(database.session_factory)
    created = await repository.create()

    assert await repository.delete(created.id) is True
    with pytest.raises(SessionNotFoundError):
        await repository.append_exchange(created.id, "user", "assistant", None)

    await database.dispose()


@pytest.mark.asyncio
async def test_append_exchange_rolls_back_both_messages_when_flush_fails(
    tmp_path: Path,
) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'rollback.db'}")
    await database.create_schema()
    repository = SessionRepository(database.session_factory)
    created = await repository.create()

    def fail_on_assistant_message(
        session: SyncSession, flush_context: object, instances: object
    ) -> None:
        if any(
            isinstance(row, MessageRow) and row.role == "assistant"
            for row in session.new
        ):
            raise RuntimeError("injected assistant-message flush failure")

    event.listen(SyncSession, "before_flush", fail_on_assistant_message)
    try:
        with pytest.raises(RuntimeError, match="injected assistant-message"):
            await repository.append_exchange(
                created.id, "user message", "assistant message", None
            )
    finally:
        event.remove(SyncSession, "before_flush", fail_on_assistant_message)

    loaded = await repository.get(created.id)
    assert loaded is not None
    assert loaded.messages == []
    async with database.session_factory() as session:
        row = await session.get(SessionRow, str(created.id))
    assert row is not None
    assert row.next_position == 0
    await database.dispose()


@pytest.mark.asyncio
async def test_schema_upgrade_initializes_counter_from_existing_messages(
    tmp_path: Path,
) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'upgrade.db'}")
    session_id = uuid4()
    async with database.engine.begin() as connection:
        await connection.execute(
            text(
                """
                CREATE TABLE conversation_sessions (
                    id VARCHAR(36) PRIMARY KEY,
                    created_at DATETIME NOT NULL
                )
                """
            )
        )
        await connection.execute(
            text(
                """
                CREATE TABLE conversation_messages (
                    id INTEGER PRIMARY KEY,
                    session_id VARCHAR(36) NOT NULL,
                    position INTEGER NOT NULL,
                    role VARCHAR(9) NOT NULL,
                    text VARCHAR NOT NULL,
                    payload JSON,
                    created_at DATETIME NOT NULL,
                    UNIQUE (session_id, position),
                    FOREIGN KEY(session_id) REFERENCES conversation_sessions (id)
                        ON DELETE CASCADE
                )
                """
            )
        )
        await connection.execute(
            text(
                "INSERT INTO conversation_sessions (id, created_at) "
                "VALUES (:id, CURRENT_TIMESTAMP)"
            ),
            {"id": str(session_id)},
        )
        await connection.execute(
            text(
                """
                INSERT INTO conversation_messages
                    (session_id, position, role, text, payload, created_at)
                VALUES
                    (:id, 0, 'user', 'existing user', NULL, CURRENT_TIMESTAMP),
                    (:id, 1, 'assistant', 'existing assistant', NULL,
                     CURRENT_TIMESTAMP)
                """
            ),
            {"id": str(session_id)},
        )

    await database.create_schema()
    await database.create_schema()
    repository = SessionRepository(database.session_factory)
    await repository.append_exchange(session_id, "new user", "new assistant", None)

    loaded = await repository.get(session_id)
    assert loaded is not None
    assert [message.position for message in loaded.messages] == [0, 1, 2, 3]
    assert [message.text for message in loaded.messages] == [
        "existing user",
        "existing assistant",
        "new user",
        "new assistant",
    ]
    assert loaded.updated_at >= loaded.created_at
    await database.dispose()


@pytest.mark.asyncio
async def test_concurrent_legacy_schema_initializers_are_idempotent(
    tmp_path: Path,
) -> None:
    url = f"sqlite+aiosqlite:///{tmp_path / 'concurrent-upgrade.db'}"
    bootstrap = Database(url)
    async with bootstrap.engine.begin() as connection:
        await connection.execute(
            text(
                """
                CREATE TABLE conversation_sessions (
                    id VARCHAR(36) PRIMARY KEY,
                    created_at DATETIME NOT NULL
                )
                """
            )
        )
    await bootstrap.dispose()

    initializers = [Database(url) for _ in range(4)]
    try:
        results = await asyncio.gather(
            *(database.create_schema() for database in initializers),
            return_exceptions=True,
        )
        assert results == [None, None, None, None]
        async with initializers[0].engine.connect() as connection:
            columns = await connection.run_sync(
                lambda sync_connection: {
                    column["name"]
                    for column in inspect(sync_connection).get_columns(
                        "conversation_sessions"
                    )
                }
            )
        assert {"next_position", "updated_at"} <= columns
    finally:
        await asyncio.gather(*(database.dispose() for database in initializers))


@pytest.mark.asyncio
async def test_append_advances_session_updated_at(tmp_path: Path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'updated-at.db'}")
    await database.create_schema()
    repository = SessionRepository(database.session_factory)
    created = await repository.create()

    await repository.append_exchange(created.id, "user", "assistant", None)
    loaded = await repository.get(created.id)

    assert loaded is not None
    assert loaded.updated_at > created.updated_at
    assert loaded.updated_at.tzinfo is UTC
    await database.dispose()


@pytest.mark.asyncio
async def test_unknown_session_is_404_and_delete_cascades(
    client: AsyncClient,
) -> None:
    assert (await client.get(f"/v1/sessions/{uuid4()}")).status_code == 404
    create_response = await client.post("/v1/sessions")
    assert create_response.status_code == 201
    created = create_response.json()
    assert created["messages"] == []
    assert (await client.delete(f"/v1/sessions/{created['id']}")).status_code == 204
    assert (await client.get(f"/v1/sessions/{created['id']}")).status_code == 404
