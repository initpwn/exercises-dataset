"""End-to-end contracts for grounded conversational exercise responses."""

from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from exercise_api.config import DatabaseSettings, RetrievalSettings, Settings
from exercise_api.database import Database
from exercise_api.dependencies import (
    get_exercise_repository,
    get_session_repository,
)
from exercise_api.exercise_repository import ExerciseRepository
from exercise_api.llm_gateway import LLMUnavailableError
from exercise_api.main import create_app
from exercise_api.session_repository import SessionRepository
from tests.fakes import FakeLLM


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
    app = create_app(settings, lifespan_enabled=False)
    app.state.llm_gateway = fake_llm
    app.dependency_overrides[get_exercise_repository] = lambda: ExerciseRepository(
        database.session_factory
    )
    app.dependency_overrides[get_session_repository] = lambda: SessionRepository(
        database.session_factory
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
