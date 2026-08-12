"""Catalog validation, projection, and transactional database synchronization."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from jsonschema import (  # type: ignore[import-untyped]
    FormatChecker,
    SchemaError,
    ValidationError,
    validate,
)
from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from exercise_api.db_models import CatalogStateRow, ExerciseRow

CATALOG_STATE_KEY = "exercise-catalog"


class CatalogValidationError(ValueError):
    """Raised when catalog JSON does not satisfy its schema or projection model."""


class CatalogRecord(BaseModel):
    """English-only database projection of a source catalog record."""

    id: str
    name: str
    category: str
    body_part: str
    equipment: str
    muscle_group: str
    secondary_muscles: list[str]
    target: str
    instructions: list[str]
    media_id: str
    image: str
    gif_url: str
    attribution: str
    created_at: datetime


@dataclass(frozen=True)
class LoadedCatalog:
    content_hash: str
    records: list[CatalogRecord]


def load_catalog(data_path: Path, schema_path: Path) -> LoadedCatalog:
    """Validate source JSON and project its English instruction steps."""
    raw_bytes = data_path.read_bytes()
    try:
        source = json.loads(raw_bytes)
        schema = json.loads(schema_path.read_bytes())
        validate(source, schema, format_checker=FormatChecker())
        records = [
            CatalogRecord.model_validate(
                {
                    "id": item["id"],
                    "name": item["name"],
                    "category": item["category"],
                    "body_part": item["body_part"],
                    "equipment": item["equipment"],
                    "muscle_group": item["muscle_group"],
                    "secondary_muscles": item["secondary_muscles"],
                    "target": item["target"],
                    "instructions": item["instruction_steps"]["en"],
                    "media_id": item["media_id"],
                    "image": item["image"],
                    "gif_url": item["gif_url"],
                    "attribution": item["attribution"],
                    "created_at": item["created_at"],
                }
            )
            for item in source
        ]
    except (json.JSONDecodeError, SchemaError, ValidationError, PydanticValidationError) as error:
        raise CatalogValidationError(str(error)) from error

    return LoadedCatalog(hashlib.sha256(raw_bytes).hexdigest(), records)


async def sync_catalog(
    session_factory: async_sessionmaker[AsyncSession], catalog: LoadedCatalog
) -> bool:
    """Synchronize all catalog rows atomically, returning whether data changed."""
    async with session_factory() as session:
        try:
            state = await session.scalar(
                select(CatalogStateRow)
                .where(CatalogStateRow.key == CATALOG_STATE_KEY)
                .with_for_update()
            )
            if state is not None and state.content_hash == catalog.content_hash:
                return False

            existing = {
                row.id: row for row in (await session.scalars(select(ExerciseRow))).all()
            }
            current_ids: set[str] = set()
            for record in catalog.records:
                current_ids.add(record.id)
                values = record.model_dump()
                row = existing.get(record.id)
                if row is None:
                    session.add(ExerciseRow(**values))
                else:
                    for field, value in values.items():
                        setattr(row, field, value)

            for stale_id in existing.keys() - current_ids:
                await session.delete(existing[stale_id])

            if state is None:
                session.add(
                    CatalogStateRow(key=CATALOG_STATE_KEY, content_hash=catalog.content_hash)
                )
            else:
                state.content_hash = catalog.content_hash

            await session.commit()
        except Exception:
            await session.rollback()
            raise

    return True
