"""Deterministic test doubles shared by API unit tests."""

from collections import deque
from collections.abc import Iterable
from typing import TypeVar

import httpx
from pydantic import BaseModel

from exercise_api.api_models import ChatMessage
from exercise_api.config import LLMSettings

T = TypeVar("T", bound=BaseModel)


def llm_settings() -> LLMSettings:
    return LLMSettings(
        base_url="http://llm.test/v1",
        api_key="test-secret",
        model="test-model",
        timeout_seconds=1,
    )


def assistant(content: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={"choices": [{"message": {"role": "assistant", "content": content}}]},
    )


class SequenceTransport(httpx.AsyncBaseTransport):
    def __init__(self, responses: Iterable[httpx.Response | Exception]) -> None:
        self.responses = deque(responses)
        self.requests: list[httpx.Request] = []
        self.calls = 0

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        self.requests.append(request)
        response = self.responses.popleft()
        if isinstance(response, Exception):
            raise response
        response.request = request
        return response


class FakeLLM:
    def __init__(self) -> None:
        self.responses: deque[dict[str, object]] = deque()
        self.messages: list[list[ChatMessage]] = []

    def queue(self, *responses: dict[str, object]) -> None:
        self.responses.extend(responses)

    async def generate_json(
        self, messages: list[ChatMessage], output_type: type[T]
    ) -> T:
        self.messages.append(messages)
        return output_type.model_validate(self.responses.popleft())
