# Exercise Chat API Final Review Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Resolve all Important final-review findings and the scoped minor issues without changing the public route set or catalog-grounding contract.

**Architecture:** Keep deterministic policy at the service/retrieval boundary: catalog vocabularies normalize model plans, lexical relevance gates generation, stored payloads supply only revalidated follow-up IDs, and server-side response policy owns defaults and medical safety. Keep persistence guarantees in the database/repository boundary with per-connection SQLite foreign keys, serialized schema initialization, atomic session delete/update operations, UTC-normalized types, and an application-level sanitized database-outage handler.

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, SQLAlchemy asyncio, SQLite, PostgreSQL/psycopg, httpx, pytest.

## Global Constraints

- English-only means no language option and English catalog/prompts/responses; accept input text in any language.
- Preserve the exact public OpenAPI paths and response contracts.
- Catalog-owned exercise fields always come from the synchronized database.
- Direct catalog endpoints never call the LLM.
- PostgreSQL remains unverified unless Docker tests actually execute.
- Preserve untracked `uv.lock`; do not touch `output/` or `tmp/`.

---

### Task 1: Persistence integrity and schema initialization

**Files:**
- Modify: `src/exercise_api/database.py`
- Modify: `src/exercise_api/db_models.py`
- Modify: `src/exercise_api/session_repository.py`
- Test: `tests/test_sessions_api.py`
- Test: `tests/postgres/test_postgres_parity.py`

**Interfaces:**
- Produces: SQLite connections with `PRAGMA foreign_keys=ON`; serialized `Database.create_schema()` for SQLite and PostgreSQL; session `updated_at`; delete-versus-append serialization with no orphan messages.

- [ ] Write regressions for every SQLite connection, deterministic delete/append interleavings, UTC-aware timestamps, updated timestamps, and concurrent legacy initialization.
- [ ] Run focused tests and record expected failures against the base revision.
- [ ] Add dialect-specific schema coordination, migration helpers, UTC timestamp normalization, and atomic repository mutations.
- [ ] Run focused SQLite tests to green; add PostgreSQL parity coverage without claiming it executed.

### Task 2: Deterministic planning, constraints, and relevance

**Files:**
- Modify: `src/exercise_api/api_models.py`
- Modify: `src/exercise_api/exercise_repository.py`
- Modify: `src/exercise_api/retrieval.py`
- Modify: `src/exercise_api/prompts.py`
- Modify: `src/exercise_api/chat_service.py`
- Test: `tests/test_retrieval.py`
- Test: `tests/test_chat_api.py`

**Interfaces:**
- Produces: catalog-derived canonical constraint vocabulary; deterministic extraction/alias normalization merged over typed planning; a relevance floor that accepts explicit constraints and broad exercise/workout requests while rejecting unrelated text before generation.

- [ ] Add failing alias, dropped-constraint, nonsense, and valid-broad-query tests.
- [ ] Run focused tests and record expected failures.
- [ ] Implement catalog vocabulary lookup, canonical alias extraction, typed-plan normalization, and lexical relevance policy.
- [ ] Run focused retrieval/chat tests to green.

### Task 3: Grounded follow-ups and server-owned safety

**Files:**
- Modify: `src/exercise_api/api_models.py`
- Modify: `src/exercise_api/prompts.py`
- Modify: `src/exercise_api/chat_service.py`
- Modify: `src/exercise_api/retrieval.py`
- Test: `tests/test_chat_api.py`

**Interfaces:**
- Consumes: recent `MessageOut.payload` values and current catalog candidates.
- Produces: bounded structured context, planner-requested prior IDs intersected with stored IDs and current hard-filtered catalog rows, deterministic workout assumptions/warnings, and one correction attempt for unsafe model-owned prose.

- [ ] Add failing multi-turn structured-context and ID-revalidation tests.
- [ ] Add failing defaults, historical/typed medical-context, and unsafe-claim correction/rejection tests.
- [ ] Run focused tests and record expected failures.
- [ ] Implement bounded structured context, follow-up selection, assumptions/warnings, and response-safety validation.
- [ ] Run focused chat tests to green.

### Task 4: Runtime failures, resource bounds, and documentation

**Files:**
- Modify: `src/exercise_api/api_models.py`
- Modify: `src/exercise_api/config.py`
- Modify: `src/exercise_api/llm_gateway.py`
- Modify: `src/exercise_api/main.py`
- Modify: `src/exercise_api/exercise_repository.py`
- Modify: `README.md`
- Modify: `config.toml`
- Test: `tests/test_readiness.py`
- Test: `tests/test_exercise_api.py`
- Test: `tests/test_llm_gateway.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: sanitized HTTP 503 plus readiness invalidation for runtime SQLAlchemy failures; trimmed direct filters; bounded message/provider/repair payloads; sanitized internal diagnostics.

- [ ] Add failing direct/session/chat outage, filter trimming, message cap, provider token cap, repair truncation, and sanitized logging tests.
- [ ] Run focused tests and record expected failures.
- [ ] Implement the minimal handlers, normalization, limits, and logging.
- [ ] Document exact limits, English-only behavior, migration coordination, and PostgreSQL verification status.
- [ ] Run focused tests to green.

### Task 5: Verification and review

**Files:**
- Create: `.superpowers/sdd/2026-08-12-exercise-chat-rest-api/final-fix-report.md`

- [ ] Run Ruff format/check, mypy, the full deterministic non-PostgreSQL suite, exact OpenAPI path check, and `git diff --check` on the complete tree.
- [ ] Check Docker availability once; run PostgreSQL tests only if the daemon is available and record the exact limitation otherwise.
- [ ] Review the complete diff against every finding and constraint; request an independent code review and resolve any Important feedback.
- [ ] Write the evidence report and commit the coherent fix wave, excluding `uv.lock`.
