# #71 Slice 1 — MediaServer abstraction + Plex behind it (zero behavior change)

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development or superpowers:executing-plans. Steps use `- [ ]` checkboxes.

**Goal:** Introduce a `MediaServer` protocol, make the existing Plex client conform, and route the core sidecar-refresh hook (`completion_watcher`) through a fan-out over a `media_servers` list — with **zero user-visible behavior change** (single Plex behaves exactly as today). This lays the seam Slice 2 plugs Jellyfin into.

**Architecture:** Protocol + delegation (no logic moves). `PlexClient` gains `type` + protocol-named `refresh_for_file`/`full_refresh` that delegate to its existing `partial_scan`/`full_scan`. `IntegrationBundle` gains a `media_servers` list (`[plex]`). A best-effort `refresh_file_on_all` fan-out helper replaces the direct `partial_scan` call in `completion_watcher`.

**Scope boundary:** admin scan/sections endpoints, health probes, and coverage audio-hints stay on `bundle.plex` for this slice (single-Plex, unchanged). They generalize in Slice 2 alongside the Jellyfin UI — generalizing them now, with no second server to show, is churn with no behavior change.

**Tech stack:** Python 3.11, `typing.Protocol` (`runtime_checkable`), pytest (async).

---

## Reference facts (verified)

- `PlexClient` (`integrations/plex.py`) already has: `is_configured()`, `translate_path()`, `partial_scan(subarr_file)->dict`, `full_scan(section_id=None)->dict`, `status()->dict`, `audio_lang_hints(titles)->dict`, `sections()`, `aclose()`. `partial_scan`/`full_scan` raise `IntegrationError` when unconfigured; return dicts with `triggered`/`scope` (+ plex-specific `section`/`plex_path`).
- `IntegrationBundle` (`coverage_engine.py:~230`) builds `self.plex = PlexClient(base_url=…, token=…, default_section=…, path_prefix=…, media_root=…)` and closes it in `aclose()`.
- `completion_watcher` reaches Plex via `self._plex` property (`completion_watcher.py:120` = `self._bundle_provider().plex if self._bundle_provider else self._plex_direct`); the refresh method (`~400`) checks `_settings.plex_partial_scan_enabled` + `self._plex.is_configured()`, resolves `canonical_to_fs`, then `await self._plex.partial_scan(subarr_full)`.

---

### Task 1: `MediaServer` protocol + `PlexClient` conformance

**Files:** Create `src/subarr/integrations/media_server.py`; Modify `src/subarr/integrations/plex.py`; Test `tests/test_media_server.py`.

- [ ] **Step 1 — write the failing test** (`tests/test_media_server.py`):

```python
from subarr.integrations.media_server import MediaServer
from subarr.integrations.plex import PlexClient


def test_plex_client_satisfies_media_server_protocol():
    c = PlexClient(base_url="http://plex:32400", token="t", default_section="all", path_prefix="", media_root="/media/library")
    assert isinstance(c, MediaServer)  # runtime_checkable structural check
    assert c.type == "plex"


async def test_refresh_for_file_delegates_to_partial_scan(monkeypatch):
    c = PlexClient(base_url="http://plex:32400", token="t", default_section="all", path_prefix="", media_root="/media/library")
    calls = {}

    async def fake_partial(p):
        calls["path"] = p
        return {"triggered": True, "scope": "partial"}

    monkeypatch.setattr(c, "partial_scan", fake_partial)
    out = await c.refresh_for_file("/media/library/TV/x.mkv")
    assert calls["path"] == "/media/library/TV/x.mkv" and out["triggered"] is True


async def test_full_refresh_delegates_to_full_scan(monkeypatch):
    c = PlexClient(base_url="http://plex:32400", token="t", default_section="all", path_prefix="", media_root="/media/library")

    async def fake_full():
        return {"triggered": True, "scope": "full"}

    monkeypatch.setattr(c, "full_scan", fake_full)
    out = await c.full_refresh()
    assert out["scope"] == "full"
```

