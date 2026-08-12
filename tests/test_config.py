from pathlib import Path

import pytest
from pydantic import ValidationError

from exercise_api.config import DatabaseSettings, LLMSettings, load_settings


def test_environment_overrides_nested_toml(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('[llm]\nmodel="toml-model"\ntimeout_seconds=60\n', encoding="utf-8")
    settings = load_settings(
        path,
        {
            "EXERCISE_API__LLM__MODEL": "env-model",
            "EXERCISE_API__LLM__TIMEOUT_SECONDS": "15",
        },
    )
    assert settings.llm.model == "env-model"
    assert settings.llm.timeout_seconds == 15


def test_rejects_non_positive_limits(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        "[retrieval]\ncandidate_limit=0\nresult_limit=10\n", encoding="utf-8"
    )
    with pytest.raises(ValidationError):
        load_settings(path, {})


def test_provider_output_token_limit_defaults_and_bounds() -> None:
    assert LLMSettings().api_format == "openai"
    assert LLMSettings().max_output_tokens == 2048
    with pytest.raises(ValidationError):
        LLMSettings(max_output_tokens=127)
    with pytest.raises(ValidationError):
        LLMSettings(max_output_tokens=8193)


def test_sqlite_database_settings_build_url_from_path() -> None:
    settings = DatabaseSettings(backend="sqlite", sqlite_path="./custom.db")

    assert settings.resolved_url == "sqlite:///./custom.db"


def test_postgres_database_settings_build_url_from_credentials() -> None:
    settings = DatabaseSettings(
        backend="postgresql",
        postgres_host="db.example.test",
        postgres_port=5433,
        postgres_database="exercises",
        postgres_user="exercise user",
        postgres_password="p@ss/word",
    )

    assert (
        settings.resolved_url == "postgresql+psycopg://exercise%20user:p%40ss%2Fword@"
        "db.example.test:5433/exercises"
    )


def test_explicit_database_url_overrides_structured_credentials() -> None:
    settings = DatabaseSettings(
        url="sqlite:///direct.db",
        backend="postgresql",
        postgres_database="ignored",
        postgres_user="ignored",
        postgres_password="ignored",
    )

    assert settings.resolved_url == "sqlite:///direct.db"


def test_environment_overrides_database_credentials(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        '[database]\nbackend = "sqlite"\nsqlite_path = "./exercise_api.db"\n',
        encoding="utf-8",
    )

    settings = load_settings(
        path,
        {
            "EXERCISE_API__DATABASE__BACKEND": "postgresql",
            "EXERCISE_API__DATABASE__POSTGRES_HOST": "localhost",
            "EXERCISE_API__DATABASE__POSTGRES_PORT": "5440",
            "EXERCISE_API__DATABASE__POSTGRES_DATABASE": "exercises",
            "EXERCISE_API__DATABASE__POSTGRES_USER": "exercise_user",
            "EXERCISE_API__DATABASE__POSTGRES_PASSWORD": "secret",
        },
    )

    assert (
        settings.database.resolved_url
        == "postgresql+psycopg://exercise_user:secret@localhost:5440/exercises"
    )
