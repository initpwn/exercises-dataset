"""Hard-filtered, deterministically ranked exercise catalog retrieval."""

from rapidfuzz import fuzz

from exercise_api.api_models import ExerciseOut, RetrievalPlan
from exercise_api.exercise_repository import ExerciseFilters, ExerciseRepository


def _best_match(needles: list[str], values: list[str]) -> float:
    if not needles or not values:
        return 0.0
    return max(fuzz.WRatio(needle, value) for needle in needles for value in values)


def _score(exercise: ExerciseOut, needles: list[str]) -> float:
    return sum(
        (
            5 * _best_match(needles, [exercise.name]),
            4 * _best_match(needles, [exercise.target]),
            3
            * _best_match(
                needles, [exercise.muscle_group, *exercise.secondary_muscles]
            ),
            2 * _best_match(needles, [exercise.equipment]),
            2 * _best_match(needles, [exercise.body_part]),
            1 * _best_match(needles, exercise.instructions),
        )
    )


class RetrievalService:
    """Retrieve exercises using hard constraints followed by weighted fuzzy rank."""

    def __init__(self, repository: ExerciseRepository) -> None:
        self._repository = repository

    async def retrieve(
        self, plan: RetrievalPlan, query: str, candidate_limit: int
    ) -> list[ExerciseOut]:
        if candidate_limit <= 0:
            return []

        candidates = await self._repository.matching_exact(
            ExerciseFilters(
                category=plan.category,
                body_part=plan.body_part,
                equipment=plan.equipment,
                muscle_group=plan.muscle_group,
                target=plan.target,
            )
        )
        needles = [
            term.strip()
            for term in [query, *plan.search_terms]
            if term.strip()
        ]
        ranked: list[ExerciseOut] = sorted(
            candidates, key=lambda item: (-_score(item, needles), item.id)
        )
        return ranked[:candidate_limit]
