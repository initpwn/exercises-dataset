"""Typed configuration loading for the exercise API."""

import os
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class DatabaseSettings(BaseModel):
    url: str = "sqlite:///./exercise_api.db"


class LLMSettings(BaseModel):
    base_url: str = "http://localhost:11434/v1"
    api_key: str = "ollama"
    model: str = "your-model"
    timeout_seconds: int = Field(default=60, gt=0)


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
