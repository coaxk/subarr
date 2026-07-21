# #71 Slice 2c — path-prefix auto-detect + dashboard card + README

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use `- [ ]` checkboxes.

**Goal:** three things: (1) **auto-detect** the media-server path prefix from the server's library locations so nobody has to set `*_PATH_PREFIX` manually (Plex + Jellyfin); (2) add the **Jellyfin dashboard card** (Overview page); (3) document `JELLYFIN_*` in the README env table.

**Architecture:** a pure `derive_path_prefix(locations, media_root, sample)` helper. Each client gains `library_locations()` + an async `_effective_prefix(sample)` (explicit env wins → cached auto-derived → identity fallback + log). `translate_path` takes an optional prefix override. `refresh_for_file`/`partial_scan` compute the effective prefix before translating. Dashboard card = one backend probe (frontend already has `SERVICE_BRAND.jellyfin`). Behavior-preserving: explicit prefix and identical-mount setups are unchanged (derive returns None → identity).

---

## Reference facts
- `derive_path_prefix` math (validated by hand): subarr file `/media/library/TV/Show/ep.mkv`, `media_root=/media/library` → rel first segment `TV`; a server location `/media/TV` ends with `/TV` → prefix = `/media`. Identical mount (`media_root=/media/library`, location `/media/library` or `/media/library/TV`): no location ends with `/TV` off a different root, or the derived prefix equals `media_root` → identity, correct.
- `JellyfinClient.libraries()` → `[{"name","paths":[...]}]` (paths = VirtualFolder Locations). `PlexClient.sections()` → `[{"id","title","paths":[...]}]`.
- `home.py` dashboard probe list (~line 305): `asyncio.gather(_probe("bazarr"...), _probe("plex", bundle.plex), _probe("tautulli"...))`. Frontend `dashboard.jsx` `SERVICE_BRAND` already has `jellyfin` (committed) + `ServiceBadge` falls back for unknowns — so ONLY the backend probe is needed.
- README env table added in v2.4.2; find it: `grep -n "PLEX_URL\|## Environment" README.md`.

---

### Task 1: pure `derive_path_prefix` helper

**Files:** Modify `src/subarr/integrations/media_server.py`; Test `tests/test_path_prefix_autodetect.py`.

- [ ] **Step 1 — failing tests:**
```python
from subarr.integrations.media_server import derive_path_prefix


def test_derives_prefix_from_differing_mount():
    # subarr sees /media/library/TV/...; server library at /media/TV -> prefix /media
    assert derive_path_prefix(["/media/TV", "/media/Movies"], "/media/library", "/media/library/TV/Show/ep.mkv") == "/media"


def test_identical_mount_returns_none():
    # server library IS the media root -> no /TV-suffixed location off a different root -> None (identity)
    assert derive_path_prefix(["/media/library"], "/media/library", "/media/library/TV/Show/ep.mkv") is None


def test_file_outside_media_root_returns_none():
    assert derive_path_prefix(["/media/TV"], "/media/library", "/other/TV/ep.mkv") is None


def test_no_matching_location_returns_none():
    assert derive_path_prefix(["/srv/Anime"], "/media/library", "/media/library/TV/ep.mkv") is None


def test_trailing_slashes_tolerated():
    assert derive_path_prefix(["/media/TV/"], "/media/library/", "/media/library/TV/ep.mkv") == "/media"
```

- [ ] **Step 2 — run, confirm fail.**
- [ ] **Step 3 — implement** in `media_server.py`:
```python
def derive_path_prefix(locations: list[str], media_root: str, sample_subarr_path: str) -> str | None:
    """Derive the path prefix P such that translate_path yields the server's own
    path: P + sample[len(media_root):] lands under one of the server's library
    `locations`. Returns None when no location aligns (caller falls back to
    identity, which is correct for identical mounts)."""
    media_root = (media_root or "").rstrip("/")
    if not media_root or not sample_subarr_path.startswith(media_root):
        return None
    rel = sample_subarr_path[len(media_root):]
    parts = [p for p in rel.split("/") if p]
    if not parts:
        return None
    first = parts[0]
    for loc in locations:
        locn = (loc or "").rstrip("/")
        if locn.endswith("/" + first):
            prefix = locn[: -len("/" + first)]
            # Identity if the derived prefix already equals the media root.
            return prefix if prefix and prefix != media_root else None
    return None
```
- [ ] **Step 4 — run, confirm pass. Step 5 — commit** `feat(#71): derive_path_prefix helper (media-server path auto-detect)`.

---

### Task 2: Jellyfin auto-detect wiring

**Files:** Modify `src/subarr/integrations/jellyfin.py`; Test `tests/test_jellyfin_client.py` (extend).

