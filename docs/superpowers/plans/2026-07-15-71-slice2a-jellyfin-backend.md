# #71 Slice 2a — Jellyfin backend + config + fan-out wiring

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use `- [ ]` checkboxes.

**Goal:** Add a `JellyfinClient` implementing the `MediaServer` protocol (the flow validated live against Jellyfin 10.11.11), wire `JELLYFIN_*` config, and include it in `IntegrationBundle.media_servers` when configured — so a user who sets `JELLYFIN_*` env gets real refreshes on Jellyfin, fanned out beside Plex. **No UI yet** (Slice 2b). Emby deferred.

**Architecture:** Mirror `PlexClient` (persistent httpx client + `CircuitBreaker` + `_request`). Auth = `X-Emby-Token` header. Targeted refresh = a cached `Path→itemId` index (one `/Items?fields=Path` query, lazily built, rebuilt on miss) → `POST /Items/{id}/Refresh`; full refresh = `POST /Library/Refresh`.

**Tech stack:** Python 3.11, httpx.AsyncClient, pytest (async, `@pytest.mark.asyncio` per test).

---

## Reference facts (validated live + from `plex.py`)

- Auth header `X-Emby-Token: <api_key>` works. `GET /Library/VirtualFolders`, `GET /Items?recursive=true&includeItemTypes=Episode,Movie&fields=Path` (returns `Items[].Path` + `Id`), `POST /Items/{id}/Refresh?metadataRefreshMode=Default`, `POST /Library/Refresh`, `GET /System/Info/Public` (version, no auth needed). Core loop proven: write sidecar → item refresh → external sub detected.
- `PlexClient` (`integrations/plex.py`) imports `_DEFAULT_TIMEOUT`, `CircuitBreaker`, `IntegrationError`, `_is_client_closed` and has `name`/`type` class attrs — **mirror its import block and `__init__`/`_request` shape**.
- Config `PLEX_*` pattern (`config.py`): field decl (`plex_url: str` ~line 80), env load (`plex_url=_env_or("PLEX_URL", …)` ~line 277), `FIELD_ENV_VARS` entry (~line 502), coerce map (`_coerce`/str). Mirror exactly for `jellyfin_*`.
- `IntegrationBundle` (`coverage_engine.py` ~line 281) builds `self.plex = PlexClient(...)`, closes it in `aclose()`, and `media_servers` returns `[self.plex]` (Slice 1).

---

### Task 1: `JellyfinClient` backend

**Files:** Create `src/subarr/integrations/jellyfin.py`; Test `tests/test_jellyfin_client.py`.

- [ ] **Step 1 — write the failing tests** (`tests/test_jellyfin_client.py`):

```python
import pytest

from subarr.integrations.jellyfin import JellyfinClient
from subarr.integrations.media_server import MediaServer


def _c():
    return JellyfinClient(base_url="http://jf:8096", api_key="k", path_prefix="/media", media_root="/media/library")


class _Resp:
    def __init__(self, data): self._data = data
    def json(self): return self._data


def test_conforms_to_protocol_and_type():
    c = _c()
    assert isinstance(c, MediaServer)
    assert c.type == "jellyfin"


def test_is_configured():
    assert _c().is_configured() is True
    assert JellyfinClient(base_url="", api_key="", path_prefix="", media_root="").is_configured() is False


def test_translate_path_applies_prefix():
    # subarr /media/library/TV/x → jellyfin /media/TV/x
    assert _c().translate_path("/media/library/TV/x.mkv") == "/media/TV/x.mkv"


@pytest.mark.asyncio
async def test_refresh_for_file_finds_item_and_refreshes(monkeypatch):
    c = _c()
    calls = []

    async def fake_request(method, path, params=None):
        calls.append((method, path, params))
        if path == "/Items":
            return _Resp({"Items": [{"Path": "/media/TV/x.mkv", "Id": "abc"}]})
        return _Resp({})

    monkeypatch.setattr(c, "_request", fake_request)
    out = await c.refresh_for_file("/media/library/TV/x.mkv")
    assert out["triggered"] is True and out["item_id"] == "abc"
    assert ("POST", "/Items/abc/Refresh", {"metadataRefreshMode": "Default"}) in calls


@pytest.mark.asyncio
async def test_refresh_for_file_no_match_is_noop(monkeypatch):
    c = _c()

    async def fake_request(method, path, params=None):
        return _Resp({"Items": []})  # index empty on build + rebuild

    monkeypatch.setattr(c, "_request", fake_request)
    out = await c.refresh_for_file("/media/library/TV/missing.mkv")
    assert out["triggered"] is False and out["reason"] == "no_item_match"


@pytest.mark.asyncio
async def test_full_refresh_posts_library_refresh(monkeypatch):
    c = _c()
    calls = []

    async def fake_request(method, path, params=None):
        calls.append((method, path))
        return _Resp({})

    monkeypatch.setattr(c, "_request", fake_request)
    out = await c.full_refresh()
    assert out["scope"] == "full" and ("POST", "/Library/Refresh") in calls


@pytest.mark.asyncio
async def test_status_reads_system_info(monkeypatch):
    c = _c()

    async def fake_request(method, path, params=None):
        return _Resp({"Version": "10.11.11", "ServerName": "JF"})

    monkeypatch.setattr(c, "_request", fake_request)
    assert (await c.status())["version"] == "10.11.11"
```

