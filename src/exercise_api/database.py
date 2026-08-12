"""Async database setup for the exercise API."""

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from exercise_api.db_models import Base


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

    async def dispose(self) -> None:
        """Release database connection-pool resources."""
        await self.engine.dispose()
