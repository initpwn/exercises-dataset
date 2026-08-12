from pathlib import Path

import pytest
from pydantic import ValidationError

from exercise_api.config import load_settings


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
