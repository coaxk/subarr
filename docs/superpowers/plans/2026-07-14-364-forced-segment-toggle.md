# Forced-Segment Settings Toggle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Settings switch for the forced-segment feature (today `SUBARR_FORCED_SEGMENT_ENABLED` env-only) so an English user can enable it without editing compose.

**Architecture:** Mirror the VAD toggle exactly. Backend: extend `GET /api/forced-segment` and add `POST /api/forced-segment/config`, persisting via `config_store` and live-applying unless env-pinned. Frontend: a `ForcedSegmentCard` in `settings.jsx` driven by a pure, unit-tested `deriveForcedSegmentToggle(status)` helper, warn-but-allow when the VAD model prerequisite is missing.

**Tech Stack:** FastAPI (sync `TestClient`), pytest; vanilla-JSX + esbuild, vitest.

---

## Reference facts (verified against current code)

- `forced_segment_enabled` is already a config field, env-loaded (`SUBARR_FORCED_SEGMENT_ENABLED`, default `"0"`), in `FIELD_ENV_VARS` and the coerce map — override-ready, no config plumbing needed.
- `config.env_is_set("forced_segment_enabled")` returns whether the env var pins it (config.py:509).
- `vad.vad_available()` is the hard-prerequisite signal (False in the test env — onnxruntime absent).
- Existing router: `routers/forced_segment.py` — `GET /api/forced-segment` returns `{state, summary}`; already auth-gated; walker/store on `app.state`.
- VAD analog to copy: `routers/vad.py` `set_config` (save_override + `object.__setattr__` unless env-pinned).
- Test fixture: `app_with_stub` (sync `TestClient`, walker/store/gen wired). VAD toggle test pattern: `tests/test_vad_router.py::test_vad_config_toggle_persists_and_reflects`.
- Frontend: `SpeechAudioCard` (settings.jsx:1430), mounted at settings.jsx:1759 (`<SpeechAudioCard />`). Helpers `Row` (141), `Toggle` (368), plus `SectionCard`. Pure helpers are `export`ed from the `.jsx` and imported in `__tests__/*.test.js` (e.g. `review-multiselect.test.js` imports from `../review.jsx`). settings.jsx currently exports only `SettingsPage`.
- Bundle: `npm run build:frontend` regenerates `*.bundle.js`; `npm run check:frontend` fails on drift.

---

### Task 1: Backend — toggle status + config endpoint

**Files:**
- Modify: `src/subarr/routers/forced_segment.py`
- Test: `tests/test_forced_segment_router.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_forced_segment_router.py`:

```python
def test_get_forced_segment_exposes_toggle_fields(app_with_stub):
    body = app_with_stub.get("/api/forced-segment").json()
    # toggle fields added alongside the existing scan fields
    assert {"enabled", "env_controlled", "vad_available"} <= body.keys()
    assert "state" in body and "summary" in body
    assert isinstance(body["vad_available"], bool)


def test_config_toggle_persists_and_live_applies(app_with_stub, tmp_path, monkeypatch):
    from subarr import config, config_store as cs

    monkeypatch.setenv("SUBARR_CONFIG_STORE", str(tmp_path / "ov.json"))
    monkeypatch.delenv("SUBARR_FORCED_SEGMENT_ENABLED", raising=False)
    prior = config.settings.forced_segment_enabled
    try:
        r = app_with_stub.post("/api/forced-segment/config", json={"enabled": True})
        assert r.status_code == 200
        body = r.json()
        assert body["enabled"] is True
        assert body["env_controlled"] is False
        assert config.settings.forced_segment_enabled is True          # live-applied
        assert cs.load_overrides().get("forced_segment_enabled") is True  # persisted
    finally:
        object.__setattr__(config.settings, "forced_segment_enabled", prior)


def test_config_toggle_env_pinned_persists_but_env_wins(app_with_stub, tmp_path, monkeypatch):
    from subarr import config, config_store as cs

    monkeypatch.setenv("SUBARR_CONFIG_STORE", str(tmp_path / "ov.json"))
    monkeypatch.setenv("SUBARR_FORCED_SEGMENT_ENABLED", "0")  # operator pins it
    prior = config.settings.forced_segment_enabled
    try:
        r = app_with_stub.post("/api/forced-segment/config", json={"enabled": True})
        assert r.status_code == 200
        assert r.json()["env_controlled"] is True
        # live value NOT mutated (env authoritative); preference still persisted
        assert config.settings.forced_segment_enabled == prior
        assert cs.load_overrides().get("forced_segment_enabled") is True
    finally:
        object.__setattr__(config.settings, "forced_segment_enabled", prior)


def test_get_vad_available_reflects_vad(app_with_stub, monkeypatch):
    from subarr import vad

    monkeypatch.setattr(vad, "vad_available", lambda: True)
    assert app_with_stub.get("/api/forced-segment").json()["vad_available"] is True
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_forced_segment_router.py -q`
Expected: the 4 new tests FAIL (missing `enabled`/`env_controlled`/`vad_available` keys; `POST /config` 404/405).

