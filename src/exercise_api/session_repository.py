"""Durable conversation-session storage."""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from exercise_api.api_models import MessageOut, SessionOut
from exercise_api.db_models import MessageRow, SessionRow


class SessionNotFoundError(LookupError):
    """Raised when a conversation session does not exist."""


class StoredSession(SessionOut):
    """Internal session view including the persistence-only update timestamp."""

    updated_at: datetime


class SessionRepository:
    """Read and mutate persistent conversation sessions."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def create(self) -> StoredSession:
        row = SessionRow(id=str(uuid4()), messages=[])
        async with self._session_factory() as database_session:
            database_session.add(row)
            await database_session.commit()
        return StoredSession(
            id=UUID(row.id),
            created_at=row.created_at,
            updated_at=row.updated_at,
            messages=[],
        )

    async def get(self, session_id: UUID) -> StoredSession | None:
        statement = (
            select(SessionRow)
            .where(SessionRow.id == str(session_id))
            .options(selectinload(SessionRow.messages))
        )
        async with self._session_factory() as database_session:
            row = await database_session.scalar(statement)
            if row is None:
                return None
            return StoredSession.model_validate(row)

    async def recent_messages(self, session_id: UUID, limit: int) -> list[MessageOut]:
        if limit <= 0:
            raise ValueError("limit must be greater than zero")
        statement = (
            select(MessageRow)
            .where(MessageRow.session_id == str(session_id))
            .order_by(MessageRow.position.desc())
            .limit(limit)
        )
        async with self._session_factory() as database_session:
            rows = list((await database_session.scalars(statement)).all())
        return [MessageOut.model_validate(row) for row in reversed(rows)]

    async def append_exchange(
        self,
        session_id: UUID,
        user_text: str,
        assistant_text: str,
        payload: dict[str, Any] | None,
    ) -> None:
        session_key = str(session_id)
        async with (
            self._session_factory() as database_session,
            database_session.begin(),
        ):
            reserved_end = await database_session.scalar(
                update(SessionRow)
                .where(SessionRow.id == session_key)
                .values(
                    next_position=SessionRow.next_position + 2,
                    updated_at=datetime.now(UTC),
                )
                .returning(SessionRow.next_position)
            )
            if reserved_end is None:
                raise SessionNotFoundError(session_key)
            first_position = reserved_end - 2
            database_session.add_all(
                [
                    MessageRow(
                        session_id=session_key,
                        position=first_position,
                        role="user",
                        text=user_text,
                        payload=None,
                    ),
                    MessageRow(
                        session_id=session_key,
                        position=first_position + 1,
                        role="assistant",
                        text=assistant_text,
                        payload=payload,
                    ),
                ]
            )

    async def delete(self, session_id: UUID) -> bool:
        async with (
            self._session_factory() as database_session,
            database_session.begin(),
        ):
            deleted_id = await database_session.scalar(
                delete(SessionRow)
                .where(SessionRow.id == str(session_id))
                .returning(SessionRow.id)
            )
            return deleted_id is not None
