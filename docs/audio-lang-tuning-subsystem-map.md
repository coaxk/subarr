# Subarr — Audio-Language Verification + Tuning Lab (Arena) + #155 Library Audit: Subsystem Map

> Onboarding map for the subsystem built across the v1.2 sprint. Read this
> **after** a general codebase pass. It is the technical bedrock for the audio
> "verify, don't parrot" thesis — the thing the *arr metadata can't do: subarr
> *listens* and tells you the truth about the audio track.

---

## 1. File inventory

### Backend core (`src/subarr/`)
| File | Role |
|---|---|
| `arena.py` | Pure orchestration: `parse_robust_detect()`, `run_arena()`, `AsrRunner`, `_aggregate()`, `_confidence()`, dataclasses (`ConfigVariant`/`VariantOutcome`/`ClipResult`/`AggregateRow`/`ArenaResult`) |
| `arena_service.py` | `ArenaService` lifecycle (create/start/subscribe/aclose) + **`resolve_source_language()`** classifier; `ArenaRun` dataclass |
| `arena_store.py` | SQLite WAL persistence for sweeps; `aggregate_by_language()` (herd view); `reconcile_interrupted()`; numpy coercion in `_json_default` |
| `arena_sampler.py` | Cuts up to 3 strata WAV clips via ffmpeg + silero VAD; `build_samples()`, `select_windows()`, `_cut_clip()` (`-map 0:a:<track>`) |
| `arena_explain.py` | Optional ollama plain-language explainer for a result |
| `audio_audit.py` | **`AudioAuditWalker`** (#155) — opt-in, throttled, GPU-polite background walker; `AuditState`, `_derive_status()`, Tier-2 writeback |
| `audio_audit_store.py` | SQLite store for audit findings (`audio_lang_audit`); `AuditFinding`; tolerates missing `track_languages` (migration 011) |
| `audio_lang_store.py` | Per-file verifications (`audio_lang_verifications`) + series intent (`series_lang_intent`); `resolve_audio_language_override()` |
| `coverage_engine.py` | `build_coverage()`; **`_classify_audio_label()`** (funnel L0–L4); Bazarr-blind synthetic rows; `_refine_audio_sources()`; `_SeriesIntentLookup` |
| `coverage_cache.py` | Single-row snapshot store + `background_refresh_loop()`; `eager_probe_targets()` |
| `paths.py` | 4 path representations: `canonical_to_fs()`, `fs_to_canonical()`, `canonical_to_subgen_batch()`, `subgen_to_canonical()`; `VIDEO_EXTS`; `settings.media_root` |
| `subgen_client.py` | `detect_language_robust()`, `asr()`, `probe_capabilities()` → caps `asr_arena`, `asr_vanilla_base`, `robust_language_detection` |
| `probe_store.py` | ffprobe results per canonical path; `.audio` streams carry `.language` |
| `app.py` | Lifespan wiring (arena ~178–205, audit ~241–349, shutdown ~534–570) |

### Routers
| File | Prefix | Endpoints |
|---|---|---|
| `routers/arena.py` | `/api/arena` | POST `/run`, GET `/runs`, GET `/by-language`, GET `/audio-issues`, GET `/{id}`, DELETE `/{id}`, POST `/{id}/language`, GET `/{id}/events` (SSE) |
| `routers/audio_audit.py` | `/api/audio-audit` | POST `/start?scope=coverage\|library`, POST `/stop`, GET `` |
| `routers/audio_lang.py` | `/api/audio-lang` | verifications CRUD + bulk-for-series, series-intent CRUD, `/sample-positions`, `/sample`, `/pending-review`, `/whisper-detect` |

### Migrations (`src/subarr/migrations/`)
| File | Adds |
|---|---|
| `008_init_schema_parity.sql` | `audio_lang_verifications`, `series_lang_intent`, `coverage_snapshot`, enrichment cols, probe source col |
| `009_arena_runs.sql` | `arena_runs` + indexes |
| `010_audio_audit.sql` | `audio_lang_audit` + indexes (status, checked_at) |
| `011_audio_audit_tracks.sql` | `ALTER … ADD COLUMN track_languages TEXT DEFAULT '[]'` (migrate runner tolerates duplicate-column) |

### Frontend (`src/subarr/static/v1/home-hifi/`)
| File | Role |
|---|---|
| `arena.jsx` | Tuning Lab UI: `SweepForm`, `FilePicker`, `SweepList`/detail, `ByLanguagePanel`, **`AudioIssuesPanel`** (scan controls + legend + progress bar + merged findings), `AudioLegend`, `AUDIO_BADGE`, `timeAgo` |
| `coverage.jsx` | **`AudioReviewModal`** (shared audio player + lang picker + Whisper-detect); `open-audio-review` / `audio-lang-verified` events |
| `atoms.jsx` | `LANG_INFO`/`LANG_ALIAS`/`normalizeLang`/`LangTag` (flag SVGs), design tokens |

---

## 2. Data flow

### 2a. Robust detection → classifier
`subgen /detect_language_robust` (3 chunks across the file middle) → `parse_robust_detect(resp)` →
`{language, n_agreeing, n_total, unanimous (n_total≥2 AND n_agreeing==n_total), languages_heard:[iso…]}` or `None` (n_total==0).

**Per-chunk agreement shape is the discriminator:**
| Shape | Meaning |
|---|---|
| Unanimous (3/3 same) | trust it → override a wrong tag (`mislabel`) |
| Split w/ real 2nd lang (≥2 agree + ≥2 distinct heard) | `bilingual` (mixed) — keep tag as nominal primary |
| 1/1/1 no majority | `confused` — Whisper unsure, trust the tag |

### 2b. `resolve_source_language(detect, tag, submit, multitrack)` → `(lang, source, mixed, mislabel)`
Precedence: **submit → user**; **unanimous → whisper** (mislabel if tag≠lang AND not multitrack); else **tag → tagged** (mixed if ≥2 agree + ≥2 heard, not unanimous); else **plurality ≥2 → whisper-weak**; else **None**. `multitrack` suppresses `mislabel` (different track ≠ mislabel).

### 2c. Audit walker (`_audit_one`) status derivation (`_derive_status`)
`multitrack → mislabel → bilingual → undetermined(no detect) → confused(no majority) → agrees`. Actionable = `mislabel|bilingual|multitrack`.

### 2d. Tier-2 feedback writeback
On a **unanimous mislabel** the walker writes a `whisper-robust` verification to `audio_lang_store`: `conf=0.7` (non-risky) / `conf=0.45` (risky JA/KO/ZH, below the 0.5 override gate → display-only). Coverage's `_classify_audio_label` Layer-1 picks up `whisper`-sourced rows; `resolve_audio_language_override` forwards non-risky to subgen as `audio_language_override`.

### 2e. Funnel precedence (`_classify_audio_label`, coverage_engine)
L0 **user verification** (authoritative, `audio_verified=True`) → L1 **whisper-robust** (`audio_source=whisper`, NOT verified) → L2.5 **Tautulli live** → L2.6 **Plex persisted** → L3 **ffprobe tag** (lowest trust; sets `audio_label_suspect/unknown`).

---

## 3. Stores & schemas (one SQLite file, WAL, per-store conn + lock, autocommit)

- **`arena_runs`** (009): `id, media_path, source_language, status, source_text, variants(JSON), outcomes(JSON), result(JSON), winner, error, created_at, updated_at`. `reconcile_interrupted()` flips `pending|queued|running`→`error` on boot.
- **`audio_lang_audit`** (010+011): `canonical_path PK, tag_lang, detected_lang, status, languages_heard(JSON), n_agreeing, n_total, mtime, checked_at, track_languages(JSON)`.
- **`audio_lang_verifications`** (008): `canonical_path PK, lang_code, source('user'|'auto-high-conf'|'whisper-robust'), confidence, verified_at, verified_by, evidence(JSON)`.
- **`series_lang_intent`** (008): `series_prefix PK (MUST end '/'), lang_code, source, confidence, …`. `AudioLangStore.get()` does longest-prefix fallback.
- **`coverage_snapshot`** (008): single-row JSON snapshot; `CoverageCache` mirrors in-memory.

---

## 4. Multitrack vs bilingual
- **Multitrack** = ≥2 separate audio streams w/ distinct ffprobe language tags (original + dub). `_track_langs()` returns ordered distinct codes (e.g. `["de","ru"]`). Suppresses mislabel; UI shows `DE + RU` (from `track_languages`, **not** what was heard in the one listened track — this was the display bug fixed late in the sprint).
- **Bilingual** = two languages in ONE track. UI shows `DE/EN` (from `languages_heard`).
- **Sweep fan-out**: a file with ≥2 distinct track langs creates one `ArenaRun` per track (`track_index=N` → `-map 0:a:N`), labeled by that track's language.

---

## 5. Deploy model (CRITICAL)
- `subarr-next` container, port **9923** (dev). **NEVER 9922** (prod).
- Container **bind-mounts** `C:\Projects\subarr-ui\src\subarr` → site-packages/subarr. Backend deploy = `wsl -e bash -lc "docker restart subarr-next"`. No `docker cp`.
- Frontend: `npm run build:frontend` **first**, then restart. `chrome.jsx` is shared/inlined → editing it rebuilds all bundles.
- **Restart cancels in-flight arena sweeps AND audit scans.** Sweeps → `reconcile_interrupted` marks them `error`. Audit findings are durable (SQLite) + resumable (mtime-skip), so a re-scan only re-checks changed files.

## 6. Gotchas
- caps live on **`/queue`** not `/status`; `asr_arena` gates the lab (clear 503 if missing).
- path translation: walker + arena detect use `canonical_to_subgen_batch`; arena `asr()` uploads a temp WAV clip (`local_file=`) so it needs **no** shared mount. Don't canonicalize subgen queue keys (cancel bug #58).
- `asr_vanilla_base` (subarr-subgen v4.11+) forces `base="vanilla"` so recipe scores are host-independent (federated comparability).
- **flex-shrink trap**: full-height panels in a flex-column with `overflow:auto` need `flexShrink:0` or they collapse.
- numpy float32 from the QE judge needs `_json_default` coercion or the run strands in `running`.
- verified-wins filter hides **only `source=='user'`** so Tier-2 `whisper-robust` rows don't self-erase.
