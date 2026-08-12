# Exercise Chat REST API Design

## Purpose

Build a Python FastAPI service that accepts conversational English requests and returns either matching exercises or a generated workout. Responses include natural-language guidance and structured data that client applications can render.

The service uses `data/exercises.json` as its authoritative exercise catalog. It does not train or fine-tune a language model. Instead, it retrieves relevant catalog records and asks a configurable OpenAI-compatible language model to select and arrange those records.

## Scope

Version 1 provides:

- Exercise discovery from conversational English requests.
- Workout generation from conversational English requests.
- Direct, non-LLM exercise listing, filtering, random selection, and filter-value endpoints.
- Renderable exercise metadata, instructions, images, and GIF paths.
- Server-managed, persistent conversation sessions.
- SQLite and PostgreSQL support selected through `config.toml`.
- A configurable OpenAI-compatible LLM endpoint, including local Ollama or a compatible cloud endpoint.
- Conservative defaults when the user omits workout details.

Version 1 does not provide:

- User authentication or API-key enforcement.
- Languages other than English.
- Model training or fine-tuning.
- Provider-specific structured-output features.
- Semantic embedding retrieval. The retrieval boundary will allow it to be added later.
- Medical diagnosis, rehabilitation plans, or claims that an exercise is medically safe.

## Architecture

The application is divided into five components with narrow interfaces.

### Session service

Creates session IDs and persists conversation messages through SQLAlchemy. The configured database URL selects SQLite or PostgreSQL without changing application code. The service limits LLM context to the most recent configured number of messages while retaining complete stored history.

### Exercise catalog

Loads and validates `data/exercises.json` during startup, then transactionally synchronizes the records into the configured database. It exposes English exercise records and catalog-derived vocabularies for body parts, targets, muscles, and equipment. The service is not ready if validation or synchronization fails.

The JSON file remains the catalog's source of truth in version 1. A content hash prevents unnecessary synchronization. When the file changes, the catalog service upserts current records and removes database records no longer present in the file in one transaction. Exercises, sessions, and messages use separate tables.

### Retrieval service

Accepts a user request and returns ranked catalog candidates from the exercises table. It detects explicit body-part, target-muscle, and equipment constraints using values derived from the catalog. Explicit constraints are hard filters. Remaining candidates are ranked using weighted matches across name, target, muscle fields, equipment, and English instructions.

Retrieval exposes an internal interface so a semantic embedding implementation can be added without changing the public API.

### LLM gateway

Calls a configured OpenAI-compatible chat-completions endpoint. The first LLM call receives recent session history and the current request, then produces a retrieval plan containing the request intent and normalized conversational constraints. After deterministic retrieval, the second LLM call receives a bounded candidate list and produces a JSON decision containing selected catalog IDs, workout prescription, assumptions, warnings, and conversational answer.

Provider-specific JSON-schema or tool-calling support is not required. The gateway parses ordinary JSON text and performs one repair request when the first response is malformed.

### Response validator

Validates every selected exercise ID against the catalog. It discards unknown IDs and hydrates all authoritative exercise fields from `data/exercises.json`. The LLM may propose sets, repetitions, rest periods, ordering, and notes, but it cannot create or overwrite exercise names, instructions, equipment, muscle metadata, images, or GIF paths.

If too few valid selections remain after validation, the service makes one corrected generation request. It never returns invented exercises.

## Request flow

1. The client sends conversational English text and, optionally, an existing session ID.
2. The session service creates or loads the session and retrieves its recent history.
3. The first LLM call classifies the request as exercise search or workout generation and extracts conversational constraints.
4. The retrieval service combines those constraints with deterministic catalog matching and returns bounded candidates.
5. The second LLM call selects and explains relevant candidates for an exercise search, or selects candidates and assigns ordering, sets, repetitions, rest, and notes for a workout.
6. The response validator rejects unknown selections and hydrates catalog-owned fields.
7. The session service stores the successful user and assistant exchange.
8. The API returns conversational text and structured renderable data.

## REST API

### Chat

`POST /v1/chat`

Request:

```json
{
  "session_id": "optional-existing-session-uuid",
  "message": "Create a beginner chest workout using dumbbells"
}
```

`session_id` is optional. When absent, the server creates a session and returns its ID. `message` must be a non-empty string. The API accepts and returns English only.

Representative workout response:

```json
{
  "session_id": "uuid",
  "answer": "Here is a beginner-friendly dumbbell chest workout...",
  "intent": "workout",
  "assumptions": [
    "No known injuries or movement restrictions",
    "Beginner experience level",
    "Standard dumbbells and a bench are available"
  ],
  "workout": {
    "name": "Beginner Dumbbell Chest Workout",
    "estimated_duration_minutes": 35,
    "exercises": [
      {
        "id": "1274",
        "name": "deep push up",
        "category": "chest",
        "body_part": "chest",
        "target": "pectorals",
        "equipment": "dumbbell",
        "muscle_group": "triceps",
        "secondary_muscles": ["triceps", "shoulders"],
        "instructions": [
          "Start in a high plank position with your hands slightly wider than shoulder-width apart and your body in a straight line."
        ],
        "image": "images/1274-vptOQ4N.jpg",
        "gif_url": "videos/1274-vptOQ4N.gif",
        "media_id": "vptOQ4N",
        "attribution": "© Gym visual — https://gymvisual.com/",
        "created_at": "2026-03-18T12:31:32.877433+00:00",
        "sets": 3,
        "reps": "8-12",
        "rest_seconds": 90,
        "notes": "Use a controlled tempo."
      }
    ]
  },
  "results": [],
  "warnings": []
}
```

For `exercise_search`, `results` contains renderable catalog records and `workout` is `null`. For `workout`, `workout` is populated and `results` is empty. The exact response models will be defined as typed Pydantic schemas during implementation.

### Direct exercise catalog

These endpoints read the exercises table directly and never call the LLM provider. They remain usable when the LLM provider is unavailable.

`GET /exercises` accepts these optional query parameters:

- `page`: integer, default `1`, minimum `1`.
- `limit`: integer, default `20`, minimum `1`, maximum `100`.
- `category`: case-insensitive partial match.
- `body_part`: case-insensitive partial match.
- `equipment`: case-insensitive partial match.
- `muscle_group`: case-insensitive partial match.
- `target`: case-insensitive partial match.

Multiple filters use AND semantics. Filter values are trimmed; an empty value is treated as absent. Each filter is a literal substring match, so SQL wildcard characters in user input are escaped. Results use ascending exercise ID order so pagination is stable across requests. A page beyond the available results returns an empty `data` array while preserving the matching `total`. `totalPages` is `ceil(total / limit)`, or `0` when no records match.

Response:

```json
{
  "data": [],
  "total": 1324,
  "page": 1,
  "limit": 20,
  "totalPages": 67
}
```

Each object in `data` contains the catalog-owned renderable fields: `id`, `name`, `category`, `body_part`, `equipment`, `muscle_group`, `secondary_muscles`, `target`, English `instructions` as an ordered string array, `image`, `gif_url`, `media_id`, `attribution`, and `created_at`. Chat responses reuse this same exercise representation and add workout-only prescription fields when applicable.

`GET /exercises/random` returns one complete exercise object selected from the exercises table. It returns `404` only if the catalog is empty.

The remaining non-LLM endpoints return JSON arrays of sorted unique strings read from the exercises table:

- `GET /categories` returns category values.
- `GET /body-parts` returns body-part values.
- `GET /equipment` returns equipment values.

String sorting is case-insensitive and deterministic. Duplicate values that differ only by case collapse to one canonical catalog value.

### Session management

- `POST /v1/sessions` creates an empty session.
- `GET /v1/sessions/{id}` returns the session and its conversation history.
- `DELETE /v1/sessions/{id}` permanently deletes the session and its messages.

### Health

`GET /health` reports database, dataset, and application readiness without calling the LLM provider.

### Status behavior

- `404` for an unknown session.
- `422` for malformed request data.
- `502` when LLM output remains invalid after one repair attempt.
- `503` when the database or configured LLM provider is unavailable.
- A valid request with no matching exercises returns `200`, an explanatory answer, empty structured results, and a warning.

## Configuration

The service reads non-secret defaults from `config.toml`:

```toml
[database]
url = "sqlite:///./exercise_api.db"
# PostgreSQL example:
# url = "postgresql+psycopg://user:password@localhost/exercises"

[llm]
base_url = "http://localhost:11434/v1"
api_key = "ollama"
model = "your-model"
timeout_seconds = 60

[retrieval]
candidate_limit = 30
result_limit = 10

[sessions]
history_message_limit = 20
```

Environment variables using the `EXERCISE_API__<SECTION>__<KEY>` convention override configuration values; for example, `EXERCISE_API__LLM__API_KEY` and `EXERCISE_API__DATABASE__URL`. Deployments should use these variables for the LLM API key and database credentials rather than committing secrets to `config.toml`.

