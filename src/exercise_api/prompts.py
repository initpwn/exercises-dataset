"""Provider-neutral prompt construction for structured model responses."""

import json

from exercise_api.api_models import (
    ChatMessage,
    ExerciseOut,
    MessageOut,
    PriorTurnContext,
    RetrievalPlan,
)
from exercise_api.exercise_repository import ConstraintField

_ENGLISH_OUTPUT_REQUIREMENT = "All generated response strings must be English."
MAX_REPAIR_OUTPUT_CHARS = 8192
MAX_REPAIR_ERROR_CHARS = 2048
_TRUNCATION_MARKER = "\n...[truncated]...\n"


def _bounded_excerpt(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    available = limit - len(_TRUNCATION_MARKER)
    head = available // 2
    tail = available - head
    return f"{value[:head]}{_TRUNCATION_MARKER}{value[-tail:]}"


def retrieval_plan_messages(
    history: list[MessageOut],
    current_text: str,
    vocabulary: dict[ConstraintField, list[str]] | None = None,
    prior_context: list[PriorTurnContext] | None = None,
) -> list[ChatMessage]:
    """Build the intent/constraint planning request with bounded history."""
    vocabulary_text = (
        " Normalize constraints to this authoritative catalog vocabulary: "
        f"{json.dumps(vocabulary, ensure_ascii=False)}."
        if vocabulary is not None
        else ""
    )
    messages = [
        ChatMessage(
            role="system",
            content=(
                "Classify the current request as exercise_search or workout and "
                "return only JSON matching RetrievalPlan. Extract only constraints "
                "the user actually stated: category, body_part, equipment, "
                "muscle_group, target, and concise search_terms. Use conversation "
                "history to resolve follow-up references. Set uses_prior_context "
                "when the current request refers to prior results and copy only "
                "applicable referenced_ids from server-validated structured "
                "context. Set medical_context when current text or history includes "
                "pain, injury, pregnancy, rehabilitation, or a medical condition, "
                "including when input is not English. For workouts, extract stated "
                "experience_level, fitness_goal, duration_minutes, training_volume, "
                "and restrictions; leave omitted details null. For each extracted "
                "workout preference, copy the exact supporting input substring into "
                "the corresponding *_evidence field; never infer an unstated value. "
                "Input text may be in any "
                f"language.{vocabulary_text} {_ENGLISH_OUTPUT_REQUIREMENT}"
            ),
        )
    ]
    if prior_context:
        messages.append(
            ChatMessage(
                role="system",
                content=(
                    "Server-validated structured response context (catalog names "
                    "were reloaded; preserve exercise order and prescriptions): "
                    + json.dumps(
                        [turn.model_dump() for turn in prior_context],
                        ensure_ascii=False,
                    )
                ),
            )
        )
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
    prior_context: list[PriorTurnContext] | None = None,
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
    context_text = (
        "\nServer-validated prior structured context:\n"
        + json.dumps([turn.model_dump() for turn in prior_context], ensure_ascii=False)
        if prior_context
        else ""
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
                f"{context_text}"
                "Authoritative catalog candidates:\n"
                f"{json.dumps(candidate_facts, ensure_ascii=False)}"
            ),
        ),
    ]


def grounding_correction_message(
    valid_candidate_ids: list[str],
    result_limit: int,
    problems: list[str] | None = None,
) -> ChatMessage:
    """Request the sole allowed correction using only retrievable IDs."""
    problem_text = (
        " The previous response had these grounding or safety problems: "
        + "; ".join(problems)
        + " Remove unsafe medical-safety or suitability claims."
        if problems
        else ""
    )
    return ChatMessage(
        role="user",
        content=(
            f"{_ENGLISH_OUTPUT_REQUIREMENT} Correct the response and return the "
            "same JSON structure. Select at "
            f"least one and at most {result_limit} exercises, and use IDs only from "
            "this valid candidate list: "
            f"{json.dumps(valid_candidate_ids)}. Return no other IDs.{problem_text}"
        ),
    )


def json_repair_message(malformed_output: str, validation_error: str) -> ChatMessage:
    """Ask a model to repair one invalid structured response."""
    bounded_output = _bounded_excerpt(malformed_output, MAX_REPAIR_OUTPUT_CHARS)
    bounded_error = _bounded_excerpt(validation_error, MAX_REPAIR_ERROR_CHARS)
    return ChatMessage(
        role="user",
        content=(
            "Return only valid JSON matching the requested structure. "
            f"The previous response was invalid ({bounded_error}). "
            "Repair this output:\n"
            f"{bounded_output}"
        ),
    )
