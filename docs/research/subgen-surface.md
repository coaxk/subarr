# subgen surface research (full config / API / engine-kwarg audit)

**Why this exists:** subarr's #1 build principle is "audit the full API surface of every dependency before building — what are we calling but throwing away?" We ran that on Sonarr, Radarr, Tautulli, and Bazarr early, but **skipped subgen — the load-bearing core — until 2026-06-03.** This doc is that overdue audit. Sources read 2026-06-03: `github.com/McCloudS/subgen` (`subgen.py` + README, version string `2026.05.3`), `github.com/SYSTRAN/faster-whisper` (~1.1.x `transcribe.py` + `vad.py`), `github.com/jianfch/stable-ts` (~2.x `faster_transcribe`).

Quote exact names. Where README and source disagree, **source wins** (noted inline).

---

## 1. What subarr uses today (the gap)

subarr drives subgen via **`POST /batch`** with `directory`, `reverse`, `forceLanguage`, `audio_language_override` (the last two via the subarr-subgen patch stack). `/batch` is **locked to subgen's global `TRANSCRIBE_OR_TRANSLATE` env** — no per-request task/kwargs control. The richer per-request surface (`/asr`) and the push-completion hook are unused. Harvest items filed from this audit are linked in §7.

---

## 2. subgen HTTP endpoints

| Method | Path | Params | Purpose |
|---|---|---|---|
| GET | `/` | — | web UI |
| GET | `/status` | — | returns subgen version |
| GET | `/plex` `/webhook` `/jellyfin` `/asr` `/emby` `/detect-language` `/tautulli` | — | GET info stubs |
| POST | `/tautulli` | `source` (hdr), `event`, `file` | Tautulli webhook |
| POST | `/plex` | `user_agent` (hdr), `payload` (form) | Plex webhook |
| POST | `/jellyfin` | `user_agent` (hdr), `NotificationType`, `file`, `ItemId` | Jellyfin webhook |
| POST | `/emby` | `user_agent` (hdr), `data` (form) | Emby webhook |
| POST | `/batch` | `directory` (req), `forceLanguage` | on-demand transcribe a folder/file on disk |
| POST | `/asr` | see §4 | direct ASR of an uploaded audio file (Bazarr-compatible) — **the per-request control channel** |
| POST | `/detect-language` | `audio_file` (file), `encode`, `video_file`, `detect_lang_length`, `detect_lang_offset` | language detection only |

No `/subtitle` / `/subsync` routes exist.

## 3. Transcribe vs translate (exact logic)

- Global switch: `TRANSCRIBE_OR_TRANSLATE` (default `transcribe`). `translate` = Whisper X→English; when set, output language is hard-forced to English (`if transcribe_or_translate == 'translate': language = LanguageCode.ENGLISH`).
- Per-file call: `model.transcribe(data, language=force_language.to_iso_639_1(), task=transcription_type, **args)`.
- **`/batch` has NO `task` param** — inherits the global. Webhooks likewise.
- **`/asr` DOES expose per-request `task`** (`Query(default="transcribe", enum=["transcribe","translate"])`) + `language`. The only per-request task override in vanilla subgen.
- ⚠️ subgen README: `large-v3-turbo` does **not** support translation. Any task toggle must be model-aware.

## 4. `/asr` per-request params (richest control surface)

`task` (transcribe|translate enum), `language`, `video_file`, `initial_prompt`, `audio_file` (upload), `encode` (ffmpeg pre-encode), `output` (`txt`|`vtt`|`srt`|`tsv`|`json`), `word_timestamps`. Note `output=json` returns structured segments rather than an `.srt`.

## 5. subgen environment variables

### Model / device / concurrency
`WHISPER_MODEL` (`medium`; `tiny`/`base`/`small`/`medium`/`large-v3`/`distil-large-v3`/`large-v3-turbo`), `WHISPER_THREADS` (4), `CONCURRENT_TRANSCRIPTIONS` (2; Bazarr requests exempt), `TRANSCRIBE_DEVICE` (`cpu`; `gpu`→`cuda`), `COMPUTE_TYPE` (`auto`), `MODEL_PATH` (`./models`), `MODEL_CLEANUP_DELAY` (30s), `CLEAR_VRAM_ON_COMPLETE` (True), `ASR_TIMEOUT` (18000s), `SUBGEN_KWARGS` (`'{}'`, see §6).

