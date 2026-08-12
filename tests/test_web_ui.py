"""Browser UI smoke contracts."""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_app_ui_is_served_with_api_contract_hooks(client: AsyncClient) -> None:
    response = await client.get("/app")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    html = response.text
    assert "localStorage" in html
    assert "/v1/chat" in html
    assert "/exercises" in html
    assert "/categories" in html
    assert "/body-parts" in html
    assert "/equipment" in html
    assert "exerciseApiUi.sessionId" in html


@pytest.mark.asyncio
async def test_exercise_media_is_served_for_web_ui(client: AsyncClient) -> None:
    response = await client.get("/images/0001-2gPfomN.jpg")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