- [ ] **Step 2 — run, confirm fail** (`ImportError`): `python -m pytest tests/test_jellyfin_client.py -q`

- [ ] **Step 3 — implement** `src/subarr/integrations/jellyfin.py`. Mirror `plex.py`'s import block for `_DEFAULT_TIMEOUT`, `CircuitBreaker`, `IntegrationError`, `_is_client_closed` (import them from the same places `plex.py` does):

```python
"""#71/#72 — Jellyfin backend for the MediaServer abstraction.

subarr writes subtitle sidecars to disk; this asks Jellyfin to pick them up.
Jellyfin has no path-based refresh (unlike Plex) and works by item UUID, and
`/Items` has no server-side path filter — so we cache a Path→itemId index
(one `/Items?fields=Path` query, lazily built + rebuilt on miss) and
`POST /Items/{id}/Refresh`. Auth is the `X-Emby-Token` header. Validated live
against Jellyfin 10.11.11."""

from __future__ import annotations

import logging

import httpx

from ..circuit_breaker import CircuitBreaker
from . import IntegrationError
from .base import _DEFAULT_TIMEOUT, _is_client_closed

log = logging.getLogger(__name__)


class JellyfinClient:
    name = "jellyfin"
    type = "jellyfin"

    def __init__(self, base_url: str, api_key: str, path_prefix: str = "", media_root: str = "", breaker=None):
        self._base_url = (base_url or "").rstrip("/")
        self._api_key = api_key or ""
        self._path_prefix = (path_prefix or "").rstrip("/")
        self._media_root = (media_root or "").rstrip("/")
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            headers={"X-Emby-Token": self._api_key} if self._api_key else {},
            timeout=_DEFAULT_TIMEOUT,
        )
        self._breaker = breaker or CircuitBreaker(name=self.name)
        self._path_index: dict[str, str] | None = None  # jellyfin Path -> itemId

    def is_configured(self) -> bool:
        return bool(self._base_url and self._api_key)

    def translate_path(self, subarr_path: str) -> str:
        if not self._path_prefix or not self._media_root:
            return subarr_path
        if subarr_path.startswith(self._media_root):
            return self._path_prefix + subarr_path[len(self._media_root):]
        return subarr_path

    async def _request(self, method: str, path: str, params: dict | None = None) -> httpx.Response:
        """GET/POST via the persistent client with breaker guard. Mirrors
        PlexClient._request policy (5xx/transport → failure; 4xx reachable but
        raises; client-closed race degrades cleanly)."""
        if not self._breaker.allow():
            raise IntegrationError(f"{self.name}: circuit open — Jellyfin failing, backing off")
        try:
            r = await self._client.request(method, path, params=params)
        except httpx.HTTPError as e:
            self._breaker.record_failure()
            raise IntegrationError(f"{self.name} {path}: {e}") from e
        except RuntimeError as e:
            if not _is_client_closed(e):
                raise
            raise IntegrationError(f"{self.name} {path}: client closed mid-request") from e
        if r.status_code >= 500:
            self._breaker.record_failure()
        else:
            self._breaker.record_success()
        if r.status_code >= 400:
            raise IntegrationError(f"{self.name} {path}: HTTP {r.status_code}: {r.text[:200]}")
        return r

    async def _build_path_index(self) -> dict[str, str]:
        r = await self._request(
            "GET", "/Items",
            params={"recursive": "true", "includeItemTypes": "Episode,Movie",
                    "fields": "Path", "enableTotalRecordCount": "false"},
        )
        items = r.json().get("Items", [])
        return {it["Path"]: it["Id"] for it in items if it.get("Path") and it.get("Id")}

    async def _find_item_id(self, jf_path: str) -> str | None:
        if self._path_index is None:
            self._path_index = await self._build_path_index()
        item_id = self._path_index.get(jf_path)
        if item_id is None:
            self._path_index = await self._build_path_index()  # maybe newly added
            item_id = self._path_index.get(jf_path)
        return item_id

    async def refresh_for_file(self, subarr_file: str) -> dict:
        jf_path = self.translate_path(subarr_file)
        item_id = await self._find_item_id(jf_path)
        if item_id is None:
            log.warning("jellyfin: no item matched %s (path-prefix mismatch or not indexed)", jf_path)
            return {"triggered": False, "reason": "no_item_match", "path": jf_path}
        await self._request("POST", f"/Items/{item_id}/Refresh", params={"metadataRefreshMode": "Default"})
        log.info("jellyfin: refreshed item %s for %s", item_id, jf_path)
        return {"triggered": True, "scope": "item", "item_id": item_id, "path": jf_path}

    async def full_refresh(self) -> dict:
        await self._request("POST", "/Library/Refresh")
        return {"triggered": True, "scope": "full"}

    async def status(self) -> dict:
        r = await self._request("GET", "/System/Info/Public")
        d = r.json()
        return {"version": d.get("Version"), "server_name": d.get("ServerName")}

    async def libraries(self) -> list[dict]:
        r = await self._request("GET", "/Library/VirtualFolders")
        return [{"name": v.get("Name"), "paths": v.get("Locations", [])} for v in r.json()]

    async def audio_lang_hints(self, titles) -> dict:
        # Deferred: Jellyfin audio-track hints land in a later slice. Returning
        # {} keeps the protocol satisfied without adding N-query cost now.
        return {}

    async def aclose(self) -> None:
        await self._client.aclose()
```

