"""Startup readiness and provider-failure isolation contracts."""

from collections.abc import AsyncIterator
from pathlib import Path
from typing import TypeVar, cast

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel

from exercise_api.api_models import ChatMessage
from exercise_api.config import DatabaseSettings, Settings
from exercise_api.llm_gateway import LLMUnavailableError
from exercise_api.main import create_app

T = TypeVar("T", bound=BaseModel)


class UnavailableLLM:
    async def generate_json(
        self, messages: list[ChatMessage], output_type: type[T]
    ) -> T:
        raise LLMUnavailableError("provider detail must not escape")


async def _started_client(
    tmp_path: Path, catalog_path: Path, *, unavailable_llm: bool = False
) -> AsyncIterator[AsyncClient]:
    settings = Settings(
        database=DatabaseSettings(url=f"sqlite:///{tmp_path / 'readiness.db'}")
    )
    app = create_app(settings)
    app.state.catalog_path = catalog_path
    app.state.schema_path = Path("data/exercises.schema.json")
    if unavailable_llm:
        app.state.llm_gateway = UnavailableLLM()

    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
    ):
        yield client


async def _health_after_startup(app: FastAPI) -> tuple[int, dict[str, str]]:
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
    ):
        response = await client.get("/health")
        return response.status_code, cast(dict[str, str], response.json())


@pytest.fixture
async def degraded_client(tmp_path: Path) -> AsyncIterator[AsyncClient]:
    invalid_catalog = tmp_path / "invalid-catalog.json"
    invalid_catalog.write_text('[{"id":"invalid"}]', encoding="utf-8")
    async for client in _started_client(tmp_path, invalid_catalog):
        yield client


@pytest.fixture
async def ready_client(tmp_path: Path) -> AsyncIterator[AsyncClient]:
    async for client in _started_client(
        tmp_path, Path("tests/fixtures/catalog.json"), unavailable_llm=True
    ):
        yield client


@pytest.mark.asyncio
async def test_invalid_catalog_exposes_degraded_health_only(
    degraded_client: AsyncClient,
) -> None:
    health = await degraded_client.get("/health")
    assert health.status_code == 503
    assert health.json()["catalog"] == "failed"
    assert (await degraded_client.get("/exercises")).status_code == 503
    assert (await degraded_client.post("/v1/sessions")).status_code == 503
    assert (
        await degraded_client.post("/v1/chat", json={"message": "find curls"})
    ).status_code == 503


@pytest.mark.asyncio
async def test_llm_outage_does_not_break_direct_catalog(
    ready_client: AsyncClient,
) -> None:
    assert (await ready_client.get("/exercises")).status_code == 200
    assert (
        await ready_client.post("/v1/chat", json={"message": "find curls"})
    ).status_code == 503
    assert (await ready_client.get("/health")).status_code == 200


def test_module_exports_deferred_startup_app() -> None:
    from exercise_api import main

    assert isinstance(getattr(main, "app", None), FastAPI)
    assert main.app.state.settings is None
    assert main.app.state.database is None


@pytest.mark.asyncio
async def test_settings_failure_keeps_health_callable(tmp_path: Path) -> None:
    failed_app = create_app(settings_path=tmp_path / "missing.toml")

    status_code, body = await _health_after_startup(failed_app)

    assert status_code == 503
    assert body == {
        "status": "degraded",
        "database": "failed",
        "catalog": "failed",
    }


@pytest.mark.asyncio
async def test_database_construction_failure_keeps_health_callable() -> None:
    settings = Settings(database=DatabaseSettings(url="not-a-database-url"))
    failed_app = create_app(settings)

    status_code, body = await _health_after_startup(failed_app)

    assert status_code == 503
    assert body["database"] == "failed"
    assert body["catalog"] == "failed"


@pytest.mark.asyncio
async def test_disabled_lifespan_defaults_to_degraded() -> None:
    disabled_app = create_app(Settings(), lifespan_enabled=False)
    async with AsyncClient(
        transport=ASGITransport(app=disabled_app), base_url="http://test"
    ) as client:
        health = await client.get("/health")
        direct = await client.get("/exercises")

    assert health.status_code == 503
    assert direct.status_code == 503
