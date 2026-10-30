# #633: choose transcribe vs translate per file

Issue: coaxk/subarr#633 (AztecGuyGDL). Design agreed in the issue thread (comments 2026-10-22 and 2026-10-29). This spec covers two repos:

- **Part A**, `coaxk/subarr-subgen`, worktree `C:\Projects\subarr-subgen-633`, branch `feat/633-advertise-track-choice`.
- **Part B**, `coaxk/subarr`, worktree `C:\Projects\subarr-633`, branch `feat/633-per-file-task`.
- **Part C** is the shared contract fixture, which is committed to both repos.

## Problem

subgen's global `TRANSCRIBE_OR_TRANSLATE` cannot serve a mixed-language library. A user who wants en + es subtitles, with Lingarr making the second language, needs:

| Source audio | Whisper task | Output |
|---|---|---|
| en | transcribe | en |
| es | transcribe | es |
| ja | translate | en |

subgen already accepts `POST /batch?task=transcribe|translate` (patch 0015) and advertises `per_request_task`. However, subarr's `scan_runner` never passes `task`, and `producible_subtitle_languages()` (the #505 gate) is deliberately not widened for that reason.

The rule has to use the audio language **subgen will actually use**, not the default track. subgen picks it in this order (upstream `choose_transcribe_language`, `upstream/subgen.py:1775`):

