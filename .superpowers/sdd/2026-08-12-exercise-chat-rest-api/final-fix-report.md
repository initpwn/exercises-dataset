# Exercise Chat REST API final-review fix report

## Outcome

All seven Important findings and the five scoped minor findings were resolved in
one implementation wave. The final deterministic suite passes with 138 tests;
the three Docker-backed PostgreSQL tests are deselected by default. The public
route set and the pre-existing public session response schema are unchanged.

Implementation commit:

- `fa0c1d7bde5f5db46e6c3a97cd8de715121ebfa2` —
  `fix(api): resolve final review findings`

Base reviewed:

- `b757dbd7c485974bf4003bbac7a53bf7d092810f`

## Finding 1: SQLite integrity and delete/append serialization

### Implementation

- A SQLAlchemy connection listener enables `PRAGMA foreign_keys=ON` and a
  bounded SQLite busy timeout on every pooled connection.
- Session deletion is one `DELETE ... RETURNING` transaction. Appends reserve
  positions and update the parent in the same transaction, so a committed
  delete either serializes after the append and cascades it, or the append sees
  the missing parent and raises `SessionNotFoundError`.
- ORM cascades use `passive_deletes=True`, leaving the declared database cascade
  authoritative.

### RED / GREEN evidence

- RED: `.venv\Scripts\python.exe -m pytest tests/test_sessions_api.py -q`
  produced `7 failed, 5 passed` on the reviewed implementation, including
  foreign-key, stale-delete interleave, concurrent initializer, UTC, and
  `updated_at` regressions.
- GREEN: the focused persistence suite produced `12 passed in 0.75s`.
- The deterministic stale-delete interleave and the public concurrent race both
  assert that no message rows remain without their parent session. A committed
  delete followed by append asserts `SessionNotFoundError`.

## Finding 2: catalog-derived deterministic constraints

### Implementation

- The repository exposes canonical values for category, body part, equipment,
  muscle group, and target from the synchronized catalog.
- Unicode/case/separator normalization and conservative unambiguous compact and
  singular/plural aliases map variants such as `bodyweight` and `dumbbells` to
  canonical catalog values.
- Current-text detection is limited to the design-scoped body-part, equipment,
  and target fields. It overrides conflicting planner values. Duplicate values
  across fields do not create accidental cross-field hard filters.
- Typed planner fields are canonicalized, but generic input cannot acquire an
  invented hard constraint. Non-English input retains the typed translation
  path. Exact SQL filters remain exact after normalization.

### RED / GREEN evidence

- The initial focused constraint/relevance run exposed nine failures before the
  catalog vocabulary and deterministic merge were implemented.
- A later reviewer regression demonstrated that `chest` was applied to multiple
  fields; the targeted test failed with ID `0008` absent, then passed after
  extraction was restricted to the design fields and collisions were removed.
- The final retrieval/context/chat group passed `65 passed in 4.53s` and the
  complete final suite passed.

## Finding 3: deterministic relevance and no-match behavior

### Implementation

- Relevance uses current user text as evidence; planner search terms cannot make
  unrelated English text relevant.
- Only a deterministic current-text hard constraint, catalog-revalidated prior
  IDs, or the typed arbitrary-language path bypasses the lexical floor.
- Genuinely broad exercise/workout requests use stable catalog order. Nonsense
  and unrelated requests return HTTP 200 with empty structured output and the
  no-match warning before the generation call.
- Valid broad regressions cover ordinary exercise/workout wording plus `help me
  get fit` and `general fitness` wording.

### RED / GREEN evidence

- RED: the first broad-policy expansion produced `2 failed, 2 passed`; after the
  deterministic generic-request vocabulary was completed it produced `5
  passed` with the nonsense regression included.
- RED: adversarial planner terms (`pectorals`) and a hallucinated catalog
  equipment constraint both returned arbitrary results before the evidence
  split. Both now return no match with exactly one planner call.

## Finding 4: structured follow-up context

