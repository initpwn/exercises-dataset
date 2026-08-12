"""Provider-neutral structured-output gateway."""

import json
import logging
import re
from typing import TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from exercise_api.api_models import ChatMessage, RetrievalPlan
from exercise_api.config import LLMSettings
from exercise_api.prompts import json_repair_message

T = TypeVar("T", bound=BaseModel)
logger = logging.getLogger(__name__)


class LLMUnavailableError(Exception):
    """Raised when the configured model endpoint cannot serve a request."""


class LLMInvalidResponseError(Exception):
    """Raised when the model returns an invalid ordinary or structured response."""


class LLMCompletion(BaseModel):
    """Minimal provider response exposed by the opt-in smoke command."""

    model: str
    content: str
    status: int


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

    async def complete(self, messages: list[ChatMessage]) -> LLMCompletion:
        """Return one ordinary chat completion without structured validation."""
        async with httpx.AsyncClient(
            transport=self._transport,
            timeout=self._settings.timeout_seconds,
        ) as client:
            response = await self._post_completion(client, messages)

        try:
            payload = response.json()
            model, content = _completion_fields(
                payload, self._settings.api_format, self._settings.model
            )
            if not isinstance(model, str) or not isinstance(content, str):
                raise TypeError("completion fields must be text")
        except (json.JSONDecodeError, KeyError, IndexError, TypeError, AttributeError):
            raise LLMInvalidResponseError(
                "Model returned an invalid chat completion"
            ) from None

        return LLMCompletion(model=model, content=content, status=response.status_code)

    async def _post_completion(
        self, client: httpx.AsyncClient, messages: list[ChatMessage]
    ) -> httpx.Response:
        try:
            if self._settings.api_format == "lmstudio":
                system = [m.content for m in messages if m.role == "system"]
                non_system = [m for m in messages if m.role != "system"]
                body = {
                    "model": self._settings.model,
                    "input": "\n\n".join(
                        f"{message.role.capitalize()}: {message.content}"
                        for message in non_system
                    ),
                    "temperature": 0,
                    "max_output_tokens": self._settings.max_output_tokens,
                    "store": False,
                }
                if system:
                    body["system_prompt"] = "\n\n".join(system)
                url = f"{self._settings.base_url.rstrip('/')}/chat"
            else:
                body = {
                    "model": self._settings.model,
                    "messages": [message.model_dump() for message in messages],
                    "temperature": 0,
                    "max_tokens": self._settings.max_output_tokens,
                }
                url = f"{self._settings.base_url.rstrip('/')}/chat/completions"
            headers = {}
            if self._settings.api_key:
                headers["Authorization"] = f"Bearer {self._settings.api_key}"
            response = await client.post(url, headers=headers, json=body)
            response.raise_for_status()
            return response
        except (httpx.RequestError, httpx.HTTPStatusError) as exc:
            logger.warning("Model provider request failed (%s)", type(exc).__name__)
            raise LLMUnavailableError("Model service is unavailable") from None

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
                logger.warning(
                    "Model returned invalid structured output; repairing (%s)",
                    invalid.validation_error,
                )
                request_messages.append(
                    json_repair_message(
                        invalid.malformed_output, invalid.validation_error
                    )
                )

            try:
                return await self._generate_once(client, request_messages, output_type)
            except _InvalidStructuredResponse as invalid:
                logger.warning(
                    "Model structured-output repair failed (%s)",
                    invalid.validation_error,
                )

        raise LLMInvalidResponseError("Model returned invalid structured output")

    async def _generate_once(
        self,
        client: httpx.AsyncClient,
        messages: list[ChatMessage],
        output_type: type[T],
    ) -> T:
        response = await self._post_completion(client, messages)

        try:
            content = _completion_fields(
                response.json(), self._settings.api_format, self._settings.model
            )[1]
            decoded = json.loads(_extract_json(content))
            decoded = _normalize_structured_payload(decoded, output_type)
            return output_type.model_validate(decoded)
        except (
            json.JSONDecodeError,
            ValidationError,
            KeyError,
            IndexError,
            TypeError,
        ) as exc:
            raise _InvalidStructuredResponse(
                _response_text(
                    response, self._settings.api_format, self._settings.model
                ),
                str(exc),
            ) from None


def _extract_json(content: str) -> str:
    fenced = re.fullmatch(
        r"\s*```(?:json)?\s*(.*?)\s*```\s*", content, flags=re.IGNORECASE | re.DOTALL
    )
    if fenced:
        return fenced.group(1)
    decoder = json.JSONDecoder()
    for index, character in enumerate(content):
        if character not in "[{":
            continue
        try:
            _, end = decoder.raw_decode(content[index:])
        except json.JSONDecodeError:
            continue
        return content[index : index + end]
    return content


def _normalize_structured_payload(payload: object, output_type: type[T]) -> object:
    """Flatten the common semantic wrapper emitted by smaller planner models."""
    if output_type is not RetrievalPlan or not isinstance(payload, dict):
        return payload
    normalized = dict(payload)
    if "intent" not in payload and "classification" in payload:
        constraints = payload.get("constraints")
        preferences = payload.get("workout_preferences")
        normalized = {
            key: value
            for key, value in payload.items()
            if key not in {"classification", "constraints", "workout_preferences"}
        }
        normalized["intent"] = payload["classification"]
        if isinstance(constraints, dict):
            normalized.update(constraints)
        if isinstance(preferences, dict):
            normalized.update(preferences)
    for key in ("category", "body_part", "equipment", "muscle_group", "target"):
        value = normalized.get(key)
        if isinstance(value, list) and len(value) == 1:
            normalized[key] = value[0]
    if normalized.get("medical_context") is None:
        normalized["medical_context"] = False
    return normalized


def _completion_fields(
    payload: object, api_format: str, configured_model: str
) -> tuple[str, str]:
    if not isinstance(payload, dict):
        raise TypeError("completion payload must be an object")
    if api_format == "lmstudio":
        output = payload.get("output")
        if isinstance(output, list):
            messages = [
                item["content"]
                for item in output
                if isinstance(item, dict)
                and item.get("type") == "message"
                and isinstance(item.get("content"), str)
            ]
            content = messages[-1] if messages else ""
        elif isinstance(output, str):
            content = output
        else:
            content = payload.get("output_text", "")
        model = (
            payload.get("model") or payload.get("model_instance_id") or configured_model
        )
    else:
        choices = payload["choices"]
        content = choices[0]["message"]["content"]
        model = payload.get("model", configured_model)
    if not isinstance(model, str) or not isinstance(content, str) or not content:
        raise TypeError("completion fields must be text")
    return model, content


def _response_text(
    response: httpx.Response, api_format: str, configured_model: str
) -> str:
    try:
        content = _completion_fields(response.json(), api_format, configured_model)[1]
    except (json.JSONDecodeError, KeyError, IndexError, TypeError):
        return response.text
    return content if isinstance(content, str) else response.text
