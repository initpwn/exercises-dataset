"""Grounded two-stage conversational search and workout orchestration."""

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
    PriorTurnContext,
    RetrievalPlan,
    WorkoutDecision,
    WorkoutExercise,
    WorkoutOut,
)
from exercise_api.config import Settings
from exercise_api.conversation_context import (
    ConversationContextBuilder,
    is_follow_up,
    reference_ids,
)
from exercise_api.exercise_repository import ExerciseRepository
from exercise_api.prompts import (
    grounded_answer_messages,
    grounding_correction_message,
    retrieval_plan_messages,
)
from exercise_api.retrieval import RetrievalService
from exercise_api.safety import (
    decision_problems,
    has_medical_context,
    response_assumptions,
    response_warnings,
    retain_user_stated_preferences,
)
from exercise_api.session_repository import SessionNotFoundError, SessionRepository

T = TypeVar("T", bound=BaseModel)


class StructuredLLM(Protocol):
    """Provider-neutral structured generation contract consumed by chat."""

    async def generate_json(
        self, messages: list[ChatMessage], output_type: type[T]
    ) -> T: ...


class UngroundedLLMResponseError(Exception):
    """Raised when the sole grounding correction still selects unknown IDs."""


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
        self._context = ConversationContextBuilder(exercises)

    async def chat(self, request: ChatRequest) -> ChatResponse:
        session_id = await self._resolve_session(request.session_id)
        history = await self._sessions.recent_messages(
            session_id, self._settings.sessions.history_message_limit
        )
        prior_context = await self._context.build(history)
        vocabulary = await self._retrieval.vocabulary()
        plan = await self._llm.generate_json(
            retrieval_plan_messages(
                history,
                request.message,
                vocabulary.prompt_values(),
                prior_context,
            ),
            RetrievalPlan,
        )
        plan = self._retrieval.normalize_plan(plan, request.message, vocabulary)
        plan = retain_user_stated_preferences(plan, request.message)
        uses_prior_context = is_follow_up(plan, request.message)
        referenced_ids = reference_ids(plan, prior_context, uses_prior_context)
        plan = plan.model_copy(
            update={
                "uses_prior_context": uses_prior_context,
                "referenced_ids": referenced_ids or [],
            }
        )
        medical_context = has_medical_context(plan, history, request.message)
        retrieval_plan = plan
        body_weight_preferred = False
        if (
            plan.intent == "workout"
            and plan.equipment is None
            and not uses_prior_context
        ):
            body_weight = vocabulary.aliases["equipment"].get("bodyweight")
            if body_weight is not None:
                retrieval_plan = plan.model_copy(update={"equipment": body_weight})
                body_weight_preferred = True
        candidates = await self._retrieval.retrieve(
            retrieval_plan,
            request.message,
            self._settings.retrieval.candidate_limit,
            vocabulary=vocabulary,
            reference_ids=referenced_ids if uses_prior_context else None,
            enforce_plan_constraints=body_weight_preferred,
        )
        if not candidates and body_weight_preferred:
            candidates = await self._retrieval.retrieve(
                plan,
                request.message,
                self._settings.retrieval.candidate_limit,
                vocabulary=vocabulary,
            )

        if not candidates:
            response = ChatResponse(
                session_id=session_id,
                answer="I could not find catalog exercises matching those constraints.",
                intent=plan.intent,
                assumptions=response_assumptions(
                    plan, [], medical_context, uses_prior_context
                ),
                workout=None,
                results=[],
                warnings=response_warnings(medical_context, [_NO_MATCH_WARNING]),
            )
        else:
            response = await self._generate_grounded_response(
                session_id,
                request.message,
                plan,
                candidates,
                prior_context,
                medical_context,
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
        prior_context: list[PriorTurnContext],
        medical_context: bool,
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
            prior_context,
        )
        decision = cast(
            GroundedDecision,
            await self._llm.generate_json(generation_messages, output_type),
        )
        candidate_ids = {candidate.id for candidate in candidates}
        hydrated = await self._hydrate(decision, candidate_ids)
        problems = decision_problems(plan, decision, hydrated)
        if problems:
            correction_messages = [
                *generation_messages,
                grounding_correction_message(
                    [item.id for item in candidates],
                    self._settings.retrieval.result_limit,
                    problems,
                ),
            ]
            decision = cast(
                GroundedDecision,
                await self._llm.generate_json(correction_messages, output_type),
            )
            hydrated = await self._hydrate(decision, candidate_ids)
            problems = decision_problems(plan, decision, hydrated)
            if problems:
                raise UngroundedLLMResponseError(
                    "Model response remained ungrounded or unsafe"
                )

        assert hydrated is not None
        warnings = response_warnings(medical_context, decision.warnings)
        assumptions = response_assumptions(
            plan, decision.assumptions, medical_context, plan.uses_prior_context
        )
        if isinstance(decision, ExerciseSearchDecision):
            return ChatResponse(
                session_id=session_id,
                answer=decision.answer,
                intent="exercise_search",
                assumptions=assumptions,
                results=cast(list[ExerciseOut], hydrated),
                workout=None,
                warnings=warnings,
            )
        return ChatResponse(
            session_id=session_id,
            answer=decision.answer,
            intent="workout",
            assumptions=assumptions,
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