### Implementation

- Stored assistant payloads are parsed through `ChatResponse`, limited to the
  newest five turns, ten exercises per turn, 500 note characters, and 8,192
  serialized characters.
- Every stored ID is reloaded from the current catalog. Deleted IDs are removed;
  names are rehydrated; order, sets, reps, rest, and safe notes are retained.
- Explicit planner references are intersected across all bounded validated
  turns in their originating order. With no explicit list, the newest turn is
  used.
- The validated structured context is supplied to both planning and generation.

### RED / GREEN evidence

- RED: stored payload IDs initially never reached the planner/generator and the
  multi-turn cases failed. The first grounded-context GREEN run was part of `33
  passed` focused chat tests.
- RED: an explicit older-turn reference returned `[]`; the unit regression and
  a three-chat API regression now select the older ID while excluding the
  newest unrelated ID from the valid-candidate section.
- The bounded-context regression independently asserts all three documented
  limits.

## Finding 5: server-owned defaults and medical guardrails

### Implementation

- Missing workout details produce deterministic beginner, general-fitness,
  30–45-minute, moderate-volume, restriction, and body-weight assumptions.
- Planner workout preferences can suppress a default only when deterministic
  English evidence exists or the planner supplies a verbatim evidence substring
  that the server finds in arbitrary-language input.
- Medical context is the union of typed planner context, current deterministic
  matching, and recent user history. Deterministic wording covers common pain,
  hurt, tear, sprain, injury, pregnancy, rehabilitation, and named-condition
  forms.
- The professional-guidance warning is always appended server-side for medical
  context. Contradictory “no restrictions” assumptions are removed.
- Model-owned answer, assumptions, warnings, workout name, and notes are checked
  for medical-safety/suitability/recommendation claims. ID and semantic failures
  share the single correction budget; a repeated violation returns 502 without
  persisting the exchange.
- Extreme duration and set prescriptions, and non-follow-up prescriptions that
  contradict omitted-duration/moderate-volume defaults, trigger correction.

### RED / GREEN evidence

- RED: the original focused safety run produced seven failures for defaults,
  history, typed non-English medical input, unsafe claims, and persistence.
- RED: the frozen adversarial review produced seven API failures for common
  injury wording, conditional safety claims, hallucinated preferences/extreme
  prescriptions, and planner relevance; the older-reference/schema pair added
  two failures. All are covered in the final GREEN suite.
- Additional RED cases for `suitable if ... arthritis` and `recommended for
  arthritis` failed before the medical-claim vocabulary was expanded; all three
  parameterized conditional-claim cases now pass after one correction.

## Finding 6: sanitized runtime database failures

### Implementation

- Operational, interface, pool timeout, and disconnection failures map to the
  exact sanitized 503 body `{"detail":"Database service is unavailable"}` and
  flip database readiness to failed.
- Invalidated `DBAPIError` wrappers are mapped only when
  `connection_invalidated` is true; ordinary programming/integrity errors are
  not broadly misclassified.
- Logs include only a stable message and exception class, never SQL text,
  parameters, credentials, or provider details.

### RED / GREEN evidence

- The original resource/runtime focused command produced `10 failed, 1 passed`
  before handlers and bounds were implemented, then `11 passed`.
- RED: invalidated `ProgrammingError` probes failed for direct, session, and chat
  routes. GREEN: all three passed, followed by `14 passed in 0.45s` for the full
  readiness file. Every route regression verifies the subsequent `/health` 503.

## Finding 7: concurrent schema upgrades

### Implementation

- Schema creation, inspection, ALTER, and backfill execute under one
  cross-process transaction boundary.
- SQLite uses `BEGIN EXCLUSIVE`; PostgreSQL uses a transaction-scoped advisory
  lock with a stable signed 64-bit lock ID.
- Four independent SQLite engines concurrently upgrade the same legacy file in
  the regression. PostgreSQL parity performs concurrent initialization when its
  Docker suite is available.
