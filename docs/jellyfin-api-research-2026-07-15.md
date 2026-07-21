# Jellyfin / Emby Media-Server Integration — API Research (#71 / #72)

RTFM pass before any code. Maps every Plex call subarr makes to its Jellyfin
equivalent, surfaces the architectural gaps, and lists what must be verified
against a live Jellyfin instance. Source: Jellyfin stable OpenAPI spec
(api.jellyfin.org/openapi/jellyfin-openapi-stable.json) via Context7.

## subarr's actual media-server surface (from `integrations/plex.py`)

subarr's media-server usage is **narrow and sidecar-based**. It writes `.srt`
sidecar files to disk, then tells the server to rescan so it picks them up. It
does **not** upload subtitles through the Plex API.

| Plex method | Purpose | HTTP |
|---|---|---|
| `is_configured` / auth | connectivity | `X-Plex-Token` query param |
| `translate_path` | subarr path → server path | (client-side) |
| `sections()` | list libraries + filesystem Locations | `GET /library/sections` |
| `_section_for_path` | match a file path → its library section | (client-side, over Locations) |
| `full_scan` | full library refresh | `POST /library/sections/{id}/refresh` |
| `partial_scan(file)` | **targeted folder refresh** after a sub lands | `POST /library/sections/{id}/refresh?path=<dir>` |
| `status()` | health / reachability | `GET /` (identity) |
| `audio_lang_hints(titles)` | read audio-track languages | Plex library metadata |

The load-bearing call is `partial_scan` — fired by `completion_watcher` right
after a sidecar is written.

## Plex → Jellyfin mapping

| subarr need | Plex | Jellyfin | Notes |
|---|---|---|---|
| **Auth** | `X-Plex-Token` (query) | API key via `Authorization: MediaBrowser Token="<key>"` header (also accepts `X-Emby-Token: <key>` and `?api_key=`) | API keys are created in Dashboard → API Keys. **Verify exact header on live instance.** |
| **List libraries + paths** | `GET /library/sections` → `<Location>` | `GET /Library/VirtualFolders` → `VirtualFolderInfo.Locations[]` | Near-perfect analog: `Name`, `Locations[]` (fs paths), `CollectionType`, `ItemId`. Path→library matching works identically (prefix-match over `Locations`). |
| **Full refresh** | `POST /library/sections/{id}/refresh` | `POST /Library/Refresh` | Jellyfin refreshes **all** libraries; no section id. Simpler, but coarser. **Verify** whether a single-library scan trigger exists. |
| **Targeted refresh** | `POST /library/sections/{id}/refresh?path=<dir>` (one shot, by path) | **No path-based refresh.** Two-step: (1) resolve file → itemId, (2) `POST /Items/{itemId}/Refresh?metadataRefreshMode=Default` | **The key gap** — see below. |
| **Read audio-track langs** | Plex metadata | `GET /Items?fields=MediaStreams&recursive=true&includeItemTypes=Episode,Movie` → `MediaStreams[]` where `Type=Audio`, read `Language` | Direct analog; richer (per-stream `IsExternal`/`IsForced`/`Path`/`DisplayTitle`). |
| **Health** | `GET /` | `GET /System/Info/Public` (no auth) or `GET /System/Ping` | Standard. |
| **(new) Direct sub upload** | — (Plex has none) | `POST /Videos/{itemId}/Subtitles` with `UploadSubtitleDto {Language, Format, IsForced, IsHearingImpaired, Data(base64)}`; `DELETE /Videos/{itemId}/Subtitles/{index}` | Jellyfin-only capability. Probably **not** adopted — see below. |

## The one real architectural gap: targeted refresh

Plex's `partial_scan` is a single path-scoped call. Jellyfin has **no path-based
refresh** and refreshes by **item UUID**. So the Jellyfin equivalent is:

1. **Resolve the video file → itemId.** `GET /Items` has **no server-side path
   filter** (only `searchTerm` name-search). So subarr must query
   (`searchTerm=<filename-stem>&recursive=true&includeItemTypes=Episode,Movie&fields=Path`)
   and **match `Path` client-side**, or walk the containing library.
