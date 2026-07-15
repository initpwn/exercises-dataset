# Go Account and Catalog API Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Go REST API for Google and email/password accounts plus a versioned English exercise catalog.

**Architecture:** Go owns account identity and reads the repository dataset into a queryable catalog. PostgreSQL stores users, credentials, expiring verification/reset tokens, and rotating refresh sessions. Android workout data remains client-local.

**Tech Stack:** Go, chi, pgx, PostgreSQL, Argon2id, JWT, Google ID token validation, SMTP mail adapter, testify, testcontainers-go.

## Global Constraints

- English catalog fields only in v1.
- Never store raw passwords, reset/verification tokens, or refresh tokens.
- Account endpoints return `{ "code": "...", "message": "..." }` errors.
- Access tokens are short-lived; refresh tokens rotate.
- Plans, routines, sessions, and history are never uploaded in v1.

---

## File structure

- `backend/cmd/api/main.go` — configuration and dependency composition.
- `backend/internal/auth/` — account service, password/token logic, Google verifier, mailer interface.
- `backend/internal/catalog/` — dataset loader and filtering service.
- `backend/internal/httpapi/` — router and HTTP handlers.
- `backend/internal/storage/postgres/` and `backend/migrations/` — account persistence.

### Task 1: Scaffold service and health endpoint

**Files:** Create `backend/go.mod`, `backend/cmd/api/main.go`, `backend/internal/httpapi/router.go`; test `backend/internal/httpapi/router_test.go`.

**Produces:** `httpapi.NewRouter(Dependencies) http.Handler`.

- [ ] **Step 1: Write the failing test**

```go
func TestHealth(t *testing.T) {
 rr := httptest.NewRecorder()
 httpapi.NewRouter(httpapi.Dependencies{}).ServeHTTP(rr, httptest.NewRequest(http.MethodGet, "/healthz", nil))
 require.Equal(t, http.StatusOK, rr.Code)
 require.JSONEq(t, `{"status":"ok"}`, rr.Body.String())
}
```

- [ ] **Step 2: Run it**

Run: `cd backend; go test ./internal/httpapi -run TestHealth -count=1`

Expected: FAIL because `NewRouter` is absent.

- [ ] **Step 3: Implement the route**

```go
func NewRouter(_ Dependencies) http.Handler {
 r := chi.NewRouter()
 r.Get("/healthz", func(w http.ResponseWriter, _ *http.Request) {
  w.Header().Set("Content-Type", "application/json")
  _, _ = w.Write([]byte(`{"status":"ok"}`))
 })
 return r
}
```

- [ ] **Step 4: Verify and commit**

Run: `cd backend; go test ./internal/httpapi -count=1`

Expected: PASS.

```bash
git add backend && git commit -m "feat(api): scaffold Go service"
```

### Task 2: Persist account identity and tokens

**Files:** Create `backend/migrations/000001_accounts.sql`, `backend/internal/auth/repository.go`, `backend/internal/storage/postgres/account_repository.go`; test `backend/internal/storage/postgres/account_repository_test.go`.

**Produces:** `AccountRepository` methods `CreateUser`, `FindUserByEmail`, `CreateToken`, `ConsumeToken`, `CreateSession`, `RotateSession`, and `RevokeSession`.

- [ ] **Step 1: Write the failing duplicate-email test**

```go
func TestCreateUserRejectsDuplicateEmail(t *testing.T) {
 repo := newTestAccountRepository(t)
 require.NoError(t, repo.CreateUser(ctx, auth.NewUser("a@example.com", "hash")))
 require.ErrorIs(t, repo.CreateUser(ctx, auth.NewUser("A@example.com", "hash")), auth.ErrEmailExists)
}
```

- [ ] **Step 2: Run it**

Run: `cd backend; go test ./internal/storage/postgres -run TestCreateUserRejectsDuplicateEmail -count=1`

Expected: FAIL.

- [ ] **Step 3: Create the migration and repository**

```sql
CREATE TABLE users (id UUID PRIMARY KEY, email CITEXT UNIQUE NOT NULL, password_hash TEXT,
 email_verified_at TIMESTAMPTZ, google_subject TEXT UNIQUE, created_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE TABLE account_tokens (id UUID PRIMARY KEY, user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 kind TEXT NOT NULL, token_hash BYTEA NOT NULL UNIQUE, expires_at TIMESTAMPTZ NOT NULL, consumed_at TIMESTAMPTZ);
CREATE TABLE refresh_sessions (id UUID PRIMARY KEY, user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 token_hash BYTEA NOT NULL UNIQUE, expires_at TIMESTAMPTZ NOT NULL, revoked_at TIMESTAMPTZ, replaced_by UUID);
```

- [ ] **Step 4: Verify and commit**

Run: `cd backend; go test ./internal/storage/postgres -count=1`

Expected: PASS using a PostgreSQL test container.

```bash
git add backend && git commit -m "feat(api): persist account identities"
```

### Task 3: Add email registration, verification, login, refresh, logout, and reset

**Files:** Create `backend/internal/auth/password.go`, `service.go`, `mailer.go`, `backend/internal/httpapi/auth_handlers.go`; test `backend/internal/auth/service_test.go` and `backend/internal/httpapi/auth_handlers_test.go`.

**Produces:** `POST /v1/auth/register`, `/verify-email`, `/login`, `/refresh`, `/logout`, `/forgot-password`, and `/reset-password`.

- [ ] **Step 1: Write failing registration and token-rotation tests**