Startup validates required configuration, catalog availability, database connectivity, and catalog synchronization. When any of these steps fail, the process remains available only to expose a `503` readiness response from `/health`; chat, session, and direct catalog endpoints remain unavailable. LLM connectivity is checked when handling a chat request. LLM failure does not make the database-backed direct catalog endpoints unavailable.

## Safe defaults and limitations

When a workout request omits important details, the API generates immediately using these defaults:

- Beginner experience level.
- General fitness goal.
- A 30-45 minute session.
- Moderate training volume and rest periods.
- No known injuries or medical restrictions.
- Only explicitly named equipment; when none is named, prefer body-weight exercises.

The response always lists applied assumptions. If the user mentions pain, injury, pregnancy, rehabilitation, or a medical condition, the response avoids medical advice, includes an appropriate warning, and recommends qualified professional guidance.

The catalog does not contain difficulty or contraindication fields. The API therefore must not claim that a selected exercise is medically safe, appropriate for a specific condition, or objectively beginner-level. "Beginner" affects conservative volume and the model's selection preference, and the response must present it as a recommendation rather than a catalog fact.

## Persistence and transaction behavior

Exercises, catalog synchronization metadata, sessions, and messages persist across server restarts. The exercises table stores the English renderable catalog representation and indexed filter columns. JSON-compatible columns store secondary muscles and English instruction steps in both SQLite and PostgreSQL.

A session has a UUID, creation and update timestamps, and ordered messages. Each message stores its role, text, structured assistant payload when applicable, and timestamp.

The service records a completed exchange only after response validation succeeds. Failed or malformed assistant generations are not stored as successful assistant messages. Database operations use transactions so a partial exchange cannot be presented as completed history.

Deleting a session also deletes its messages.

## Failure handling

- Catalog schema, synchronization, or startup validation failure keeps the application unready while allowing `/health` to report the failure.
- Database unavailability returns `503` for endpoints requiring persistence.
- LLM connection failure or timeout returns `503` for chat requests without affecting direct catalog reads.
- Malformed LLM output triggers one repair attempt, followed by `502` if still invalid.
- Unknown model-selected IDs are discarded. If the remaining selection cannot satisfy the response, one corrected generation attempt is allowed.
- No matching catalog records produces a successful empty response with a warning.
- Internal logs record provider and validation failures without logging API keys or database credentials.

## Verification strategy

### Unit tests

- Catalog loading and schema validation.
- Vocabulary construction and constraint detection.
- Hard filtering and weighted ranking.
- Pagination calculations, stable ordering, partial-match filters, and combined-filter semantics.
- Unique category, body-part, and equipment normalization and sorting.
- LLM JSON extraction, validation, and repair behavior.
- Unknown exercise ID rejection and catalog hydration.
- Conservative defaults and warning rules.
- Pydantic request and response contracts.

### Integration tests

- Exercise-search and workout-generation request flows.
- Direct exercise listing with default and boundary pagination.
- Every direct filter individually and in combination.
- Random exercise retrieval and empty-catalog behavior.
- Category, body-part, and equipment value endpoints.
- Direct catalog availability while the mock LLM provider is unavailable.
- Catalog synchronization after the JSON source changes, including deleted records.
- New session creation and existing session continuation.
- Session history persistence across application restart.
- Session retrieval and deletion.
- SQLite integration suite.
- PostgreSQL integration suite using an isolated test database.
- Mock OpenAI-compatible server behavior for successful output, malformed JSON, corrected JSON, timeout, and connection failure.
- Readiness behavior for valid and invalid catalog/database states.

### Optional live smoke tests

Separate opt-in scripts verify one Ollama configuration and one cloud OpenAI-compatible configuration. Live-provider tests are not part of the deterministic default test suite because they require credentials, network access, and provider availability.

## Acceptance criteria

- A client can submit an English conversational query without a session ID and receive a new session ID, conversational answer, and structured response.
- A client can page and filter `/exercises` without invoking the LLM, using the specified response envelope and limits.
- `/exercises/random` returns one complete catalog record.
- `/categories`, `/body-parts`, and `/equipment` return sorted unique database values.
- The same session ID continues the conversation after a server restart.
- The same application code works with SQLite or PostgreSQL through configuration.
- The same LLM gateway works with configured OpenAI-compatible local or cloud endpoints.
- Every returned exercise ID exists in `data/exercises.json`, and catalog-owned fields exactly match that record.
- Search results honor explicit equipment, target, and body-part constraints.
- Workout responses include assumptions, ordered exercises, sets, repetitions, rest, renderable media paths, and English instructions.
- Missing workout details cause immediate conservative generation rather than follow-up questions.
- The API never invents an exercise when no catalog match exists.
