"""Deterministic workout defaults and medical-response guardrails."""

import re

from exercise_api.api_models import (
    ExerciseOut,
    GroundedDecision,
    MessageOut,
    RetrievalPlan,
    WorkoutDecision,
    WorkoutExercise,
)

_MEDICAL_CONTEXT_PATTERN = re.compile(
    r"\bpain(?:ful)?\b|"
    r"\bhurt(?:s|ing)?\b|"
    r"\binjur(?:y|ies|ed)\b|"
    r"\b(?:tear(?:s|ing)?|tore|torn|sprain(?:s|ed|ing)?)\b|"
    r"\bpregnan(?:t|cy)\b|"
    r"\brehab(?:bing|bed|s|ilitation)?\b|"
    r"\b(?:medical|health|heart)\s+conditions?\b|"
    r"\b(?:arthritis|asthma|diabetes|hypertension)\b",
    flags=re.IGNORECASE,
)
_UNSAFE_MEDICAL_CLAIM_PATTERN = re.compile(
    r"(?:"
    r"\b(?:this|that|the|your|my|our)\s+(?:workout|routine|session|exercise|movement|plan)\s+"
    r"(?:is|are|remains?)\s+(?:generally\s+)?(?:safe|suitable|appropriate)\b|"
    r"\byou\s+can\s+safely\s+perform\b|"
    r"\bmedically\s+(?:safe|suitable|appropriate)\b|"
    r"\bsafe\s+(?:for|with|during)\b|"
    r"\bsuitable\s+(?:for|with|during)\b|"
    r"\bappropriate\s+(?:for|with|during)\b|"
    r"\b(?:approved|recommended)\s+for\s+"
    r"(?:pregnancy|pain|injur(?:y|ies)|rehab(?:ilitation)?|"
    r"a\s+medical\s+condition|arthritis|asthma|diabetes|hypertension)\b|"
    r"\b(?:will\s+not|won't)\s+(?:aggravate|worsen|hurt)\b"
    r"|\b(?:is|are|remains?)\s+(?:generally\s+)?"
    r"(?:safe|suitable|appropriate)\b[^.!?]{0,120}"
    r"\b(?:pain|injur(?:y|ies)|pregnan(?:t|cy)|rehab(?:ilitation)?|"
    r"medical\s+conditions?|arthritis|asthma|diabetes|hypertension)\b"
    r")",
    flags=re.IGNORECASE,
)
_NO_RESTRICTIONS_PATTERN = re.compile(
    r"\bno\s+(?:known\s+)?(?:"
    r"injur(?:y|ies)|medical\s+(?:conditions?|restrictions?)|restrictions?|"
    r"limitations?|contraindications?|health\s+concerns?"
    r")\b",
    flags=re.IGNORECASE,
)
_MEDICAL_WARNING = (
    "This is general exercise information, not medical advice; seek qualified "
    "professional guidance before exercising with pain, injury, pregnancy, "
    "rehabilitation needs, or a medical condition."
)
_WORKOUT_DEFAULTS = (
    ("experience_level", "Beginner experience level"),
    ("fitness_goal", "General fitness goal"),
    ("duration_minutes", "30-45 minute session"),
    ("training_volume", "Moderate training volume and rest periods"),
)
_EXPERIENCE_PATTERN = re.compile(
    r"\b(?:beginner|novice|new\s+to|intermediate|advanced|experienced)\b",
    flags=re.IGNORECASE,
)
_GOAL_PATTERN = re.compile(
    r"\b(?:strength|muscle|hypertrophy|weight\s+loss|fat\s+loss|fitness|"
    r"endurance|mobility|flexibility|conditioning)\b",
    flags=re.IGNORECASE,
)
_DURATION_PATTERN = re.compile(
    r"\b(?:\d+\s*(?:minutes?|mins?|hours?|hrs?)|half\s+an?\s+hour)\b",
    flags=re.IGNORECASE,
)
_VOLUME_PATTERN = re.compile(
    r"\b(?:volume|intensity|sets?|reps?|repetitions?)\b", flags=re.IGNORECASE
)
_RESTRICTIONS_PATTERN = re.compile(
    r"\b(?:restrictions?|limitations?|avoid|cannot|can't|no\s+injur(?:y|ies))\b",
    flags=re.IGNORECASE,
)
_REPS_NUMBER_PATTERN = re.compile(r"\d+")
_MAX_REPS_PER_SET = 100
_MAX_REST_SECONDS = 600


def contains_unsafe_medical_claim(text: str) -> bool:
    """Return whether model-owned text makes a prohibited affirmative claim."""
    return _UNSAFE_MEDICAL_CLAIM_PATTERN.search(text) is not None