```go
func TestRegisterSendsVerification(t *testing.T) {
 mailer := &fakeMailer{}; service := auth.NewService(repo, mailer, clock)
 _, err := service.Register(ctx, "person@example.com", "correct horse battery staple")
 require.NoError(t, err); require.Len(t, mailer.Messages, 1)
}
func TestRefreshRotatesToken(t *testing.T) {
 pair := issueVerifiedPair(t, service); next, err := service.Refresh(ctx, pair.RefreshToken)
 require.NoError(t, err); require.NotEqual(t, pair.RefreshToken, next.RefreshToken)
 _, err = service.Refresh(ctx, pair.RefreshToken); require.ErrorIs(t, err, auth.ErrInvalidSession)
}
```

- [ ] **Step 2: Run tests**

Run: `cd backend; go test ./internal/auth -run 'TestRegister|TestRefresh' -count=1`

Expected: FAIL.

- [ ] **Step 3: Implement secure credentials and non-enumerating reset**

```go
func HashPassword(password string) (string, error) {
 if utf8.RuneCountInString(password) < 12 { return "", ErrWeakPassword }
 return argon2id.CreateHash(password, argon2id.DefaultParams)
}
func (s *Service) ForgotPassword(ctx context.Context, email string) error {
 user, err := s.repo.FindUserByEmail(ctx, normalizeEmail(email))
 if errors.Is(err, ErrUserNotFound) { return nil }
 if err != nil { return err }
 raw, err := s.issueToken(ctx, user.ID, ResetPassword); if err != nil { return err }
 return s.mailer.SendPasswordReset(ctx, user.Email, raw)
}
```

- [ ] **Step 4: Verify handlers and commit**

Run: `cd backend; go test ./internal/auth ./internal/httpapi -count=1`

Expected: PASS for successful and expired verification/reset, invalid credentials, duplicate email, refresh reuse, and logout.

```bash
git add backend && git commit -m "feat(api): add email account lifecycle"
```

### Task 4: Add Google login and verified-email linking

**Files:** Create `backend/internal/auth/google.go`; modify `service.go` and `auth_handlers.go`; test `backend/internal/auth/google_test.go`.

**Produces:** `POST /v1/auth/google` accepting `{ "idToken": "..." }`.

- [ ] **Step 1: Write the failing linking test**

```go
func TestGoogleLoginLinksVerifiedMatchingEmail(t *testing.T) {
 user := createVerifiedEmailUser(t, repo, "person@example.com")
 tokens, err := service.LoginGoogle(ctx, "google-token")
 require.NoError(t, err); require.Equal(t, user.ID, tokens.UserID)
}
```

- [ ] **Step 2: Run it**

Run: `cd backend; go test ./internal/auth -run TestGoogleLoginLinksVerifiedMatchingEmail -count=1`

Expected: FAIL.

- [ ] **Step 3: Implement the narrow verifier contract**

```go
type GoogleIdentityVerifier interface { Verify(context.Context, string) (GoogleIdentity, error) }
type GoogleIdentity struct { Subject, Email string; EmailVerified bool }
func (s *Service) LoginGoogle(ctx context.Context, token string) (TokenPair, error) {
 identity, err := s.google.Verify(ctx, token)
 if err != nil || !identity.EmailVerified { return TokenPair{}, ErrInvalidGoogleIdentity }
 return s.findOrLinkGoogleUser(ctx, identity)
}
```

- [ ] **Step 4: Verify and commit**

Run: `cd backend; go test ./internal/auth ./internal/httpapi -count=1`

Expected: PASS.

```bash
git add backend && git commit -m "feat(api): add Google sign in"
```

### Task 5: Load the dataset and expose catalog API

**Files:** Create `backend/internal/catalog/exercise.go`, `loader.go`, `service.go`, `backend/internal/httpapi/catalog_handlers.go`; modify `router.go`; test catalog and handler packages.

**Produces:** `GET /v1/exercises`, `/v1/exercises/{id}`, `/v1/catalog/filters`, `/v1/catalog/version`.

- [ ] **Step 1: Write failing filter/pagination tests**

```go
func TestListFiltersAndPaginates(t *testing.T) {
 svc := catalog.NewService(fixtureExercises(t))
 result := svc.List(catalog.ListQuery{Equipment: "dumbbell", Limit: 1, Page: 1})
 require.Equal(t, 1, result.Total); require.Equal(t, "dumbbell curl", result.Items[0].Name)
}
```

- [ ] **Step 2: Run it**

Run: `cd backend; go test ./internal/catalog -count=1`

Expected: FAIL.

- [ ] **Step 3: Decode dataset and select English step arrays**

```go
type Exercise struct { ID, Name, Category, BodyPart, Equipment, Target string; Instructions []string; Image, GIFURL string }
type datasetExercise struct { ID, Name, Category, BodyPart, Equipment, Target, Image, GIFURL string; InstructionSteps struct { EN []string `json:"en"` } `json:"instruction_steps"` }
func Load(path string) ([]Exercise, error) {
 file, err := os.Open(path); if err != nil { return nil, err }; defer file.Close()
 var source []datasetExercise
 if err := json.NewDecoder(file).Decode(&source); err != nil { return nil, err }
 out := make([]Exercise, 0, len(source))
 for _, item := range source { out = append(out, Exercise{ID:item.ID, Name:item.Name, Category:item.Category, BodyPart:item.BodyPart, Equipment:item.Equipment, Target:item.Target, Instructions:item.InstructionSteps.EN, Image:item.Image, GIFURL:item.GIFURL}) }
 return out, nil
}
```

- [ ] **Step 4: Implement strict HTTP query parsing, then verify and commit**

Run: `cd backend; go test ./... && go vet ./...`

Expected: PASS, including unknown ID 404 and invalid pagination 400.

```bash
git add backend && git commit -m "feat(api): expose English exercise catalog"
```