- [ ] **Step 3: Implement the endpoint + helper**

Edit `src/subarr/routers/forced_segment.py`. Add imports at the top (after the existing `from fastapi import ...`):

```python
from pydantic import BaseModel

from .. import config, config_store, vad
```

Add the model and helper above the routes:

```python
class ForcedSegmentConfig(BaseModel):
    enabled: bool


def _toggle_status() -> dict:
    """Fields the Settings toggle needs: is it on, is it env-pinned (operator
    authoritative -> UI locks), and is the VAD model present (hard prerequisite;
    without it every scan records vad-unavailable and silently does nothing)."""
    return {
        "enabled": bool(getattr(config.settings, "forced_segment_enabled", False)),
        "env_controlled": config.env_is_set("forced_segment_enabled"),
        "vad_available": vad.vad_available(),
    }
```

Extend the existing `GET ""` handler to merge the toggle fields into its return:

```python
@router.get("")
async def get_scan(request: Request) -> dict:
    walker = getattr(request.app.state, "forced_segment", None)
    store = getattr(request.app.state, "forced_segment_store", None)
    state = walker.get_state() if walker is not None else None
    return {
        **_toggle_status(),
        "state": state.to_dict() if state is not None else None,
        "summary": store.summary() if store is not None else {},
    }
```

Add the config endpoint (place after `get_scan`):

```python
@router.post("/config")
def set_config(body: ForcedSegmentConfig) -> dict:
    """Persist the enable/disable choice (survives restart via #112) and patch the
    running Settings so it takes effect on the next import. If the operator pinned
    SUBARR_FORCED_SEGMENT_ENABLED, env stays authoritative live (we still persist
    the preference, but env wins on reload)."""
    config_store.save_override("forced_segment_enabled", body.enabled)
    if not config.env_is_set("forced_segment_enabled"):
        object.__setattr__(config.settings, "forced_segment_enabled", body.enabled)
    return _toggle_status()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_forced_segment_router.py -q`
Expected: PASS (all, including the 4 existing walker tests).

- [ ] **Step 5: Commit**

```bash
git add src/subarr/routers/forced_segment.py tests/test_forced_segment_router.py
git commit -m "feat(#364): forced-segment enable toggle endpoint + status fields"
```

---

### Task 2: Frontend — pure helper + card + mount + bundle

**Files:**
- Modify: `src/subarr/static/v1/home-hifi/settings.jsx`
- Create: `src/subarr/static/v1/home-hifi/__tests__/forced-segment-toggle.test.js`
- Regenerate: `src/subarr/static/v1/home-hifi/settings.bundle.js` (via build)

- [ ] **Step 1: Write the failing helper test**

Create `src/subarr/static/v1/home-hifi/__tests__/forced-segment-toggle.test.js`:

