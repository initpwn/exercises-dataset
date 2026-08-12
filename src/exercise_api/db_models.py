"""SQLAlchemy rows persisted by the exercise API."""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class ExerciseRow(Base):
    __tablename__ = "exercises"

    id: Mapped[str] = mapped_column(String(4), primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    category: Mapped[str] = mapped_column(String, nullable=False, index=True)
    body_part: Mapped[str] = mapped_column(String, nullable=False, index=True)
    equipment: Mapped[str] = mapped_column(String, nullable=False, index=True)
    muscle_group: Mapped[str] = mapped_column(String, nullable=False, index=True)
    secondary_muscles: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    target: Mapped[str] = mapped_column(String, nullable=False, index=True)
    instructions: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    media_id: Mapped[str] = mapped_column(String, nullable=False)
    image: Mapped[str] = mapped_column(String, nullable=False)
    gif_url: Mapped[str] = mapped_column(String, nullable=False)
    attribution: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CatalogStateRow(Base):
    __tablename__ = "catalog_state"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class SessionRow(Base):
    __tablename__ = "conversation_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    messages: Mapped[list["MessageRow"]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="MessageRow.position",
    )


class MessageRow(Base):
    __tablename__ = "conversation_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("conversation_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(String(9), nullable=False)
    text: Mapped[str] = mapped_column(String, nullable=False)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    session: Mapped[SessionRow] = relationship(back_populates="messages")

    __table_args__ = (
        CheckConstraint("role IN ('user', 'assistant')", name="valid_message_role"),
        # Positions are stable ordering keys within a conversation.
        UniqueConstraint(
            "session_id", "position", name="uq_message_session_position"
        ),
        {"sqlite_autoincrement": True},
    )
