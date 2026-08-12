"""Public OpenAPI surface contract."""

from exercise_api.main import app


def test_openapi_exposes_only_the_supported_paths() -> None:
    assert sorted(app.openapi()["paths"]) == [
        "/body-parts",
        "/categories",
        "/equipment",
        "/exercises",
        "/exercises/random",
        "/health",
        "/v1/chat",
        "/v1/sessions",
        "/v1/sessions/{id}",
    ]
