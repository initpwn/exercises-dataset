"""Bounds for structured follow-up context supplied to the model."""

import json

from exercise_api.api_models import (
    PriorExerciseContext,
    PriorTurnContext,
    RetrievalPlan,
)
from exercise_api.conversation_context import (
    MAX_CONTEXT_CHARS,
    MAX_CONTEXT_EXERCISES,
    MAX_CONTEXT_TURNS,
    bound_context,
    is_follow_up,
    reference_ids,
)


def test_structured_context_is_bounded_by_turn_exercise_and_character_limits() -> None:
    turns = [
        PriorTurnContext(
            message_position=turn,
            intent="workout",
            exercises=[
                PriorExerciseContext(
                    order=exercise + 1,
                    id=f"{turn:02d}{exercise:02d}",
                    name=f"Exercise {exercise}",
                    sets=3,
                    reps="10",
                    rest_seconds=60,
                    notes="n" * 1_000,
                )
                for exercise in range(12)
            ],
        )
        for turn in range(6)
    ]

    bounded = bound_context(turns)
    serialized = json.dumps([turn.model_dump() for turn in bounded], ensure_ascii=False)

    assert len(bounded) <= MAX_CONTEXT_TURNS
    assert all(len(turn.exercises) <= MAX_CONTEXT_EXERCISES for turn in bounded)
    assert len(serialized) <= MAX_CONTEXT_CHARS
    assert bounded[-1].message_position == 5


def test_relative_clause_that_does_not_force_prior_context() -> None:
    plan = RetrievalPlan(intent="exercise_search")

    assert not is_follow_up(plan, "find an exercise that uses dumbbells")


def test_explicit_reference_can_resolve_an_older_bounded_turn() -> None:
    turns = [
        PriorTurnContext(
            message_position=position,
            intent="exercise_search",
            exercises=[PriorExerciseContext(order=1, id=exercise_id, name=exercise_id)],
        )
        for position, exercise_id in [(1, "older"), (3, "middle"), (5, "latest")]
    ]
    plan = RetrievalPlan(
        intent="workout", uses_prior_context=True, referenced_ids=["older"]
    )

    assert reference_ids(plan, turns, uses_prior_context=True) == ["older"]
