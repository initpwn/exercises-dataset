"""Contract tests for the direct exercise catalog API."""

from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from exercise_api.catalog import LoadedCatalog, sync_catalog
from exercise_api.config import DatabaseSettings, Settings
from exercise_api.database import Database
from exercise_api.dependencies import get_exercise_repository
from exercise_api.exercise_repository import ExerciseRepository
from exercise_api.main import create_app
from tests.factories import catalog_record


@pytest.mark.asyncio
async def test_list_filters_and_paginates_seeded_catalog(
    client: AsyncClient,
) -> None:
    response = await client.get(
        "/exercises",
        params={"equipment": "DUMB", "body_part": "hes", "page": 1, "limit": 1},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["page"] == 1
    assert body["limit"] == 1
    assert body["total"] == 2
    assert body["totalPages"] == 2
    assert body["data"][0]["instructions"] == ["First step", "Second step"]
    assert body["data"][0]["attribution"].startswith("© Gym visual")


@pytest.mark.asyncio
async def test_direct_value_and_random_endpoints(client: AsyncClient) -> None:
    assert (await client.get("/categories")).json() == ["chest", "upper arms"]
    assert (await client.get("/body-parts")).json() == ["chest", "upper arms"]
    assert (await client.get("/equipment")).json() == ["body weight", "dumbbell"]
    random_response = await client.get("/exercises/random")
    assert random_response.status_code == 200
    assert random_response.json()["id"] in {"0001", "0002", "0003"}


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("category", "hes"),
        ("body_part", "CHE"),
        ("equipment", "DUMB"),
        ("muscle_group", "tric"),
        ("target", "PECT"),
    ],
)
@pytest.mark.asyncio
async def test_each_filter_is_case_insensitive_partial_match(
    client: AsyncClient,
    field: str,
    value: str,
) -> None:
    body = (await client.get("/exercises", params={field: value})).json()
    assert body["total"] == 2


@pytest.mark.asyncio
async def test_defaults_to_page_one_limit_twenty(client: AsyncClient) -> None:
    body = (await client.get("/exercises")).json()
    assert body["page"] == 1
    assert body["limit"] == 20


@pytest.mark.asyncio
async def test_limit_over_one_hundred_is_422(client: AsyncClient) -> None:
    assert (await client.get("/exercises", params={"limit": 101})).status_code == 422


@pytest.mark.asyncio
async def test_page_beyond_end_has_empty_data_and_preserves_total(
    client: AsyncClient,
) -> None:
    body = (await client.get("/exercises", params={"page": 2})).json()
    assert body["data"] == []
    assert body["total"] == 3


@pytest.mark.asyncio
async def test_zero_matches_has_zero_total_pages(client: AsyncClient) -> None:
    body = (await client.get("/exercises", params={"category": "missing"})).json()
    assert body["total"] == 0
    assert body["totalPages"] == 0


@pytest.mark.asyncio
async def test_filters_use_and_semantics(client: AsyncClient) -> None:
    body = (
        await client.get(
            "/exercises",
            params={"category": "chest", "equipment": "dumb", "target": "pect"},
        )
    ).json()
    assert body["data"]
    assert all(
        "chest" in row["category"].lower()
        and "dumb" in row["equipment"].lower()
        and "pect" in row["target"].lower()
        for row in body["data"]
    )


@pytest.mark.asyncio
async def test_percent_and_underscore_are_literal(
    client: AsyncClient, database: Database
) -> None:
    await sync_catalog(
        database.session_factory,
        LoadedCatalog(
            "api-fixture-with-literal-wildcards",
            [
                catalog_record("0001", "Dumbbell bench press"),
                catalog_record("0002", "Dumbbell fly"),
                catalog_record(
                    "0003",
                    "Chin up",
                    category="upper arms",
                    body_part="upper arms",
                    equipment="body weight",
                    muscle_group="biceps",
                    target="biceps",
                ),
                catalog_record("0004", "Band curl", equipment="band_100%"),
                catalog_record("0005", "Wildcard decoy", equipment="bandX1000"),
            ],
        ),
    )
    body = (await client.get("/exercises", params={"equipment": "_100%"})).json()
    assert body["total"] == 1
    assert [row["id"] for row in body["data"]] == ["0004"]


@pytest.mark.asyncio
async def test_results_are_ordered_by_id(client: AsyncClient) -> None:
    body = (await client.get("/exercises")).json()
    assert [row["id"] for row in body["data"]] == ["0001", "0002", "0003"]


@pytest.mark.asyncio
async def test_random_empty_catalog_is_404(tmp_path: Path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'empty.db'}")
    await database.create_schema()
    settings = Settings(database=DatabaseSettings(url="sqlite:///unused.db"))
    app = create_app(settings, lifespan_enabled=False)
    app.dependency_overrides[get_exercise_repository] = lambda: ExerciseRepository(
        database.session_factory
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as empty_client:
        response = await empty_client.get("/exercises/random")
    await database.dispose()
    assert response.status_code == 404