**Note:** the import block above is verified against `plex.py:48-50` (correct as written). `IntegrationError` comes from `integrations/__init__.py` (`from . import IntegrationError`); `_DEFAULT_TIMEOUT`/`_is_client_closed` from `.base`; `CircuitBreaker` from `..circuit_breaker`.

- [ ] **Step 4 — run, confirm pass** (all tests green): `python -m pytest tests/test_jellyfin_client.py -q`

- [ ] **Step 5 — lint + commit:**
```
ruff check src/subarr/integrations/jellyfin.py && ruff format src/subarr/integrations/jellyfin.py tests/test_jellyfin_client.py
git add src/subarr/integrations/jellyfin.py tests/test_jellyfin_client.py
git commit -m "feat(#71): JellyfinClient backend (validated flow, cached path index)"
```

---

### Task 2: `JELLYFIN_*` config

**Files:** Modify `src/subarr/config.py`; Test `tests/test_jellyfin_config.py`.

- [ ] **Step 1 — failing test:**

```python
def test_jellyfin_config_loads_from_env(monkeypatch):
    monkeypatch.setenv("JELLYFIN_URL", "http://jf:8096")
    monkeypatch.setenv("JELLYFIN_API_KEY", "secret")
    monkeypatch.setenv("JELLYFIN_PATH_PREFIX", "/media")
    from subarr import config
    import importlib; importlib.reload(config)
    s = config.load()
    assert s.jellyfin_url == "http://jf:8096"
    assert s.jellyfin_api_key == "secret"
    assert s.jellyfin_path_prefix == "/media"


def test_jellyfin_defaults_empty(monkeypatch):
    for v in ("JELLYFIN_URL", "JELLYFIN_API_KEY", "JELLYFIN_PATH_PREFIX"):
        monkeypatch.delenv(v, raising=False)
    from subarr import config
    import importlib; importlib.reload(config)
    s = config.load()
    assert s.jellyfin_url == "" and s.jellyfin_api_key == "" and s.jellyfin_path_prefix == ""
```

