"""Grounded two-stage conversational search and workout orchestration."""

import re
from typing import Protocol, TypeVar, cast
from uuid import UUID

from pydantic import BaseModel

from exercise_api.api_models import (
    ChatMessage,
    ChatRequest,
    ChatResponse,
    ExerciseOut,
    ExerciseSearchDecision,
    GroundedDecision,
    RetrievalPlan,
    WorkoutDecision,
    WorkoutExercise,
    WorkoutOut,
)
from exercise_api.config import Settings
from exercise_api.exercise_repository import ExerciseRepository
from exercise_api.prompts import (
    grounded_answer_messages,
    grounding_correction_message,
    retrieval_plan_messages,
)
from exercise_api.retrieval import RetrievalService
from exercise_api.session_repository import SessionNotFoundError, SessionRepository

T = TypeVar("T", bound=BaseModel)


class StructuredLLM(Protocol):
    """Provider-neutral structured generation contract consumed by chat."""

    async def generate_json(
        self, messages: list[ChatMessage], output_type: type[T]
    ) -> T: ...


class UngroundedLLMResponseError(Exception):
    """Raised when the sole grounding correction still selects unknown IDs."""


_MEDICAL_CONTEXT_TERMS = (
    "pain",
    "injury",
    "injuries",
    "injured",
    "pregnant",
    "pregnancy",
    "rehab",
    "rehabilitation",
    "medical condition",
    "health condition",
    "heart condition",
    "asthma",
    "diabetes",
)
_MEDICAL_CONTEXT_PATTERN = re.compile(
    r"\b(?:" + "|".join(re.escape(term) for term in _MEDICAL_CONTEXT_TERMS) + r")\b",
    flags=re.IGNORECASE,
)
_MEDICAL_WARNING = (
    "This is general exercise information, not medical advice; seek qualified "
    "professional guidance before exercising with pain, injury, pregnancy, "
    "rehabilitation needs, or a medical condition."
)
_NO_MATCH_WARNING = "No catalog exercises matched the requested constraints."


class ChatService:
    """Coordinate sessions, retrieval, grounded generation, and persistence."""

    def __init__(
        self,
        sessions: SessionRepository,
        retrieval: RetrievalService,
        exercises: ExerciseRepository,
        llm: StructuredLLM,
        settings: Settings,
    ) -> None:
        self._sessions = sessions
        self._retrieval = retrieval
        self._exercises = exercises
        self._llm = llm
        self._settings = settings

    async def chat(self, request: ChatRequest) -> ChatResponse:
        session_id = await self._resolve_session(request.session_id)
        history = await self._sessions.recent_messages(
            session_id, self._settings.sessions.history_message_limit
        )
        plan = await self._llm.generate_json(
            retrieval_plan_messages(history, request.message), RetrievalPlan
        )
        candidates = await self._retrieval.retrieve(
            plan, request.message, self._settings.retrieval.candidate_limit
        )

        if not candidates:
            response = ChatResponse(
                session_id=session_id,
                answer="I could not find catalog exercises matching those constraints.",
                intent=plan.intent,
                assumptions=[],
                workout=None,
                results=[],
                warnings=self._safety_warnings(request.message, [_NO_MATCH_WARNING]),
            )
        else:
            response = await self._generate_grounded_response(
                session_id, request.message, plan, candidates
            )

        payload = response.model_dump(mode="json")
        await self._sessions.append_exchange(
            session_id, request.message, response.answer, payload
        )
        return response

    async def _resolve_session(self, requested_id: UUID | None) -> UUID:
        if requested_id is None:
            return (await self._sessions.create()).id
        if await self._sessions.get(requested_id) is None:
            raise SessionNotFoundError(str(requested_id))
        return requested_id

    async def _generate_grounded_response(
        self,
        session_id: UUID,
        current_text: str,
        plan: RetrievalPlan,
        candidates: list[ExerciseOut],
    ) -> ChatResponse:
        output_type: type[ExerciseSearchDecision | WorkoutDecision]
        output_type = (
            ExerciseSearchDecision
            if plan.intent == "exercise_search"
            else WorkoutDecision
        )
        generation_messages = grounded_answer_messages(
            plan,
            current_text,
            candidates,
            self._settings.retrieval.result_limit,
        )
        decision = cast(
            GroundedDecision,
            await self._llm.generate_json(generation_messages, output_type),
        )
        candidate_ids = {candidate.id for candidate in candidates}
        hydrated = await self._hydrate(decision, candidate_ids)
        if hydrated is None:
            correction_messages = [
                *generation_messages,
                grounding_correction_message(
                    [item.id for item in candidates],
                    self._settings.retrieval.result_limit,
                ),
            ]
            decision = cast(
                GroundedDecision,
                await self._llm.generate_json(correction_messages, output_type),
            )
            hydrated = await self._hydrate(decision, candidate_ids)
            if hydrated is None:
                raise UngroundedLLMResponseError(
                    "Model selected unknown catalog exercise IDs"
                )

        warnings = self._safety_warnings(current_text, decision.warnings)
        if isinstance(decision, ExerciseSearchDecision):
            return ChatResponse(
                session_id=session_id,
                answer=decision.answer,
                intent="exercise_search",
                assumptions=decision.assumptions,
                results=cast(list[ExerciseOut], hydrated),
                workout=None,
                warnings=warnings,
            )
        return ChatResponse(
            session_id=session_id,
            answer=decision.answer,
            intent="workout",
            assumptions=decision.assumptions,
            workout=WorkoutOut(
                name=decision.name,
                estimated_duration_minutes=decision.estimated_duration_minutes,
                exercises=cast(list[WorkoutExercise], hydrated),
            ),
            results=[],
            warnings=warnings,
        )

    async def _hydrate(
        self, decision: GroundedDecision, candidate_ids: set[str]
    ) -> list[ExerciseOut] | list[WorkoutExercise] | None:
        selected_ids = [selection.id for selection in decision.selections]
        if not selected_ids or any(
            selected_id not in candidate_ids for selected_id in selected_ids
        ):
            return None
        if isinstance(decision, ExerciseSearchDecision):
            limited_selections = decision.selections[
                : self._settings.retrieval.result_limit
            ]
            limited_ids = [selection.id for selection in limited_selections]
            catalog = await self._exercises.by_ids(limited_ids)
            if len(catalog) != len(set(limited_ids)):
                return None
            return [catalog[selection.id] for selection in limited_selections]
        workout_selections = decision.selections[
            : self._settings.retrieval.result_limit
        ]
        workout_ids = [selection.id for selection in workout_selections]
        catalog = await self._exercises.by_ids(workout_ids)
        if len(catalog) != len(set(workout_ids)):
            return None
        return [
            WorkoutExercise(
                **catalog[selection.id].model_dump(),
                sets=selection.sets,
                reps=selection.reps,
                rest_seconds=selection.rest_seconds,
                notes=selection.notes,
            )
            for selection in workout_selections
        ]

    @staticmethod
    def _safety_warnings(text: str, warnings: list[str]) -> list[str]:
        result = list(warnings)
        if _MEDICAL_CONTEXT_PATTERN.search(text) and _MEDICAL_WARNING not in result:
            result.append(_MEDICAL_WARNING)
        return result