### Task / language / detection
`TRANSCRIBE_OR_TRANSLATE` (`transcribe`), `FORCE_DETECTED_LANGUAGE_TO` (`''`), `SHOULD_WHISPER_DETECT_AUDIO_LANGUAGE` (False), `DETECT_LANGUAGE_LENGTH` (30s), `DETECT_LANGUAGE_OFFSET` (0s), `PREFERRED_AUDIO_LANGUAGES` (`eng`), `LIMIT_TO_PREFERRED_AUDIO_LANGUAGE` (False).

### Skip logic / file filters (relevant to subarr's skip-prediction / probe-gate)
`SKIP_IF_TARGET_SUBTITLES_EXIST` (True), `SKIP_IF_EXTERNAL_SUBTITLES_EXIST` (False), `SKIP_IF_INTERNAL_SUBTITLES_LANGUAGE` (`''`), `SKIP_SUBTITLE_LANGUAGES` (`''`), `SKIP_IF_AUDIO_LANGUAGES` (`''`), `SKIP_UNKNOWN_LANGUAGE` (False), `SKIP_ONLY_SUBGEN_SUBTITLES` (False), `SKIP_IF_NO_LANGUAGE_BUT_SUBTITLES_EXIST` (False). Several check **internal/embedded** subtitle languages — exactly the signal subarr coverage rows were blind to.

### Triggers / queue / monitor
`PROCESS_ADDED_MEDIA` (True), `PROCESS_MEDIA_ON_PLAY` (True), `TRANSCRIBE_FOLDERS` (`''`), `MONITOR` (False), `PLEX_QUEUE_NEXT_EPISODE`/`_SEASON`/`_SERIES` (False), **`WEBHOOK_URL_COMPLETED`** (`''` — POST on task finish; push hook subarr could use instead of polling).

### Output / naming / integration
`SUBTITLE_LANGUAGE_NAME` (src `''`), `SUBTITLE_LANGUAGE_NAMING_TYPE` (`ISO_639_2_B`), `WORD_LEVEL_HIGHLIGHT` (False), `LRC_FOR_AUDIO_FILES` (True), `APPEND` (False), `SHOW_IN_SUBNAME_SUBGEN` (True), `SHOW_IN_SUBNAME_MODEL` (True), `CUSTOM_REGROUP` (`cm_sl=84_sl=42++++++1`; injected as `args['regroup']`). `PLEX_TOKEN`/`PLEX_SERVER`, `JELLYFIN_TOKEN`/`JELLYFIN_SERVER`. Most vars have legacy fallback names (e.g. `PLEXTOKEN`, `SKIPIFEXTERNALSUB`); prefer canonical.

### System
`WEBHOOK_PORT` (9000), `PUID` (99), `PGID` (100), `DEBUG` (True), `RELOAD_SCRIPT_ON_CHANGE` (False), `UPDATE` (False), `USE_PATH_MAPPING` (False) + `PATH_MAPPING_FROM`/`_TO`.

## 6. SUBGEN_KWARGS (the kwarg escape hatch — and its limit)

