"""Typed configuration loading for the exercise API."""

import os
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote

from pydantic import BaseModel, Field


class DatabaseSettings(BaseModel):
    url: str | None = None
    backend: Literal["sqlite", "postgresql"] = "sqlite"
    sqlite_path: str = "./exercise_api.db"
    postgres_host: str = "localhost"
    postgres_port: int = Field(default=5432, gt=0, le=65535)
    postgres_database: str = "exercises"
    postgres_user: str = "exercise_user"
    postgres_password: str = "password"

    @property
    def resolved_url(self) -> str:
        """Return explicit URL or assemble one from structured database settings."""
        if self.url:
            return self.url
        if self.backend == "sqlite":
            return f"sqlite:///{self.sqlite_path}"
        user = quote(self.postgres_user, safe="")
        password = quote(self.postgres_password, safe="")
        database = quote(self.postgres_database, safe="")
        return (
            f"postgresql+psycopg://{user}:{password}@"
            f"{self.postgres_host}:{self.postgres_port}/{database}"
        )


class LLMSettings(BaseModel):
    api_format: Literal["openai", "lmstudio"] = "openai"
    base_url: str = "http://localhost:11434/v1"
    api_key: str = "ollama"
    model: str = "your-model"
    timeout_seconds: int = Field(default=60, gt=0)
    max_output_tokens: int = Field(default=2048, ge=128, le=8192)


class RetrievalSettings(BaseModel):
    candidate_limit: int = Field(default=30, gt=0)
    result_limit: int = Field(default=10, gt=0)


class SessionSettings(BaseModel):
    history_message_limit: int = Field(default=20, gt=0)


class Settings(BaseModel):
    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    llm: LLMSettings = Field(default_factory=LLMSettings)
    retrieval: RetrievalSettings = Field(default_factory=RetrievalSettings)
    sessions: SessionSettings = Field(default_factory=SessionSettings)


def load_settings(
    path: Path = Path("config.toml"), environ: Mapping[str, str] | None = None
) -> Settings:
    """Load TOML settings and apply EXERCISE_API__ nested environment overrides."""
    with path.open("rb") as config_file:
        data: dict[str, Any] = tomllib.load(config_file)

    environment = os.environ if environ is None else environ
    prefix = "EXERCISE_API__"
    for name, value in environment.items():
        if not name.startswith(prefix):
            continue

        section, key = name.removeprefix(prefix).split("__", maxsplit=1)
        data.setdefault(section.lower(), {})[key.lower()] = value

    return Settings.model_validate(data)
