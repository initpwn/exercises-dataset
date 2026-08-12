"""Async database setup for the exercise API."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy import event, inspect, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from exercise_api.db_models import Base

_SCHEMA_LOCK_ID = 0x4558455243495345


def _session_columns(connection: Connection) -> set[str]:
    columns = inspect(connection).get_columns("conversation_sessions")
    return {str(column["name"]) for column in columns}


def _configure_sqlite_connection(
    dbapi_connection: Any, connection_record: object
) -> None:
    """Enable declared integrity constraints on each pooled SQLite connection."""
    del connection_record
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=5000")
    finally:
        cursor.close()


def async_database_url(url: str) -> str:
    """Normalize supported synchronous database URLs for SQLAlchemy asyncio."""
    if url.startswith("sqlite:///"):
        return url.replace("sqlite:///", "sqlite+aiosqlite:///", 1)
    if url.startswith("postgresql+psycopg2://"):
        return url.replace("postgresql+psycopg2://", "postgresql+psycopg://", 1)
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url


class Database:
    """Own the async engine and session factory for an API database."""

    def __init__(self, url: str) -> None:
        self.engine = create_async_engine(async_database_url(url))
        if self.engine.dialect.name == "sqlite":
            event.listen(
                self.engine.sync_engine, "connect", _configure_sqlite_connection
            )
        self.session_factory = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )

    async def create_schema(self) -> None:
        """Create and upgrade schema under a cross-process database lock."""
        async with self._schema_connection() as connection:
            await connection.run_sync(Base.metadata.create_all)
            columns = await connection.run_sync(_session_columns)
            if "next_position" not in columns:
                await connection.execute(
                    text(
                        "ALTER TABLE conversation_sessions "
                        "ADD COLUMN next_position INTEGER NOT NULL DEFAULT 0"
                    )
                )
                await connection.execute(
                    text(
                        """
                        UPDATE conversation_sessions
                        SET next_position = COALESCE(
                            (
                                SELECT MAX(position) + 1
                                FROM conversation_messages
                                WHERE conversation_messages.session_id =
                                    conversation_sessions.id
                            ),
                            0
                        )
                        """
                    )
                )
            if "updated_at" not in columns:
                await self._add_session_updated_at(connection)

    @asynccontextmanager
    async def _schema_connection(self) -> AsyncIterator[AsyncConnection]:
        """Serialize inspection plus DDL across application processes."""
        async with self.engine.connect() as connection:
            if connection.dialect.name == "sqlite":
                await connection.exec_driver_sql("BEGIN EXCLUSIVE")
                try:
                    yield connection
                except BaseException:
                    await connection.rollback()
                    raise
                else:
                    await connection.commit()
                return

            async with connection.begin():
                if connection.dialect.name == "postgresql":
                    await connection.execute(
                        text("SELECT pg_advisory_xact_lock(:lock_id)"),
                        {"lock_id": _SCHEMA_LOCK_ID},
                    )
                yield connection

    @staticmethod
    async def _add_session_updated_at(connection: AsyncConnection) -> None:
        if connection.dialect.name == "sqlite":
            await connection.execute(
                text(
                    "ALTER TABLE conversation_sessions "
                    "ADD COLUMN updated_at DATETIME NOT NULL "
                    "DEFAULT '1970-01-01 00:00:00+00:00'"
                )
            )
        else:
            await connection.execute(
                text(
                    "ALTER TABLE conversation_sessions "
                    "ADD COLUMN updated_at TIMESTAMP WITH TIME ZONE"
                )
            )
        await connection.execute(
            text(
                "UPDATE conversation_sessions "
                "SET updated_at = COALESCE("
                "(SELECT MAX(conversation_messages.created_at) "
                "FROM conversation_messages "
                "WHERE conversation_messages.session_id = "
                "conversation_sessions.id), created_at) "
                "WHERE updated_at IS NULL OR "
                "updated_at = '1970-01-01 00:00:00+00:00'"
            )
        )
        if connection.dialect.name == "postgresql":
            await connection.execute(
                text(
                    "ALTER TABLE conversation_sessions "
                    "ALTER COLUMN updated_at SET NOT NULL"
                )
            )

    async def dispose(self) -> None:
        """Release database connection-pool resources."""
        await self.engine.dispose()