```js
// #364 — forced-segment Settings toggle: pure render-decision helper.
import { describe, it, expect } from 'vitest';
import { deriveForcedSegmentToggle } from '../settings.jsx';

describe('deriveForcedSegmentToggle', () => {
  it('env-pinned -> disabled with env hint', () => {
    const v = deriveForcedSegmentToggle({ enabled: true, env_controlled: true, vad_available: true });
    expect(v.checked).toBe(true);
    expect(v.disabled).toBe(true);
    expect(v.hint).toMatch(/SUBARR_FORCED_SEGMENT_ENABLED/);
  });

  it('not pinned -> enabled, persist hint', () => {
    const v = deriveForcedSegmentToggle({ enabled: false, env_controlled: false, vad_available: true });
    expect(v.checked).toBe(false);
    expect(v.disabled).toBe(false);
    expect(v.hint).toMatch(/Persists across restarts/);
    expect(v.warning).toBeNull();
  });

  it('enabled but VAD model missing -> warning (warn, not block)', () => {
    const v = deriveForcedSegmentToggle({ enabled: true, env_controlled: false, vad_available: false });
    expect(v.disabled).toBe(false);      // still allowed
    expect(v.warning).toMatch(/speech-detection/i);
  });

  it('off + VAD missing -> no warning yet', () => {
    const v = deriveForcedSegmentToggle({ enabled: false, env_controlled: false, vad_available: false });
    expect(v.warning).toBeNull();
  });

  it('null status -> safe defaults', () => {
    const v = deriveForcedSegmentToggle(null);
    expect(v.checked).toBe(false);
    expect(v.disabled).toBe(false);
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `npx vitest run src/subarr/static/v1/home-hifi/__tests__/forced-segment-toggle.test.js`
Expected: FAIL — `deriveForcedSegmentToggle` is not exported.

- [ ] **Step 3: Add the exported helper**

In `settings.jsx`, just above `function SpeechAudioCard() {` (line ~1430), add:

```jsx
// #364 forced-segment toggle: pure render-decision helper (unit-tested).
export function deriveForcedSegmentToggle(status) {
  const s = status || {};
  const envLocked = !!s.env_controlled;
  return {
    checked: !!s.enabled,
    disabled: envLocked,
    hint: envLocked
      ? 'Locked by SUBARR_FORCED_SEGMENT_ENABLED (env wins)'
      : 'Persists across restarts',
    warning: s.enabled && s.vad_available === false
      ? 'Requires the speech-detection (VAD) model — enable Speech-aware audio above first, or scans will find nothing.'
      : null,
  };
}
```

- [ ] **Step 4: Run the helper test to verify it passes**

Run: `npx vitest run src/subarr/static/v1/home-hifi/__tests__/forced-segment-toggle.test.js`
Expected: PASS (5/5).

- [ ] **Step 5: Add the `ForcedSegmentCard` component**

In `settings.jsx`, immediately after the `SpeechAudioCard` function (after its closing `}` at line ~1531), add:

```jsx
// #364 forced-segment deep-scan enable toggle. Polls /api/forced-segment; the
// switch persists via /api/forced-segment/config (#112 layer) and live-applies
// unless env-pinned. VAD is a hard prerequisite (warn-but-allow).
function ForcedSegmentCard() {
  const [status, setStatus] = useState(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);

  const refetch = async () => {
    try {
      const r = await fetch('/api/forced-segment', { credentials: 'same-origin' });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      setStatus(await r.json());
    } catch (e) { setStatus({ error: e.message }); }
    finally { setLoading(false); }
  };
  useEffect(() => { refetch(); }, []);

  const view = deriveForcedSegmentToggle(status);

  const toggle = async () => {
    if (!status || view.disabled || busy) return;
    setBusy(true);
    try {
      const r = await fetch('/api/forced-segment/config', {
        method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled: !status.enabled }),
      });
      if (r.ok) {
        const next = await r.json();
        setStatus((prev) => ({ ...prev, ...next }));
      }
    } finally { setBusy(false); }
  };

  const muted = { fontSize: 'var(--text-sm)', color: 'var(--fg-2)' };
  if (loading) return <SectionCard label="Foreign-scene deep-scan"><div style={muted}>Checking…</div></SectionCard>;
  if (status?.error) return <SectionCard label="Foreign-scene deep-scan"><div style={muted}>Could not query: {status.error}</div></SectionCard>;

  return (
    <SectionCard label="Foreign-scene deep-scan" action={<button className="btn sm" onClick={refetch}>Refresh</button>}>
      <div style={{ fontSize: 'var(--text-sm)', color: 'var(--fg-2)', lineHeight: 1.5 }}>
        Deep-scans your English media for short foreign-language scenes and writes a scoped
        <code> .forced.en.srt</code> covering just those scenes. GPU-spending and opt-in — off by
        default.
      </div>
      <Row label="Enabled" hint={view.hint}
        control={<Toggle on={view.checked} busy={busy || view.disabled} onToggle={toggle} />} />
      {view.warning && (
        <div style={{ padding: '10px 14px', background: 'rgba(245,158,11,0.06)',
          border: '1px solid rgba(245,158,11,0.20)', borderRadius: 'var(--radius-md)',
          fontSize: 'var(--text-sm)', color: 'var(--fg-1)' }}>
          {view.warning}
        </div>
      )}
    </SectionCard>
  );
}
```

- [ ] **Step 6: Mount the card after `<SpeechAudioCard />`**

At settings.jsx:1759, change:

```jsx
      <SpeechAudioCard />
