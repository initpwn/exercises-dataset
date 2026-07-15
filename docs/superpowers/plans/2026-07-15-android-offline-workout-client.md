# Android Offline Workout Client Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an English-only offline-first Android app for catalog browsing, reusable plans, scheduled routines, timed progressive workouts, and removable history.

**Architecture:** Feature-scoped Compose view models use Room as the source of truth for the bundled catalog, plans, schedules, overrides, active snapshots, and history. Account/API code is isolated from the workout domain, preserving offline training.

**Tech Stack:** Kotlin, Compose, Material 3, Room, DataStore, Hilt, Navigation Compose, Coil, Media3, Credential Manager, Retrofit, JUnit, Turbine, Compose UI tests.

## Global Constraints

- English only; import `instruction_steps.en`.
- Catalog, routines, plans, sessions, and history work without a network.
- Routines are `weekly` or calendar-anchored `interval`.
- `progressive` is routine-level; progressive uses Start/Pause/Finish, non-progressive has no controls or timing.
- History uses immutable plan snapshots and entries are removable.

---

## File structure

- `android/app/src/main/assets/` — catalog JSON and local media manifest.
- `android/app/src/main/java/com/example/workouts/data/` — Room and API implementations.
- `android/app/src/main/java/com/example/workouts/domain/` — schedule/session logic.
- `android/app/src/main/java/com/example/workouts/feature/` — Today, Routines, Plans, Explore, History, Profile.

### Task 1: Scaffold Compose navigation

**Files:** Create `android/settings.gradle.kts`, root/app Gradle files, manifest, `MainActivity.kt`, `navigation/AppNavigation.kt`; test `AppNavigationTest.kt`.

**Produces:** `today`, `routines`, `explore`, `history`, and `profile` destinations.

- [ ] **Step 1: Write failing navigation test**

```kotlin
@Test fun bottomNavigationOpensRoutines() = runComposeUiTest {
 setContent { WorkoutApp() }
 onNodeWithText("Routines").performClick()
 onNodeWithText("Your routines").assertIsDisplayed()
}
```

- [ ] **Step 2: Run it**

Run: `cd android; ./gradlew connectedDebugAndroidTest -Pandroid.testInstrumentationRunnerArguments.class=com.example.workouts.AppNavigationTest`

Expected: FAIL because `WorkoutApp` is absent.

- [ ] **Step 3: Implement destinations**

```kotlin
enum class TopLevelDestination(val route: String, val label: String) {
 Today("today", "Today"), Routines("routines", "Routines"), Explore("explore", "Explore"),
 History("history", "History"), Profile("profile", "Profile")
}
```

- [ ] **Step 4: Verify and commit**

Run: `cd android; ./gradlew connectedDebugAndroidTest -Pandroid.testInstrumentationRunnerArguments.class=com.example.workouts.AppNavigationTest`

Expected: PASS.

```bash
git add android && git commit -m "feat(android): scaffold Compose workout app"
```

### Task 2: Import the English catalog into Room and build Explore

**Files:** Create assets copy of `data/exercises.json`, catalog entity/DAO/importer/repository, `feature/explore/ExploreViewModel.kt`, `ExploreScreen.kt`, `ExerciseDetailScreen.kt`; test importer and screen packages.

**Produces:** offline search/filter/detail and “Add to plan”.

- [ ] **Step 1: Write failing English-only importer test**

```kotlin
@Test fun importerPersistsOnlyEnglishSteps() = runTest {
 importer.import(jsonWithEnglishAndSpanish())
 assertThat(dao.exercise("0001").first().instructionSteps).containsExactly("Lie down", "Lift")
}
```

- [ ] **Step 2: Run it**

Run: `cd android; ./gradlew testDebugUnitTest --tests '*CatalogImporterTest'`

Expected: FAIL.

- [ ] **Step 3: Implement catalog storage and UI state**

```kotlin
@Entity(tableName = "exercises")
data class ExerciseEntity(@PrimaryKey val id: String, val name: String, val category: String,
 val equipment: String, val target: String, val imagePath: String, val gifPath: String, val instructionStepsJson: String)
data class ExploreUiState(val query: String = "", val equipment: String? = null, val category: String? = null, val target: String? = null)
```

- [ ] **Step 4: Verify search/filter UI and commit**

Run: `cd android; ./gradlew testDebugUnitTest --tests '*CatalogImporterTest' && ./gradlew connectedDebugAndroidTest -Pandroid.testInstrumentationRunnerArguments.class=com.example.workouts.feature.explore.ExploreScreenTest`

