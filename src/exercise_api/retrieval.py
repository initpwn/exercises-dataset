"""Hard-filtered, deterministically ranked exercise catalog retrieval."""

import re
import unicodedata
from dataclasses import dataclass

from rapidfuzz import fuzz

from exercise_api.api_models import ExerciseOut, RetrievalPlan
from exercise_api.exercise_repository import (
    ConstraintField,
    ExerciseFilters,
    ExerciseRepository,
)

_CONSTRAINT_FIELDS: tuple[ConstraintField, ...] = (
    "category",
    "body_part",
    "equipment",
    "muscle_group",
    "target",
)
_DETERMINISTIC_FIELDS: tuple[ConstraintField, ...] = (
    "body_part",
    "equipment",
    "target",
)
_GENERIC_QUERY_WORDS = {
    "a",
    "an",
    "and",
    "another",
    "build",
    "create",
    "do",
    "exercise",
    "exercises",
    "find",
    "fit",
    "fitness",
    "for",
    "general",
    "get",
    "give",
    "help",
    "into",
    "make",
    "me",
    "movement",
    "movements",
    "my",
    "need",
    "of",
    "option",
    "options",
    "or",
    "please",
    "routine",
    "routines",
    "show",
    "some",
    "something",
    "suggest",
    "the",
    "those",
    "to",
    "turn",
    "use",
    "using",
    "want",
    "with",
    "workout",
    "workouts",
}
_MIN_RELEVANCE = 70.0


