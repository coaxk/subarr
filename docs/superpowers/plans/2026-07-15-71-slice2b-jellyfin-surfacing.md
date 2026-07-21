# #71 Slice 2b — Jellyfin surfacing (Settings card + test + health)

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use `- [ ]` checkboxes.

**Goal:** Make the Jellyfin backend (Slice 2a) UI-configurable, mirroring exactly how Plex surfaces: a Settings integration card with an inline credential editor + Test Connection, and a health dot. **No onboarding wizard step** — Plex has none (it's Settings-only), so Jellyfin matches.

**Architecture:** Register `jellyfin` in the existing integration framework. Backend: `_CREDENTIAL_FIELDS["jellyfin"]` (drives the generic config/credentials/test endpoints) + a `_test_jellyfin` probe in `onboarding.py`'s shared handler map (reused by both the wizard test-endpoint and the Settings editor) + an always-on health probe. Frontend: add `jellyfin` to `CREDENTIAL_SCHEMA` + `INTEGRATION_ORDER`.

**Tech stack:** FastAPI, pytest (async); vanilla JSX + esbuild, vitest.

---

## Reference facts (verified)

- `routers/integrations.py`:
  - `_CREDENTIAL_FIELDS` (line 39): `{"plex": {"url": ("plex_url", str), "token": ("plex_token", str)}, ...}`. `_SECRET_KEYS = {"api_key", "token"}` masks secrets.
  - `POST /integrations/{name}/test` (line 175) **reuses** `onboarding.test_connection` — so adding to `_CREDENTIAL_FIELDS` + onboarding's handler map wires BOTH the wizard and the Settings editor.
  - Health probe list (~line 285): `_probe("plex", integrations.plex)`. `_probe` (line 208) returns a not-configured result early when `not client.is_configured()` — safe to always include.
- `routers/onboarding.py`: `_test_plex` (line 284) probes `PlexClient.status()`; the `_HANDLERS` map (~line 157) has `"plex": _test_plex`.
- `settings.jsx`: `CREDENTIAL_SCHEMA` (line 156) — `plex: [{key:'url',...},{key:'token',label:'Plex token',secret:true}]`; `INTEGRATION_ORDER` (line 411) — `['bazarr','sonarr','radarr','plex','tautulli','subgen','ollama']`; the rail (`buildRailItems`, ~line 439) iterates `INTEGRATION_ORDER` against `health.integrations`.
- `IntegrationBundle` has `self.jellyfin` (Slice 2a); `JellyfinClient.status()` returns `{version, server_name}`; config fields `jellyfin_url`/`jellyfin_api_key`/`jellyfin_path_prefix` exist.

---

### Task 1: backend registry (credentials + test + health)

**Files:** Modify `src/subarr/routers/integrations.py`, `src/subarr/routers/onboarding.py`; Test `tests/test_jellyfin_surfacing.py`.

- [ ] **Step 1 — failing tests** (`tests/test_jellyfin_surfacing.py`), using the app fixture the other integration-router tests use (grep `tests/` for `app_with_stub` + `/integrations/`):

```python
import pytest

pytestmark = pytest.mark.asyncio  # ONLY if the file is all-async; otherwise mark each async test


async def test_jellyfin_test_endpoint_reports_unreachable_cleanly(app_with_stub):
    # unknown/unreachable host → 200 with ok:false (never a 500)
    r = app_with_stub.post("/api/integrations/jellyfin/test",
                           json={"url": "http://127.0.0.1:59999", "api_key": "x"})
    assert r.status_code == 200
    assert r.json()["ok"] is False


async def test_jellyfin_test_endpoint_requires_creds(app_with_stub):
    r = app_with_stub.post("/api/integrations/jellyfin/test", json={"url": "", "api_key": ""})
    assert r.status_code == 200 and r.json()["ok"] is False


async def test_jellyfin_config_endpoint_known(app_with_stub):
    # jellyfin is now a known integration (in _CREDENTIAL_FIELDS) → 200, not 404
    r = app_with_stub.get("/api/integrations/jellyfin/config")
    assert r.status_code == 200
    body = r.json()
    assert "url" in body and "api_key" in body  # api_key masked


async def test_health_includes_jellyfin(app_with_stub):
    r = app_with_stub.get("/api/integrations/health")
    names = {i["name"] for i in r.json().get("integrations", [])}
    assert "jellyfin" in names
```

(Match the sync/async idiom of the existing integrations-router tests; if they use a sync `TestClient`, drop the async marks and the `await`.)

- [ ] **Step 2 — run, confirm fail.**

- [ ] **Step 3a — `integrations.py`:** add to `_CREDENTIAL_FIELDS` (after the `plex` entry):
```python
    "jellyfin": {"url": ("jellyfin_url", str), "api_key": ("jellyfin_api_key", str)},
```
And add to the health probe list (after `_probe("plex", integrations.plex)`):
```python
        _probe("jellyfin", integrations.jellyfin),
```

- [ ] **Step 3b — `onboarding.py`:** add a `_test_jellyfin` handler mirroring `_test_plex` (place next to it), and register it in the `_HANDLERS` map (`"jellyfin": _test_jellyfin`):
```python
async def _test_jellyfin(body: TestRequest) -> dict[str, Any]:
    """Probe Jellyfin's /System/Info/Public via JellyfinClient.status(). The
    API key rides in `api_key` (generic field). aclose() the throwaway client
    even on failure so the test path never leaks a connection pool."""
    from ..integrations.jellyfin import JellyfinClient

    key = body.api_key or body.token or ""
    c = JellyfinClient(base_url=body.url, api_key=key)
    if not c.is_configured():
        return {"ok": False, "version": None, "detail": None, "error": "Jellyfin URL and API key are both required"}
    try:
        status = await c.status()
    finally:
        await c.aclose()
    version = status.get("version")
    return {"ok": True, "version": version, "detail": f"Jellyfin {version or 'connected'}", "error": None}
```

- [ ] **Step 4 — run, confirm pass.** (`test_connection` already returns 200-with-error on a raise, so the unreachable test passes.)

- [ ] **Step 5 — lint + commit:**
```
ruff check src/subarr/routers/integrations.py src/subarr/routers/onboarding.py
git add src/subarr/routers/integrations.py src/subarr/routers/onboarding.py tests/test_jellyfin_surfacing.py
git commit -m "feat(#71): register jellyfin in integration registry (creds, test, health)"
```

---

### Task 2: frontend Settings card

**Files:** Modify `src/subarr/static/v1/home-hifi/settings.jsx`; regenerate `settings.bundle.js`.

- [ ] **Step 1 — implement** (small, no new pure helper needed — this is data wiring the existing rail/editor consume):
  - `CREDENTIAL_SCHEMA` (line ~156): add
    ```jsx
    jellyfin: [{ key: 'url', label: 'URL', secret: false }, { key: 'api_key', label: 'API key', secret: true }],
    ```
  - `INTEGRATION_ORDER` (line ~411): add `'jellyfin'` right after `'plex'`:
    ```jsx
    const INTEGRATION_ORDER = ['bazarr', 'sonarr', 'radarr', 'plex', 'jellyfin', 'tautulli', 'subgen', 'ollama'];
    ```
- [ ] **Step 2 — rebuild the bundle:** `npm run build:frontend` then `npm run check:frontend` (must exit 0).
- [ ] **Step 3 — frontend suite:** `npx vitest run` (existing tests stay green; the rail builder now includes jellyfin from the health payload — no logic change, just an extra known name).
- [ ] **Step 4 — commit:**
```
git add src/subarr/static/v1/home-hifi/settings.jsx src/subarr/static/v1/home-hifi/settings.bundle.js
git commit -m "feat(#71): jellyfin Settings integration card (schema + rail order)"
```

---

### Task 3: verification + live smoke + PR

- [ ] **Step 1 — targeted suites:** `python -m pytest tests/test_jellyfin_surfacing.py $(grep -rl "integrations/health\|integrations/.*test" tests/ | tr '\n' ' ') -q` — green.
- [ ] **Step 2 — lint + bundle drift:** ruff + `npm run check:frontend`.
- [ ] **Step 3 — LIVE SMOKE:** hit the real Settings test endpoint against the test Jellyfin. From the host: `POST /api/integrations/jellyfin/test` on the running dev instance (or drive `_test_jellyfin` directly) with the test Jellyfin url + api key → expect `{ok:true, version:"10.11.11"}`. Confirms the Settings "Test Connection" button will work end-to-end. (Do NOT paste the key into the report; confirmation-only.)
- [ ] **Step 4 — regression:** push; CI runs the full suite + frontend + bundle-drift. Zero behavior change for Plex-only installs (jellyfin card shows as unconfigured until creds are entered; health probe returns not-configured without network I/O).
- [ ] **Step 5 — PR** (base main): `feat(#71): Jellyfin Settings surfacing (Slice 2b)`. Body: registry entry + `_test_jellyfin` + health dot + Settings card; live test-endpoint smoke result; notes that Jellyfin is now fully UI-configurable (creds persist via the existing config_store credential path, decoupled/best-effort per v2.4.2). This completes the Jellyfin epic for #71/#72 (Emby still deferred).

---

## Self-Review notes

- **Spec coverage:** delivers the surfacing half of Slice 2 — Settings card + Test + health — mirroring Plex exactly. No wizard step (Plex has none). Registry reuse means the generic config/credentials endpoints work for jellyfin with just the `_CREDENTIAL_FIELDS` entry.
- **Behavior preservation:** an unconfigured jellyfin health probe returns early (no network); the rail shows a configurable-but-offline card; Plex-only installs are unaffected.
- **Consistency:** `api_key` is in `_SECRET_KEYS` → masked in GET config. `_test_jellyfin` acloses its throwaway client (improves on `_test_plex`, which leaks — noted, not changed here).
- **Type consistency:** `_CREDENTIAL_FIELDS["jellyfin"]` field keys (`url`, `api_key`) match `CREDENTIAL_SCHEMA.jellyfin` keys and the `JellyfinClient(base_url, api_key)` ctor.
