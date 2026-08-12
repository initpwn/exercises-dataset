# Exercise Chat REST API Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an English-only FastAPI service that exposes direct exercise catalog endpoints and persistent conversational exercise/workout generation through any configured OpenAI-compatible LLM.

**Architecture:** `data/exercises.json` is validated and transactionally synchronized into SQLite or PostgreSQL. Direct endpoints and hybrid retrieval query that database; a provider-neutral HTTP gateway performs two LLM calls for retrieval planning and grounded response generation, and catalog hydration prevents invented exercise data. SQLAlchemy persists catalog data and server-side conversation sessions.

**Tech Stack:** Python 3.11+, FastAPI, Pydantic 2, SQLAlchemy 2 async, aiosqlite, psycopg 3, httpx, jsonschema, RapidFuzz, pytest, pytest-asyncio, Ruff, mypy, and testcontainers-postgres.

## Global Constraints

- English only; API exercise instructions come from `instruction_steps.en`.
- `data/exercises.json` remains the authoritative catalog source.
- Support SQLite and PostgreSQL through `config.toml`; `EXERCISE_API__<SECTION>__<KEY>` environment variables override TOML.
- Direct catalog endpoints never call the LLM and remain available during LLM outages.
- Chat returns conversational text and renderable structured exercise data.
- Every returned exercise ID must exist in the synchronized catalog; catalog-owned fields must never come from LLM output.
- Missing workout details produce immediate conservative defaults, not follow-up questions.
- No authentication in version 1.
- Preserve media attribution in every complete exercise object.
- Keep `output/` and `tmp/` out of all commits unless separately requested.

---

## File structure

- `pyproject.toml` — runtime/test dependencies and tool configuration.
- `config.toml` — safe local defaults; secrets are overridden by environment variables.
- `src/exercise_api/config.py` — typed TOML and environment loading.
- `src/exercise_api/database.py` — async engine/session construction and schema initialization.
- `src/exercise_api/db_models.py` — exercise, catalog-state, session, and message tables.
- `src/exercise_api/api_models.py` — public requests/responses and internal LLM contracts.
- `src/exercise_api/catalog.py` — JSON validation, hashing, and transactional synchronization.
- `src/exercise_api/exercise_repository.py` — direct filtering, pagination, random, distinct, and ID hydration queries.
- `src/exercise_api/session_repository.py` — persistent session/message operations.
- `src/exercise_api/retrieval.py` — constraint enforcement and weighted candidate ranking.
- `src/exercise_api/llm_gateway.py` — OpenAI-compatible HTTP calls, JSON extraction, repair, and provider errors.
- `src/exercise_api/prompts.py` — retrieval-plan, grounded-answer, repair, and correction prompts.
- `src/exercise_api/chat_service.py` — two-stage orchestration, defaults, warnings, validation, and exchange persistence.
- `src/exercise_api/dependencies.py` — FastAPI dependency providers.
- `src/exercise_api/routes/` — health, exercise, session, and chat routers.
- `src/exercise_api/main.py` — application factory and readiness-aware lifespan.
- `tests/` — unit and SQLite API integration tests.
- `tests/factories.py` — complete catalog-record builders shared by tests.
- `tests/fakes.py` — deterministic HTTP transports and fake LLM gateway.
- `tests/postgres/` — Docker-backed PostgreSQL parity tests.
- `scripts/smoke_llm.py` — opt-in live provider smoke test.

### Task 1: Project configuration and typed settings

