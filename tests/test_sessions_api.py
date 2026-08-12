"""Persistence and HTTP contract tests for conversation sessions."""

from collections.abc import AsyncIterator
from pathlib import Path
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from exercise_api.config import DatabaseSettings, Settings
from exercise_api.database import Database
from exercise_api.dependencies import get_session_repository
from exercise_api.main import create_app
from exercise_api.session_repository import SessionRepository


@pytest.fixture
async def client(tmp_path: Path) -> AsyncIterator[AsyncClient]:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'sessions-api.db'}")
    await database.create_schema()
    app = create_app(
        Settings(database=DatabaseSettings(url="sqlite:///unused.db")),
        lifespan_enabled=False,
    )
    app.dependency_overrides[get_session_repository] = lambda: SessionRepository(
        database.session_factory
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
    await second_db.dispose()


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
