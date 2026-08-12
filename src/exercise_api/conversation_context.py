"""Catalog-grounded, bounded structured context for conversational follow-ups."""

import json
import re

from exercise_api.api_models import (
    ChatResponse,
    MessageOut,
    PriorExerciseContext,
    PriorTurnContext,
    RetrievalPlan,
    WorkoutExercise,
)
from exercise_api.exercise_repository import ExerciseRepository
from exercise_api.safety import contains_unsafe_medical_claim

MAX_CONTEXT_TURNS = 5
MAX_CONTEXT_EXERCISES = 10
MAX_CONTEXT_CHARS = 8192
MAX_CONTEXT_NOTE_CHARS = 500
_FOLLOW_UP_PATTERN = re.compile(
    r"\b(?:those|these|them|same|previous|above)\b", flags=re.IGNORECASE
)


def bound_context(turns: list[PriorTurnContext]) -> list[PriorTurnContext]:
    """Keep newest validated context within every documented resource bound."""
    fitted = [turn.model_copy(deep=True) for turn in turns[-MAX_CONTEXT_TURNS:]]
    for turn in fitted:
        turn.exercises = turn.exercises[:MAX_CONTEXT_EXERCISES]
        for exercise in turn.exercises:
            if exercise.notes:
                exercise.notes = exercise.notes[:MAX_CONTEXT_NOTE_CHARS]
    while (
        fitted
        and len(json.dumps([turn.model_dump() for turn in fitted], ensure_ascii=False))
        > MAX_CONTEXT_CHARS
    ):
        if len(fitted) > 1:
            fitted.pop(0)
        elif len(fitted[0].exercises) > 1:
            fitted[0].exercises.pop()
        elif fitted[0].exercises[0].notes:
            fitted[0].exercises[0].notes = None
        else:
            fitted.clear()
    return fitted


def is_follow_up(plan: RetrievalPlan, current_text: str) -> bool:
    """Combine typed planning with a deterministic English reference fallback."""
    return (
        plan.uses_prior_context or _FOLLOW_UP_PATTERN.search(current_text) is not None
    )


def reference_ids(
    plan: RetrievalPlan,
    prior_context: list[PriorTurnContext],
    uses_prior_context: bool,
) -> list[str] | None:
    """Resolve planner references against the newest validated turn in stored order."""
    if not uses_prior_context:
        return None
    if not prior_context:
        return []
    if not plan.referenced_ids:
        return [item.id for item in prior_context[-1].exercises]
    requested = set(plan.referenced_ids)
    stored_order: list[str] = []
    for turn in prior_context:
        for item in turn.exercises:
            if item.id in requested and item.id not in stored_order:
                stored_order.append(item.id)
    return stored_order


class ConversationContextBuilder:
    """Read stored payloads while trusting only current catalog facts."""

    def __init__(self, exercises: ExerciseRepository) -> None:
        self._exercises = exercises

    async def build(self, history: list[MessageOut]) -> list[PriorTurnContext]:
        assistant_messages = [
            message
            for message in history
            if message.role == "assistant" and message.payload is not None
        ][-MAX_CONTEXT_TURNS:]
        parsed: list[tuple[MessageOut, ChatResponse]] = []
        all_ids: list[str] = []
        for message in assistant_messages:
            try:
                response = ChatResponse.model_validate(message.payload)
            except ValueError:
                continue
            parsed.append((message, response))
            exercises = (
                response.results
                if response.workout is None
                else response.workout.exercises
            )
            all_ids.extend(
                exercise.id for exercise in exercises[:MAX_CONTEXT_EXERCISES]
            )

        current_catalog = await self._exercises.by_ids(all_ids)
        turns: list[PriorTurnContext] = []
        for message, response in parsed:
            exercises = (
                response.results
                if response.workout is None
                else response.workout.exercises
            )
            items: list[PriorExerciseContext] = []
            seen: set[str] = set()
            for order, exercise in enumerate(
                exercises[:MAX_CONTEXT_EXERCISES], start=1
            ):
                if exercise.id in seen or exercise.id not in current_catalog:
                    continue
                seen.add(exercise.id)
                if isinstance(exercise, WorkoutExercise):
                    notes = exercise.notes
                    if notes and contains_unsafe_medical_claim(notes):
                        notes = None
                    items.append(
                        PriorExerciseContext(
                            order=order,
                            id=exercise.id,
                            name=current_catalog[exercise.id].name,
                            sets=exercise.sets,
                            reps=exercise.reps,
                            rest_seconds=exercise.rest_seconds,
                            notes=notes,
                        )
                    )
                else:
                    items.append(
                        PriorExerciseContext(
                            order=order,
                            id=exercise.id,
                            name=current_catalog[exercise.id].name,
                        )
                    )
            if items:
                turns.append(
                    PriorTurnContext(
                        message_position=message.position,
                        intent=response.intent,
                        exercises=items,
                    )
                )
        return bound_context(turns)
