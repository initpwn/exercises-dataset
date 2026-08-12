"""Hybrid retrieval tests over a synchronized exercise catalog."""

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from exercise_api.api_models import RetrievalPlan
from exercise_api.catalog import LoadedCatalog, sync_catalog
from exercise_api.database import Database
from exercise_api.exercise_repository import ExerciseRepository
from exercise_api.retrieval import RetrievalService
from tests.factories import catalog_record


@pytest.fixture
async def retrieval(tmp_path: Path) -> AsyncIterator[RetrievalService]:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'retrieval.db'}")
    await database.create_schema()
    await sync_catalog(
        database.session_factory,
        LoadedCatalog(
            "retrieval-fixture",
            [
                catalog_record("0001", "dumbbell chest press"),
                catalog_record("0002", "barbell chest press", equipment="barbell"),
                catalog_record(
                    "0003",
                    "dumbbell biceps curl",
                    category="upper arms",
                    body_part="upper arms",
                    muscle_group="biceps",
                    target="biceps",
                ),
                catalog_record(
                    "0004",
                    "standing cable pull",
                    category="upper arms",
                    body_part="upper arms",
                    equipment="cable",
                    muscle_group="back",
                    secondary_muscles=["forearms"],
                    target="lats",
                    instructions=["Keep your biceps curled throughout the movement."],
                ),
                catalog_record(
                    "0005",
                    "balanced movement",
                    category="test",
                    body_part="arms",
                    equipment="test rig",
                    muscle_group="biceps",
                    secondary_muscles=["triceps"],
                    target="neutral",
                    instructions=["Perform the movement."],
                ),
                catalog_record(
                    "0006",
                    "biceps triceps exercise",
                    category="test",
                    body_part="arms",
                    equipment="test rig",
                    muscle_group="back",
                    secondary_muscles=["forearms"],
                    target="neutral",
                    instructions=["Perform the movement."],
                ),
                catalog_record(
                    "0007",
                    "body weight push up",
                    equipment="body weight",
                ),
                catalog_record(
                    "0008",
                    "suspension chest press",
                    category="strength",
                    equipment="suspension trainer",
                ),
            ],
        ),
    )
    yield RetrievalService(ExerciseRepository(database.session_factory))
    await database.dispose()


@pytest.mark.asyncio
async def test_explicit_constraints_are_hard_filters(
    retrieval: RetrievalService,
) -> None:
    plan = RetrievalPlan(
        intent="exercise_search", equipment=" DUMBBELL ", body_part="Chest"
    )
    results = await retrieval.retrieve(plan, "press for my chest", candidate_limit=10)
    assert results
    assert all(
        item.equipment == "dumbbell" and item.body_part == "chest" for item in results
    )


@pytest.mark.parametrize(
    ("plan_equipment", "query", "expected_equipment"),
    [
        ("dumbbells", "show me chest presses", "dumbbell"),
        (None, "show me a bodyweight exercise", "body weight"),
    ],
)
@pytest.mark.asyncio
async def test_catalog_aliases_normalize_plans_and_current_text(
    retrieval: RetrievalService,
    plan_equipment: str | None,
    query: str,
    expected_equipment: str,
) -> None:
    plan = RetrievalPlan(intent="exercise_search", equipment=plan_equipment)

    results = await retrieval.retrieve(plan, query, candidate_limit=10)

    assert results
    assert all(item.equipment == expected_equipment for item in results)


@pytest.mark.asyncio
async def test_current_explicit_constraint_overrides_conflicting_planner_value(
    retrieval: RetrievalService,
) -> None:
    plan = RetrievalPlan(intent="exercise_search", equipment="barbell")

    results = await retrieval.retrieve(
        plan, "show me exercises using dumbbells", candidate_limit=10
    )

    assert results
    assert all(item.equipment == "dumbbell" for item in results)


@pytest.mark.asyncio
async def test_text_constraint_does_not_create_cross_field_hard_filters(
    retrieval: RetrievalService,
) -> None:
    results = await retrieval.retrieve(
        RetrievalPlan(intent="exercise_search"),
        "show me chest exercises",
        candidate_limit=10,
    )

    assert "0008" in [item.id for item in results]


@pytest.mark.asyncio
async def test_name_and_target_outweigh_instruction_only_match(
    retrieval: RetrievalService,
) -> None:
    results = await retrieval.retrieve(
        RetrievalPlan(intent="exercise_search", search_terms=["biceps curl"]),
        "biceps curl",
        candidate_limit=3,
    )
    assert results[0].name == "dumbbell biceps curl"


@pytest.mark.asyncio
async def test_muscle_group_and_secondary_muscles_score_separately(
    retrieval: RetrievalService,
) -> None:
    results = await retrieval.retrieve(
        RetrievalPlan(
            intent="exercise_search",
            equipment="test rig",
            search_terms=["biceps", "triceps"],
        ),
        "",
        candidate_limit=2,
    )
    assert [item.name for item in results] == [
        "balanced movement",
        "biceps triceps exercise",
    ]


@pytest.mark.asyncio
async def test_no_results_does_not_loosen_explicit_constraints(
    retrieval: RetrievalService,
) -> None:
    results = await retrieval.retrieve(
        RetrievalPlan(intent="exercise_search", equipment="kettlebell"),
        "chest press",
        candidate_limit=10,
    )
    assert results == []


@pytest.mark.asyncio
async def test_unrelated_request_fails_deterministic_relevance_floor(
    retrieval: RetrievalService,
) -> None:
    results = await retrieval.retrieve(
        RetrievalPlan(intent="exercise_search", search_terms=["zyxqv nonsense"]),
        "zyxqv nonsense",
        candidate_limit=10,
    )

    assert results == []


@pytest.mark.parametrize(
    "query",
    [
        "show me exercises",
        "make me a workout",
        "help me get fit",
        "suggest something for general fitness",
    ],
)
@pytest.mark.asyncio
async def test_valid_broad_requests_bypass_lexical_relevance_floor(
    retrieval: RetrievalService, query: str
) -> None:
    intent = "workout" if "workout" in query else "exercise_search"

    results = await retrieval.retrieve(
        RetrievalPlan(intent=intent), query, candidate_limit=3
    )

    assert [item.id for item in results] == ["0001", "0002", "0003"]


@pytest.mark.asyncio
async def test_equal_scores_are_ordered_by_exercise_id(
    retrieval: RetrievalService,
) -> None:
    results = await retrieval.retrieve(
        RetrievalPlan(intent="exercise_search", body_part="chest"),
        "",
        candidate_limit=2,
    )
    assert [item.id for item in results] == ["0001", "0002"]


@pytest.mark.asyncio
async def test_candidate_limit_caps_ranked_results(
    retrieval: RetrievalService,
) -> None:
    results = await retrieval.retrieve(
        RetrievalPlan(intent="exercise_search"),
        "exercise",
        candidate_limit=2,
    )
    assert len(results) == 2
