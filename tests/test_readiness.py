"""Startup readiness and provider-failure isolation contracts."""

from collections.abc import AsyncIterator
from pathlib import Path
from typing import TypeVar

import pytest
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


@pytest.mark.asyncio
async def test_llm_outage_does_not_break_direct_catalog(
    ready_client: AsyncClient,
) -> None:
    assert (await ready_client.get("/exercises")).status_code == 200
    assert (
        await ready_client.post("/v1/chat", json={"message": "find curls"})
    ).status_code == 503
    assert (await ready_client.get("/health")).status_code == 200