2. **`POST /Items/{itemId}/Refresh`** (`metadataRefreshMode=Default`) — Jellyfin
   re-scans the item and picks up the freshly-written external sidecar.

Implications:
- The lookup adds a round-trip and can be imprecise (name collisions across
  seasons/versions → match on full `Path`, not just name).
- **Fallback:** if the lookup fails, `POST /Library/Refresh` (full) still works,
  just heavier. The abstraction should degrade to this.
- This is the single biggest reason the `MediaServer` interface (#71) must model
  `refresh_for_file(path)` as a capability with **very different** Plex vs
  Jellyfin implementations, not a thin URL swap.

## Why keep the sidecar model on Jellyfin (not the upload API)

Jellyfin *can* take a subtitle via `POST /Videos/{itemId}/Subtitles` (base64).
Tempting, but the sidecar-write + refresh model is still the right one:
- subarr's whole pipeline produces `.srt` **files** on disk; other tools (Bazarr,
  the user, subsyncarr) see and manage those sidecars. An API-uploaded sub lives
  in Jellyfin's own store, invisible to that ecosystem.
- It keeps Plex and Jellyfin behaviourally identical (write sidecar → refresh),
  which is exactly what an abstraction wants.
- The upload API still needs the itemId anyway, so it saves nothing on the lookup.

Note the upload path as a **future option** (e.g. servers with a read-only media
mount), not the default.

## Emby (#72 also covers Emby)

Jellyfin forked from Emby, so the API is largely shared (`/Library/VirtualFolders`,
`/Items`, `/Items/{id}/Refresh`, `X-Emby-Token`). Expect the same abstraction to
cover Emby with minor auth/endpoint deltas. **Verify against a live Emby only if a
user actually asks;** Jellyfin is the demand driver.

## Proposed `MediaServer` abstraction shape (#71) — for the design phase

A protocol the current Plex client already nearly satisfies:

```
class MediaServer(Protocol):
    def is_configured(self) -> bool: ...
    def translate_path(self, subarr_path: str) -> str: ...
    async def libraries(self) -> list[Library]:        # name + fs locations + type
    async def refresh_for_file(self, subarr_file: str) -> RefreshResult:  # the core hook
    async def full_refresh(self) -> RefreshResult:
    async def status(self) -> ServerStatus:
    async def audio_lang_hints(self, titles) -> dict[str, str]:
```

- Plex impl = today's `plex.py` (path refresh).
- Jellyfin impl = auth header + VirtualFolders + the two-step `refresh_for_file`
  (item lookup → item refresh, full-refresh fallback).
- `completion_watcher`, `admin`, `coverage_engine` depend on the protocol, not on
  `plex` concretely. A `media_server` factory selects the backend from config.

## Must-verify on a live Jellyfin (RTFM → then TEST)

Install Jellyfin locally, point it at the same NAS library, and confirm by hand:
1. Exact **auth header** that works (`Authorization: MediaBrowser Token=` vs `X-Emby-Token`).
2. `GET /Library/VirtualFolders` returns the library `Locations[]` as real fs paths.
3. **The core loop:** write a `<video>.en.srt` sidecar → resolve itemId by path →
   `POST /Items/{itemId}/Refresh` → confirm Jellyfin lists the new external
   subtitle (`MediaStreams[].IsExternal=true`). This is subarr's whole value prop;
   it must work end-to-end before we design further.
4. Whether a **single-library** refresh exists (vs all-libraries `POST /Library/Refresh`).
5. Item-lookup-by-path reliability + latency on a real library.

## Next steps

1. Install Jellyfin (`jellyfin/` compose in DockerStacks, same NAS mount).
2. Hand-validate the 5 items above with curl against the live instance.
3. Brainstorm the `MediaServer` abstraction design (#71), then slice: abstraction
   + Plex-behind-interface first (no behaviour change), Jellyfin backend second.
