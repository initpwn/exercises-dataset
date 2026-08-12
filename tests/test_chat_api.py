"""End-to-end contracts for grounded conversational exercise responses."""

from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from exercise_api.api_models import ExerciseOut, RetrievalPlan
from exercise_api.catalog import LoadedCatalog, sync_catalog
from exercise_api.config import DatabaseSettings, RetrievalSettings, Settings
from exercise_api.database import Database
from exercise_api.llm_gateway import LLMUnavailableError
from exercise_api.main import create_app
from exercise_api.prompts import (
    grounded_answer_messages,
    grounding_correction_message,
    retrieval_plan_messages,
)
from tests.factories import catalog_record
from tests.fakes import FakeLLM


def test_all_chat_prompt_stages_require_english_response_strings() -> None:
    plan = RetrievalPlan(intent="exercise_search", search_terms=["press"])
    candidate = ExerciseOut.model_validate(catalog_record("0001", "Press"))
    prompts = [
        retrieval_plan_messages([], "Muéstrame ejercicios"),
        grounded_answer_messages(plan, "Muéstrame ejercicios", [candidate], 2),
        [grounding_correction_message(["0001"], 2)],
    ]

    for messages in prompts:
        content = " ".join(message.content for message in messages)
        assert "all generated response strings must be english" in content.lower()

    assert "Muéstrame ejercicios" in prompts[0][-1].content


@pytest.fixture
def fake_llm() -> FakeLLM:
    return FakeLLM()