- The README documents the initializer permissions and locking behavior.

### RED / GREEN evidence

- Before locking, two independent SQLite initializers reproduced
  `OperationalError: table exercises already exists`.
- GREEN: all four independent initializers return successfully and both legacy
  session columns are present.
- Live PostgreSQL verification was not possible; exact limitation is recorded
  below.

## Scoped minor findings

1. Direct filter strings are trimmed; whitespace-only values are absent in both
   partial and exact repository paths.
2. One `UTCDateTime` type decorator normalizes and restores aware UTC values for
   exercise, session, message, and session-update timestamps on both dialects.
3. Session `updated_at` is created, migrated/backfilled, and advanced atomically
   on append. It remains persistence-only so the frozen public `SessionOut`
   schema does not change.
4. Startup, catalog, provider, repair, and database failures have sanitized
   internal logging with explicit secret-leak regressions.
5. Chat input is limited to 4,000 Unicode characters; provider output defaults
   to 2,048 tokens and is configurable from 128 to 8,192; malformed output and
   validation diagnostics included in the repair prompt are capped at 8,192 and
   2,048 characters. README and `config.toml` document the exact limits.

## Final verification

Executed after the final behavior change:

```text
.venv\Scripts\python.exe -m ruff format --check src tests scripts
37 files already formatted

.venv\Scripts\python.exe -m ruff check src tests scripts
All checks passed!

.venv\Scripts\python.exe -m mypy src scripts
Success: no issues found in 22 source files

.venv\Scripts\python.exe -m pytest -q
138 passed, 3 deselected in 10.69s

exact OpenAPI assertion
PASS: /body-parts, /categories, /equipment, /exercises,
/exercises/random, /health, /v1/chat, /v1/sessions,
/v1/sessions/{id}

git diff --check
PASS
```

The OpenAPI regression also asserts that public `SessionOut` contains exactly
`id`, `created_at`, and `messages`, all required.

## Docker / PostgreSQL limitation

Docker availability was checked exactly once:

```text
docker info --format '{{.ServerVersion}}'
failed to connect to the docker API at
npipe:////./pipe/dockerDesktopLinuxEngine; ... The system cannot find the file
specified.
```

Because the daemon was unavailable, the PostgreSQL suite was not started.
PostgreSQL remains **unverified**. The parity tests are present for concurrent
initialization, UTC/update timestamps, and delete/append orphan protection, but
this report makes no live PostgreSQL claim.

## Files changed

- Persistence/runtime: `database.py`, `db_models.py`,
  `session_repository.py`, `main.py`.
- Retrieval/chat policy: `api_models.py`, `exercise_repository.py`,
  `retrieval.py`, `conversation_context.py`, `safety.py`, `chat_service.py`,
  `prompts.py`.
- Provider/configuration: `config.py`, `llm_gateway.py`, `config.toml`.
- Documentation/plan: `README.md` and the final-review implementation plan.
- Tests: chat, context, retrieval, exercise endpoints, session persistence,
  readiness, gateway, configuration, OpenAPI, and PostgreSQL parity files.

## Self-review and remaining concerns

- Catalog-owned response fields are still hydrated exclusively from current
  synchronized rows. Direct endpoints do not call the LLM.
- The exact public path set and public session schema are regression-tested.
- `uv.lock` remains untracked and untouched. `output/` and `tmp/` were not
  accessed or changed.
- Deterministic relevance is intentionally lexical and conservative; a future
  embedding implementation can improve semantic recall without changing the
  retrieval boundary.
- Arbitrary-language constraint and medical understanding necessarily uses the
  typed planner. Workout preferences additionally require server-verifiable
  verbatim evidence before suppressing defaults.
- Medical prose enforcement assumes the required English model output. The
  server rejects a broad set of affirmative safety/suitability claims, but it is
  not a general natural-language medical classifier.
- PostgreSQL remains the only unverified runtime concern because Docker was not
  available.
