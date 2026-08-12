"""Async database setup for the exercise API."""

from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from exercise_api.db_models import Base


def _has_session_position_counter(connection: Connection) -> bool:
    columns = inspect(connection).get_columns("conversation_sessions")
    return any(column["name"] == "next_position" for column in columns)


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
        self.session_factory = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )

    async def create_schema(self) -> None:
        """Create all application tables that do not already exist."""
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
            if not await connection.run_sync(_has_session_position_counter):
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

    async def dispose(self) -> None:
        """Release database connection-pool resources."""
        await self.engine.dispose()
