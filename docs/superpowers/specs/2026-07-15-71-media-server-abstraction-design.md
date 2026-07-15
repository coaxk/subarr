# Media-Server Abstraction + Jellyfin Backend — Design (#71 / #72)

**Issues:** #71 (first-class media-server integration + `MediaServer` abstraction),
#72 (Jellyfin/Emby backend).
**Date:** 2026-07-15
**Status:** approved for planning (RTFM + live validation done; Model B chosen).

## Goal

Generalize subarr's Plex-only media-server integration into a `MediaServer`
abstraction and add a **Jellyfin** backend, so users can run Plex and Jellyfin
**side by side** (Model B). Emby is stubbed in the abstraction but its backend is
deferred until demand. The user-facing job is unchanged: after subarr writes a
subtitle sidecar, tell every configured media server to pick it up.

## Grounding — validated live against Jellyfin 10.11.11

RTFM against the stable OpenAPI spec + a hands-on run on a real instance
(`docs/jellyfin-api-research-2026-07-15.md`). Confirmed on real media:

- **Auth:** `X-Emby-Token: <api_key>` header works (simplest of the accepted forms).
- **Libraries:** `GET /Library/VirtualFolders` → `Name` + `Locations[]` (fs paths).
- **Core loop (proven):** write `<video>.en.srt` → `POST /Items/{id}/Refresh?metadataRefreshMode=Default` → Jellyfin lists the external subtitle (`MediaStreams[]` `Type=Subtitle IsExternal=true lang=eng`).
- **Path→itemId lookup:** `GET /Items` has **no server-side path filter**, so resolve via `searchTerm=<name>` + client-side `Path` match (validated: 23 candidates → exact match). Full-refresh is the fallback.
- **Audio hints:** `GET /Items?fields=MediaStreams` exposes per-stream `Language`.
- Sidecar model works unchanged: Jellyfin picks up external `.srt` on item refresh.

## The `MediaServer` abstraction

A protocol the current `plex.py` already nearly satisfies:

```python
class MediaServer(Protocol):
    type: str  # "plex" | "jellyfin" | "emby"
    def is_configured(self) -> bool: ...
    def translate_path(self, subarr_path: str) -> str: ...       # subarr fs path → this server's mount
    async def libraries(self) -> list[Library]: ...              # name + fs locations
    async def refresh_for_file(self, subarr_file: str) -> RefreshResult: ...  # the core hook
    async def full_refresh(self) -> RefreshResult: ...
    async def status(self) -> ServerStatus: ...
    async def audio_lang_hints(self, titles) -> dict[str, str]: ...
```

- **PlexServer** = today's `plex.py` (one-shot path refresh `/library/sections/{id}/refresh?path=`).
- **JellyfinServer** = the validated flow: `X-Emby-Token`; `libraries` from
  `/Library/VirtualFolders`; `refresh_for_file` = searchTerm + `Path`-match →
  `POST /Items/{id}/Refresh`, falling back to `full_refresh` (`POST /Library/Refresh`)
  when no item matches; `audio_lang_hints` from `/Items?fields=MediaStreams`.
- **EmbyServer** = deferred. The abstraction leaves room (it's a thin Jellyfin
  subclass — same fork), but no backend ships in this epic.
- A factory builds each configured server from config; the app holds the **list**
  of active servers.

## Config model + back-compat

Per-type env blocks, each independent; a server is **active** when its URL + secret
are set:

- `PLEX_URL` / `PLEX_TOKEN` / `PLEX_SECTION` / `PLEX_PATH_PREFIX` / … — **unchanged**.
  Existing Plex installs upgrade with zero edits.
- `JELLYFIN_URL` / `JELLYFIN_API_KEY` / `JELLYFIN_PATH_PREFIX` — new.
- (`EMBY_*` reserved, not wired until the backend ships.)

Persisted via `config_store` like every other integration (honoring the env
clobber-guard). **Per-server path prefix** is required: each server may mount the
library at a different root (observed: Plex `/media`, subarr `/media/library`,
Jellyfin `/media`). Path matching on the server side (section/library auto-discovery)
covers multi-library layouts; the prefix only converts subarr's fs view to the
server's view.

## Fan-out + audio hints

- `refresh_for_file` and `full_refresh` **fan out to every configured server**,
  independently and **best-effort** — one server erroring or being down never
  blocks the others (mirrors `completion_watcher`'s current best-effort
  `partial_scan`; each result logged). "Configured" == "active"; no separate
  per-server enable toggle.