Expected: PASS for search, equipment/category/target filters, animation, English steps, and add-to-plan.

```bash
git add android && git commit -m "feat(android): browse local exercise catalog"
```

### Task 3: Persist reusable playlist plans

**Files:** Create `data/plans/PlanEntities.kt`, `PlanDao.kt`, `domain/plans/PlanRepository.kt`, `feature/plans/PlanEditorScreen.kt`; test `PlanDaoTest.kt`.

**Produces:** ordered named plans with per-exercise set, rep, target-weight, and rest prescriptions.

- [ ] **Step 1: Write failing order test**

```kotlin
@Test fun planRetainsExerciseOrderAndPrescription() = runTest {
 dao.save(plan("Push", item("bench", 0, 3, 8), item("press", 1, 3, 10)))
 assertThat(dao.plan("Push").first().exercises.map { it.catalogExerciseId }).containsExactly("bench", "press")
}
```

- [ ] **Step 2: Run it**

Run: `cd android; ./gradlew connectedDebugAndroidTest -Pandroid.testInstrumentationRunnerArguments.class=com.example.workouts.data.plans.PlanDaoTest`

Expected: FAIL.

- [ ] **Step 3: Implement plan exercise contract**

```kotlin
data class PlanExerciseDraft(val catalogExerciseId: String, val order: Int, val sets: Int,
 val reps: Int, val targetWeightKg: Double?, val restSeconds: Int?)
```

- [ ] **Step 4: Verify and commit**

Run: `cd android; ./gradlew connectedDebugAndroidTest -Pandroid.testInstrumentationRunnerArguments.class=com.example.workouts.data.plans.PlanDaoTest`

Expected: PASS.

```bash
git add android && git commit -m "feat(android): create reusable workout plans"
```

### Task 4: Implement routine storage and schedule resolution

**Files:** Create `data/routines/RoutineEntities.kt`, `RoutineDao.kt`, `domain/schedule/ScheduleResolver.kt`; test `ScheduleResolverTest.kt`.

**Produces:** named multiple routines, weekly slots, interval plan slots, and deterministic `resolve(routine, date, overrides)`.

- [ ] **Step 1: Write failing recovery and interval-override tests**

```kotlin
@Test fun weeklyRoutineReturnsRecoveryForWednesday() {
 assertThat(resolver.resolve(recoveryWednesdayRoutine(), LocalDate.parse("2026-07-15"), emptyList())).isEqualTo(ScheduledDay.Recovery)
}
@Test fun planIntervalOverrideControlsNextOccurrence() {
 assertThat(resolver.resolve(intervalRoutine(defaultGapDays = 3, firstPlanGapDays = 5), LocalDate.parse("2026-07-06"), emptyList()).planId).isEqualTo("second-plan")
}
```

- [ ] **Step 2: Run it**

Run: `cd android; ./gradlew testDebugUnitTest --tests '*ScheduleResolverTest'`

Expected: FAIL.

- [ ] **Step 3: Implement domain model**

```kotlin
sealed interface ScheduledDay { data class Plan(val planId: String, val kind: PlanKind): ScheduledDay; data object Recovery: ScheduledDay; data object Complete: ScheduledDay }
enum class RoutineType { WEEKLY, INTERVAL }
enum class PlanKind { STRENGTH, CARDIO }
```

- [ ] **Step 4: Verify and commit**

Run: `cd android; ./gradlew testDebugUnitTest --tests '*ScheduleResolverTest'`

Expected: PASS for weekday, recovery, cardio, interval default/override, and loop/non-loop completion.

```bash
git add android && git commit -m "feat(android): resolve weekly and interval routines"
```

### Task 5: Build routine editor and Today/ad-hoc override flow

**Files:** Create `feature/routines/RoutinesScreen.kt`, `RoutineEditorViewModel.kt`, `WeeklyRoutineEditor.kt`, `IntervalRoutineEditor.kt`, `feature/today/TodayViewModel.kt`, `TodayScreen.kt`, `ScheduleOverrideRepository.kt`; test feature packages.

**Produces:** active routine selection, routine-level Progressive and Loop checkboxes, recovery/cardio scheduling, and skip/reschedule choices.

- [ ] **Step 1: Write failing editor and conflict tests**

```kotlin
@Test fun selectingAdHocPlanPromptsWhenScheduledPlanExists() = runTest {
 assertThat(viewModel.selectAdHocPlan("arms-plan").first().conflict).isEqualTo(PlanConflict.ScheduledPlan("legs-plan"))
}
```

