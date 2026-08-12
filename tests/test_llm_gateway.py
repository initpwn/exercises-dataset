"""Contract tests for the provider-neutral chat-completions gateway."""

import json
from typing import ClassVar, Self

import httpx
import pytest
from pydantic import BaseModel, model_validator

from exercise_api.api_models import ChatMessage, RetrievalPlan
from exercise_api.llm_gateway import (
    LLMGateway,
    LLMInvalidResponseError,
    LLMUnavailableError,
)
from exercise_api.prompts import json_repair_message
from tests.fakes import SequenceTransport, assistant, llm_settings


class CountingResponse(BaseModel):
    value: str
    validations: ClassVar[int] = 0

    @model_validator(mode="after")
    def record_validation(self) -> Self:
        type(self).validations += 1
        return self


def messages() -> list[ChatMessage]:
    return [ChatMessage(role="user", content="find curls")]


def test_repair_prompt_accepts_malformed_output_before_validation_error() -> None:
    message = json_repair_message("not-json", "expected an object")

    assert "invalid (expected an object)" in message.content
    assert message.content.endswith("not-json")


def test_repair_prompt_bounds_malformed_output_and_validation_error() -> None:
    message = json_repair_message("A" * 20_000, "B" * 10_000)

    assert message.content.count("A") <= 8_192
    assert message.content.count("B") <= 2_048
    assert "[truncated]" in message.content


@pytest.mark.asyncio
async def test_request_uses_configured_openai_contract() -> None:
    transport = SequenceTransport([assistant('{"intent":"exercise_search"}')])
    gateway = LLMGateway(llm_settings(), transport=transport)

    await gateway.generate_json(messages(), RetrievalPlan)

    request = transport.requests[0]
    assert str(request.url) == "http://llm.test/v1/chat/completions"
    assert request.headers["authorization"] == "Bearer test-secret"
    assert json.loads(request.content) == {
        "model": "test-model",
        "messages": [{"role": "user", "content": "find curls"}],
        "temperature": 0,
        "max_tokens": 2048,
    }
    assert request.extensions["timeout"] == {
        "connect": 1.0,
        "read": 1.0,
        "write": 1.0,
        "pool": 1.0,
    }


@pytest.mark.asyncio
async def test_completion_returns_only_provider_metadata_and_content() -> None:
    response = httpx.Response(
        200,
        json={
            "id": "completion-1",
            "model": "served-model",
            "choices": [{"message": {"role": "assistant", "content": "Try push-ups."}}],
            "usage": {"prompt_tokens": 4, "completion_tokens": 3},
        },
    )
    gateway = LLMGateway(llm_settings(), transport=SequenceTransport([response]))

    completion = await gateway.complete(messages())

    assert completion.model == "served-model"
    assert completion.content == "Try push-ups."
    assert completion.status == 200


@pytest.mark.asyncio
async def test_extracts_markdown_fenced_json() -> None:
    transport = SequenceTransport(
        [assistant('```json\n{"intent":"exercise_search"}\n```')]
    )
    gateway = LLMGateway(llm_settings(), transport=transport)

    result = await gateway.generate_json(messages(), RetrievalPlan)

    assert result.intent == "exercise_search"


@pytest.mark.asyncio
async def test_repairs_malformed_json_once() -> None:
    transport = SequenceTransport(
        [assistant("not-json"), assistant('{"intent":"exercise_search"}')]
    )
    gateway = LLMGateway(llm_settings(), transport=transport)

    result = await gateway.generate_json(messages(), RetrievalPlan)

    assert result.intent == "exercise_search"
    assert transport.calls == 2
    repair_request = json.loads(transport.requests[1].content)
    assert repair_request["messages"][-1]["role"] == "user"
    assert "not-json" in repair_request["messages"][-1]["content"]


@pytest.mark.asyncio
async def test_repairs_typed_invalid_json_once() -> None:
    transport = SequenceTransport(
        [assistant("{}"), assistant('{"intent":"exercise_search"}')]
    )
    gateway = LLMGateway(llm_settings(), transport=transport)

    result = await gateway.generate_json(messages(), RetrievalPlan)

    assert result.intent == "exercise_search"
    assert transport.calls == 2


@pytest.mark.asyncio
async def test_validates_each_provider_response_once() -> None:
    CountingResponse.validations = 0
    transport = SequenceTransport([assistant('{"value":"ok"}')])
    gateway = LLMGateway(llm_settings(), transport=transport)

    result = await gateway.generate_json(messages(), CountingResponse)

    assert result.value == "ok"
    assert CountingResponse.validations == 1


@pytest.mark.asyncio
async def test_second_invalid_response_raises_502_error() -> None:
    transport = SequenceTransport([assistant("bad"), assistant("still bad")])
    gateway = LLMGateway(llm_settings(), transport=transport)

    with pytest.raises(LLMInvalidResponseError):
        await gateway.generate_json(messages(), RetrievalPlan)

    assert transport.calls == 2


@pytest.mark.asyncio
async def test_timeout_maps_to_unavailable() -> None:
    request = httpx.Request("POST", "http://llm.test/v1/chat/completions")
    transport = SequenceTransport([httpx.ReadTimeout("timed out", request=request)])
    gateway = LLMGateway(llm_settings(), transport=transport)

    with pytest.raises(LLMUnavailableError):
        await gateway.generate_json(messages(), RetrievalPlan)


@pytest.mark.asyncio
async def test_connection_error_maps_to_unavailable() -> None:
    request = httpx.Request("POST", "http://llm.test/v1/chat/completions")
    transport = SequenceTransport(
        [httpx.ConnectError("connection refused", request=request)]
    )
    gateway = LLMGateway(llm_settings(), transport=transport)

    with pytest.raises(LLMUnavailableError):
        await gateway.generate_json(messages(), RetrievalPlan)


@pytest.mark.asyncio
async def test_errors_never_include_api_key() -> None:
    transport = SequenceTransport(
        [httpx.Response(503, text="provider rejected test-secret")]
    )
    gateway = LLMGateway(llm_settings(), transport=transport)

    with pytest.raises(LLMUnavailableError) as error:
        await gateway.generate_json(messages(), RetrievalPlan)

    assert "test-secret" not in str(error.value)


@pytest.mark.asyncio
async def test_provider_failure_logging_is_sanitized(
    caplog: pytest.LogCaptureFixture,
) -> None:
    secret = "provider-secret-body"
    transport = SequenceTransport([httpx.Response(503, text=secret)])
    gateway = LLMGateway(llm_settings(), transport=transport)

    with caplog.at_level("WARNING"), pytest.raises(LLMUnavailableError):
        await gateway.generate_json(messages(), RetrievalPlan)

    assert "Model provider request failed" in caplog.text
    assert secret not in caplog.text