**Files:**
- Create: `pyproject.toml`
- Create: `config.toml`
- Modify: `.gitignore`
- Create: `src/exercise_api/__init__.py`
- Create: `src/exercise_api/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `load_settings(path: Path = Path("config.toml"), environ: Mapping[str, str] | None = None) -> Settings`.
- Produces: `Settings.database.url`, `Settings.llm.{base_url,api_key,model,timeout_seconds}`, `Settings.retrieval.{candidate_limit,result_limit}`, and `Settings.sessions.history_message_limit`.

- [ ] **Step 1: Write failing TOML and environment override tests**

```python
def test_environment_overrides_nested_toml(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('[llm]\nmodel="toml-model"\ntimeout_seconds=60\n', encoding="utf-8")
    settings = load_settings(
        path,
        {"EXERCISE_API__LLM__MODEL": "env-model", "EXERCISE_API__LLM__TIMEOUT_SECONDS": "15"},
    )
    assert settings.llm.model == "env-model"
    assert settings.llm.timeout_seconds == 15


def test_rejects_non_positive_limits(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('[retrieval]\ncandidate_limit=0\nresult_limit=10\n', encoding="utf-8")
    with pytest.raises(ValidationError):
        load_settings(path, {})
```

- [ ] **Step 2: Run the tests and confirm the missing module failure**

Run: `rtk python -m pytest tests/test_config.py -q`

Expected: FAIL because `exercise_api.config` does not exist.

- [ ] **Step 3: Add dependencies, defaults, and the typed loader**

Use Pydantic models with positive integer fields. Load TOML with `tomllib`, split environment keys on `__`, lowercase section/key names, merge overrides, then call `Settings.model_validate(data)`. Add these dependency groups to `pyproject.toml`:

```toml
[project]
requires-python = ">=3.11"
dependencies = [
  "fastapi>=0.115,<1", "uvicorn[standard]>=0.34,<1", "pydantic>=2.10,<3",
  "sqlalchemy[asyncio]>=2.0,<3", "aiosqlite>=0.20,<1", "psycopg[binary]>=3.2,<4",
  "httpx>=0.28,<1", "jsonschema>=4.23,<5", "rapidfuzz>=3.10,<4"
]

[project.optional-dependencies]
dev = [
  "pytest>=8.3,<9", "pytest-asyncio>=0.25,<1", "testcontainers[postgres]>=4.9,<5",
  "ruff>=0.9,<1", "mypy>=1.14,<2"
]

[tool.pytest.ini_options]
pythonpath = ["src"]
asyncio_mode = "auto"
```

Commit `config.toml` with the approved Ollama-safe defaults and add `exercise_api.db`, `.pytest_cache/`, `.mypy_cache/`, and `.ruff_cache/` to `.gitignore`.

Install the project after creating `pyproject.toml`:

Run: `rtk python -m pip install -e ".[dev]"`

Expected: installation completes with the `exercise_api` package importable.

- [ ] **Step 4: Verify settings and static checks**

Run: `rtk python -m pytest tests/test_config.py -q`

Expected: PASS.

Run: `rtk python -m ruff check src tests/test_config.py`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
rtk git add pyproject.toml config.toml .gitignore src/exercise_api tests/test_config.py
rtk git commit -m "feat(api): add typed service configuration"
```

### Task 2: Database schema and catalog synchronization

**Files:**
- Create: `src/exercise_api/database.py`
- Create: `src/exercise_api/db_models.py`
- Create: `src/exercise_api/catalog.py`
- Test: `tests/test_catalog_sync.py`
- Test fixture: `tests/fixtures/catalog.json`
- Test helper: `tests/factories.py`

**Interfaces:**
- Consumes: `Settings.database.url` from Task 1.
- Produces: `Database(url: str)`, `Database.session_factory`, `Database.create_schema()`, and `Database.dispose()`.
- Produces: `async_database_url(url: str) -> str` supporting approved SQLite URLs and Testcontainers PostgreSQL URLs.
- Produces: `load_catalog(data_path: Path, schema_path: Path) -> LoadedCatalog` and `sync_catalog(session_factory: async_sessionmaker[AsyncSession], catalog: LoadedCatalog) -> bool`.
- Produces for tests: `catalog_record(id: str, name: str, **overrides: object) -> CatalogRecord`, with valid defaults for every required catalog field.

- [ ] **Step 1: Write failing validation and synchronization tests**

```python
@pytest.mark.asyncio
async def test_sync_inserts_updates_and_deletes_records(tmp_path: Path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    await database.create_schema()
    first = LoadedCatalog("hash-1", [catalog_record("0001", "Curl"), catalog_record("0002", "Squat")])
    assert await sync_catalog(database.session_factory, first) is True
    changed = LoadedCatalog("hash-2", [catalog_record("0001", "Strict curl"), catalog_record("0003", "Row")])
    assert await sync_catalog(database.session_factory, changed) is True
    async with database.session_factory() as session:
        rows = (await session.scalars(select(ExerciseRow).order_by(ExerciseRow.id))).all()
    assert [(row.id, row.name) for row in rows] == [("0001", "Strict curl"), ("0003", "Row")]
    assert await sync_catalog(database.session_factory, changed) is False


def test_load_catalog_rejects_schema_violation(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text('[{"id":"not-four-digits"}]', encoding="utf-8")
    with pytest.raises(CatalogValidationError):
        load_catalog(path, Path("data/exercises.schema.json"))
```

- [ ] **Step 2: Run the focused test and verify failure**

Run: `rtk python -m pytest tests/test_catalog_sync.py -q`

Expected: FAIL because database models and synchronization functions are absent.

- [ ] **Step 3: Implement the async schema and transactional sync**

Create `ExerciseRow` with indexed string columns for `category`, `body_part`, `equipment`, `muscle_group`, and `target`; JSON columns for `secondary_muscles` and English `instructions`; and catalog media/timestamp fields. Create `CatalogStateRow(key, content_hash)`.

`load_catalog` must validate the complete JSON array with `data/exercises.schema.json`, project only `instruction_steps.en`, and calculate SHA-256 from the raw bytes. `sync_catalog` must lock/read catalog state, return `False` for an unchanged hash, update matching rows, insert missing rows, delete stale IDs, update the hash, and commit once. Roll back the entire operation on any exception.

Implement `catalog_record` with deterministic valid defaults: category/body part `chest`, equipment `dumbbell`, muscle group `triceps`, secondary muscles `["triceps"]`, target `pectorals`, instructions `["First step", "Second step"]`, media ID `f"media-{id}"`, matching image/GIF paths, Gym visual attribution, and a fixed UTC creation timestamp. Apply keyword overrides before `CatalogRecord.model_validate`.

Normalize approved sync database URLs before `create_async_engine`:

```python
def async_database_url(url: str) -> str:
    if url.startswith("sqlite:///"):
        return url.replace("sqlite:///", "sqlite+aiosqlite:///", 1)
    if url.startswith("postgresql+psycopg2://"):
        return url.replace("postgresql+psycopg2://", "postgresql+psycopg://", 1)
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url
```

- [ ] **Step 4: Verify sync behavior and the real dataset**

Run: `rtk python -m pytest tests/test_catalog_sync.py -q`

Expected: PASS.

Run: `rtk python -c "from pathlib import Path; from exercise_api.catalog import load_catalog; print(len(load_catalog(Path('data/exercises.json'), Path('data/exercises.schema.json')).records))"`

Expected: `1324`.

- [ ] **Step 5: Commit**

```bash
rtk git add src/exercise_api/database.py src/exercise_api/db_models.py src/exercise_api/catalog.py tests
rtk git commit -m "feat(api): synchronize exercise catalog"
```

### Task 3: Direct exercise catalog API

**Files:**
- Create: `src/exercise_api/api_models.py`
- Create: `src/exercise_api/exercise_repository.py`
- Create: `src/exercise_api/dependencies.py`
- Create: `src/exercise_api/routes/__init__.py`
- Create: `src/exercise_api/routes/exercises.py`
- Create: `src/exercise_api/main.py`
- Test: `tests/test_exercise_api.py`
- Test fixtures: `tests/conftest.py`

**Interfaces:**
- Consumes: `ExerciseRow` and `Database.session_factory` from Task 2.
- Produces: `ExerciseRepository.list(ExerciseFilters) -> ExercisePage`, `random() -> ExerciseOut | None`, `distinct(field: DistinctField) -> list[str]`, and `by_ids(ids: Sequence[str]) -> dict[str, ExerciseOut]`.
- Produces: `create_app(settings: Settings, lifespan_enabled: bool = True) -> FastAPI`.
- Defines: `DistinctField = Literal["category", "body_part", "equipment"]`; never accept a client-provided SQL column name.

- [ ] **Step 1: Write failing endpoint contract tests**

```python
@pytest.mark.asyncio
async def test_list_filters_and_paginates_seeded_catalog(client: AsyncClient) -> None:
    response = await client.get(
        "/exercises",
        params={"equipment": "DUMB", "body_part": "hes", "page": 1, "limit": 1},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["page"] == 1
    assert body["limit"] == 1
    assert body["total"] == 2
    assert body["totalPages"] == 2
    assert body["data"][0]["instructions"] == ["First step", "Second step"]
    assert body["data"][0]["attribution"].startswith("© Gym visual")


@pytest.mark.asyncio
async def test_direct_value_and_random_endpoints(client: AsyncClient) -> None:
    assert (await client.get("/categories")).json() == ["chest", "upper arms"]
    assert (await client.get("/body-parts")).json() == ["chest", "upper arms"]
    assert (await client.get("/equipment")).json() == ["body weight", "dumbbell"]
    random_response = await client.get("/exercises/random")
    assert random_response.status_code == 200
    assert random_response.json()["id"] in {"0001", "0002", "0003"}


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("category", "hes"),
        ("body_part", "CHE"),
        ("equipment", "DUMB"),
        ("muscle_group", "tric"),
        ("target", "PECT"),
    ],
)
@pytest.mark.asyncio
async def test_each_filter_is_case_insensitive_partial_match(
    client: AsyncClient,
    field: str,
    value: str,
) -> None:
    body = (await client.get("/exercises", params={field: value})).json()
    assert body["total"] == 2
```

Add named tests with these exact assertions: `test_defaults_to_page_one_limit_twenty` checks both response fields; `test_limit_over_one_hundred_is_422`; `test_page_beyond_end_has_empty_data_and_preserves_total`; `test_zero_matches_has_zero_total_pages`; `test_filters_use_and_semantics` checks every returned row satisfies all supplied fields; `test_percent_and_underscore_are_literal` seeds equipment `band_100%` and proves the query `_100%` matches that row only; `test_results_are_ordered_by_id`; and `test_random_empty_catalog_is_404`.

- [ ] **Step 2: Run the endpoint tests and verify failure**

Run: `rtk python -m pytest tests/test_exercise_api.py -q`

Expected: FAIL because the repository and routes are absent.

- [ ] **Step 3: Implement response models, repository, and routes**

Define `ExerciseOut` with every catalog-owned field. Define `ExercisePage` with `total_pages: int = Field(serialization_alias="totalPages")` and `ConfigDict(populate_by_name=True)`. Escape `\\`, `%`, and `_` before applying `column.ilike(pattern, escape="\\")`; combine supplied filters with AND; order by `ExerciseRow.id`; and use separate count and page queries.

Define exact routes:

```python
@router.get("/exercises", response_model=ExercisePage, response_model_by_alias=True)
async def list_exercises(
    repository: Annotated[ExerciseRepository, Depends(get_exercise_repository)],
    page: Annotated[int, Query(ge=1)] = 1,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    category: str | None = None,
    body_part: str | None = None,
    equipment: str | None = None,
    muscle_group: str | None = None,
    target: str | None = None,
) -> ExercisePage:
    filters = ExerciseFilters(
        page=page,
        limit=limit,
        category=category,
        body_part=body_part,
        equipment=equipment,
        muscle_group=muscle_group,
        target=target,
    )
    return await repository.list(filters)

@router.get("/exercises/random", response_model=ExerciseOut)
@router.get("/categories", response_model=list[str])
@router.get("/body-parts", response_model=list[str])
@router.get("/equipment", response_model=list[str])
```

Use a count plus random offset for portable random selection. Normalize distinct values case-insensitively in Python and return deterministic case-insensitive order.

In `tests/conftest.py`, create a temporary SQLite database, synchronize three `catalog_record` objects (`0001` and `0002` as dumbbell/chest, `0003` as body weight/upper arms), construct the app with dependency overrides, and yield an httpx `AsyncClient` using `ASGITransport`.

- [ ] **Step 4: Run direct API tests and lint**

Run: `rtk python -m pytest tests/test_exercise_api.py -q`

Expected: PASS.

Run: `rtk python -m ruff check src tests/test_exercise_api.py`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
rtk git add src/exercise_api tests/test_exercise_api.py
rtk git commit -m "feat(api): add direct exercise endpoints"
```

### Task 4: Persistent conversation sessions

**Files:**
- Modify: `src/exercise_api/db_models.py`
- Modify: `src/exercise_api/api_models.py`
- Create: `src/exercise_api/session_repository.py`
- Create: `src/exercise_api/routes/sessions.py`
- Modify: `src/exercise_api/main.py`
- Test: `tests/test_sessions_api.py`

**Interfaces:**
- Produces: `SessionRepository.create()`, `get(UUID)`, `recent_messages(UUID, limit)`, `append_exchange(UUID, user_text, assistant_text, payload)`, and `delete(UUID)`.
- Produces: `POST /v1/sessions`, `GET /v1/sessions/{id}`, and `DELETE /v1/sessions/{id}`.

- [ ] **Step 1: Write failing persistence and cascade-delete tests**

```python
@pytest.mark.asyncio
async def test_session_survives_database_reopen(tmp_path: Path) -> None:
    url = f"sqlite+aiosqlite:///{tmp_path / 'sessions.db'}"
    first_db = Database(url)
    await first_db.create_schema()
    first = SessionRepository(first_db.session_factory)
    session = await first.create()
    await first.append_exchange(session.id, "chest workout", "Here it is", {"intent": "workout"})
    await first_db.dispose()
    second_db = Database(url)
    second = SessionRepository(second_db.session_factory)
    loaded = await second.get(session.id)
    assert [message.text for message in loaded.messages] == ["chest workout", "Here it is"]
    await second_db.dispose()


@pytest.mark.asyncio
async def test_unknown_session_is_404_and_delete_cascades(client: AsyncClient) -> None:
    assert (await client.get(f"/v1/sessions/{uuid4()}")).status_code == 404
    created = (await client.post("/v1/sessions")).json()
    assert (await client.delete(f"/v1/sessions/{created['id']}")).status_code == 204
    assert (await client.get(f"/v1/sessions/{created['id']}")).status_code == 404
```

- [ ] **Step 2: Run the focused tests and verify failure**

Run: `rtk python -m pytest tests/test_sessions_api.py -q`

Expected: FAIL because session persistence is absent.

- [ ] **Step 3: Implement session rows, repository, and endpoints**

Use UUID strings for portable SQLite/PostgreSQL storage. `MessageRow` has `position`, `role`, `text`, nullable JSON `payload`, and timestamp, with a unique `(session_id, position)` constraint and cascade delete. `append_exchange` must add the user and validated assistant messages in one transaction. `recent_messages` returns the newest configured count in chronological order.

- [ ] **Step 4: Verify API and restart persistence**

Run: `rtk python -m pytest tests/test_sessions_api.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
rtk git add src/exercise_api tests/test_sessions_api.py
rtk git commit -m "feat(api): persist conversation sessions"
```

### Task 5: Hybrid catalog retrieval

**Files:**
- Modify: `src/exercise_api/api_models.py`
- Modify: `src/exercise_api/exercise_repository.py`
- Create: `src/exercise_api/retrieval.py`
- Test: `tests/test_retrieval.py`

**Interfaces:**
- Produces: `RetrievalPlan(intent, category, body_part, equipment, muscle_group, target, search_terms)`.
- Produces: `RetrievalService.retrieve(plan: RetrievalPlan, query: str, candidate_limit: int) -> list[ExerciseOut]`.
- Test fixture: build `retrieval` from a temporary synchronized SQLite catalog containing dumbbell chest press, barbell chest press, dumbbell biceps curl, and an instruction-only biceps mention.

- [ ] **Step 1: Write failing hard-filter and ranking tests**

```python
@pytest.mark.asyncio
async def test_explicit_constraints_are_hard_filters(retrieval: RetrievalService) -> None:
    plan = RetrievalPlan(intent="exercise_search", equipment="dumbbell", body_part="chest")
    results = await retrieval.retrieve(plan, "press for my chest", candidate_limit=10)
    assert results
    assert all(item.equipment == "dumbbell" and item.body_part == "chest" for item in results)


@pytest.mark.asyncio
async def test_name_and_target_outweigh_instruction_only_match(retrieval: RetrievalService) -> None:
    results = await retrieval.retrieve(
        RetrievalPlan(intent="exercise_search", search_terms=["biceps curl"]),
        "biceps curl",
        candidate_limit=3,
    )
    assert results[0].name == "dumbbell biceps curl"
```

- [ ] **Step 2: Run retrieval tests and verify failure**

Run: `rtk python -m pytest tests/test_retrieval.py -q`

Expected: FAIL because `RetrievalService` is absent.

- [ ] **Step 3: Implement filtering and deterministic weighted scoring**

Query the database with exact case-insensitive equality for normalized explicit constraints. Score filtered records with RapidFuzz using fixed weights: name `5`, target `4`, muscle group and secondary muscles `3`, equipment and body part `2`, English instructions `1`. Break equal scores by exercise ID and return at most `candidate_limit`. Do not loosen explicit constraints when no records match.

- [ ] **Step 4: Verify retrieval behavior**

Run: `rtk python -m pytest tests/test_retrieval.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
rtk git add src/exercise_api/api_models.py src/exercise_api/exercise_repository.py src/exercise_api/retrieval.py tests/test_retrieval.py
rtk git commit -m "feat(api): add grounded hybrid retrieval"
```

### Task 6: OpenAI-compatible LLM gateway

**Files:**
- Modify: `src/exercise_api/api_models.py`
- Create: `src/exercise_api/llm_gateway.py`
- Create: `src/exercise_api/prompts.py`
- Test: `tests/test_llm_gateway.py`
- Test helper: `tests/fakes.py`

**Interfaces:**
- Produces: `LLMGateway.generate_json(messages: list[ChatMessage], output_type: type[T]) -> T`.
- Produces: `LLMUnavailableError`, `LLMInvalidResponseError`, and prompt builders with no provider-specific tool-calling dependency.
- Produces for tests: `llm_settings() -> LLMSettings`, `assistant(content: str) -> httpx.Response`, `SequenceTransport`, and `FakeLLM.queue(*responses: dict[str, object])`.

- [ ] **Step 1: Write failing success, repair, timeout, and secret-safety tests**

```python
@pytest.mark.asyncio
async def test_repairs_malformed_json_once() -> None:
    transport = SequenceTransport([assistant("not-json"), assistant('{"intent":"exercise_search"}')])
    gateway = LLMGateway(llm_settings(), transport=transport)
    result = await gateway.generate_json([ChatMessage(role="user", content="find curls")], RetrievalPlan)
    assert result.intent == "exercise_search"
    assert transport.calls == 2


@pytest.mark.asyncio
async def test_second_invalid_response_raises_502_error() -> None:
    transport = SequenceTransport([assistant("bad"), assistant("still bad")])
    gateway = LLMGateway(llm_settings(), transport=transport)
    with pytest.raises(LLMInvalidResponseError):
        await gateway.generate_json([ChatMessage(role="user", content="find curls")], RetrievalPlan)
```

Add `test_request_uses_configured_openai_contract` to assert URL `llm_settings().base_url + "/chat/completions"`, bearer authorization, model, temperature, and timeout; `test_extracts_markdown_fenced_json`; `test_timeout_maps_to_unavailable`; and `test_errors_never_include_api_key`.

`llm_settings` returns base URL `http://llm.test/v1`, API key `test-secret`, model `test-model`, and timeout `1`. `SequenceTransport` implements `httpx.AsyncBaseTransport`, stores queued responses/exceptions, increments `calls`, and returns or raises the next item from `handle_async_request`. `assistant` builds the standard `choices[0].message.content` response. `FakeLLM.generate_json` pops its next queued dictionary and validates it with the requested Pydantic type.

- [ ] **Step 2: Run gateway tests and verify failure**

Run: `rtk python -m pytest tests/test_llm_gateway.py -q`

Expected: FAIL because the gateway is absent.

- [ ] **Step 3: Implement standard chat-completions HTTP and one repair**

Send `model`, `messages`, and `temperature: 0` with httpx. Parse `choices[0].message.content`, strip an optional fenced JSON block, `json.loads`, and validate with `output_type.model_validate`. On parse/validation failure, append a repair instruction containing the validation error and malformed output, call exactly once more, then raise `LLMInvalidResponseError`. Map httpx timeout/connect/status failures to `LLMUnavailableError` without embedding secrets.

- [ ] **Step 4: Verify provider-neutral behavior**

Run: `rtk python -m pytest tests/test_llm_gateway.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
rtk git add src/exercise_api/api_models.py src/exercise_api/llm_gateway.py src/exercise_api/prompts.py tests/fakes.py tests/test_llm_gateway.py
rtk git commit -m "feat(api): add OpenAI-compatible LLM gateway"
```

### Task 7: Grounded conversational search and workout generation

**Files:**
- Modify: `src/exercise_api/api_models.py`
- Modify: `src/exercise_api/prompts.py`
- Create: `src/exercise_api/chat_service.py`
- Create: `src/exercise_api/routes/chat.py`
- Modify: `src/exercise_api/main.py`
- Test: `tests/test_chat_api.py`

**Interfaces:**
- Consumes: session repository, retrieval service, exercise repository, and LLM gateway.
- Produces: `ChatService.chat(request: ChatRequest) -> ChatResponse` and `POST /v1/chat`.
- Test fixtures: `fake_llm` is the Task 6 `FakeLLM`; `chat_client` uses the seeded SQLite app from `tests/conftest.py` with its LLM dependency overridden by `fake_llm`.

- [ ] **Step 1: Write failing end-to-end orchestration tests with a fake gateway**

```python
@pytest.mark.asyncio
async def test_workout_hydrates_catalog_fields_and_applies_defaults(chat_client: AsyncClient, fake_llm: FakeLLM) -> None:
    fake_llm.queue(
        {"intent": "workout", "equipment": "dumbbell", "body_part": "chest", "search_terms": ["press"]},
        {
            "answer": "Use this conservative beginner session.",
            "name": "Beginner Chest",
            "estimated_duration_minutes": 35,
            "selections": [{"id": "0001", "sets": 3, "reps": "8-12", "rest_seconds": 90, "notes": "Controlled tempo"}],
            "assumptions": ["Beginner experience level"],
            "warnings": [],
        },
    )
    response = await chat_client.post("/v1/chat", json={"message": "make a dumbbell chest workout"})
    assert response.status_code == 200
    body = response.json()
    assert body["intent"] == "workout"
    assert body["workout"]["exercises"][0]["name"] == "authoritative catalog name"
    assert body["workout"]["exercises"][0]["sets"] == 3
    assert body["session_id"]
```

Add named tests for: exercise-search populating `results` and leaving `workout` null; existing-session history appearing in the next planning request; unknown session returning `404`; one unknown-ID correction producing only hydrated valid IDs; repeated unknown IDs returning `502`; no candidates returning `200` with an empty result and warning after only the planning call; LLM timeout returning `503`; injury text adding a professional-guidance warning; and failed output leaving the session without a successful assistant message. Each test asserts the fake gateway call count so repair/correction limits are enforced.

- [ ] **Step 2: Run chat tests and verify failure**

Run: `rtk python -m pytest tests/test_chat_api.py -q`

Expected: FAIL because chat orchestration is absent.

- [ ] **Step 3: Implement typed LLM decisions and the two-stage flow**

Define discriminated response models for `exercise_search` and `workout`. The planning prompt includes recent history and current text. The generation prompt includes only candidate IDs and catalog facts, demands selections from that list, and includes these defaults when absent: beginner, general fitness, 30–45 minutes, moderate volume/rest, no known restrictions, and body weight when equipment is unspecified.

After generation, fetch selected IDs with `ExerciseRepository.by_ids`, preserve LLM order, hydrate catalog fields, and attach only LLM-owned prescription fields. If IDs are invalid or fewer than required, make one correction call listing valid candidate IDs. Store the user and final validated assistant payload atomically only after validation succeeds.

If text mentions pain, injury, pregnancy, rehabilitation, or a medical condition, add a non-medical warning and professional-guidance recommendation. Never claim catalog difficulty or medical suitability.

- [ ] **Step 4: Verify chat behavior**

Run: `rtk python -m pytest tests/test_chat_api.py -q`

Expected: PASS.

Run: `rtk python -m mypy src/exercise_api`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
rtk git add src/exercise_api tests/test_chat_api.py
rtk git commit -m "feat(api): add grounded exercise chat"
```

### Task 8: Readiness, startup, and error isolation

**Files:**
- Create: `src/exercise_api/routes/health.py`
- Modify: `src/exercise_api/main.py`
- Modify: `src/exercise_api/dependencies.py`
- Test: `tests/test_readiness.py`

**Interfaces:**
- Produces: `GET /health` with component state and HTTP `200` when ready or `503` when catalog/database initialization failed.
- Produces: readiness guards for chat, session, and direct catalog routes.
- Test fixtures: `degraded_client` starts with an invalid temporary catalog; `ready_client` starts with a valid synchronized SQLite catalog and a fake gateway that raises `LLMUnavailableError`.

- [ ] **Step 1: Write failing degraded-startup and LLM-isolation tests**

```python
@pytest.mark.asyncio
async def test_invalid_catalog_exposes_degraded_health_only(degraded_client: AsyncClient) -> None:
    health = await degraded_client.get("/health")
    assert health.status_code == 503
    assert health.json()["catalog"] == "failed"
    assert (await degraded_client.get("/exercises")).status_code == 503


@pytest.mark.asyncio
async def test_llm_outage_does_not_break_direct_catalog(ready_client: AsyncClient) -> None:
    assert (await ready_client.get("/exercises")).status_code == 200
    assert (await ready_client.post("/v1/chat", json={"message": "find curls"})).status_code == 503
    assert (await ready_client.get("/health")).status_code == 200
```

- [ ] **Step 2: Run readiness tests and verify failure**

Run: `rtk python -m pytest tests/test_readiness.py -q`

Expected: FAIL because readiness state and guards are absent.

- [ ] **Step 3: Implement readiness-aware lifespan and exception mapping**

The lifespan must load settings, create the database schema, validate/sync the catalog, and record component status. It must catch initialization errors, retain sanitized failure state, and keep `/health` callable. Guard all other routes when database/catalog is unready. Register exception handlers mapping `LLMUnavailableError` to `503` and `LLMInvalidResponseError` to `502`; use FastAPI's native `422` validation response and explicit `404` session/random errors.

- [ ] **Step 4: Verify failure isolation and full SQLite suite**

Run: `rtk python -m pytest tests/test_readiness.py -q`

Expected: PASS.

Run: `rtk python -m pytest tests -q --ignore=tests/postgres`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
rtk git add src/exercise_api tests/test_readiness.py
rtk git commit -m "feat(api): add readiness and provider isolation"
```

### Task 9: PostgreSQL parity and live-provider smoke tooling

**Files:**
- Create: `tests/postgres/test_postgres_parity.py`
- Create: `scripts/smoke_llm.py`
- Modify: `pyproject.toml`
- Modify: `README.md`

**Interfaces:**
- Verifies the same catalog synchronization, filtering, and session contracts against PostgreSQL.
- Produces: `rtk python scripts/smoke_llm.py --config config.toml --message "Find body-weight chest exercises"`.

- [ ] **Step 1: Write a Docker-backed PostgreSQL parity test**

```python
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_postgres_catalog_filters_and_session_restart() -> None:
    with PostgresContainer("postgres:17-alpine") as postgres:
        database = Database(async_database_url(postgres.get_connection_url()))
        await database.create_schema()
        catalog = LoadedCatalog(
            "postgres-fixture",
            [catalog_record("0001", "Press"), catalog_record("0002", "Fly")],
        )
        await sync_catalog(database.session_factory, catalog)
        exercises = ExerciseRepository(database.session_factory)
        page = await exercises.list(ExerciseFilters(equipment="DUMB", page=1, limit=20))
        assert page.total == 2
        sessions = SessionRepository(database.session_factory)
        created = await sessions.create()
        await sessions.append_exchange(created.id, "hello", "response", {"intent": "exercise_search"})
        assert len((await sessions.get(created.id)).messages) == 2
        await database.dispose()
```

- [ ] **Step 2: Run it and confirm any PostgreSQL-specific failure before adapting code**

Run: `rtk python -m pytest tests/postgres/test_postgres_parity.py -q -m postgres`

Expected: the container starts; test initially FAILS on any unhandled PostgreSQL URL, JSON, collation, or transaction difference. If it already passes, no production adaptation is needed.

- [ ] **Step 3: Make only verified parity fixes and add the opt-in smoke script**

Normalize Testcontainers' URL to `postgresql+psycopg://`. Keep filtering semantics identical by using SQLAlchemy expressions already covered by SQLite tests. The smoke script must load `config.toml`, call `/chat/completions` through `LLMGateway`, print only model/content/status, return nonzero on failure, and never print the API key.

Document installation, `rtk uvicorn exercise_api.main:app --reload`, SQLite/PostgreSQL config examples, environment overrides, direct endpoint examples, chat/session examples, deterministic tests, Docker PostgreSQL tests, and Ollama/cloud smoke commands in `README.md`.

- [ ] **Step 4: Run PostgreSQL and documentation smoke checks**

Run: `rtk python -m pytest tests/postgres/test_postgres_parity.py -q -m postgres`

Expected: PASS with Docker available.

Run: `rtk python scripts/smoke_llm.py --help`

Expected: exit `0` and display `--config` and `--message`.

- [ ] **Step 5: Commit**

```bash
rtk git add tests/postgres scripts/smoke_llm.py pyproject.toml README.md src/exercise_api
rtk git commit -m "test(api): verify PostgreSQL and provider setup"
```

### Task 10: Final contract and quality verification

**Files:**
- Modify only files required by failures proven in this task.

**Interfaces:**
- Verifies every acceptance criterion without adding new product behavior.

- [ ] **Step 1: Run formatting, lint, and type checks**

Run: `rtk python -m ruff format --check src tests scripts`

Expected: PASS.

Run: `rtk python -m ruff check src tests scripts`

Expected: PASS.

Run: `rtk python -m mypy src/exercise_api`

Expected: PASS.

- [ ] **Step 2: Run the complete deterministic suite**

Run: `rtk python -m pytest tests -q --ignore=tests/postgres`

Expected: PASS with tests covering 1,324-record validation, direct endpoints, persistence, retrieval, LLM repair, grounded chat, and degraded readiness.

- [ ] **Step 3: Run PostgreSQL parity**

Run: `rtk python -m pytest tests/postgres -q -m postgres`

Expected: PASS when Docker is available. If Docker is unavailable, record the exact environmental failure; do not report PostgreSQL verification as passed.

- [ ] **Step 4: Review the public OpenAPI contract**

Run: `rtk python -c "from exercise_api.main import app; import json; s=app.openapi(); print(json.dumps(sorted(s['paths']), indent=2))"`

Expected paths:

```json
[
  "/body-parts",
  "/categories",
  "/equipment",
  "/exercises",
  "/exercises/random",
  "/health",
  "/v1/chat",
  "/v1/sessions",
  "/v1/sessions/{id}"
]
```

- [ ] **Step 5: Commit verified fixes, if any**

If verification required changes, commit only those files:

Run `rtk git status --short`, stage each verified source/test file explicitly with `rtk git add`, then run `rtk git commit -m "fix(api): satisfy final contract verification"`.

If no files changed, do not create an empty commit. Preserve unrelated untracked `output/` and `tmp/` content.