```
to:
```jsx
      <SpeechAudioCard />
      <ForcedSegmentCard />
```

- [ ] **Step 7: Rebuild the bundle**

Run: `npm run build:frontend`
Then verify no drift: `npm run check:frontend`
Expected: `check:frontend` exits 0 (bundle regenerated and committed together).

- [ ] **Step 8: Run the frontend suite**

Run: `npx vitest run`
Expected: all pass (existing + the 5 new helper tests).

- [ ] **Step 9: Commit**

```bash
git add src/subarr/static/v1/home-hifi/settings.jsx \
        src/subarr/static/v1/home-hifi/__tests__/forced-segment-toggle.test.js \
        src/subarr/static/v1/home-hifi/settings.bundle.js
git commit -m "feat(#364): forced-segment Settings toggle card + helper"
```

---

### Task 3: Full verification

**Files:** none (verification only)

- [ ] **Step 1: Backend suite**

Run: `python -m pytest tests/test_forced_segment_router.py tests/test_config_forced_segment.py -q`
Expected: PASS.

- [ ] **Step 2: Lint**

Run: `ruff check src/subarr/routers/forced_segment.py && ruff format --check src/subarr/routers/forced_segment.py tests/test_forced_segment_router.py`
Expected: clean (ruff format the test file if the hook did not).

- [ ] **Step 3: Frontend suite + bundle drift**

Run: `npx vitest run && npm run check:frontend`
Expected: both green.

- [ ] **Step 4: Regression — OFF by default unchanged**

Confirm no default flipped: `git diff main -- src/subarr/config.py` shows no change to the `forced_segment_enabled` default (`"0"`). The feature ships OFF; only the reachability changed.

---

## Self-Review notes

- **Spec coverage:** endpoint + status fields (Task 1) = spec Backend; card + helper + warn-but-allow + env-lock (Task 2) = spec Frontend; suite/bundle/regression (Task 3) = spec Testing + acceptance 5/6. Acceptance 1–4 are exercised by Task 1 tests (persist/live-apply/env-pin) and Task 2 helper tests (env-lock/warn).
- **Type consistency:** `_toggle_status()` returns `{enabled, env_controlled, vad_available}`; GET merges it with `{state, summary}`; POST returns it verbatim. Frontend `deriveForcedSegmentToggle` consumes exactly those three fields; `{checked, disabled, hint, warning}` is used identically in the test and the card.
- **No placeholders:** every step has concrete code/commands.
- **Out of scope (unchanged):** detector, gate, output filename, slice-3 generalization, "Scan now" button.