def _normalize(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    separated = re.sub(r"[-_/]+", " ", normalized)
    return " ".join(separated.split())


def _compact(value: str) -> str:
    return value.replace(" ", "")


def _generated_aliases(canonical: str) -> set[str]:
    aliases = {_compact(canonical)}
    if canonical.endswith("s") and len(canonical) > 3:
        aliases.add(canonical[:-1])
    else:
        aliases.add(f"{canonical}s")
        aliases.add(f"{_compact(canonical)}s")
    return {alias for alias in aliases if alias and alias != canonical}


@dataclass(frozen=True)
class CatalogVocabulary:
    """Canonical catalog values plus conservative, unambiguous aliases."""

    values: dict[ConstraintField, list[str]]
    aliases: dict[ConstraintField, dict[str, str]]

    @classmethod
    def build(cls, values: dict[ConstraintField, list[str]]) -> "CatalogVocabulary":
        aliases_by_field: dict[ConstraintField, dict[str, str]] = {}
        for field in _CONSTRAINT_FIELDS:
            canonical_values = values[field]
            exact: dict[str, str] = {
                _normalize(value): value for value in canonical_values
            }
            proposed: dict[str, set[str]] = {}
            for canonical in canonical_values:
                normalized = _normalize(canonical)
                for alias in _generated_aliases(normalized):
                    proposed.setdefault(alias, set()).add(canonical)
            aliases = dict(exact)
            for alias, matches in proposed.items():
                if alias not in exact and len(matches) == 1:
                    aliases[alias] = next(iter(matches))
            aliases_by_field[field] = aliases
        return cls(values=values, aliases=aliases_by_field)

    def canonicalize(self, field: ConstraintField, value: str) -> str:
        normalized = _normalize(value)
        return self.aliases[field].get(normalized, normalized)

    def detect(self, field: ConstraintField, text: str) -> str | None:
        normalized_text = _normalize(text)
        for alias in sorted(self.aliases[field], key=lambda item: (-len(item), item)):
            if re.search(rf"(?<!\w){re.escape(alias)}(?!\w)", normalized_text):
                return self.aliases[field][alias]
        return None

    def prompt_values(self) -> dict[ConstraintField, list[str]]:
        """Expose only catalog values, never generated aliases, to the planner."""
        return self.values


def _best_match(needles: list[str], values: list[str]) -> float:
    if not needles or not values:
        return 0.0
    return max(fuzz.WRatio(needle, value) for needle in needles for value in values)


def _score(exercise: ExerciseOut, needles: list[str]) -> float:
    return sum(
        (
            5 * _best_match(needles, [exercise.name]),
            4 * _best_match(needles, [exercise.target]),
            3 * _best_match(needles, [exercise.muscle_group]),
            3 * _best_match(needles, exercise.secondary_muscles),
            2 * _best_match(needles, [exercise.equipment]),
            2 * _best_match(needles, [exercise.body_part]),
            1 * _best_match(needles, exercise.instructions),
        )
    )


def _query_needles(query: str) -> list[str]:
    words = re.findall(r"\w+", _normalize(query))
    result: list[str] = []
    for needle in words:
        if needle and needle not in _GENERIC_QUERY_WORDS and needle not in result:
            result.append(needle)
    return result


def _is_arbitrary_language_input(query: str) -> bool:
    return any(not character.isascii() for character in query)


def _relevance(exercise: ExerciseOut, needles: list[str]) -> float:
    values = [
        exercise.name,
        exercise.target,
        exercise.muscle_group,
        *exercise.secondary_muscles,
        exercise.equipment,
        exercise.body_part,
        exercise.category,
    ]
    normalized_values = [_normalize(value) for value in values]
    scores: list[float] = []
    for needle in needles:
        if len(needle) < 4:
            value_tokens = {
                token
                for value in normalized_values
                for token in re.findall(r"\w+", value)
            }
            scores.append(100.0 if needle in value_tokens else 0.0)
        else:
            scores.append(_best_match([needle], normalized_values))
    return max(scores, default=0.0)


class RetrievalService:
    """Retrieve exercises using hard constraints followed by weighted fuzzy rank."""

    def __init__(self, repository: ExerciseRepository) -> None:
        self._repository = repository

    async def vocabulary(self) -> CatalogVocabulary:
        """Build current canonical constraint vocabulary from synchronized rows."""
        return CatalogVocabulary.build(await self._repository.constraint_values())

    @staticmethod
    def normalize_plan(
        plan: RetrievalPlan, query: str, vocabulary: CatalogVocabulary
    ) -> RetrievalPlan:
        """Merge deterministic current-text constraints over typed planning."""
        updates: dict[str, str | None] = {}
        query_has_details = bool(_query_needles(query)) or not query.strip()
        accepts_typed_translation = _is_arbitrary_language_input(query)
        detected_values: set[str] = set()
        for field in _CONSTRAINT_FIELDS:
            detected = (
                vocabulary.detect(field, query)
                if field in _DETERMINISTIC_FIELDS
                else None
            )
            if detected is not None:
                detected_values.add(_normalize(detected))
            planned = getattr(plan, field)
            updates[field] = (
                detected
                if detected is not None
                else (
                    vocabulary.canonicalize(field, planned)
                    if planned is not None
                    and (query_has_details or accepts_typed_translation)
                    else None
                )
            )
        for field in set(_CONSTRAINT_FIELDS) - set(_DETERMINISTIC_FIELDS):
            value = updates[field]
            if value is not None and _normalize(value) in detected_values:
                updates[field] = None
        return plan.model_copy(update=updates)

    async def retrieve(
        self,
        plan: RetrievalPlan,
        query: str,
        candidate_limit: int,
        *,
        vocabulary: CatalogVocabulary | None = None,
        reference_ids: list[str] | None = None,
        enforce_plan_constraints: bool = False,
    ) -> list[ExerciseOut]:
        if candidate_limit <= 0:
            return []

        active_vocabulary = vocabulary or await self.vocabulary()
        if not enforce_plan_constraints:
            plan = self.normalize_plan(plan, query, active_vocabulary)

        candidates = await self._repository.matching_exact(
            ExerciseFilters(
                category=plan.category,
                body_part=plan.body_part,
                equipment=plan.equipment,
                muscle_group=plan.muscle_group,
                target=plan.target,
            )
        )
        if reference_ids is not None:
            by_id = {candidate.id: candidate for candidate in candidates}
            return [
                by_id[exercise_id]
                for exercise_id in reference_ids[:candidate_limit]
                if exercise_id in by_id
            ]

        deterministic_constraint = any(
            active_vocabulary.detect(field, query) is not None
            for field in _DETERMINISTIC_FIELDS
        )
        query_needles = _query_needles(query)
        arbitrary_language = _is_arbitrary_language_input(query)
        internal_typed_request = not query.strip()
        meaningful = list(query_needles)
        if arbitrary_language or internal_typed_request:
            meaningful.extend(
                _normalize(term)
                for term in plan.search_terms
                if term.strip() and _normalize(term) not in meaningful
            )
        trusted_hard_constraint = (
            deterministic_constraint
            or enforce_plan_constraints
            or (
                (arbitrary_language or internal_typed_request)
                and any(
                    getattr(plan, field) is not None for field in _CONSTRAINT_FIELDS
                )
            )
        )
        if reference_ids is None and not trusted_hard_constraint and query_needles:
            candidates = [
                candidate
                for candidate in candidates
                if _relevance(candidate, query_needles) >= _MIN_RELEVANCE
            ]
        if not candidates:
            return []
        if not meaningful:
            return candidates[:candidate_limit]

        needles = [term.strip() for term in [query, *plan.search_terms] if term.strip()]
        ranked: list[ExerciseOut] = sorted(
            candidates, key=lambda item: (-_score(item, needles), item.id)
        )
        return ranked[:candidate_limit]
