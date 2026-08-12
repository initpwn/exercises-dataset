"""Provider-neutral prompt construction for structured model responses."""

import json

from exercise_api.api_models import (
    ChatMessage,
    ExerciseOut,
    MessageOut,
    RetrievalPlan,
)

_ENGLISH_OUTPUT_REQUIREMENT = "All generated response strings must be English."


def retrieval_plan_messages(
    history: list[MessageOut], current_text: str
) -> list[ChatMessage]:
    """Build the intent/constraint planning request with bounded history."""
    messages = [
        ChatMessage(
            role="system",
            content=(
                "Classify the current request as exercise_search or workout and "
                "return only JSON matching RetrievalPlan. Extract only constraints "
                "the user actually stated: category, body_part, equipment, "
                "muscle_group, target, and concise search_terms. Use conversation "
                "history to resolve follow-up references. Input text may be in any "
                f"language. {_ENGLISH_OUTPUT_REQUIREMENT}"
            ),
        )
    ]
    messages.extend(
        ChatMessage(role=message.role, content=message.text) for message in history
    )
    messages.append(ChatMessage(role="user", content=current_text))
    return messages


def grounded_answer_messages(
    plan: RetrievalPlan,
    current_text: str,
    candidates: list[ExerciseOut],
    result_limit: int,
) -> list[ChatMessage]:
    """Build a grounded selection request containing authoritative candidates."""
    candidate_facts = [candidate.model_dump(mode="json") for candidate in candidates]
    valid_ids = [candidate.id for candidate in candidates]
    response_kind = (
        "For exercise_search, return answer, selections containing only an id, "
        "assumptions, and warnings."
        if plan.intent == "exercise_search"
        else (
            "For workout, return answer, name, estimated_duration_minutes, "
            "selections containing only id, sets, reps, rest_seconds, and notes, "
            "plus assumptions and warnings."
        )
    )
    defaults = ""
    if plan.intent == "workout":
        defaults = (
            " When the conversation does not specify them, apply these conservative "
            "defaults and list them as assumptions: beginner experience, general "
            "fitness goal, 30-45 minutes, moderate volume and rest, and no known "
            "restrictions. When equipment is unspecified, prefer body weight."
        )
    return [
        ChatMessage(
            role="system",
            content=(
                "Return only JSON matching the requested response structure. "
                f"{_ENGLISH_OUTPUT_REQUIREMENT} "
                f"{response_kind} Select one or more and at most {result_limit} IDs "
                "only from this valid list: "
                f"{json.dumps(valid_ids)}. Never invent or rewrite catalog facts. "
                "Do not claim catalog difficulty, medical safety, suitability for a "
                f"condition, diagnosis, or rehabilitation guidance.{defaults}"
            ),
        ),
        ChatMessage(
            role="user",
            content=(
                f"Current request: {current_text}\n"
                f"Retrieval plan: {plan.model_dump_json()}\n"
                "Authoritative catalog candidates:\n"
                f"{json.dumps(candidate_facts, ensure_ascii=False)}"
            ),
        ),
    ]


def grounding_correction_message(
    valid_candidate_ids: list[str], result_limit: int
) -> ChatMessage:
    """Request the sole allowed correction using only retrievable IDs."""
    return ChatMessage(
        role="user",
        content=(
            f"{_ENGLISH_OUTPUT_REQUIREMENT} Correct the response and return the "
            "same JSON structure. Select at "
            f"least one and at most {result_limit} exercises, and use IDs only from "
            "this valid candidate list: "
            f"{json.dumps(valid_candidate_ids)}. Return no other IDs."
        ),
    )


def json_repair_message(malformed_output: str, validation_error: str) -> ChatMessage:
    """Ask a model to repair one invalid structured response."""
    return ChatMessage(
        role="user",
        content=(
            "Return only valid JSON matching the requested structure. "
            f"The previous response was invalid ({validation_error}). "
            "Repair this output:\n"
            f"{malformed_output}"
        ),
    )
