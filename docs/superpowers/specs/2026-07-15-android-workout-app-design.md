# Android Workout App Design

## Purpose

Create an English-only Android workout app from the exercise dataset. The app
lets a person browse 1,324 exercises, assemble reusable playlist-like workout
plans, schedule named routines, and keep local workout history. It is
offline-first, while a Go backend provides account authentication and catalog
access for future sync and catalog updates.

## Product scope

### Exercise catalog

- Bundle the English exercise catalog and its image/GIF media with the app.
- Support search plus category, equipment, and target-muscle filters.
- Exercise detail shows animation, equipment, targeted muscles, and ordered
  English instructions.
- Exercises can be added to a plan from catalog search or detail.

### Plans

A workout plan is the reusable playlist-like building block of a routine.

- A plan has a name and an ordered list of catalog exercises.
- Each plan exercise stores planned sets, reps, optional target weight, and
  optional rest duration.
- Plans are reusable by multiple routines.
- Starting a plan ad hoc can make it temporarily active.

### Routines

Users can create and name multiple routines and select one as active. A
routine references plans rather than owning copies of them.

Routine types:

1. **Weekly routine.** Monday through Sunday each resolve to a strength plan,
   cardio plan, or Recovery. This supports a three-, four-, five-, or
   seven-training-day program while retaining an explicit seven-day calendar.
   A Recovery slot intentionally has no workout plan.
2. **Interval routine.** An ordered plan sequence has a start date and a
   routine-level repeating calendar interval. A plan can override the interval
   before its next occurrence. The schedule, rather than a completion event,
   resolves the current plan, so it works for both timed and browse-only
   routines.

An interval routine may enable **Loop this routine**. When enabled, its final
plan repeats from the first plan according to the configured calendar cycle.
When disabled, the routine becomes complete after its final scheduled
occurrence and offers restart or a different active routine.

### Progressive behavior

`progressive` is a routine-level setting, separate from how the routine is
scheduled.

- **Progressive routine:** its active plan provides Start, Pause, and Finish
  controls. Start, pause intervals, and finish time are recorded in history.
  The schedule still determines which plan is due; completing a plan does not
  rewrite the calendar schedule.
- **Non-progressive routine:** plans are browsable and have no Start, Pause,
  or Finish controls. No duration is recorded. Today still shows the plan
  resolved by the weekly or interval schedule.

### Today and ad-hoc selection

Today resolves the active routine:

- Weekly routines use the current weekday.
- Interval routines use their calendar anchor, repeat interval, and any
  plan-level interval overrides.
- Recovery shows a recovery state and the upcoming scheduled workout.
- Cardio resolves to the assigned cardio plan and is presented like any other
  workout plan.

When the user selects an ad-hoc plan while another plan is scheduled or in
progress, prompt for one of two actions:

- Skip today’s scheduled plan.
- Reschedule the scheduled plan to its next available day.

The resulting skip or reschedule is a schedule override. It affects only that
occurrence and never changes the reusable routine template.

### History

- Progressive completed plans have start, pause, finish, elapsed duration,
  source (scheduled or ad hoc), and completed exercise/set data.
- Non-progressive scheduled or browsed plans appear as untimed reference
  entries.
- Users can remove history entries. Removing one never modifies a plan,
  routine, or catalog exercise.
- An active session uses a snapshot of its plan, so later template edits do not
  alter past history.

## Android user experience

Bottom navigation contains Today, Routines, Explore, History, and Profile.

- **Today:** decisive current-state screen. It shows the due plan with Start
  for progressive routines, an untimed view action for non-progressive ones,
  a recovery state, or an empty state for no active routine.
- **Routines:** create, name, activate, edit, duplicate, and archive routines.
  Weekly editing uses weekday rows; interval editing exposes an ordered plan
  list, calendar anchor, default repeat interval, optional plan overrides, and
  Loop this routine.
- **Plan editor:** an ordered exercise playlist with set/rep/weight/rest
  configuration.
- **Explore:** catalog discovery and plan insertion.
- **History:** timed and untimed entries, drill-down details, and removal.
- **Profile:** Google/email account state, unit preferences, and future sync
  status. The app itself is English-only in v1.

## Local-first data model

Android uses Room as the source of truth. The local database contains catalog
records, plans, routines, weekly slots, interval schedule definitions,
schedule overrides, active-session snapshots, exercise/set logs, history
entries, and local profile preferences.

Sync-ready records use stable UUIDs plus created/updated timestamps. Workout
data does not leave the device in v1; those fields make future sync additive.

Suggested core records:

- `Routine`: UUID, name, type, `progressive`, `loopEnabled`, active state,
  interval anchor/default interval, timestamps.
- `WeeklySlot`: routine UUID, weekday, slot type, optional plan UUID.
- `IntervalPlanSlot`: routine UUID, order, plan UUID, optional next-gap days.
- `WorkoutPlan`: UUID, name, timestamps.
- `PlanExercise`: plan UUID, catalog exercise ID, order, sets, reps, target
  weight, rest duration.
- `ScheduleOverride`: routine UUID, scheduled date, action (skip/reschedule),
  replacement date, timestamps.
- `WorkoutSession` and `SetLog`: immutable session snapshot and progressive
  duration data.
- `HistoryEntry`: plan UUID, source, optional session UUID/timestamps, and
  removal state.

## Go backend

The Go service does not own training data in v1. It supplies account identity
and a catalog API.

### Account endpoints

- Email/password registration.
- Email verification with a one-time expiring link or code.
- Email/password login.
- Google Sign-In token exchange and account linking when the verified email
  matches an existing account.
- Access token refresh with refresh-token rotation.
- Logout/session revocation.
- Forgot-password initiation and password-reset confirmation.

Passwords use Argon2id hashes. Mail delivery is implemented behind an adapter
so the chosen provider is configuration rather than account business logic.

### Catalog endpoints

- Paginated exercise listing with search and category, body-part, equipment,
  and target filters.
- Exercise lookup by ID.
- Filter-value endpoints for categories, body parts, and equipment.
- Catalog version metadata for future updates.

## Failure behavior

- Catalog, plan creation, schedule resolution, and active workouts function
  without network access.
- Authentication actions explain that connectivity is required when offline.
- Unverified email, expired verification/reset link, invalid credentials, and
  failed Google authentication have specific recoverable UI states.
- Recovery days cannot be marked missed automatically.
- Schedule overrides are persisted so Today does not repeat a resolved prompt.

## Verification

- Go unit and integration tests: registration, verification, email login,
  Google login/linking, refresh/logout, password reset, filters, pagination,
  and catalog lookup.
- Android database tests: weekly-day resolution, recovery/cardio handling,
  calendar interval resolution, loop behavior, plan-level interval overrides,
  skip/reschedule, active-plan conflicts, history removal, and immutable
  session snapshots.
- Compose UI tests: routine creation/editing, plan playlist editing, account
  states, ad-hoc conflict prompt, and progressive Start/Pause/Finish flow.
- Offline release check: browse catalog, create each routine type, resolve a
  due plan, complete a progressive workout, create a non-progressive history
  entry, then reconnect without data loss.

## Deferred scope

- Cross-device workout-data sync.
- Non-English app UI and catalog instructions.
- Social/community features, coaching, health-platform integrations, and
  recommendations.