- `audio_lang_hints`: query the **first configured server** that supports it. All
  servers index the same files, so one source suffices — no merging.

## Multi-instance arr interaction (#161) — covered, no coupling

The media-server layer is **orthogonal to arr instances** because it operates on
resolved file paths, not on which Sonarr/Radarr owns a file:

- arr instances (#161) → subarr libraries (media roots) → `canonical_to_fs`
  resolves any file to an fs path (already library/instance-aware via #134
  `@slug` heads) → the media server matches that path to **its own** libraries.
- Plex has **no** per-instance/per-library binding today (`plex_section` is a
  global fallback; the client auto-discovers the section per file by path).
  Jellyfin's searchTerm + `Path`-match is the same auto-discovery.
- With multiple arr instances → multiple libraries → the fan-out refreshes each
  server, and each refreshes only the content **it** hosts (a no-op on the rest:
  Plex matches no section, Jellyfin matches no item). No arr↔media-server mapping
  is needed, and none will be added.

## Surfacing (Model B — one-per-type, coexisting)

The integration registry gains `jellyfin` (and reserves `emby`) beside `plex`:

- **Settings:** independent integration cards per configured server. Reuse the
  existing `CREDENTIAL_SCHEMA` / `CredentialEditor` /
  `GET|PUT /api/integrations/{name}/…` machinery:
  `CREDENTIAL_SCHEMA.jellyfin = [url, api_key(secret)]`. Add `_test_jellyfin`
  (probe `/System/Info/Public`, then an authed `/Library/VirtualFolders` to
  confirm the key).
- **Onboarding:** a "Media Servers" step listing Plex / Jellyfin, each optional —
  add any or all. Reuses the wizard's per-service test + the (now-fixed) persist
  path.
- **Health:** one dot per configured server (`status()` per backend). **System
  actions:** "scan" fans out `full_refresh` to all configured servers.

## Emby — deferred

The `MediaServer` protocol and the factory account for `type="emby"`, but no Emby
backend, config wiring, or UI ships in this epic. Add it as a thin `JellyfinServer`
subclass + surfacing when a user asks. Jellyfin is the demand driver.

## Slicing (de-risk the refactor first)

- **Slice 1 — abstraction + Plex behind it. Zero behavior/UX change.** Extract
  `MediaServer`, make `PlexServer` implement it, route `completion_watcher` /
  `admin` / `coverage_engine` through the protocol (single-server list = `[plex]`).
  Fully covered by existing Plex tests; nothing new is user-visible. This lands the
  risky refactor invisibly.
- **Slice 2 — Jellyfin backend + surfacing.** `JellyfinServer` (validated flow),
  config (`JELLYFIN_*` + back-compat), the fan-out list, the registry/onboarding/
  settings/health surfacing, and the `_test_jellyfin` probe.
- (**Slice 3 — Emby** — deferred, not scheduled.)

## Testing

- **Slice 1:** existing Plex integration tests must pass unchanged (the refactor is
  behavior-preserving); add a thin `MediaServer` protocol-conformance test for
  `PlexServer`. The fan-out list with a single Plex behaves exactly as today.
- **Slice 2:** unit-test `JellyfinServer` against recorded/faked HTTP responses
  (VirtualFolders parse; `refresh_for_file` = searchTerm + Path-match → item
  refresh; full-refresh fallback when no match; `audio_lang_hints` from
  MediaStreams; `X-Emby-Token` header). Fan-out best-effort: one server raising
  never suppresses the other. Config back-compat: `PLEX_*`-only still yields a
  single Plex server; adding `JELLYFIN_*` yields both. A live smoke against the
  test Jellyfin (the proven core loop) before merge.
- **Frontend:** pure helpers for the new integration cards, per the `__tests__/`
  convention. Rebuild the bundle (drift check).

## Acceptance criteria

1. Existing Plex-only installs behave identically after Slice 1 (zero change).
2. A user can configure Plex **and** Jellyfin; a landed sub refreshes **both**.
3. A server being down/erroring does not block refresh on the other.
4. Multi-instance arr content refreshes on whichever server(s) host it, with no
   arr↔server configuration.
5. `PLEX_*` env unchanged; `JELLYFIN_*` adds the second server.
6. Full suite + vitest + bundle-drift green; live core-loop smoke passes.

## Out of scope

Emby backend; multiple servers of the same type (arbitrary instance list — deferred
edge case); direct subtitle upload via the Jellyfin API (sidecar model retained);
any change to the subtitle pipeline itself.