1. the forced language (subarr's `audio_language_override` becomes it; patch 0009)
2. `FORCE_DETECTED_LANGUAGE_TO`
3. `PREFERRED_AUDIO_LANGUAGES`, in list order, against the file's tracks
4. the track flagged default

subgen does not advertise steps 2 and 3, so subarr cannot copy that order today.

## The rule (shared vocabulary)

- `W` is the user's **wanted subtitle languages**, a new setting (Part B). It is ISO 639-1, lowercase and non-empty while the feature is on.
- `L` is the **source language**: the result of mirroring subgen's order (B2). It is an ISO 639-1 code, or `None` for unknown.
- `task(L, W)`:
  - `L is None` → `None` (no task sent; subgen uses its global mode; gate unchanged)
  - `L in W` → `"transcribe"`
  - `L not in W and "en" in W` → `"translate"`
  - otherwise → `None`
- **Producible set** for the gate (B4): `{L}` for transcribe, `{"en"}` for translate. When the task is `None`, the existing `producible_subtitle_languages(global_mode, audio_langs)` result applies, unchanged.

The #505 gate keeps intersecting the producible set with the row's Bazarr `missing_subtitles`, exactly as today. `W` only picks the task. Because the gate still sees the per-title Bazarr `missing_subtitles`, a global `W` that differs from a title's Bazarr profile can only cause a hold-back (as today). It can never cause a submit-refuse loop.

## Part A: subarr-subgen

### A1. New patch `patches/0062-advertise-track-choice.patch`

Add the following to the `capabilities` block served by `GET /queue` (`queue_status`, where 0048 and 0050 add `skip_audio_languages` / `transcribe_or_translate`), next to `transcribe_or_translate`:

- `"preferred_audio_languages"`: the list from `preferred_audio_languages`, **in order, unfiltered, with duplicates kept**. Each entry is `lc.to_iso_639_1()`. An entry that parsed to `LanguageCode.NONE` is emitted as JSON `null`, **not dropped**. Reason: upstream's `find_language_audio_track` matches a `NONE` entry against an untagged track, returns `NONE`, and `choose_transcribe_language` treats that falsy return as "no preferred match" and falls through to the default track. That skips any later entries. Dropping nulls would make subarr predict a later entry that subgen never reaches.
- `"force_detected_language_to"`: `force_detected_language_to.to_iso_639_1()`, or `null` when it is unset or `NONE`.

The comment style must match 0050. Cite coaxk/subarr#633 and say why the null entries are kept.

Patch hygiene: generate it the way the existing patches are generated (`scripts/apply-patches.sh` applies `patches/series` in order onto the `upstream` submodule at the pinned gitlink; read the header comment of `scripts/apply-patches.sh` for the gitlink vs `upstream.pin` trap). Append it to `patches/series`.

### A2. Patch `0063-bump-patch-rev-v4.38.patch`

`subarr_subgen_patch_rev = 'v4.38'`, same shape as 0061. Append it to `series`. Update every other place that records the current rev or the latest patch number. Find them with `git grep -n "v4.37"` and `git grep -n "0061"` outside `patches/`, and update CHANGELOG/README the way 0058-0061 did.

### A3. Tests (subgen)

- `tests/smoke_track_choice.py`: a runtime smoke, run inside the built image like `tests/smoke_patched_behaviour.py` (same docstring run command, same `check()` / `FAILS` pattern, non-zero exit on any failure). It must:
  1. For every case in `tests/fixtures/track_choice_cases.json` (Part C), set `subgen.force_detected_language_to` and `subgen.preferred_audio_languages` from the case (`LanguageCode.from_string`, with `null` → `LanguageCode.NONE`). Build `audio_tracks` as upstream `get_audio_tracks` would (`language` = `LanguageCode.from_iso_639_2(raw_tag or "Unknown")`, `default` bool). Call `subgen.choose_transcribe_language("x.mkv", forced, audio_tracks=tracks)`, where `forced` is `LanguageCode.from_string(case.forced)` when set, else `None`. Assert `result.to_iso_639_1()` equals `expected_source` (`None` for unknown).
  2. Call `subgen.queue_status()` (or whatever returns the capabilities dict; read 0048/0050). Assert that both new keys are present and that their values reflect the module globals it just set, including a `null` entry and list order.
- Wire the smoke wherever `smoke_patched_behaviour.py` is wired in CI (find it in `.github/workflows/`). If it is not run in CI, say so in the PR and run it locally against a locally built image.
- `tests/test_capability_audit.py`: these two keys are values, not boolean flags (like `skip_audio_languages`), so the 16-flag list does not change. Confirm the existing tests still pass.

## Part B: subarr

### B1. Capabilities (`src/subarr/subgen_client.py`)

Add the following to `SubgenCapabilities`:
- `preferred_audio_languages: tuple[str | None, ...] | None = None`. `None` means not advertised (older subgen). A JSON `null` entry becomes `None` **in place**; keep the position and keep duplicates. Each non-null entry is a 639-1 code subgen produced with `to_iso_639_1()`, parsed with `normalize_lang`. If any non-null entry cannot be mapped, do NOT turn it into `None`, because that would fake a fall-through. Instead set `preferred_audio_languages_unreadable: tuple[str, ...]` to those entries; `active_config` then returns `None`, and the API reports "subgen advertised a preferred audio language subarr cannot read: X".
- `force_detected_language_to: str | None = None`, normalized. Absent or `null` → `None`. A non-empty advertised value that cannot be mapped is NOT turned into `None` (that would fake a fall-through to the preferred list): set `force_detected_language_unreadable: str | None` to the raw value; `active_config` then returns `None` and the API reports "subgen advertised a forced detected language subarr cannot read: X".

Parse both next to `transcribe_or_translate` (`subgen_client.py` ~545) and include them in the fingerprint/`to_dict` wherever the sibling fields appear.

Remove nothing from the `producible_subtitle_languages` docstring. Append one paragraph pointing at the new module (B2) as the per-file path when the feature is active.

### B2. New module `src/subarr/per_file_task.py` (pure, no I/O, no config imports)

```python
def subgen_track_language(raw_tag: str | None) -> str | None
```
Mirror upstream `LanguageCode.from_iso_639_2`. It returns an ISO 639-1 code **only** when `raw_tag` is exactly a three-letter ISO 639-2/T or /B code that upstream knows. Upstream compares case-sensitively and does not strip. subarr's probe store already lowercases tags (`media_probe.py:212`), so compare the stored value as-is and do not strip. KNOWN DIVERGENCE, which must be documented in the docstring and not fixed: a file tagged in upper case (`ENG`) reads as unknown in subgen and as `en` here. Such tags are rare in practice, and fixing it would mean storing raw tags in the probe store. A two-letter tag such as `en`, `und`, empty, `None` and unknown codes all give `None`. **Do not use `normalize_lang` here**: it accepts two-letter codes and names, so subgen and subarr would disagree on a file tagged `en`. If `langs.py` has no 639-2 to 639-1 table, add one, generated from upstream `language_code.py` (read `C:\Projects\subarr-subgen-633\upstream\language_code.py`). Add a test that pins a few B-vs-T pairs (`ger`/`deu`, `fre`/`fra`, `chi`/`zho`) and `en` → `None`.

```python
def resolve_source_language(
    tracks: Sequence[tuple[str | None, bool]],   # (raw ffprobe tag, default) in ffprobe stream order
    *, forced: str | None, force_detected: str | None,
    preferred: Sequence[str | None],
) -> str | None
```
This exactly mirrors upstream order:
1. `forced` (already an ISO 639-1 code; callers pass `normalize_lang(x)`, and an unmappable value is passed as `None`) → if truthy, return it.
2. `force_detected` → if truthy, return it.
3. For each `p` in `preferred`, in order: if NO track has `subgen_track_language(tag) == p`, continue to the next `p`. If one does, then return `p` when `p` is not `None`. When `p is None` (it matched an unknown-language track), stop scanning `preferred` and go to step 4. That is upstream's falsy-NONE fall-through, and it only happens when such a track exists. Duplicates in `preferred` are harmless; keep them.
4. The first track with `default=True` → `subgen_track_language(tag)` (may be `None`).
5. `None`.

```python
def choose_task(source: str | None, wanted: Collection[str]) -> Literal["transcribe", "translate"] | None
def producible_for_task(task, source) -> set[str]   # {source} | {"en"}
```

```python
@dataclass(frozen=True)
class PerFileTaskConfig:
    wanted: frozenset[str]
    preferred: tuple[str | None, ...]
    force_detected: str | None

def active_config(settings_enabled: bool, wanted: Iterable[str], caps) -> PerFileTaskConfig | None
```
The function returns non-`None` only when all six of these hold: `settings_enabled`, `wanted` is non-empty after normalization, `caps.per_request_task` is true, `caps.preferred_audio_languages is not None`, `caps.preferred_audio_languages_unreadable` is empty, and `caps.force_detected_language_unreadable` is falsy. In every other case it returns `None`, the feature is inert, and behaviour is byte-for-byte today's. There is **no** fallback to `eng` when the list is not advertised (issue comment 2026-10-29).

### B3. Setting and API

- `config.py`: `per_file_task_enabled: bool = False` and `per_file_task_wanted: list[str] = []`, coerced like `forced_segment_enabled` (`config.py:157` / `:624`). The list is normalized with `normalize_lang`, deduplicated, and order-preserving.
- New router `src/subarr/routers/per_file_task.py`, modelled on `routers/forced_segment.py`:
  - `GET /api/per-file-task/config` → `{"enabled", "wanted", "available": bool, "unavailable_reason": str | None, "preferred_audio_languages", "force_detected_language_to"}`. `available` is true when caps allow it (`per_request_task` and an advertised list). The reason names which one is missing ("subgen does not accept a per-request task" / "subgen does not advertise its preferred audio languages; update subarr-subgen to v4.38+").
  - `POST /api/per-file-task/config` body `{enabled: bool, wanted: list[str]}`. Respond **400** if any code fails `normalize_lang`, and 400 for `enabled=true` with an empty list. Persist both via `config_store.save_override`, and verify the value on disk the way the forced-segment tests do. The UI field is split on commas and whitespace before POST. Saving `enabled=true` while unavailable is allowed (the config is kept); the GET reports `available:false`.
  - Register the router where `forced_segment` is registered. It must be behind the same auth as other `/api` routes.
- UI: `static/v1/home-hifi/settings.jsx`, in the subgen panel next to the capabilities card (~1722). Add a toggle "Translate audio that is not a wanted language into English" and a text field "Wanted subtitle languages (e.g. en, es)". While unavailable, the controls are disabled and show `unavailable_reason`. Add a one-line explanation: "Transcribes files whose audio is a wanted language, translates the rest to English. Uses the same audio track subgen will pick." Rebuild the bundle with the repo's bundle build so the CI `bundle drift check` passes. Add a component test next to the existing `__tests__` that covers available, unavailable (with reason shown) and a save.

### B4. Gate (`coverage_engine.py`)

- Where `item.audio_langs` is first set from the probe (`_apply_episode_probe` ~1167 and `_attach_probe_movie` ~1222-1237; do both), also store `item.audio_tracks: list[tuple[str | None, bool]]` = `[(stream.language, stream.default) for stream in probe.audio]` in probe order. Add the field to `CoverageItem` with default `[]`. Check that it does not leak into any API payload that has a frozen shape; if `CoverageItem` is serialized wholesale, exclude it there.
- `build_coverage` computes `cfg = active_config(settings.per_file_task_enabled, settings.per_file_task_wanted, subgen_caps)` once and passes it to every `_score` call (the six `subgen_output_task=` sites) as `per_file_task=cfg`.
- In `_score`'s #505 block, when `cfg` is not `None` and `item.audio_tracks` is non-empty:
  - `forced` = `resolve_audio_language_override(audio_lang_store, item.file_canonical_path, caller="coverage", log=<a logger set to WARNING so its INFO lines are not emitted per row>, skip_audio_languages=caps.skip_audio_languages)`. This is **the same call the feeder makes**, so gate and dispatch agree. Pass what `_score` needs as a new keyword (a resolved `forced` map computed once in `build_coverage`, or the store); do not open a new store.
  - `L = resolve_source_language(item.audio_tracks, forced=normalize_lang(forced) if forced else None, force_detected=cfg.force_detected, preferred=cfg.preferred)`, `t = choose_task(L, cfg.wanted)`. The gate reads `item.audio_tracks` (the raw probe tracks), never `item.audio_langs`, which `_classify_audio_label` later rewrites from user/Plex/Whisper hints. User verdicts reach `L` only through `forced`, exactly as at dispatch.
  - If `t` is not `None`: `_producible = producible_for_task(t, L)`, and append the score reason `f"per-file task: {t} ({L} audio)"` so the row explains itself.
  - Otherwise the existing code path runs unchanged.
- The hold-back reason strings (`coverage_engine.py:1782`, `auto_queue.py:227`) are unchanged.

### B5. Dispatch

- `scan_runner.py`: `self._tasks` ALREADY EXISTS and holds asyncio Tasks (`scan_runner.py:118`, `:207`, `:184` shutdown). Do NOT reuse that name. Add `self._path_tasks: dict[str, dict[str, str | None]]` (scan id → canonical path → task). `start(..., path_tasks: dict[str, str | None] | None = None)` stores it, and it is cleaned up wherever `_overrides` is cleaned up. The `batch(...)` call (`scan_runner.py:275`, inside the loop over `scan.paths`) passes `task=self._path_tasks.get(scan_id, {}).get(path)`. The task is sent **only** if the caps current at that moment advertise `per_request_task` (re-read them at batch time, because caps can refresh mid-scan); otherwise `None`.
- `submission_language.py`: add `resolve_task_for_file(canonical, *, override: str | None, caps, probe_store, enabled: bool, wanted: Iterable[str]) -> str | None`. It builds `cfg = active_config(enabled, wanted, caps)` and returns `None` if `cfg` is `None`. It then reads `probe_store.get(canonical)`; a missing or unreadable probe gives `None` (never raises). Tracks are `[(a.language, a.default) for a in probe.audio]`, and `forced` is `normalize_lang(override)` if set. It returns `choose_task(L, cfg.wanted)`. One INFO line names the file (scrubbed), `L`, the task and the reason.
- `app.py` `_feeder_submit`, in this order:
  1. `decision = resolve_for_job(...)` (unchanged).
  2. If `job.task` is set (no producer sets it today), it wins: `path_tasks = {job.canonical_path: job.task}` and skip to 4.
  3. Otherwise, for a FILE job: `path_tasks = {job.canonical_path: resolve_task_for_file(job.canonical_path, override=decision.override, ...)}`, so `forced` is exactly the override being sent. For a DIRECTORY job, see Folders.
  4. `scan = scans.create(paths, ...)` and `runner.start(scan, audio_language_override=decision.override, ..., path_tasks=path_tasks)`. Provenance is recorded exactly as today, against `job.canonical_path`.
- **Folders** (`canonical_to_fs(job.canonical_path)` is a directory and `cfg` is active). Nothing is re-enqueued and the feeder loop is unchanged; one job stays one job:
  - List video files recursively (`os.walk`, `VIDEO_EXTS` only, sorted canonical paths). Zero files → submit the folder as today, with no task.
  - More than `PER_FILE_TASK_FOLDER_CAP = 500` files → submit the folder as today, with no task, and log WARNING with the count.
  - Compute each file's task with `override=decision.override`: the runner sends the JOB's override on every `/batch` of a folder scan, so subgen forces that language for each file, and `forced` must match what is sent. A folder under a declared series intent therefore plans the same task as the file job would.
  - If every file gets the same task (including all `None`), the paths are `[folder]` and `path_tasks = {folder: that task}`.
  - If the tasks differ, the paths are the file list and `path_tasks` maps each file to its task. The scan carries N paths, and the runner already loops `scan.paths` with one `/batch` per path. Before relying on that, check that `scans.create` accepts many paths and that the completion/provenance plumbing handles a multi-path scan whose provenance is the folder; `git grep -n "scans.create(" src` shows existing multi-path uses. If a multi-path scan does not complete correctly, STOP and report rather than redesigning.
- The arena, forced-segment and other `asr` paths are untouched.

### B6. Tests (subarr), each in its own file under `tests/`

`tests/test_per_file_task_633.py`:
- Every Part C case through `resolve_source_language` (parametrized from `tests/fixtures/subgen_track_choice_cases.json`).
- `choose_task`, with the issue table plus: `L` None; `L` not in W and en not in W; W = {en} with ja → translate; W = {es} with ja → None.
- `active_config` returns None in each of the four missing-input states, and non-None when all four hold.
- `subgen_track_language` pins (B2).

`tests/test_per_file_task_gate_633.py`:
- With the feature active, a row with ja audio, W = {en, es}, missing = {en, es}, global mode transcribe → **not** gated (today it is gated), with the score reason present.
- es audio, existing es, missing = {en}, W = {en, es} → gated (the Lingarr race case). This must fail if B4 uses `missing` instead of `W` to choose the task.
- A dual-audio jpn + eng file with preferred [eng] → transcribe en.
- Feature inactive (each of the four reasons) → identical gate result to today on the same rows.
- No probe tracks → unchanged behaviour.

`tests/test_per_file_task_dispatch_633.py`:
- The feeder passes `task` through to `SubgenClient.batch` (use the existing feeder/runner test doubles; find them with `git grep -l "_feeder_submit\|PendingQueueFeeder" tests`).
- No task when caps lack `per_request_task`.
- **Agreement:** for each fixture row, the task the gate implied equals the task dispatch sends. Drive both through the real functions on the same file and settings.
- Folder uniform → one scan path (the folder) with that task. Folder mixed → one scan with N file paths and per-path tasks, with each `/batch` call carrying its own task, ONE pending job marked submitted, and provenance against the folder. Folder over the cap → the folder alone, no task, WARNING logged. Folder with zero videos → the folder alone, no task. Nested subfolders are counted.
- `job.task` preset → sent as-is.
- Caps lose `per_request_task` between start and batch → no task sent.
- The forced override path: a verified-language file whose override differs from its tracks uses the override as `L` in both gate and dispatch.
- Known, documented divergence (gate vs folder job): the gate predicts each file with the file's OWN override, while a file inside a FOLDER job gets the folder's override at dispatch. Folder jobs come only from manual Scan-tab submits, which the gate does not filter, so the divergence cannot affect auto-queued work.
- Known, documented divergence (test pins the current behaviour): a job enqueued with a stored `audio_language_override` that differs from the store's current verdict. Dispatch uses the stored one ("decided when queued"), while the gate predicted from the store.

`tests/test_per_file_task_api_633.py`: GET shape, the POST 400s, persistence on disk, and the auth requirement.

Existing suites that must stay green unchanged: `tests/test_unproducible_language_505.py`, `tests/test_subgen_per_request_task.py`.

## Part C: contract fixture

`track_choice_cases.json` is committed byte-identical as `subarr-subgen: tests/fixtures/track_choice_cases.json` and `subarr: tests/fixtures/subgen_track_choice_cases.json`. Shape:

```json
{"version": 1, "cases": [
  {"name": "...", "tracks": [["jpn", true], ["eng", false]], "preferred": ["eng"],
   "force_detected": null, "forced": null, "expected_source": "en"}
]}
```

Minimum cases:
1. jpn default + eng, preferred [eng] → en (AztecGuyGDL's dual-audio case)
2. jpn only, default, preferred [eng] → ja
3. eng only → en; spa only (default) → es
4. preferred [jpn, eng] vs [eng, jpn] on jpn + eng → ja / en (list order matters)
5. force_detected = ja on an eng-only file → ja
6. forced = fr beats force_detected and preferred → fr
7. no default flag and no preferred match → null
8. track tagged two-letter `en`, preferred [eng] → no match. Then the default: if that track is the default, the result is null (the upstream `from_iso_639_2` quirk)
9. preferred [null, eng] with an `und` track and an eng track → falls to the default track (the falsy-NONE fall-through), not en
10. `ger` and `deu` tags both match preferred [de]
11. preferred [null, eng] with NO unknown-language track, and an eng track → en (the null entry matches nothing, so the scan continues)
12. preferred [eng, eng] duplicates → same as [eng]
13. forced given as a 3-letter code is out of scope for the fixture: subarr normalizes before calling. The fixture `forced` values are 639-1.

A subarr test asserts that the file's sha256 equals a constant in the test, and the comment next to the constant says to update both repos together. The subgen smoke asserts the same constant. A change on one side without the other then fails one suite loudly.

## Edge cases (every state, each with a test above)

- Older subgen (no list advertised) → inert, and the UI explains why.
- `per_request_task` absent → inert.
- Toggle on with an empty W → rejected at save; and inert if it reaches config another way.
- Untagged / `und` / two-letter-tagged tracks → follow upstream, which may mean `L` None → global mode.
- No probe yet → `L` None → global mode, gate unchanged.
- A verified-language override → it is `L` in both gate and dispatch.
- A multilingual verdict → no override → tracks decide.
- English audio withheld as an override (#498) → tracks decide.
- Mixed folder, uniform folder, a huge folder, a single file.
- A job already carrying `task` → kept.
- Global mode translate + feature on + L in W → transcribe is sent (the feature's point); global transcribe + L not in W + en in W → translate.

## Out of scope

`LIMIT_TO_PREFERRED_AUDIO_LANGUAGE` (a skip, not a choice), reading Bazarr language profiles, and any change to subgen's choice logic itself.

## Rollout

The subgen PR merges and releases v4.38 first. The subarr PR then merges to main and goes to `:main` for AztecGuyGDL to test. GPU validation is not needed: nothing here transcribes. Any live smoke against `subgen-next` runs outside 04:00-10:00.