- [ ] **Step 2: Run it**

Run: `cd android; ./gradlew testDebugUnitTest --tests '*TodayViewModelTest'`

Expected: FAIL.

- [ ] **Step 3: Implement Today states and one-occurrence overrides**

```kotlin
sealed interface TodayUiState {
 data class ScheduledPlan(val plan: WorkoutPlan, val progressive: Boolean): TodayUiState
 data class Recovery(val nextPlan: WorkoutPlan?): TodayUiState
 data class RoutineComplete(val routineName: String): TodayUiState
 data object NoActiveRoutine: TodayUiState
}
```

- [ ] **Step 4: Verify and commit**

Run: `cd android; ./gradlew testDebugUnitTest --tests '*TodayViewModelTest' && ./gradlew connectedDebugAndroidTest -Pandroid.testInstrumentationRunnerArguments.class=com.example.workouts.feature.routines.RoutineEditorTest`

Expected: PASS for weekly assignments, interval configuration, active routine, skip, and reschedule.

```bash
git add android && git commit -m "feat(android): schedule routines and Today plans"
```

### Task 6: Add progressive sessions and removable history

**Files:** Create `data/history/HistoryEntities.kt`, `domain/session/SessionService.kt`, `feature/session/ActivePlanScreen.kt`, `feature/history/HistoryScreen.kt`; test session/history packages.

**Produces:** progressive Start/Pause/Finish, no controls for non-progressive plans, immutable plan snapshots, timed/untimed history, and removal.

- [ ] **Step 1: Write failing timing and untimed-entry tests**

```kotlin
@Test fun progressiveFinishStoresElapsedTime() = runTest {
 service.start(plan, now); service.pause(now.plusMinutes(10)); service.finish(now.plusMinutes(20))
 assertThat(history.first().durationSeconds).isEqualTo(600)
}
@Test fun nonProgressiveViewCreatesUntimedReference() = runTest {
 service.recordReference(plan, today); assertThat(history.first().startedAt).isNull()
}
```

- [ ] **Step 2: Run it**

Run: `cd android; ./gradlew testDebugUnitTest --tests '*SessionServiceTest'`

Expected: FAIL.

- [ ] **Step 3: Implement conditional controls and snapshot logs**

```kotlin
fun controlsFor(progressive: Boolean): List<SessionControl> = if (progressive) listOf(Start, Pause, Finish) else emptyList()
```

- [ ] **Step 4: Verify and commit**

Run: `cd android; ./gradlew testDebugUnitTest --tests '*SessionServiceTest' && ./gradlew connectedDebugAndroidTest -Pandroid.testInstrumentationRunnerArguments.class=com.example.workouts.feature.history.HistoryScreenTest`

Expected: PASS for pause duration, finish, no non-progressive controls, history removal, and plan-edit snapshot isolation.

```bash
git add android && git commit -m "feat(android): record sessions and history"
```

### Task 7: Add account UI without online workout dependency

**Files:** Create `data/account/AuthApi.kt`, `TokenStore.kt`, `feature/profile/ProfileViewModel.kt`, `AuthScreens.kt`; test `ProfileViewModelTest.kt`.

**Consumes:** the Go API account endpoints.

**Produces:** email registration/verification/login/reset, Google Sign-In, encrypted session storage, and recoverable error states.

- [ ] **Step 1: Write failing offline-login test**

```kotlin
@Test fun loginWhileOfflineShowsOfflineState() = runTest {
 api.offline = true; viewModel.login("person@example.com", "correct horse battery staple")
 assertThat(viewModel.state.value.error).isEqualTo(AuthError.Offline)
}
```

- [ ] **Step 2: Run it**

Run: `cd android; ./gradlew testDebugUnitTest --tests '*ProfileViewModelTest'`

Expected: FAIL.

- [ ] **Step 3: Implement auth states**

```kotlin
sealed interface AuthError { data object Offline: AuthError; data object InvalidCredentials: AuthError; data object VerificationExpired: AuthError }
```

- [ ] **Step 4: Verify offline release scenario and commit**

Run: `cd android; ./gradlew testDebugUnitTest connectedDebugAndroidTest lintDebug`

Expected: PASS after creating a weekly and interval routine, resolving recovery/cardio, completing a progressive plan, recording an untimed plan, deleting history, and restarting offline.

```bash
git add android && git commit -m "feat(android): add account flows and offline release coverage"
```