(Add `pytestmark = pytest.mark.asyncio` per the repo's strict-asyncio convention; see an existing async test file.)

- [ ] **Step 2 — run, confirm fail:** `python -m pytest tests/test_media_server.py -q` (ImportError: no `media_server` module).

- [ ] **Step 3 — implement the protocol** (`src/subarr/integrations/media_server.py`):

```python
"""#71 — the media-server abstraction. Plex, Jellyfin (slice 2), and Emby
(deferred) implement this. subarr writes subtitle sidecars to disk, then asks
every configured media server to pick them up; the protocol is that contract."""

from __future__ import annotations

import logging
from typing import Protocol, runtime_checkable

log = logging.getLogger(__name__)


@runtime_checkable
class MediaServer(Protocol):
    """Structural interface for a media server subarr can drive. `type` is a
    stable discriminator ("plex" | "jellyfin" | "emby")."""

    type: str

    def is_configured(self) -> bool: ...
    def translate_path(self, subarr_path: str) -> str: ...
    async def refresh_for_file(self, subarr_file: str) -> dict: ...
    async def full_refresh(self) -> dict: ...
    async def status(self) -> dict: ...
    async def audio_lang_hints(self, titles) -> dict: ...
    async def aclose(self) -> None: ...


async def refresh_file_on_all(servers, subarr_file: str) -> list[dict]:
    """Fan a per-file refresh out to every CONFIGURED server, best-effort: one
    server erroring or being unconfigured never blocks the others. Returns the
    per-server result dicts (skipped servers omitted)."""
    results: list[dict] = []
    for srv in servers:
        try:
            if not srv.is_configured():
                continue
            results.append(await srv.refresh_for_file(subarr_file))
        except Exception as e:  # noqa: BLE001 — a failing server must not abort the loop
            log.warning("media-server refresh failed on %s: %s", getattr(srv, "type", "?"), e)
    return results
```

- [ ] **Step 4 — make PlexClient conform** (`src/subarr/integrations/plex.py`): add a class attribute `type = "plex"` on `PlexClient`, and two delegating methods (place next to `partial_scan`/`full_scan`):

```python
    type = "plex"

    async def refresh_for_file(self, subarr_file: str) -> dict:
        """MediaServer protocol name for the per-file targeted refresh."""
        return await self.partial_scan(subarr_file)

    async def full_refresh(self) -> dict:
        """MediaServer protocol name for the full library refresh."""
        return await self.full_scan()
```

- [ ] **Step 5 — run, confirm pass:** `python -m pytest tests/test_media_server.py -q` (3 pass).

- [ ] **Step 6 — commit:**
```
git add src/subarr/integrations/media_server.py src/subarr/integrations/plex.py tests/test_media_server.py
git commit -m "feat(#71): MediaServer protocol + PlexClient conformance + fan-out helper"
```

---

### Task 2: `IntegrationBundle.media_servers`

**Files:** Modify `src/subarr/coverage_engine.py`; Test `tests/test_media_server_bundle.py`.

- [ ] **Step 1 — failing test** (`tests/test_media_server_bundle.py`):

```python
from subarr.coverage_engine import IntegrationBundle
from subarr.integrations.media_server import MediaServer


def test_bundle_media_servers_lists_plex(subarr_env):
    b = IntegrationBundle()
    servers = b.media_servers
    assert isinstance(servers, list) and len(servers) == 1
    assert servers[0] is b.plex
    assert isinstance(servers[0], MediaServer)
```

(Use whatever fixture existing bundle tests use to construct `IntegrationBundle` under a seeded env — grep `tests/` for `IntegrationBundle()` to match the idiom.)

- [ ] **Step 2 — run, confirm fail** (`AttributeError: media_servers`).

- [ ] **Step 3 — implement** — add to `IntegrationBundle` (next to the `bazarr` alias property):

```python
    @property
    def media_servers(self) -> list:
        """All constructed media-server clients (currently just Plex). Callers
        fan out over this and filter on is_configured(). Slice 2 appends
        Jellyfin when JELLYFIN_* is set."""
        return [self.plex]
```

- [ ] **Step 4 — run, confirm pass.**

- [ ] **Step 5 — commit:**
```
git add src/subarr/coverage_engine.py tests/test_media_server_bundle.py
git commit -m "feat(#71): IntegrationBundle.media_servers list (currently [plex])"
```

---

### Task 3: route `completion_watcher` refresh through the fan-out

**Files:** Modify `src/subarr/completion_watcher.py`; Test the existing completion/plex test file.

- [ ] **Step 1 — write the failing test.** Find the existing test that exercises the partial-scan hook: `grep -rl "partial_scan\|_maybe_plex" tests/`. Add a test asserting the hook now fans out over `media_servers` and isolates a failing server. Sketch (adapt to the file's fixtures):

```python
async def test_completion_refresh_fans_out_and_isolates_failure(monkeypatch, ...):
    # two fake servers: one raises, one records — the raise must not stop the other
    class Srv:
        type = "x"
        def __init__(self, boom): self.boom = boom; self.got = None
        def is_configured(self): return True
        async def refresh_for_file(self, p):
            if self.boom: raise RuntimeError("down")
            self.got = p; return {"triggered": True}
    bad, good = Srv(True), Srv(False)
    # wire a watcher whose bundle.media_servers == [bad, good]; call the refresh hook
    # assert good.got == <resolved fs path> despite bad raising
```

- [ ] **Step 2 — run, confirm fail.**

- [ ] **Step 3 — implement.** In `completion_watcher.py`:
  - Add a `_media_servers` property mirroring `_plex` so test injection still works:
    ```python
        @property
        def _media_servers(self):
            if self._bundle_provider:
                return self._bundle_provider().media_servers
            return [self._plex_direct] if self._plex_direct else []
    ```
  - In the refresh method (`~400`), replace the single `partial_scan` call with the fan-out. Keep the existing gate (`_settings.plex_partial_scan_enabled`), the `canonical_to_fs` resolve, and the per-file skip. Replace the `if self._plex is None / is_configured()` guard with a `media_servers`-based one and call the helper:
    ```python
    from .integrations.media_server import refresh_file_on_all
    servers = [s for s in self._media_servers if s and s.is_configured()]
    if not servers:
        return
    # ... existing plex_partial_scan_enabled gate + canonical_to_fs resolve ...
    results = await refresh_file_on_all(servers, subarr_full)
    for r in results:
        log.info("media-server refresh fired: %s (ledger entry: %s)", r, video_canonical)
    ```
  Preserve the `plex_partial_scan_enabled` gate name for now (Slice 2 generalizes it). Do not change what happens when unconfigured/disabled.

- [ ] **Step 4 — run the affected test file + confirm pass.**

- [ ] **Step 5 — commit:**
```
git add src/subarr/completion_watcher.py tests/<affected_test>.py
git commit -m "feat(#71): route completion-watcher refresh through media_servers fan-out"
```

---

### Task 4: full verification (behavior-preserving)

- [ ] **Step 1 — targeted suites:** `python -m pytest tests/test_media_server.py tests/test_media_server_bundle.py $(grep -rl "partial_scan\|completion" tests/ | tr '\n' ' ') -q` — all green.
- [ ] **Step 2 — lint:** `ruff check src/subarr/integrations/media_server.py src/subarr/integrations/plex.py src/subarr/completion_watcher.py src/subarr/coverage_engine.py && ruff format --check <those + new tests>`.
- [ ] **Step 3 — regression:** the full backend suite must pass unchanged — Slice 1 adds an abstraction with no behavior change. Push the branch and let CI run the full suite (matches the repo's CI-gated workflow).
- [ ] **Step 4 — confirm zero behavior change:** existing Plex partial-scan/full-scan tests pass untouched; a single configured Plex still refreshes exactly as before (fan-out over `[plex]`).

---

## Self-Review notes

- **Spec coverage:** delivers the spec's Slice 1 core (protocol + Plex behind it + fan-out through `media_servers`). Deliberate refinement: admin/health/coverage-audio-hints generalization is deferred to Slice 2 (documented above) — no behavior change is lost, and it's more coherent beside the Jellyfin surfacing.
- **Type consistency:** `refresh_for_file`/`full_refresh` return the same dicts `partial_scan`/`full_scan` already return; `media_servers` is a `list`; the fan-out helper filters on `is_configured()`.
- **No placeholders:** protocol + helper + delegators are shown in full; test sketches for Task 3 are marked to adapt to existing fixtures (the only non-literal steps, because they must match the current watcher test harness).
- **Zero-behavior-change guard:** the `plex_partial_scan_enabled` gate, `canonical_to_fs` resolve, and unconfigured/disabled short-circuits are all preserved.
