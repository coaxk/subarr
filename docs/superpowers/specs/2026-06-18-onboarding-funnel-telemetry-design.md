# Onboarding-funnel telemetry + trustworthy detection (activation epic — slice 2, instrument-first)

**Goal:** make the next #202 analysis definitive by capturing *where* in onboarding users drop off, and by ensuring per-ping detection (subgen reachability) reflects current state rather than the boot/default probe.

**Why instrument before building:** the slice-1 data showed we can't tell *why* 40% never connect an arr — friction at the arr step vs never reaching it vs networking. And `subgen_kind="unreachable"` is confounded by detection timing (first ping fires at boot against the default `SUBGEN_URL`, pre-setup). After the #268 pollution fix, clean data is accruing; adding a funnel signal makes the follow-up conclusive. No friction fix is built until that data lands.

**Spans two repos:** `subarr` (the client payload) and `subarr-telemetry` (the Cloudflare Worker + D1). Ship the telemetry side first so the columns exist before the client emits the fields (unknown fields are silently dropped by the worker's enumerated INSERT).

---

## Part A — subarr-telemetry (ship first)

### D1 migration `migrations/0004_onboarding_funnel.sql`
```sql
ALTER TABLE pings ADD COLUMN onboarding_step INTEGER;
ALTER TABLE pings ADD COLUMN onboarding_complete INTEGER;  -- 0/1
```
Coarse + non-identifying — fits the privacy-by-construction schema.

### Worker INSERT
Add `onboarding_step`, `onboarding_complete` to the enumerated allow-list of accepted columns (the privacy enforcement layer). Validate types (int / 0-1) and clamp/ignore out-of-range, consistent with existing field handling. Forbidden-fields behaviour unchanged.

### Tests
Worker unit test: a payload carrying the two fields is stored; a payload with forbidden fields still drops them; missing fields default to NULL.

## Part B — subarr (ship after A is live)

### Payload fields
- `make_default_stats_provider(app_state)` adds `onboarding_step` (int) + `onboarding_complete` (bool), read from `app_state.onboarding.get()` (`.step`, `.is_complete`). Best-effort: absent store → omit / defaults.
- `TelemetryPayload` gains `onboarding_step: int | None` + `onboarding_complete: bool`; `to_dict()` includes them; `build_payload()` populates them from the stats dict.

### Trustworthy detection (the "before-the-fact" fix)
- Add an optional `subgen_caps_refresher` (async callable) to `TelemetryCollector`. When set, `send_now()` awaits it **best-effort** (try/except, never blocks/breaks the send) *before* `build_payload()`, so `subgen_kind` reflects current reachability rather than the boot probe.
- The app wires it in `lifespan` to a callable that re-probes subgen and updates `app.state.subgen_caps` (reusing `probe_capabilities`). The existing sync `subgen_caps_provider` still reads the (now-refreshed) cached value.
- Not deferring the first ping — that would drop single-ping abandoners, the exact cohort we measure. Every ping is self-describing via `onboarding_step`.

### Privacy
The forbidden-fields test (`test_payload_never_includes_forbidden_fields`) must still pass — the two new fields are coarse and allowed; nothing identifying is added.

### Tests
- Payload carries `onboarding_step` / `onboarding_complete` from a fake onboarding state.
- `send_now()` awaits the refresher when set, and still sends if the refresher raises (best-effort).
- `send_now()` with no refresher behaves exactly as today (back-comp).

## Rollout
1. Merge + deploy Part A (telemetry). Verify the columns exist (a real ping lands them).
2. Merge Part B (subarr client).
3. Let ~2 weeks of clean data accrue, then re-run the #202 funnel by `onboarding_step` to locate the drop — and only then design the targeted friction fix (slice 3).

## Out of scope
The friction fix itself (slice 3, gated on this data). Backfilling the polluted historical rows (separate, deferred).
