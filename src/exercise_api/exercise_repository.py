"""Database queries for the synchronized exercise catalog."""

from __future__ import annotations

import builtins
import random
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm.attributes import InstrumentedAttribute

from exercise_api.api_models import ExerciseOut, ExercisePage
from exercise_api.db_models import ExerciseRow

DistinctField = Literal["category", "body_part", "equipment"]
ConstraintField = Literal[
    "category", "body_part", "equipment", "muscle_group", "target"
]


@dataclass(frozen=True)
class ExerciseFilters:
    """Filtering and pagination inputs for an exercise listing."""

    page: int = 1
    limit: int = 20
    category: str | None = None
    body_part: str | None = None
    equipment: str | None = None
    muscle_group: str | None = None
    target: str | None = None


def _literal_contains(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


class ExerciseRepository:
    """Read-only access to exercise catalog rows."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def list(self, filters: ExerciseFilters) -> ExercisePage:
        conditions = []
        for field in (
            "category",
            "body_part",
            "equipment",
            "muscle_group",
            "target",
        ):
            value = getattr(filters, field)
            normalized_value = value.strip() if value is not None else ""
            if normalized_value:
                column = getattr(ExerciseRow, field)
                conditions.append(
                    column.ilike(_literal_contains(normalized_value), escape="\\")
                )

        async with self._session_factory() as session:
            total = await session.scalar(
                select(func.count()).select_from(ExerciseRow).where(*conditions)
            )
            rows = (
                await session.scalars(
                    select(ExerciseRow)
                    .where(*conditions)
                    .order_by(ExerciseRow.id)
                    .offset((filters.page - 1) * filters.limit)
                    .limit(filters.limit)
                )
            ).all()

        total_count = int(total or 0)
        return ExercisePage(
            data=[ExerciseOut.model_validate(row) for row in rows],
            page=filters.page,
            limit=filters.limit,
            total=total_count,
            total_pages=(total_count + filters.limit - 1) // filters.limit,
        )

    async def random(self) -> ExerciseOut | None:
        async with self._session_factory() as session:
            total = await session.scalar(select(func.count()).select_from(ExerciseRow))
            if not total:
                return None
            row = await session.scalar(
                select(ExerciseRow)
                .order_by(ExerciseRow.id)
                .offset(random.randrange(total))
                .limit(1)
            )
        return ExerciseOut.model_validate(row) if row is not None else None

    async def matching_exact(
        self, filters: ExerciseFilters
    ) -> builtins.list[ExerciseOut]:
        """Return rows satisfying every normalized constraint exactly."""
        conditions = []
        for field in (
            "category",
            "body_part",
            "equipment",
            "muscle_group",
            "target",
        ):
            value = getattr(filters, field)
            normalized_value = value.strip() if value is not None else ""
            if normalized_value:
                column = getattr(ExerciseRow, field)
                conditions.append(func.lower(column) == normalized_value.lower())

        async with self._session_factory() as session:
            rows = (
                await session.scalars(
                    select(ExerciseRow).where(*conditions).order_by(ExerciseRow.id)
                )
            ).all()
        return [ExerciseOut.model_validate(row) for row in rows]

    async def distinct(self, field: DistinctField) -> builtins.list[str]:
        columns: dict[DistinctField, InstrumentedAttribute[str]] = {
            "category": ExerciseRow.category,
            "body_part": ExerciseRow.body_part,
            "equipment": ExerciseRow.equipment,
        }
        async with self._session_factory() as session:
            values = (await session.scalars(select(columns[field]))).all()

        ordered = sorted(values, key=lambda value: (value.casefold(), value))
        normalized: dict[str, str] = {}
        for value in ordered:
            normalized.setdefault(value.casefold(), value)
        return list(normalized.values())

    async def constraint_values(
        self,
    ) -> dict[ConstraintField, builtins.list[str]]:
        """Return canonical catalog vocabularies for every hard-filter field."""
        columns: dict[ConstraintField, InstrumentedAttribute[str]] = {
            "category": ExerciseRow.category,
            "body_part": ExerciseRow.body_part,
            "equipment": ExerciseRow.equipment,
            "muscle_group": ExerciseRow.muscle_group,
            "target": ExerciseRow.target,
        }
        result: dict[ConstraintField, builtins.list[str]] = {}
        async with self._session_factory() as session:
            for field, column in columns.items():
                values = (await session.scalars(select(column))).all()
                ordered = sorted(values, key=lambda value: (value.casefold(), value))
                normalized: dict[str, str] = {}
                for value in ordered:
                    normalized.setdefault(value.casefold(), value)
                result[field] = list(normalized.values())
        return result

    async def by_ids(self, ids: Sequence[str]) -> dict[str, ExerciseOut]:
        if not ids:
            return {}
        async with self._session_factory() as session:
            rows = (
                await session.scalars(
                    select(ExerciseRow)
                    .where(ExerciseRow.id.in_(ids))
                    .order_by(ExerciseRow.id)
                )
            ).all()
        return {row.id: ExerciseOut.model_validate(row) for row in rows}