- [ ] **Step 1 — failing tests** (monkeypatch `_request` to return VirtualFolders + an item index):
  - `refresh_for_file` with EMPTY explicit prefix but a server library at `/media/TV` → derives `/media`, translates the file, matches the item, refreshes.
  - explicit `path_prefix` set → auto-detect NOT invoked (explicit wins).
  - `_effective_prefix` caches (a second call doesn't re-fetch libraries).
- [ ] **Step 2 — run, confirm fail.**
- [ ] **Step 3 — implement**: add `self._auto_prefix: str | None = None` in `__init__`; add `library_locations()` (flatten `libraries()` paths); add `_effective_prefix(sample)`:
```python
    async def library_locations(self) -> list[str]:
        libs = await self.libraries()
        return [p for lib in libs for p in (lib.get("paths") or [])]

    async def _effective_prefix(self, sample_subarr_path: str) -> str:
        """Explicit JELLYFIN_PATH_PREFIX wins; else derive from library locations
        (cached); else identity. Cached value "" means 'derived nothing, use identity'."""
        if self._path_prefix:
            return self._path_prefix
        if self._auto_prefix is not None:
            return self._auto_prefix
        derived = None
        try:
            from .media_server import derive_path_prefix
            derived = derive_path_prefix(await self.library_locations(), self._media_root, sample_subarr_path)
        except Exception as e:  # noqa: BLE001
            log.warning("jellyfin: path-prefix auto-detect failed: %s", e)
        self._auto_prefix = derived or ""
        log.info("jellyfin: path-prefix %s (media_root=%s)",
                 f"auto-detected {derived!r}" if derived else "auto-detect found none; using identity",
                 self._media_root)
        return self._auto_prefix
```
  Make `translate_path` accept an optional override: `def translate_path(self, subarr_path, prefix=None):` using `p = self._path_prefix if prefix is None else prefix`. In `refresh_for_file`, compute `prefix = await self._effective_prefix(subarr_file)` and pass it: `jf_path = self.translate_path(subarr_file, prefix)`.
- [ ] **Step 4 — run, confirm pass. Step 5 — commit** `feat(#71): Jellyfin path-prefix auto-detect (explicit wins, identity fallback)`.

---

### Task 3: Plex auto-detect wiring (same pattern, behind the same safe guards)

**Files:** Modify `src/subarr/integrations/plex.py`; Test `tests/test_plex_partial_scan.py` (extend).

- [ ] Mirror Task 2 on `PlexClient`: `self._auto_prefix=None`; `library_locations()` (flatten `sections()` paths); `_effective_prefix(sample)` (explicit `PLEX_PATH_PREFIX` wins → derive from section locations → identity); `translate_path(path, prefix=None)`; `partial_scan` computes `prefix = await self._effective_prefix(subarr_file_path)` and translates with it before section discovery.
- [ ] **Tests**: explicit `PLEX_PATH_PREFIX` still wins (existing tests unchanged); empty prefix + a section at `/media/TV` derives `/media`; identical-mount derives None → identity (existing behavior preserved). Existing `test_plex_partial_scan.py` must stay green.
- [ ] **Commit** `feat(#71): Plex path-prefix auto-detect (parity with Jellyfin, explicit wins)`.

---

### Task 4: Jellyfin dashboard card

**Files:** Modify `src/subarr/routers/home.py`; Test the home/dashboard test file (grep `tests/` for the dashboard-tiles test).

- [ ] Add jellyfin to the dashboard probe list **conditionally** (only when configured, so Plex-only installs don't get an empty tile): build the gather list, then `if bundle.jellyfin.is_configured(): probe_list.append(_probe("jellyfin", bundle.jellyfin))`. Keep the existing five unconditional.
- [ ] **Test**: with a configured jellyfin stub, the dashboard tiles include `jellyfin`; unconfigured → absent. (frontend needs no change — `SERVICE_BRAND.jellyfin` + `ServiceBadge` already handle rendering.)
- [ ] **Commit** `feat(#71): jellyfin dashboard tile (probed when configured)`.

---

### Task 5: README env table

**Files:** Modify `README.md`.

- [ ] Find the env table (`grep -n "PLEX_URL\|PLEX_TOKEN\|## Environment" README.md`). Add a Jellyfin block near Plex:
  - `JELLYFIN_URL` — Jellyfin server URL (optional; coexists with Plex).
  - `JELLYFIN_API_KEY` — Jellyfin API key (Dashboard → API Keys).
  - `JELLYFIN_PATH_PREFIX` — **optional**; the path Jellyfin sees the media at (e.g. `/media`). Auto-detected from the server's libraries when unset; set only to override.
- [ ] Sweep for any env vars added since the table was written that are missing (e.g. `SUBARR_FORCED_SEGMENT_ENABLED` if not present) — `grep -oE "SUBARR_[A-Z_]+|JELLYFIN_[A-Z_]+" src/subarr/config.py | sort -u` vs the README table; add any gaps.
- [ ] **Commit** `docs(#71): document JELLYFIN_* env vars (path prefix auto-detected)`.

---

### Task 6: verification + live smoke + PR

- [ ] Targeted suites (`test_path_prefix_autodetect`, `test_jellyfin_client`, `test_plex_partial_scan`, dashboard test) + ruff + `npm run check:frontend`.
- [ ] **LIVE SMOKE — the proof**: temporarily **remove** `JELLYFIN_PATH_PREFIX` from subarr-next's env (or override it empty), recreate, and confirm the client **auto-derives `/media`** and a real `refresh_for_file` still matches the item. Then restore the env var (belt-and-suspenders) OR leave it removed since auto-detect now covers it — note the choice.
- [ ] Push; CI (full suite + frontend + bundle-drift). Zero behavior change for explicit-prefix / identical-mount installs.
- [ ] **PR** (base main): `feat(#71): path-prefix auto-detect + Jellyfin dashboard card + README`. Body: the three deliverables, the live auto-detect proof, and that it kills the path-prefix footgun for both servers.

---

## Self-Review notes
- **Behavior-preserving**: explicit `*_PATH_PREFIX` always wins (existing setups untouched); identical-mount derives None → identity (today's behavior). Only the previously-broken empty-prefix-different-mount case improves.
- **No frontend change for the dashboard card** — `SERVICE_BRAND.jellyfin` is already committed; only the backend probe was missing.
- **Type consistency**: `translate_path(path, prefix=None)` stays protocol-compatible (optional arg); `_effective_prefix` returns a str; `derive_path_prefix` returns `str | None`.