- [ ] **Step 2 — run, confirm fail.**
- [ ] **Step 3 — implement** in `config.py`, mirroring the `plex_url`/`plex_token`/`plex_path_prefix` lines exactly: add `jellyfin_url: str`, `jellyfin_api_key: str`, `jellyfin_path_prefix: str` fields; load `jellyfin_url=os.environ.get("JELLYFIN_URL", "")`, `jellyfin_api_key=os.environ.get("JELLYFIN_API_KEY", "")`, `jellyfin_path_prefix=os.environ.get("JELLYFIN_PATH_PREFIX", "")`; add `FIELD_ENV_VARS` entries (`"jellyfin_url": "JELLYFIN_URL"`, etc.); add coerce entries (`str`) so they're config-store-overridable (needed by Slice 2b's credential editor).
- [ ] **Step 4 — run, confirm pass.**
- [ ] **Step 5 — commit:** `git commit -m "feat(#71): JELLYFIN_* config fields (url, api_key, path_prefix)"`

---

### Task 3: wire Jellyfin into `IntegrationBundle`

**Files:** Modify `src/subarr/coverage_engine.py`; Test `tests/test_media_server_bundle.py` (extend).

- [ ] **Step 1 — failing test** (add to the existing bundle test file):

```python
def test_media_servers_includes_jellyfin_when_configured(monkeypatch):
    monkeypatch.setenv("JELLYFIN_URL", "http://jf:8096")
    monkeypatch.setenv("JELLYFIN_API_KEY", "k")
    # reload config + build a bundle (match the fixture idiom the file already uses)
    ...
    b = IntegrationBundle()
    types = [s.type for s in b.media_servers]
    assert "plex" in types and "jellyfin" in types
    configured = [s.type for s in b.media_servers if s.is_configured()]
    assert "jellyfin" in configured
```

- [ ] **Step 2 — run, confirm fail.**
- [ ] **Step 3 — implement** in `IntegrationBundle.__init__` (next to `self.plex = PlexClient(...)`): build `self.jellyfin = JellyfinClient(base_url=settings.jellyfin_url, api_key=settings.jellyfin_api_key, path_prefix=settings.jellyfin_path_prefix, media_root=str(settings.media_root))`. Update `media_servers` to `return [self.plex, self.jellyfin]`. Add `self.jellyfin.aclose()` to `aclose()`.
- [ ] **Step 4 — run, confirm pass.** The Slice-1 `test_bundle_media_servers_lists_plex` will now see 2 servers — **update that assertion** to `len(...) == 2` and keep `servers[0] is b.plex`.
- [ ] **Step 5 — commit:** `git commit -m "feat(#71): build Jellyfin client into IntegrationBundle.media_servers"`

---

### Task 4: verification + live smoke + PR

- [ ] **Step 1 — targeted suites:** `python -m pytest tests/test_jellyfin_client.py tests/test_jellyfin_config.py tests/test_media_server_bundle.py tests/test_media_server.py tests/test_plex_partial_scan.py -q` — all green.
- [ ] **Step 2 — lint:** ruff check + format-check the new/changed files.
- [ ] **Step 3 — LIVE SMOKE against the test Jellyfin** (the whole point — prove the real loop through the client, not just fakes). Inside `subarr-next` (has httpx + the media mount), with the test Jellyfin URL + api key set, drive the real `JellyfinClient`: build it → `refresh_for_file` on a real episode path (after writing a `.en.srt` sidecar via subarr-next) → confirm Jellyfin lists the external sub (the exact loop validated by hand). Clean up the sidecar. Document the result in the PR.
- [ ] **Step 4 — regression:** push the branch; CI runs the full suite (fan-out now includes an unconfigured Jellyfin by default → no behavior change for Plex-only installs; confirm).
- [ ] **Step 5 — open the PR** (base main): title `feat(#71): Jellyfin backend + config + fan-out wiring (Slice 2a)`; body covers the client, config, bundle wiring, the live-smoke result, and that it's env-configurable (UI in Slice 2b). Zero behavior change for existing Plex-only installs (Jellyfin unconfigured by default).

---

## Self-Review notes

- **Spec coverage:** delivers Slice 2's backend half — `JellyfinServer` (validated flow), `JELLYFIN_*` config with back-compat (Plex untouched), and inclusion in the fan-out list. Surfacing (registry/onboarding/settings/health) is Slice 2b.
- **Behavior preservation:** an unconfigured Jellyfin is in `media_servers` but filtered out by the fan-out's `is_configured()` check → Plex-only installs are unaffected.
- **Design choice (noted):** cached `Path→itemId` index over the research's `searchTerm` heuristic — exact-path match, no per-show-name guessing, O(1) after first build; rebuilt once on miss (new files). `audio_lang_hints` returns `{}` for now (deferred, protocol satisfied).
- **Import caveat flagged:** the implementer must copy plex.py's exact import paths for the shared helpers.
