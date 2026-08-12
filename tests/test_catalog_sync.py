import asyncio
import hashlib
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from exercise_api.catalog import (
    CatalogValidationError,
    LoadedCatalog,
    load_catalog,
    sync_catalog,
)
from exercise_api.database import Database, async_database_url
from exercise_api.db_models import CatalogStateRow, ExerciseRow
from tests.factories import catalog_record


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("sqlite:///catalog.db", "sqlite+aiosqlite:///catalog.db"),
        ("sqlite+aiosqlite:///catalog.db", "sqlite+aiosqlite:///catalog.db"),
        (
            "postgresql+psycopg2://user:pass@host/db",
            "postgresql+psycopg://user:pass@host/db",
        ),
        ("postgresql://user:pass@host/db", "postgresql+psycopg://user:pass@host/db"),
    ],
)
def test_async_database_url_normalizes_approved_urls(url: str, expected: str) -> None:
    assert async_database_url(url) == expected


def test_load_catalog_validates_hashes_and_projects_english_steps() -> None:
    path = Path("tests/fixtures/catalog.json")

    loaded = load_catalog(path, Path("data/exercises.schema.json"))

    assert loaded.content_hash == hashlib.sha256(path.read_bytes()).hexdigest()
    assert len(loaded.records) == 1
    assert loaded.records[0].instructions == ["First step", "Second step"]


def test_load_catalog_rejects_schema_violation(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text('[{"id":"not-four-digits"}]', encoding="utf-8")
    with pytest.raises(CatalogValidationError):
        load_catalog(path, Path("data/exercises.schema.json"))


@pytest.mark.asyncio
async def test_sync_inserts_updates_and_deletes_records(tmp_path: Path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    await database.create_schema()
    first = LoadedCatalog(
        "hash-1", [catalog_record("0001", "Curl"), catalog_record("0002", "Squat")]
    )
    assert await sync_catalog(database.session_factory, first) is True
    changed = LoadedCatalog(
        "hash-2",
        [catalog_record("0001", "Strict curl"), catalog_record("0003", "Row")],
    )
    assert await sync_catalog(database.session_factory, changed) is True
    async with database.session_factory() as session:
        rows = (
            await session.scalars(select(ExerciseRow).order_by(ExerciseRow.id))
        ).all()
    assert [(row.id, row.name) for row in rows] == [
        ("0001", "Strict curl"),
        ("0003", "Row"),
    ]
    assert await sync_catalog(database.session_factory, changed) is False
    await database.dispose()


@pytest.mark.asyncio
async def test_sync_rolls_back_all_catalog_changes_when_commit_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'rollback.db'}")
    await database.create_schema()
    original = LoadedCatalog(
        "hash-1", [catalog_record("0001", "Curl"), catalog_record("0002", "Squat")]
    )
    await sync_catalog(database.session_factory, original)

    async def fail_after_flush(session: AsyncSession) -> None:
        await session.flush()
        raise RuntimeError("injected commit failure")

    monkeypatch.setattr(AsyncSession, "commit", fail_after_flush)
    changed = LoadedCatalog(
        "hash-2",
        [catalog_record("0001", "Strict curl"), catalog_record("0003", "Row")],
    )

    with pytest.raises(RuntimeError, match="injected commit failure"):
        await sync_catalog(database.session_factory, changed)

    async with database.session_factory() as session:
        rows = (
            await session.scalars(select(ExerciseRow).order_by(ExerciseRow.id))
        ).all()
        state = await session.get(CatalogStateRow, "exercise-catalog")
    assert [(row.id, row.name) for row in rows] == [
        ("0001", "Curl"),
        ("0002", "Squat"),
    ]
    assert state is not None
    assert state.content_hash == "hash-1"
    await database.dispose()


@pytest.mark.asyncio
async def test_concurrent_initial_sync_serializes_on_catalog_state(
    tmp_path: Path,
) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'concurrent.db'}")
    await database.create_schema()
    catalog = LoadedCatalog(
        "shared-hash",
        [catalog_record(f"{index:04d}", f"Exercise {index}") for index in range(100)],
    )

    results = await asyncio.gather(
        sync_catalog(database.session_factory, catalog),
        sync_catalog(database.session_factory, catalog),
        return_exceptions=True,
    )

    assert sorted(results, key=str) == [False, True]
    async with database.session_factory() as session:
        rows = (await session.scalars(select(ExerciseRow))).all()
    assert len(rows) == 100
    await database.dispose()