Parsed `ast.literal_eval(os.getenv('SUBGEN_KWARGS','{}'))`, then `args.update(kwargs)` and splatted into **every** `model.transcribe(**args)`. This is how any faster-whisper/stable-ts kwarg subgen has no env for gets set. **Constraint: it is GLOBAL ENV ONLY — there is no per-request kwargs channel.** Per-language tuning (the settings tester, #65) therefore needs a subarr-subgen patch to accept per-request kwargs, or it can't work against the live model without an env rewrite + restart per run.

## 7. Engine kwarg surface (faster-whisper `transcribe()`) — hallucination-flagged

🔴 = high impact on hallucination/quality, 🟡 = situational.

`beam_size` (5)🟡, `best_of` (5), `patience` (1), `length_penalty` (1), **`repetition_penalty`** (1)🔴, **`no_repeat_ngram_size`** (0)🔴, **`temperature`** (`[0,0.2,0.4,0.6,0.8,1.0]` — a fallback *schedule*, not scalar)🔴, **`compression_ratio_threshold`** (2.4)🔴, **`log_prob_threshold`** (-1.0)🔴, **`no_speech_threshold`** (0.6)🔴, **`condition_on_previous_text`** (True → set **False** to break repetition loops)🔴, **`prompt_reset_on_temperature`** (0.5)🔴, `initial_prompt` (None)🟡, `prefix`, `suppress_blank` (True), `suppress_tokens` ([-1]), `without_timestamps` (False), `max_initial_timestamp` (1.0), `word_timestamps` (False)🟡, `prepend_punctuations`/`append_punctuations`, `multilingual` (False), **`vad_filter`** (False)🔴, `vad_parameters` (§7a), `max_new_tokens`, `chunk_length`, `clip_timestamps` ("0"), **`hallucination_silence_threshold`** (None; needs `word_timestamps`)🔴, `hotwords` (None)🟡, `language_detection_threshold` (0.5), `language_detection_segments` (1).

**`BatchedInferencePipeline.transcribe()`** = same set, but defaults differ: `without_timestamps=True`, **`vad_filter=True`**, `clip_timestamps=None`, plus `batch_size` (8).

### 7a. VadOptions (silero)
`threshold` (0.5)🔴, `neg_threshold` (None→max(threshold-0.15,0.01)), `min_speech_duration_ms` (0), `max_speech_duration_s` (inf)🟡, `min_silence_duration_ms` (2000)🔴, `speech_pad_ms` (400), `min_silence_at_max_speech` (98), `use_max_poss_sil_at_max_speech` (True). Last three are version-dependent — verify against subgen's pinned faster-whisper.

## 8. Model + compute options (`WhisperModel.__init__`)

`model_size_or_path`, `device` (auto/cpu/cuda), `device_index`, `compute_type` (`default`/`auto`/`int8`/`int8_float16`/`int8_bfloat16`/`float16`/`bfloat16`/`float32`), `cpu_threads`, `num_workers`, `download_root`, `local_files_only`, `revision`. Sizes incl. `large-v3-turbo` (≈large-v3 quality, much faster, **no translate**) and distil variants (faster/smaller, English-leaning). `float16` GPU standard; `int8` smallest/fastest (slight accuracy cost); `float32` highest precision.

## 9. stable-ts additions (`faster_transcribe`)

Layers timestamp/audio post-processing over faster-whisper. `word_timestamps` (True), `regroup` (True)🟡, **`suppress_silence`** (True)🔴, **`suppress_word_ts`** (True)🔴, `use_word_position` (True), `q_levels` (20), `k_size` (5), **`vad`** (False)🔴 + **`vad_threshold`** (0.35)🔴 + `vad_onnx` (False), `min_word_dur`, `min_silence_dur`, `nonspeech_error` (0.1)🟡, **`only_voice_freq`** (False — band-limit 200–5000Hz)🟡, **`denoiser`**🟡, **`demucs`** (False — vocal isolation, big win on musical content)🟡, `only_ffmpeg`, `check_sorted`, `progress_callback`. **Two distinct VAD systems**: faster-whisper `vad_filter` (pre-filter, removes audio) vs stable-ts `vad` (post-hoc timestamp mask) — surface as separate controls. Confirm subgen calls `transcribe_stable(...)` (defaults differ).

## 10. Harvest punch-list (filed to Project #6)

| Item | What | Issue |
|---|---|---|
| Per-job task toggle | add `task`/`forceTask` to `/batch` (subarr-subgen patch); model-aware (turbo no-translate). First rung of v2 multilingual. | see board |
| Push completion | use `WEBHOOK_URL_COMPLETED` instead of polling subgen `/queue` | see board |
| Per-request kwargs channel | subarr-subgen patch so #65 tuning can set per-language kwargs (SUBGEN_KWARGS is global-only) — **blocks #65** | see board |
| Skip-logic awareness | read subgen's embedded-sub skip vars to sharpen subarr skip-prediction / probe-gate (relates #79) | see board |
| `/detect-language` cross-check | cheap language probe as an audio-funnel cross-check | see board |
