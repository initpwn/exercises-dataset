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


def test_session_response_schema_remains_backward_compatible() -> None:
    schema = app.openapi()["components"]["schemas"]["SessionOut"]

    assert set(schema["properties"]) == {"id", "created_at", "messages"}
    assert set(schema["required"]) == {"id", "created_at", "messages"}
