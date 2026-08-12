"""Opt-in smoke check for the configured OpenAI-compatible provider."""

import argparse
import asyncio
import json
import sys
from pathlib import Path

from exercise_api.api_models import ChatMessage
from exercise_api.config import load_settings
from exercise_api.llm_gateway import LLMGateway


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Send one message to the configured chat-completions provider."
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config.toml"),
        help="TOML configuration file (default: config.toml)",
    )
    parser.add_argument(
        "--message",
        required=True,
        help="User message sent to the configured model",
    )
    return parser


async def _smoke(config: Path, message: str) -> dict[str, str | int]:
    settings = load_settings(config)
    completion = await LLMGateway(settings.llm).complete(
        [ChatMessage(role="user", content=message)]
    )
    return completion.model_dump()


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = asyncio.run(_smoke(args.config, args.message))
    except Exception:  # noqa: BLE001 - command boundary must not disclose secrets.
        print("LLM smoke test failed", file=sys.stderr)
        return 1

    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