@pytest.fixture
async def chat_client(
    database: Database, fake_llm: FakeLLM
) -> AsyncIterator[AsyncClient]:
    settings = Settings(
        database=DatabaseSettings(url="sqlite:///unused.db"),
        retrieval=RetrievalSettings(candidate_limit=30, result_limit=2),
    )
    app = create_app(
        settings,
        lifespan_enabled=False,
        initialized_database=database,
        initial_readiness={"database": "ready", "catalog": "ready"},
        llm_gateway=fake_llm,
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


@pytest.mark.asyncio
async def test_workout_hydrates_catalog_fields_and_applies_defaults(
    chat_client: AsyncClient, fake_llm: FakeLLM
) -> None:
    fake_llm.queue(
        {
            "intent": "workout",
            "equipment": "dumbbell",
            "body_part": "chest",
            "search_terms": ["press"],
        },
        {
            "answer": "Use this conservative beginner session.",
            "name": "Beginner Chest",
            "estimated_duration_minutes": 35,
            "selections": [
                {
                    "id": "0001",
                    "sets": 3,
                    "reps": "8-12",
                    "rest_seconds": 90,
                    "notes": "Controlled tempo",
                }
            ],
            "assumptions": ["Beginner experience level"],
            "warnings": [],
        },
    )

    response = await chat_client.post(
        "/v1/chat", json={"message": "make a dumbbell chest workout"}
    )

    assert response.status_code == 200
    body = response.json()
    exercise = body["workout"]["exercises"][0]
    assert body["intent"] == "workout"
    assert body["results"] == []
    assert exercise["name"] == "Dumbbell bench press"
    assert exercise["instructions"] == ["First step", "Second step"]
    assert exercise["sets"] == 3
    assert exercise["reps"] == "8-12"
    assert exercise["rest_seconds"] == 90
    assert body["session_id"]
    assert len(fake_llm.messages) == 2
    generation_prompt = " ".join(message.content for message in fake_llm.messages[1])
    for expected_default in (
        "beginner",
        "general fitness",
        "30-45 minutes",
        "moderate volume and rest",
        "no known restrictions",
    ):
        assert expected_default in generation_prompt.lower()


@pytest.mark.asyncio
async def test_exercise_search_populates_results_and_leaves_workout_null(
    chat_client: AsyncClient, fake_llm: FakeLLM
) -> None:
    fake_llm.queue(
        {
            "intent": "exercise_search",
            "equipment": "body weight",
            "search_terms": ["chin up"],
        },
        {
            "answer": "Try this catalog exercise.",
            "selections": [{"id": "0003"}],
            "assumptions": [],
            "warnings": [],
        },
    )

    response = await chat_client.post(
        "/v1/chat", json={"message": "find a body weight chin up"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["intent"] == "exercise_search"
    assert body["workout"] is None
    assert [result["id"] for result in body["results"]] == ["0003"]
    assert body["results"][0]["name"] == "Chin up"
    assert len(fake_llm.messages) == 2


@pytest.mark.asyncio
async def test_results_are_capped_by_configured_result_limit(
    chat_client: AsyncClient, fake_llm: FakeLLM
) -> None:
    fake_llm.queue(
        {"intent": "exercise_search", "search_terms": ["exercise"]},
        {
            "answer": "Here are the ordered options.",
            "selections": [{"id": "0003"}, {"id": "0002"}, {"id": "0001"}],
            "assumptions": [],
            "warnings": [],
        },
    )

    response = await chat_client.post("/v1/chat", json={"message": "show me exercises"})

    assert response.status_code == 200
    assert [result["id"] for result in response.json()["results"]] == [
        "0003",
        "0002",
    ]
    assert len(fake_llm.messages) == 2


@pytest.mark.asyncio
async def test_existing_session_history_appears_in_next_planning_request(
    chat_client: AsyncClient, fake_llm: FakeLLM
) -> None:
    session_id = (await chat_client.post("/v1/sessions")).json()["id"]
    fake_llm.queue(
        {"intent": "exercise_search", "search_terms": ["press"]},
        {
            "answer": "The press is first.",
            "selections": [{"id": "0001"}],
            "assumptions": [],
            "warnings": [],
        },
        {"intent": "exercise_search", "search_terms": ["fly"]},
        {
            "answer": "The fly is another option.",
            "selections": [{"id": "0002"}],
            "assumptions": [],
            "warnings": [],
        },
    )
    first = await chat_client.post(
        "/v1/chat", json={"session_id": session_id, "message": "find a press"}
    )
    assert first.status_code == 200

    second = await chat_client.post(
        "/v1/chat", json={"session_id": session_id, "message": "another option"}
    )

    assert second.status_code == 200
    planning_history = fake_llm.messages[2]
    assert [(message.role, message.content) for message in planning_history[-3:]] == [
        ("user", "find a press"),
        ("assistant", "The press is first."),
        ("user", "another option"),
    ]
    assert len(fake_llm.messages) == 4


@pytest.mark.asyncio
async def test_follow_up_reuses_revalidated_ids_order_and_prescriptions(
    chat_client: AsyncClient, fake_llm: FakeLLM
) -> None:
    session_id = (await chat_client.post("/v1/sessions")).json()["id"]
    fake_llm.queue(
        {"intent": "workout", "equipment": "dumbbell"},
        {
            "answer": "Here is the first workout.",
            "name": "First Workout",
            "estimated_duration_minutes": 30,
            "selections": [
                {
                    "id": "0001",
                    "sets": 2,
                    "reps": "10",
                    "rest_seconds": 60,
                    "notes": "Slow tempo",
                },
                {
                    "id": "0002",
                    "sets": 3,
                    "reps": "6",
                    "rest_seconds": 90,
                    "notes": "Full range",
                },
            ],
        },
        {"intent": "workout", "uses_prior_context": True},
        {
            "answer": "Here is the shorter revision.",
            "name": "Shorter Revision",
            "estimated_duration_minutes": 20,
            "selections": [
                {
                    "id": "0001",
                    "sets": 3,
                    "reps": "10",
                    "rest_seconds": 60,
                    "notes": "One extra set",
                },
                {
                    "id": "0002",
                    "sets": 4,
                    "reps": "6",
                    "rest_seconds": 60,
                    "notes": "One extra set",
                },
            ],
        },
    )
    first = await chat_client.post(
        "/v1/chat",
        json={"session_id": session_id, "message": "make me a dumbbell workout"},
    )
    assert first.status_code == 200

    second = await chat_client.post(
        "/v1/chat",
        json={
            "session_id": session_id,
            "message": "turn those into a shorter workout and add one set",
        },
    )

    assert second.status_code == 200
    assert [exercise["id"] for exercise in second.json()["workout"]["exercises"]] == [
        "0001",
        "0002",
    ]
    planning_prompt = " ".join(message.content for message in fake_llm.messages[2])
    generation_prompt = " ".join(message.content for message in fake_llm.messages[3])
    for prompt in (planning_prompt, generation_prompt):
        assert '"id": "0001"' in prompt
        assert '"id": "0002"' in prompt
        assert '"order": 1' in prompt
        assert '"sets": 2' in prompt
        assert '"notes": "Slow tempo"' in prompt
    valid_id_section = generation_prompt.split("Never invent", maxsplit=1)[0]
    assert valid_id_section.index('"0001"') < valid_id_section.index('"0002"')


@pytest.mark.asyncio
async def test_explicit_follow_up_can_reference_a_non_latest_structured_turn(
    chat_client: AsyncClient, fake_llm: FakeLLM
) -> None:
    session_id = (await chat_client.post("/v1/sessions")).json()["id"]
    fake_llm.queue(
        {"intent": "exercise_search", "equipment": "dumbbell"},
        {"answer": "First option.", "selections": [{"id": "0001"}]},
        {
            "intent": "exercise_search",
            "equipment": "body weight",
            "search_terms": ["chin up"],
        },
        {"answer": "Latest option.", "selections": [{"id": "0003"}]},
        {
            "intent": "workout",
            "uses_prior_context": True,
            "referenced_ids": ["0001"],
        },
        {
            "answer": "Workout from the first option.",
            "name": "Older Option Workout",
            "estimated_duration_minutes": 30,
            "selections": [{"id": "0001", "sets": 3, "reps": "8", "rest_seconds": 60}],
        },
    )

    await chat_client.post(
        "/v1/chat",
        json={"session_id": session_id, "message": "show a dumbbell press"},
    )
    await chat_client.post(
        "/v1/chat",
        json={"session_id": session_id, "message": "show a bodyweight chin up"},
    )
    third = await chat_client.post(
        "/v1/chat",
        json={
            "session_id": session_id,
            "message": "turn the first result into a workout",
        },
    )

    assert third.status_code == 200
    assert [item["id"] for item in third.json()["workout"]["exercises"]] == ["0001"]
    valid_id_section = fake_llm.messages[5][0].content.split(
        "Never invent", maxsplit=1
    )[0]
    assert '"0001"' in valid_id_section
    assert '"0003"' not in valid_id_section


@pytest.mark.asyncio
async def test_follow_up_discards_prior_ids_removed_from_current_catalog(
    chat_client: AsyncClient, fake_llm: FakeLLM, database: Database
) -> None:
    session_id = (await chat_client.post("/v1/sessions")).json()["id"]
    fake_llm.queue(
        {"intent": "exercise_search"},
        {
            "answer": "Here are two options.",
            "selections": [{"id": "0003"}, {"id": "0001"}],
        },
        {"intent": "workout", "uses_prior_context": True},
        {
            "answer": "Here is the remaining option.",
            "name": "Current Catalog Workout",
            "estimated_duration_minutes": 20,
            "selections": [
                {
                    "id": "0001",
                    "sets": 2,
                    "reps": "10",
                    "rest_seconds": 60,
                }
            ],
        },
    )
    first = await chat_client.post(
        "/v1/chat",
        json={"session_id": session_id, "message": "show me exercises"},
    )
    assert first.status_code == 200
    await sync_catalog(
        database.session_factory,
        LoadedCatalog(
            "removed-prior-id",
            [
                catalog_record("0001", "Dumbbell bench press"),
                catalog_record("0002", "Dumbbell fly"),
            ],
        ),
    )

    second = await chat_client.post(
        "/v1/chat",
        json={"session_id": session_id, "message": "turn those into a workout"},
    )

    assert second.status_code == 200
    assert [item["id"] for item in second.json()["workout"]["exercises"]] == ["0001"]
    planning_prompt = " ".join(message.content for message in fake_llm.messages[2])
    generation_prompt = " ".join(message.content for message in fake_llm.messages[3])
    assert '"id": "0001"' in planning_prompt
    assert '"id": "0003"' not in planning_prompt
    valid_id_section = generation_prompt.split("Never invent", maxsplit=1)[0]
    assert '"0001"' in valid_id_section
    assert '"0003"' not in valid_id_section


@pytest.mark.asyncio
async def test_unknown_session_returns_404_without_calling_llm(
    chat_client: AsyncClient, fake_llm: FakeLLM
) -> None:
    response = await chat_client.post(
        "/v1/chat", json={"session_id": str(uuid4()), "message": "find curls"}
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "Session not found"}
    assert len(fake_llm.messages) == 0


@pytest.mark.asyncio
async def test_chat_message_over_four_thousand_characters_is_422(
    chat_client: AsyncClient, fake_llm: FakeLLM
) -> None:
    response = await chat_client.post("/v1/chat", json={"message": "x" * 4001})

    assert response.status_code == 422
    assert len(fake_llm.messages) == 0


@pytest.mark.asyncio
async def test_one_unknown_id_correction_returns_only_hydrated_valid_ids(
    chat_client: AsyncClient, fake_llm: FakeLLM
) -> None:
    fake_llm.queue(
        {"intent": "exercise_search", "body_part": "chest"},
        {
            "answer": "Initial answer.",
            "selections": [{"id": "invented"}, {"id": "0001"}],
            "assumptions": [],
            "warnings": [],
        },
        {
            "answer": "Corrected answer.",
            "selections": [{"id": "0002"}, {"id": "0001"}],
            "assumptions": [],
            "warnings": [],
        },
    )

    response = await chat_client.post(
        "/v1/chat", json={"message": "show chest exercises"}
    )

    assert response.status_code == 200
    body = response.json()
    assert [result["id"] for result in body["results"]] == ["0002", "0001"]
    assert [result["name"] for result in body["results"]] == [
        "Dumbbell fly",
        "Dumbbell bench press",
    ]
    assert body["answer"] == "Corrected answer."
    assert len(fake_llm.messages) == 3
    correction_prompt = fake_llm.messages[2][-1].content
    assert "0001" in correction_prompt
    assert "0002" in correction_prompt
    assert "invented" not in correction_prompt


@pytest.mark.asyncio
async def test_existing_id_outside_candidates_requires_correction(
    chat_client: AsyncClient, fake_llm: FakeLLM
) -> None:
    fake_llm.queue(
        {"intent": "exercise_search", "body_part": "chest"},
        {
            "answer": "Out-of-scope answer.",
            "selections": [{"id": "0003"}],
            "assumptions": [],
            "warnings": [],
        },
        {
            "answer": "Grounded answer.",
            "selections": [{"id": "0001"}],
            "assumptions": [],
            "warnings": [],
        },
    )

    response = await chat_client.post(
        "/v1/chat", json={"message": "show chest exercises"}
    )

    assert response.status_code == 200
    assert [result["id"] for result in response.json()["results"]] == ["0001"]
    assert response.json()["answer"] == "Grounded answer."
    assert len(fake_llm.messages) == 3


@pytest.mark.asyncio
async def test_repeated_unknown_ids_return_502_after_one_correction(
    chat_client: AsyncClient, fake_llm: FakeLLM
) -> None:
    fake_llm.queue(
        {"intent": "exercise_search", "body_part": "chest"},
        {
            "answer": "Initial answer.",
            "selections": [{"id": "invented"}],
            "assumptions": [],
            "warnings": [],
        },
        {
            "answer": "Still invalid.",
            "selections": [{"id": "still-invented"}],
            "assumptions": [],
            "warnings": [],
        },
    )

    response = await chat_client.post(
        "/v1/chat", json={"message": "show chest exercises"}
    )

    assert response.status_code == 502
    assert len(fake_llm.messages) == 3


@pytest.mark.asyncio
async def test_no_candidates_returns_empty_warning_after_planning_call_only(
    chat_client: AsyncClient, fake_llm: FakeLLM
) -> None:
    fake_llm.queue(
        {
            "intent": "workout",
            "equipment": "kettlebell",
            "search_terms": ["swing"],
        }
    )

    response = await chat_client.post(
        "/v1/chat", json={"message": "make a kettlebell swing workout"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["intent"] == "workout"
    assert body["results"] == []
    assert body["workout"] is None
    assert body["warnings"]
    assert len(fake_llm.messages) == 1


@pytest.mark.asyncio
async def test_nonsense_returns_no_match_without_generation_call(
    chat_client: AsyncClient, fake_llm: FakeLLM
) -> None:
    fake_llm.queue({"intent": "exercise_search", "search_terms": ["zyxqv nonsense"]})

    response = await chat_client.post("/v1/chat", json={"message": "zyxqv nonsense"})

    assert response.status_code == 200
    assert response.json()["results"] == []
    assert response.json()["warnings"]
    assert len(fake_llm.messages) == 1


@pytest.mark.parametrize(
    "hallucinated_plan",
    [
        {"intent": "exercise_search", "search_terms": ["pectorals"]},
        {"intent": "exercise_search", "equipment": "dumbbell"},
    ],
)
@pytest.mark.asyncio
async def test_planner_hallucinations_cannot_make_unrelated_text_relevant(
    chat_client: AsyncClient,
    fake_llm: FakeLLM,
    hallucinated_plan: dict[str, object],
) -> None:
    fake_llm.queue(
        hallucinated_plan,
        {"answer": "Arbitrary result.", "selections": [{"id": "0001"}]},
    )

    response = await chat_client.post(
        "/v1/chat", json={"message": "what is the weather zyxqv"}
    )

    assert response.status_code == 200
    assert response.json()["results"] == []
    assert response.json()["warnings"]
    assert len(fake_llm.messages) == 1


@pytest.mark.asyncio
async def test_broad_workout_request_still_reaches_grounded_generation(
    chat_client: AsyncClient, fake_llm: FakeLLM
) -> None:
    fake_llm.queue(
        {"intent": "workout"},
        {
            "answer": "Here is a general workout.",
            "name": "General Workout",
            "estimated_duration_minutes": 30,
            "selections": [
                {
                    "id": "0003",
                    "sets": 3,
                    "reps": "8-12",
                    "rest_seconds": 90,
                }
            ],
        },
    )

    response = await chat_client.post("/v1/chat", json={"message": "make me a workout"})

    assert response.status_code == 200
    assert response.json()["workout"]["exercises"][0]["id"] == "0003"
    assert len(fake_llm.messages) == 2
    valid_id_section = " ".join(
        message.content for message in fake_llm.messages[1]
    ).split("Never invent", maxsplit=1)[0]
    assert '"0003"' in valid_id_section
    assert '"0001"' not in valid_id_section


@pytest.mark.asyncio
async def test_current_text_constraint_cannot_be_dropped_by_planner(
    chat_client: AsyncClient, fake_llm: FakeLLM
) -> None:
    fake_llm.queue(
        {"intent": "exercise_search"},
        {
            "answer": "Here is a dumbbell option.",
            "selections": [{"id": "0001"}],
        },
    )

    response = await chat_client.post(
        "/v1/chat", json={"message": "show me exercises using dumbbells"}
    )

    assert response.status_code == 200
    generation_prompt = " ".join(message.content for message in fake_llm.messages[1])
    assert '"0001"' in generation_prompt
    assert '"0002"' in generation_prompt
    assert '"0003"' not in generation_prompt


@pytest.mark.asyncio
async def test_bodyweight_alias_uses_catalog_canonical_equipment(
    chat_client: AsyncClient, fake_llm: FakeLLM
) -> None:
    fake_llm.queue(
        {"intent": "exercise_search", "equipment": "bodyweight"},
        {"answer": "Try this option.", "selections": [{"id": "0003"}]},
    )

    response = await chat_client.post(
        "/v1/chat", json={"message": "find a bodyweight movement"}
    )

    assert response.status_code == 200
    assert [result["id"] for result in response.json()["results"]] == ["0003"]
    planning_prompt = fake_llm.messages[0][0].content.lower()
    assert "body weight" in planning_prompt


@pytest.mark.asyncio
async def test_workout_defaults_are_enforced_when_model_omits_assumptions(
    chat_client: AsyncClient, fake_llm: FakeLLM
) -> None:
    fake_llm.queue(
        {"intent": "workout"},
        {
            "answer": "Here is a conservative workout.",
            "name": "General Workout",
            "estimated_duration_minutes": 30,
            "selections": [
                {
                    "id": "0003",
                    "sets": 3,
                    "reps": "6-8",
                    "rest_seconds": 90,
                }
            ],
            "assumptions": [],
        },
    )

    response = await chat_client.post("/v1/chat", json={"message": "make me a workout"})

    assert response.status_code == 200
    assumptions = response.json()["assumptions"]
    assert assumptions[:6] == [
        "Beginner experience level",
        "General fitness goal",
        "30-45 minute session",
        "Moderate training volume and rest periods",
        "No known injuries or medical restrictions",
        "Body-weight exercises preferred because no equipment was specified",
    ]


@pytest.mark.asyncio
async def test_hallucinated_preferences_do_not_suppress_defaults_or_extreme_correction(
    chat_client: AsyncClient, fake_llm: FakeLLM
) -> None:
    fake_llm.queue(
        {
            "intent": "workout",
            "experience_level": "advanced",
            "fitness_goal": "competition",
            "duration_minutes": 180,
            "training_volume": "extreme",
            "restrictions": "none",
        },
        {
            "answer": "Extreme workout.",
            "name": "Extreme Workout",
            "estimated_duration_minutes": 600,
            "selections": [
                {
                    "id": "0003",
                    "sets": 1_000_000,
                    "reps": "1000",
                    "rest_seconds": 0,
                }
            ],
        },
        {
            "answer": "Here is a conservative workout.",
            "name": "General Workout",
            "estimated_duration_minutes": 35,
            "selections": [
                {
                    "id": "0003",
                    "sets": 3,
                    "reps": "8-12",
                    "rest_seconds": 90,
                }
            ],
        },
    )

    response = await chat_client.post("/v1/chat", json={"message": "make me a workout"})

    assert response.status_code == 200
    assert response.json()["workout"]["estimated_duration_minutes"] == 35
    assert response.json()["workout"]["exercises"][0]["sets"] == 3
    assert response.json()["assumptions"][:5] == [
        "Beginner experience level",
        "General fitness goal",
        "30-45 minute session",
        "Moderate training volume and rest periods",
        "No known injuries or medical restrictions",
    ]
    assert len(fake_llm.messages) == 3


@pytest.mark.asyncio
async def test_typed_non_english_medical_context_adds_server_warning(
    chat_client: AsyncClient, fake_llm: FakeLLM
) -> None:
    fake_llm.queue(
        {
            "intent": "exercise_search",
            "body_part": "chest",
            "medical_context": True,
            "search_terms": ["chest"],
        },
        {
            "answer": "Here are general exercise options.",
            "selections": [{"id": "0001"}],
            "assumptions": ["No known medical restrictions"],
            "warnings": [],
        },
    )

    response = await chat_client.post(
        "/v1/chat",
        json={"message": "Estoy embarazada; muéstrame ejercicios para el pecho"},
    )

    assert response.status_code == 200
    warnings = " ".join(response.json()["warnings"]).lower()
    assert "not medical advice" in warnings
    assert "professional guidance" in warnings
    assert "no known medical restrictions" not in response.text.lower()


@pytest.mark.asyncio
async def test_recent_user_medical_context_applies_to_follow_up(
    chat_client: AsyncClient, fake_llm: FakeLLM
) -> None:
    session_id = (await chat_client.post("/v1/sessions")).json()["id"]
    fake_llm.queue(
        {
            "intent": "exercise_search",
            "body_part": "chest",
            "medical_context": True,
        },
        {"answer": "General options.", "selections": [{"id": "0001"}]},
        {"intent": "exercise_search"},
        {"answer": "More general options.", "selections": [{"id": "0002"}]},
    )
    first = await chat_client.post(
        "/v1/chat",
        json={
            "session_id": session_id,
            "message": "I have arthritis; show chest exercises",
        },
    )
    assert first.status_code == 200

    second = await chat_client.post(
        "/v1/chat",
        json={"session_id": session_id, "message": "show me another exercise"},
    )

    assert second.status_code == 200
    assert any(
        "professional guidance" in warning.lower()
        for warning in second.json()["warnings"]
    )


@pytest.mark.asyncio
async def test_unsafe_medical_claim_is_corrected_once(
    chat_client: AsyncClient, fake_llm: FakeLLM
) -> None:
    fake_llm.queue(
        {"intent": "workout", "body_part": "chest", "medical_context": True},
        {
            "answer": "This workout is medically safe for pregnancy.",
            "name": "Pregnancy Workout",
            "estimated_duration_minutes": 20,
            "selections": [
                {
                    "id": "0001",
                    "sets": 2,
                    "reps": "8",
                    "rest_seconds": 90,
                    "notes": "This will not aggravate your condition.",
                }
            ],
            "assumptions": ["No known restrictions"],
        },
        {
            "answer": "This is general exercise information.",
            "name": "General Chest Session",
            "estimated_duration_minutes": 35,
            "selections": [
                {
                    "id": "0001",
                    "sets": 2,
                    "reps": "8",
                    "rest_seconds": 90,
                    "notes": "Use a controlled tempo.",
                }
            ],
            "assumptions": ["No known restrictions"],
        },
    )

    response = await chat_client.post(
        "/v1/chat", json={"message": "I am pregnant; make a chest workout"}
    )

    assert response.status_code == 200
    serialized = response.text.lower()
    assert "medically safe" not in serialized
    assert "will not aggravate" not in serialized
    assert "no known restrictions" not in serialized
    assert "professional guidance" in serialized
    assert len(fake_llm.messages) == 3
    assert "unsafe medical" in fake_llm.messages[2][-1].content.lower()


@pytest.mark.parametrize(
    "unsafe_claim",
    [
        "This is safe to perform if you have a knee injury.",
        "This is suitable if you have arthritis.",
        "This is recommended for arthritis.",
    ],
)
@pytest.mark.asyncio
async def test_conditional_safe_claim_is_corrected_once(
    chat_client: AsyncClient, fake_llm: FakeLLM, unsafe_claim: str
) -> None:
    fake_llm.queue(
        {"intent": "exercise_search", "body_part": "chest"},
        {
            "answer": unsafe_claim,
            "selections": [{"id": "0001"}],
        },
        {
            "answer": "This is general exercise information.",
            "selections": [{"id": "0001"}],
        },
    )

    response = await chat_client.post(
        "/v1/chat", json={"message": "My knee hurts; show chest exercises"}
    )

    assert response.status_code == 200
    assert unsafe_claim.lower() not in response.text.lower()
    assert "professional guidance" in response.text.lower()
    assert len(fake_llm.messages) == 3


@pytest.mark.asyncio
async def test_repeated_unsafe_medical_claim_returns_502_without_persistence(
    chat_client: AsyncClient, fake_llm: FakeLLM
) -> None:
    session_id = (await chat_client.post("/v1/sessions")).json()["id"]
    unsafe = {
        "answer": "This is appropriate for arthritis.",
        "selections": [{"id": "0001"}],
    }
    fake_llm.queue({"intent": "exercise_search", "body_part": "chest"}, unsafe, unsafe)

    response = await chat_client.post(
        "/v1/chat",
        json={"session_id": session_id, "message": "show chest exercises"},
    )

    assert response.status_code == 502
    stored = await chat_client.get(f"/v1/sessions/{session_id}")
    assert stored.json()["messages"] == []
    assert len(fake_llm.messages) == 3


@pytest.mark.asyncio
async def test_medical_no_match_still_adds_professional_warning(
    chat_client: AsyncClient, fake_llm: FakeLLM
) -> None:
    fake_llm.queue(
        {
            "intent": "workout",
            "equipment": "kettlebell",
            "medical_context": True,
        }
    )

    response = await chat_client.post(
        "/v1/chat", json={"message": "Estoy lesionado; usa una pesa rusa"}
    )

    assert response.status_code == 200
    assert response.json()["workout"] is None
    assert any(
        "professional guidance" in warning.lower()
        for warning in response.json()["warnings"]
    )


@pytest.mark.asyncio
async def test_llm_timeout_returns_503_after_one_attempt(
    chat_client: AsyncClient,
    fake_llm: FakeLLM,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    async def unavailable(*_: object) -> object:
        nonlocal calls
        calls += 1
        raise LLMUnavailableError("Model service is unavailable")

    monkeypatch.setattr(fake_llm, "generate_json", unavailable)

    response = await chat_client.post("/v1/chat", json={"message": "find curls"})

    assert response.status_code == 503
    assert calls == 1


@pytest.mark.asyncio
async def test_injury_text_adds_professional_guidance_warning(
    chat_client: AsyncClient, fake_llm: FakeLLM
) -> None:
    fake_llm.queue(
        {
            "intent": "exercise_search",
            "equipment": "body weight",
            "search_terms": ["chin up"],
        },
        {
            "answer": "Here is a catalog result, not medical advice.",
            "selections": [{"id": "0003"}],
            "assumptions": [],
            "warnings": [],
        },
    )

    response = await chat_client.post(
        "/v1/chat",
        json={"message": "I have a shoulder injury; can I do chin ups?"},
    )

    assert response.status_code == 200
    warnings = " ".join(response.json()["warnings"]).lower()
    assert "not medical advice" in warnings
    assert "professional guidance" in warnings
    assert len(fake_llm.messages) == 2


@pytest.mark.parametrize(
    "message",
    [
        "I have a heart condition; suggest a chest exercise.",
        "I have asthma; suggest a chest exercise.",
        "I have diabetes; suggest a chest exercise.",
        "My shoulder is painful; suggest a chest exercise.",
        "I have multiple medical conditions; suggest a chest exercise.",
        "I am rehabbing an injury; suggest a chest exercise.",
        "I am rehabbing after surgery; suggest a chest exercise.",
        "I tore my ACL; suggest a chest exercise.",
        "My knee hurts; suggest a chest exercise.",
        "I sprained my ankle; suggest a chest exercise.",
    ],
)
@pytest.mark.asyncio
async def test_named_medical_conditions_add_professional_guidance_warning(
    chat_client: AsyncClient, fake_llm: FakeLLM, message: str
) -> None:
    fake_llm.queue(
        {"intent": "exercise_search", "body_part": "chest"},
        {
            "answer": "Here are catalog exercises.",
            "selections": [{"id": "0001"}],
            "assumptions": [],
            "warnings": [],
        },
    )

    response = await chat_client.post("/v1/chat", json={"message": message})

    assert response.status_code == 200
    warnings = " ".join(response.json()["warnings"]).lower()
    assert "not medical advice" in warnings
    assert "professional guidance" in warnings


@pytest.mark.parametrize(
    "message",
    [
        "Suggest a chest exercise.",
        "I am painting a room; suggest a chest exercise.",
    ],
)
@pytest.mark.asyncio
async def test_non_medical_request_does_not_add_medical_warning(
    chat_client: AsyncClient, fake_llm: FakeLLM, message: str
) -> None:
    fake_llm.queue(
        {"intent": "exercise_search", "body_part": "chest"},
        {
            "answer": "Here are catalog exercises.",
            "selections": [{"id": "0001"}],
            "assumptions": [],
            "warnings": [],
        },
    )

    response = await chat_client.post("/v1/chat", json={"message": message})

    assert response.status_code == 200
    assert response.json()["warnings"] == []


@pytest.mark.asyncio
async def test_failed_output_leaves_session_without_successful_exchange(
    chat_client: AsyncClient, fake_llm: FakeLLM
) -> None:
    session_id = (await chat_client.post("/v1/sessions")).json()["id"]
    fake_llm.queue(
        {"intent": "exercise_search", "body_part": "chest"},
        {
            "answer": "Initial answer.",
            "selections": [{"id": "invented"}],
            "assumptions": [],
            "warnings": [],
        },
        {
            "answer": "Still invalid.",
            "selections": [{"id": "still-invented"}],
            "assumptions": [],
            "warnings": [],
        },
    )

    failed = await chat_client.post(
        "/v1/chat",
        json={"session_id": session_id, "message": "show chest exercises"},
    )
    stored = await chat_client.get(f"/v1/sessions/{session_id}")

    assert failed.status_code == 502
    assert stored.status_code == 200
    assert stored.json()["messages"] == []
    assert len(fake_llm.messages) == 3
