"""Executable contract tests for the opt-in live-provider smoke command."""

import json
import os
import subprocess
import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest


class _ProviderHandler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        body = json.dumps(
            {
                "id": "completion-1",
                "model": "served-model",
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "Try push-ups.",
                        }
                    }
                ],
            }
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


@contextmanager
def _provider_url() -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _ProviderHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        yield f"http://{host}:{port}/v1"
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def _write_config(path: Path, base_url: str) -> None:
    path.write_text(
        "\n".join(
            [
                "[llm]",
                f'base_url = "{base_url}"',
                'api_key = "super-secret-key"',
                'model = "configured-model"',
                "timeout_seconds = 1",
            ]
        ),
        encoding="utf-8",
    )


def _smoke_subprocess_environment() -> dict[str, str]:
    return {
        name: value
        for name, value in os.environ.items()
        if not name.upper().startswith("EXERCISE_API__")
    }


def test_smoke_command_ignores_inherited_exercise_api_overrides(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / "config.toml"
    monkeypatch.setenv(
        "EXERCISE_API__LLM__BASE_URL", "http://127.0.0.1:1/hostile-provider"
    )
    with _provider_url() as base_url:
        _write_config(config, base_url)
        result = subprocess.run(
            [
                sys.executable,
                "scripts/smoke_llm.py",
                "--config",
                str(config),
                "--message",
                "Find body-weight chest exercises",
            ],
            capture_output=True,
            check=False,
            env=_smoke_subprocess_environment(),
            text=True,
        )

    assert result.returncode == 0
    assert json.loads(result.stdout) == {
        "model": "served-model",
        "content": "Try push-ups.",
        "status": 200,
    }
    assert result.stderr == ""
    assert "super-secret-key" not in result.stdout


def test_smoke_command_fails_without_disclosing_api_key(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    _write_config(config, "http://127.0.0.1:1/v1")

    result = subprocess.run(
        [
            sys.executable,
            "scripts/smoke_llm.py",
            "--config",
            str(config),
            "--message",
            "hello",
        ],
        capture_output=True,
        check=False,
        env=_smoke_subprocess_environment(),
        text=True,
    )

    assert result.returncode != 0
    assert result.stdout == ""
    assert "super-secret-key" not in result.stderr
