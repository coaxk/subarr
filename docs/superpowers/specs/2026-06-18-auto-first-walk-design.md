# Auto-first-walk on setup completion (activation epic — slice 1)

**Goal:** deliver subarr's "aha" (coverage gaps on the dashboard) automatically when a user finishes setup, instead of relying on them to manually trigger the first walk.

**Motivation (#202, on de-polluted telemetry):** of real retained installs, **34% configure an arr but never walk** — they finish onboarding and land on an empty dashboard. `onFinish()` only calls `/complete` then redirects; the walk fires only if the user manually clicks "Run" in the wizard's walk step.

**Scope:** auto-trigger the first walk server-side on completion (arr-guarded) + a dashboard zero-scans empty-state CTA as a safety net. The 40%-never-connect-an-arr cohort is slice 2 (separate spec).

---

## Components

### 1. Shared walk-kick helper
Extract the walk-start logic currently inline in `first_walk()` (`routers/onboarding.py`) into:

`async def _kick_first_walk(app_state) -> dict` — resolves `probe_roots` from onboarding progress (default `["TV", "Movies"]`), persists them onto the `coverage_walk` schedule (so ongoing walks ffprobe too), and starts the walks via `app_state.probe_walker`. Returns the same `{walks, schedule_probe_roots, schedule_persisted}` shape `first_walk()` returns today.

Both the existing `POST /api/onboarding/first-walk` endpoint and `complete()` call it — single source of truth, no duplicated logic.

### 2. `complete()` auto-kick (core)
In `POST /api/onboarding/complete`, after `_apply_progress_to_settings` + `_rebuild_runtime_clients`, kick the first walk **only when all three hold**:

1. **First completion** — read `store.get().is_complete` *before* `store.complete()`; if it was already complete, skip (no re-walk on a re-run / idempotent `/complete`).
2. **An arr is configured** — any of bazarr/sonarr/radarr (the coverage sources). Skips the no-arr cohort (slice 2) for whom a walk yields nothing.
3. **No scan has ever run** — zero rows in the scans store. Prevents a duplicate walk when the user already clicked "Run" in the wizard's walk step.

Behaviour: best-effort + non-blocking — wrapped in `try/except`; any failure logs and **never breaks the `/complete` response**. `start_walk` only enqueues (returns fast), so awaiting the helper is cheap; the walk itself runs in the background as today.

Frontend `onFinish()` is unchanged (still `complete()` + redirect) — the walk now fires server-side, so it runs even if the user closes the tab.

### 3. Dashboard zero-scans empty-state CTA
When the dashboard loads with **zero coverage data / no scans**, render a prominent empty-state: *"No coverage data yet — run your first walk"* with a one-click Run button that hits the walk trigger. Safety net for anyone who lands walk-less (no-arr, auto-walk error, opted out, established install via #262). The existing post-completion `WelcomeCard` (4-step getting-started) stays as-is.

## Data flow
1. User clicks "Finish setup →" → `onFinish()` → `POST /api/onboarding/complete`.
2. `complete()`: apply progress, rebuild clients, then *(first-completion ∧ arr-configured ∧ no prior scan)* → `_kick_first_walk()` (best-effort) → return.
3. Dashboard: scans exist → coverage view; zero scans → empty-state CTA (which can also trigger a walk).

## Error handling
- Auto-walk is best-effort: missing media root / walker error / no probe_walker → log, never raise into `/complete`.
- Empty-state CTA surfaces its own button error (reuses the page's run-now pattern: credentials, ok-check, no false success).

## Testing
- **`complete()` auto-kick:** kicks when first-completion + arr configured + no prior scan (mock walker → assert `start_walk` called). Does **not** kick when (a) already complete, (b) no arr, (c) a scan already exists. (`app_with_stub`, integration stubs, monkeypatch the walker.)
- **`_kick_first_walk` helper:** resolves roots, persists schedule, starts walks — reuse/extend the existing first-walk coverage so the extraction is behaviour-preserving.
- **Frontend:** dashboard renders the empty-state CTA at zero scans and hides it when data is present (light vitest on the decision helper).

## Out of scope (slice 2)
Integration-setup friction for the 40% who never connect an arr (stronger Docker auto-discovery + value framing) — separate spec.
