"""SQLAlchemy rows persisted by the exercise API."""

from datetime import datetime

from sqlalchemy import JSON, DateTime, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


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
