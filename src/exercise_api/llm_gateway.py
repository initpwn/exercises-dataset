"""OpenAI-compatible, provider-neutral structured-output gateway."""

import json
import re
from typing import TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from exercise_api.api_models import ChatMessage
from exercise_api.config import LLMSettings
from exercise_api.prompts import json_repair_message

T = TypeVar("T", bound=BaseModel)


class LLMUnavailableError(Exception):
    """Raised when the configured model endpoint cannot serve a request."""


class LLMInvalidResponseError(Exception):
    """Raised when the model twice returns invalid structured output."""


class _InvalidStructuredResponse(Exception):
    def __init__(self, malformed_output: str, validation_error: str) -> None:
        self.malformed_output = malformed_output
        self.validation_error = validation_error


class LLMGateway:
    """Generate validated JSON through the ordinary chat-completions contract."""

    def __init__(
        self,
        settings: LLMSettings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._settings = settings
        self._transport = transport

    async def generate_json(
        self, messages: list[ChatMessage], output_type: type[T]
    ) -> T:
        request_messages = list(messages)
        async with httpx.AsyncClient(
            transport=self._transport,
            timeout=self._settings.timeout_seconds,
        ) as client:
            try:
                return await self._generate_once(client, request_messages, output_type)
            except _InvalidStructuredResponse as invalid:
                request_messages.append(
                    json_repair_message(
                        invalid.malformed_output, invalid.validation_error
                    )
                )

            try:
                return await self._generate_once(client, request_messages, output_type)
            except _InvalidStructuredResponse:
                pass

        raise LLMInvalidResponseError("Model returned invalid structured output")

    async def _generate_once(
        self,
        client: httpx.AsyncClient,
        messages: list[ChatMessage],
        output_type: type[T],
    ) -> T:
        try:
            response = await client.post(
                f"{self._settings.base_url.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {self._settings.api_key}"},
                json={
                    "model": self._settings.model,
                    "messages": [message.model_dump() for message in messages],
                    "temperature": 0,
                },
            )
            response.raise_for_status()
        except (httpx.TimeoutException, httpx.ConnectError, httpx.HTTPStatusError):
            raise LLMUnavailableError("Model service is unavailable") from None

        try:
            content = response.json()["choices"][0]["message"]["content"]
            if not isinstance(content, str):
                raise TypeError("message content must be text")
            decoded = json.loads(_extract_json(content))
            return output_type.model_validate(decoded)
        except (
            json.JSONDecodeError,
            ValidationError,
            KeyError,
            IndexError,
            TypeError,
        ) as exc:
            raise _InvalidStructuredResponse(
                _response_text(response), str(exc)
            ) from None


def _extract_json(content: str) -> str:
    fenced = re.fullmatch(
        r"\s*```(?:json)?\s*(.*?)\s*```\s*", content, flags=re.IGNORECASE | re.DOTALL
    )
    return fenced.group(1) if fenced else content


def _response_text(response: httpx.Response) -> str:
    try:
        content = response.json()["choices"][0]["message"]["content"]
    except (json.JSONDecodeError, KeyError, IndexError, TypeError):
        return response.text
    return content if isinstance(content, str) else response.text