def _excessive_reps(reps: str) -> bool:
    return any(
        int(match.group()) > _MAX_REPS_PER_SET
        for match in _REPS_NUMBER_PATTERN.finditer(reps)
    )


def has_medical_context(
    plan: RetrievalPlan, history: list[MessageOut], current_text: str
) -> bool:
    """Combine typed arbitrary-language planning with deterministic user text."""
    return (
        plan.medical_context
        or _MEDICAL_CONTEXT_PATTERN.search(current_text) is not None
        or any(
            message.role == "user"
            and _MEDICAL_CONTEXT_PATTERN.search(message.text) is not None
            for message in history
        )
    )


def retain_user_stated_preferences(
    plan: RetrievalPlan, current_text: str
) -> RetrievalPlan:
    """Keep typed workout preferences only when the request supplies evidence."""
    if plan.intent != "workout":
        return plan
    evidence = {
        "experience_level": _EXPERIENCE_PATTERN.search(current_text) is not None,
        "fitness_goal": _GOAL_PATTERN.search(current_text) is not None,
        "duration_minutes": _DURATION_PATTERN.search(current_text) is not None,
        "training_volume": _VOLUME_PATTERN.search(current_text) is not None,
        "restrictions": _RESTRICTIONS_PATTERN.search(current_text) is not None
        or _MEDICAL_CONTEXT_PATTERN.search(current_text) is not None,
    }
    normalized_text = current_text.casefold()
    for field in evidence:
        quoted = getattr(plan, f"{field}_evidence")
        if (
            quoted is not None
            and quoted.strip()
            and quoted.strip().casefold() in normalized_text
        ):
            evidence[field] = True
    return plan.model_copy(
        update={field: None for field, match in evidence.items() if not match}
    )


def decision_problems(
    plan: RetrievalPlan,
    decision: GroundedDecision,
    hydrated: list[ExerciseOut] | list[WorkoutExercise] | None,
) -> list[str]:
    """Describe catalog-grounding or prohibited-prose failures generically."""
    problems: list[str] = []
    if hydrated is None:
        problems.append("Selected IDs were not all valid catalog candidates")
    texts = [decision.answer, *decision.assumptions, *decision.warnings]
    if isinstance(decision, WorkoutDecision):
        texts.append(decision.name)
        texts.extend(
            selection.notes
            for selection in decision.selections
            if selection.notes is not None
        )
        if decision.estimated_duration_minutes > 240:
            problems.append("Workout duration exceeded the server limit")
        if (
            plan.duration_minutes is None
            and not plan.uses_prior_context
            and not 30 <= decision.estimated_duration_minutes <= 45
        ):
            problems.append("Workout duration contradicted the server default")
        if any(selection.sets > 20 for selection in decision.selections):
            problems.append("Workout prescription exceeded the server limit")
        if any(
            _excessive_reps(selection.reps)
            or selection.rest_seconds == 0
            or selection.rest_seconds > _MAX_REST_SECONDS
            for selection in decision.selections
        ):
            problems.append("Workout prescription exceeded the server limit")
        if (
            plan.training_volume is None
            and not plan.uses_prior_context
            and any(selection.sets > 6 for selection in decision.selections)
        ):
            problems.append("Workout volume contradicted the server default")
    if any(contains_unsafe_medical_claim(text) for text in texts):
        problems.append("Unsafe medical-safety or suitability claim")
    return problems


def response_assumptions(
    plan: RetrievalPlan,
    assumptions: list[str],
    medical_context: bool,
    uses_prior_context: bool,
) -> list[str]:
    """Apply workout defaults and remove contradictory model assumptions."""
    filtered = [
        assumption
        for assumption in assumptions
        if not (medical_context and _NO_RESTRICTIONS_PATTERN.search(assumption))
    ]
    if plan.intent != "workout":
        return filtered
    result = [text for field, text in _WORKOUT_DEFAULTS if getattr(plan, field) is None]
    if plan.restrictions is None and not medical_context:
        result.append("No known injuries or medical restrictions")
    if plan.equipment is None and not uses_prior_context:
        result.append(
            "Body-weight exercises preferred because no equipment was specified"
        )
    for assumption in filtered:
        if assumption not in result:
            result.append(assumption)
    return result


def response_warnings(medical_context: bool, warnings: list[str]) -> list[str]:
    """Append the mandatory medical-context warning once."""
    result = list(warnings)
    if medical_context and _MEDICAL_WARNING not in result:
        result.append(_MEDICAL_WARNING)
    return result
