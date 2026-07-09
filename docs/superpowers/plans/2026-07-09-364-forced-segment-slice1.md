# Forced-Segment Generation (#364) — Slice 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** For an English-tagged file that hides a short foreign-language scene and has no forced track, detect the foreign speech at import and emit a scoped, absolute-timed, forced-flagged `<basename>.forced.en.srt` covering only those scenes.

**Architecture:** subarr owns all detection intelligence and output; subgen stays a thin primitive. A pure detector (silero-VAD utterances → per-utterance LID → merged foreign spans, biased to completeness) feeds a translate step and an SRT emitter. Two triggers — a GPU-polite manual walker (audio-audit pattern) and a best-effort at-import hook (completion_watcher) — both gated on one OFF-by-default toggle and de-duplicated by a `(canonical_path, mtime, size)` scan cache. Path-contained, no-clobber writeback.

**Tech Stack:** Python 3.11, FastAPI, SQLite (WAL, migration-owned schema), silero VAD via onnxruntime, ffmpeg subprocess clipping, subgen HTTP client (`/detect_language_robust`, `/asr task=translate`), pytest + pytest-asyncio (strict), httpx.MockTransport.

---

## Feasibility finding (read before starting — grounds Task 0)

Two real subgen primitives were read in `src/subarr/subgen_client.py`:

- **`SubgenClient.asr(path=None, *, local_file=None, task="transcribe", language=None, kwargs=None, initial_prompt=None, base=None, return_language=False, timeout_s=7200.0)`** (`subgen_client.py:572`). **Accepts an UPLOADED clip** via `local_file=` — it reads the file and posts multipart `files={"audio_file": (...)}` (`subgen_client.py:620-630`). Returns the subtitle text; with `return_language=True` returns `(text, x_detected_language)`. `task="translate"` is supported and validated (`subgen_client.py:602`). This is the confirmed upload channel the arena already uses. Gate on `caps.asr_arena`.
- **`SubgenClient.detect_language_robust(path: str, chunks=3, chunk_length_s=30, track=None)`** (`subgen_client.py:237`). **PATH-ONLY** — it posts `params={"path": path, ...}` (`subgen_client.py:248-254`); there is **no upload parameter**. So the spec's literal "tier-1 LID via `/detect_language_robust` on an ffmpeg-clipped utterance" only works if subgen can *see the clip on a filesystem path* (a shared scratch mount), OR subgen is patched to accept an upload. Gate on `caps.robust_language_detection`.

**Consequence for slice 1 (the branch Task 0 must settle):**
- **Branch A (preferred, matches spec, cheapest GPU):** subarr writes each utterance clip into a **subgen-visible scratch dir** (a shared mount, mirroring how `subgen_media_prefix` maps `media_root`), then calls `detect_language_robust(path=<subgen-visible clip path>)`. `parse_robust_detect` (`arena.py:65`) gives `(language, n_agreeing, n_total, unanimous)` → confidence = `n_agreeing/n_total`.
- **Branch B (works today with zero shared-fs assumption, no fork):** LID via `asr(local_file=clip, task="transcribe", return_language=True)` → `(text, detected_lang)`, confidence unknown so treated as `1.0` and the over-flag bias compensates. More GPU per utterance (a transcribe instead of a cheap detect), but within the "opt-in, expensive-ish" envelope the spec accepts.

This plan **injects the LID function** into the orchestrator so both the detector and the orchestrator are testable *today* with fakes. Task 0 verifies which branch the live deployment needs and Task 7 ships **both** adapters, selecting Branch A when a scratch dir is configured and falling back to Branch B otherwise. **Translate always uploads (`/asr`), so it needs no shared fs.**

**DECISION (controller, 2026-07-09): ship both, but Branch B is an honest, degraded fallback.** When the generator selects Branch B (no subgen-visible scratch configured), it MUST emit a **one-time WARNING per generator instance** making the cost explicit, e.g. `log.warning("forced-segment: LID is using the /asr upload path — this transcribes every utterance and is much slower than the cheap detect path. Configure a subgen-visible scratch dir (SUBARR_FORCED_SEGMENT_SCRATCH_SUBGEN) for the fast path, or wait for the local-LID upgrade (#364 slice 2).")`. Branch A stays the default whenever a scratch dir is set. Task 7 adds this warning (a module-level `_warned_branch_b` guard or a one-shot flag on the generator); its test asserts the warning fires once on the Branch-B path and not on Branch A.

---

## File Structure

| File | Create / Modify | Responsibility |
|------|-----------------|----------------|
| `src/subarr/forced_segment.py` | Create | Pure detector core (utterance classification, span merge, mostly-foreign predicate), the SRT emitter, the gate predicate, the VAD→utterance adapter, the ffmpeg clip helper, tunable `ForcedSegmentParams`. No subgen, no DB. |
| `src/subarr/forced_segment_service.py` | Create | `ForcedSegmentGenerator` (orchestration: VAD → LID → merge → mostly-foreign bail → translate → emit → path-contained no-clobber write → scan-cache record → aftercare note) and the two subgen LID/translate adapters. `ForcedSegmentWalker` (manual, GPU-polite, resumable, Health-supervised). |
| `src/subarr/forced_segment_store.py` | Create | `ForcedSegmentScanStore` — `(canonical_path, mtime, size)` idempotence cache (mirrors `probe_store.py`). |
| `src/subarr/migrations/028_forced_segment_scans.sql` | Create | Schema for the scan cache table. |
| `src/subarr/config.py` | Modify (`:106`, `:285`, `:494`, `:525`) | Add `forced_segment_enabled: bool` (env `SUBARR_FORCED_SEGMENT_ENABLED`, default off) + FIELD_ENV_VARS/coerce entries. |
| `src/subarr/completion_watcher.py` | Modify (`:68`, `:252`, `:446`) | At-import hook: `_maybe_forced_segment(entry)` best-effort, gated, background-scheduled, never blocks completion. |
| `src/subarr/routers/forced_segment.py` | Create | `POST /api/forced-segment/start`, `POST /stop`, `GET ""` (walker control + progress), mirroring `routers/audio_audit.py`. |
| `src/subarr/app.py` | Modify (`:398`, `:630`) | Register `"forced-segment"` on the Health roster; wire the store, generator, walker, at-import hook. |
| `tests/test_forced_segment_detector.py` | Create | Detector core (pure). |
| `tests/test_forced_segment_srt.py` | Create | SRT emitter (pure). |
| `tests/test_forced_segment_gate.py` | Create | Gate predicate (pure). |
| `tests/test_forced_segment_store.py` | Create | Migration 028 + scan-cache hit/miss/stale. |
| `tests/test_config_forced_segment.py` | Create | Env-flag parse. |
| `tests/test_forced_segment_adapters.py` | Create | LID + translate adapters against a fake subgen. |
| `tests/test_forced_segment_service.py` | Create | Orchestration end-to-end with a fake subgen + injected VAD/clip. |
| `tests/test_forced_segment_walker.py` | Create | Walker (fakes): resumable, GPU-polite, Health record. |
| `tests/test_completion_forced_segment.py` | Create | At-import hook: enabled-schedules / disabled-noop / never-blocks. |
| `tests/test_forced_segment_feasibility.py` | Create | Task 0 contract guard (upload vs path-only). |

**Conventions baked in (subarr):** run one test file at a time `python -m pytest tests/<f>.py -q`; pytest-asyncio is STRICT — every async test needs `@pytest.mark.asyncio`; endpoint tests are SYNC via `app_with_stub` (TestClient). `conftest.py` reloads config/paths/integration modules per test, so re-import reloaded symbols *inside* the test body; `Settings` is a frozen dataclass — toggle via `monkeypatch.setenv` + `importlib.reload(config)`. The ruff PostToolUse hook strips a just-added unused top-level import — add import + usage together (or import locally), and `ruff format` any heredoc-appended test before committing. Best-effort background steps use `getattr(self, "_health", None)` guards and swallow-and-**log** (never silent — the #416 lesson). Commit locally only; footer `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`; no apostrophes in commit messages.

---

### Task 0: Feasibility gate — confirm subgen accepts an uploaded clip

**Files:**
- Test: `tests/test_forced_segment_feasibility.py`

This task encodes the feasibility finding as a regression guard and forces the Branch A/B decision before any code is built on it. It does NOT hit a live subgen (deterministic CI); it asserts the client CONTRACT that the rest of the plan relies on, using `httpx.MockTransport` to capture exactly what each method puts on the wire.

- [ ] **Step 1: Write the failing test**

```python
"""#364 Task 0 — feasibility gate. Encodes the subgen-client contract the
forced-segment pipeline depends on: /asr accepts an UPLOADED clip (multipart),
/detect_language_robust is PATH-ONLY. If either contract changes, this fails
loudly so the pipeline's transport assumptions are revisited."""

from __future__ import annotations

import httpx
import pytest

from subarr.subgen_client import SubgenClient


def _client(capture: dict):
    def handler(req: httpx.Request) -> httpx.Response:
        capture["path"] = req.url.path
        capture["params"] = dict(req.url.params)
        capture["content_type"] = req.headers.get("content-type", "")
        capture["body_len"] = len(req.content or b"")
        if req.url.path == "/asr":
            return httpx.Response(200, text="1\n00:00:00,000 --> 00:00:02,000\nhola\n")
        if req.url.path == "/detect_language_robust":
            return httpx.Response(200, json={"aggregate": {"language": "es", "n_agreeing": 3, "n_total": 3}, "chunks": []})
        return httpx.Response(404)

    c = SubgenClient(base_url="http://subgen.test:9000")
    c._client = httpx.AsyncClient(base_url="http://subgen.test:9000", transport=httpx.MockTransport(handler))
    return c


@pytest.mark.asyncio
async def test_asr_uploads_a_local_clip_as_multipart(tmp_path):
    clip = tmp_path / "utt.wav"
    clip.write_bytes(b"RIFFfake-wav-bytes")
    cap: dict = {}
    c = _client(cap)
    text = await c.asr(local_file=str(clip), task="translate")
    await c.aclose()
    assert cap["path"] == "/asr"
    assert cap["params"].get("task") == "translate"
    assert "multipart/form-data" in cap["content_type"]  # the clip was UPLOADED
    assert cap["body_len"] > 0
    assert "hola" in text


@pytest.mark.asyncio
async def test_detect_language_robust_is_path_only_no_upload(tmp_path):
    cap: dict = {}
    c = _client(cap)
    resp = await c.detect_language_robust("/media-scratch/utt.wav")
    await c.aclose()
    assert cap["path"] == "/detect_language_robust"
    assert cap["params"].get("path") == "/media-scratch/utt.wav"  # server-visible path, not an upload
    assert "multipart/form-data" not in cap["content_type"]
    assert resp["aggregate"]["language"] == "es"
```

- [ ] **Step 2: Run test to verify it passes (contract already holds)**

Run: `python -m pytest tests/test_forced_segment_feasibility.py -q`
Expected: PASS (2 passed). If `test_asr_uploads_a_local_clip_as_multipart` FAILS, `/asr` upload is broken — STOP and escalate (the whole slice depends on it). If `test_detect_language_robust_is_path_only_no_upload` fails because a `local_file`/upload param now exists, subgen gained upload LID — record that and prefer it in Task 7.

- [ ] **Step 3: Record the branch decision inline in the test module docstring**

Append to the module docstring:

```
BRANCH DECISION (fill at execution): if a subgen-visible scratch mount is available,
Task 7 uses Branch A (detect_language_robust on a shared clip path — cheapest).
Otherwise Task 7 uses Branch B (asr upload + return_language as LID). Translate
always uploads via /asr.
```

- [ ] **Step 4: Commit**

```bash
git add tests/test_forced_segment_feasibility.py
git commit -m "test(364): feasibility gate for forced-segment subgen contract"
```

---

### Task 1: Detector core — classify utterances, merge foreign spans, mostly-foreign bail (pure)

**Files:**
- Create: `src/subarr/forced_segment.py`
- Test: `tests/test_forced_segment_detector.py`

- [ ] **Step 1: Write the failing test**

```python
"""#364 slice 1 — pure detector core. A stub LID (no subgen, no audio) drives
utterance classification, span merge (min-duration + merge-gap), the over-flag
bias, and the mostly-foreign bail."""

from __future__ import annotations

from subarr.forced_segment import (
    ForcedSegmentParams,
    Span,
    assemble_foreign_spans,
    classify_utterances,
    foreign_fraction,
    is_mostly_foreign,
)

# Utterances are (start_s, end_s). LID stub returns (lang, confidence) per utterance.
UTTS = [(0.0, 3.0), (3.2, 6.0), (10.0, 14.0), (14.5, 18.0)]


def _lid(mapping):
    return lambda utt: mapping[utt]


def test_confident_english_is_not_foreign():
    p = ForcedSegmentParams()
    lid = _lid({UTTS[0]: ("en", 0.95)})
    classified = classify_utterances([UTTS[0]], lid, p)
    assert classified == [(UTTS[0], False)]


def test_non_english_is_foreign_at_any_confidence():
    p = ForcedSegmentParams()
    lid = _lid({UTTS[0]: ("fr", 0.55)})
    assert classify_utterances([UTTS[0]], lid, p) == [(UTTS[0], True)]


def test_low_confidence_over_flags_to_foreign():
    p = ForcedSegmentParams(conf_floor=0.6, over_flag_low_confidence=True)
    lid = _lid({UTTS[0]: ("en", 0.2)})  # uncertain English -> over-flagged
    assert classify_utterances([UTTS[0]], lid, p) == [(UTTS[0], True)]
    p2 = ForcedSegmentParams(conf_floor=0.6, over_flag_low_confidence=False)
    assert classify_utterances([UTTS[0]], lid, p2) == [(UTTS[0], False)]


def test_contiguous_foreign_merge_within_gap_and_min_duration_floor():
    # Two adjacent French utterances (gap 0.2s) merge into one span >= floor;
    # an isolated 0.5s French blip is dropped by the min-duration floor.
    p = ForcedSegmentParams(min_span_s=2.5, merge_gap_s=1.5)
    utts = [(0.0, 3.0), (3.2, 6.0), (20.0, 20.5)]
    lid = _lid({utts[0]: ("fr", 0.9), utts[1]: ("fr", 0.9), utts[2]: ("es", 0.9)})
    spans = assemble_foreign_spans(utts, lid, p)
    assert spans == [Span(start_ms=0, end_ms=6000)]  # merged; blip dropped


def test_gap_larger_than_merge_gap_stays_separate():
    p = ForcedSegmentParams(min_span_s=2.0, merge_gap_s=1.0)
    utts = [(0.0, 3.0), (10.0, 13.0)]
    lid = _lid({utts[0]: ("fr", 0.9), utts[1]: ("fr", 0.9)})
    spans = assemble_foreign_spans(utts, lid, p)
    assert spans == [Span(0, 3000), Span(10000, 13000)]


def test_mostly_foreign_bail_predicate():
    p = ForcedSegmentParams(mostly_foreign_fraction=0.5)
    utts = [(0.0, 3.0), (3.0, 6.0), (6.0, 9.0)]
    lid = _lid({utts[0]: ("fr", 0.9), utts[1]: ("fr", 0.9), utts[2]: ("en", 0.9)})
    classified = classify_utterances(utts, lid, p)
    assert round(foreign_fraction(classified), 3) == round(6.0 / 9.0, 3)
    assert is_mostly_foreign(classified, p) is True
    # A single short foreign scene in a long English file does NOT bail.
    utts2 = [(0.0, 60.0), (60.0, 63.0)]
    lid2 = _lid({utts2[0]: ("en", 0.9), utts2[1]: ("fr", 0.9)})
    assert is_mostly_foreign(classify_utterances(utts2, lid2, p), p) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_forced_segment_detector.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'subarr.forced_segment'`.

- [ ] **Step 3: Write minimal implementation**

Create `src/subarr/forced_segment.py`:

```python
"""#364 slice 1 — forced-segment detection: pure core + thin I/O helpers.

subarr owns the detection intelligence; subgen stays a thin primitive. This
module is deliberately split into a PURE core (utterance classification, span
merge, mostly-foreign bail, the gate predicate, the SRT emitter — all unit-
tested with no subgen and no audio) and thin I/O wrappers (VAD adapter, ffmpeg
clip) that mirror the existing subarr subprocess/VAD patterns.

All granularity/bias values are named, tunable ForcedSegmentParams — never magic
numbers. Slice 1 is English-primary (primary_lang='en'); slice 3 generalises the
gate to the file's real audio language.
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from typing import Callable

log = logging.getLogger(__name__)

# A speech utterance is (start_s, end_s); a LID call returns (lang|None, confidence 0..1).
Utterance = tuple[float, float]
LidFn = Callable[[Utterance], "tuple[str | None, float]"]

ENGLISH_TAGS = {"en", "eng"}


@dataclass(frozen=True)
class Span:
    """A merged foreign span in absolute file time (milliseconds)."""

    start_ms: int
    end_ms: int

    @property
    def duration_ms(self) -> int:
        return max(0, self.end_ms - self.start_ms)


@dataclass(frozen=True)
class ForcedSegmentParams:
    # Detection granularity + bias. Opt-in feature, so thresholds bias toward
    # OVER-flagging: a false positive costs a few GPU seconds + maybe a spurious
    # cue; a false negative loses the scene the user turned this on for.
    primary_lang: str = "en"  # slice 1: English-primary; slice 3 makes this the real audio lang
    min_span_s: float = 2.5  # min-duration output floor (spoken-LID needs ~2-3s to be sure)
    merge_gap_s: float = 1.5  # merge foreign spans separated by <= this so one convo is one cue-set
    conf_floor: float = 0.5  # below this, LID is "uncertain"
    over_flag_low_confidence: bool = True  # uncertain -> treat as foreign (completeness bias)
    mostly_foreign_fraction: float = 0.5  # > this fraction of speech foreign => bail (not a forced case)
    # Overlap tiling for a long continuous utterance that straddles a language
    # switch (spec item 3): tile windows with ~50% stride so a boundary is never
    # hidden at a window edge. 0 disables tiling (slice-1 default keeps it simple
    # — VAD utterances are already pause-bounded; tiling is opt-in tuning).
    max_utterance_s: float = 0.0
    overlap_stride_s: float = 15.0


def _is_english(lang: str | None, params: ForcedSegmentParams) -> bool:
    return bool(lang) and lang.lower() in {params.primary_lang, "eng"} | ENGLISH_TAGS


def classify_utterances(
    utterances: list[Utterance], lid: LidFn, params: ForcedSegmentParams
) -> list[tuple[Utterance, bool]]:
    """Label each utterance foreign (True) / not (False). Foreign iff a
    non-English language was detected at any confidence, OR (over-flag bias) the
    LID was uncertain (confidence < conf_floor). Confident English is not
    foreign."""
    out: list[tuple[Utterance, bool]] = []
    for utt in utterances:
        lang, conf = lid(utt)
        if lang is not None and not _is_english(lang, params):
            foreign = True
        elif params.over_flag_low_confidence and conf < params.conf_floor:
            foreign = True  # uncertain -> suspect (bias to completeness)
        else:
            foreign = False
        out.append((utt, foreign))
    return out


def foreign_fraction(classified: list[tuple[Utterance, bool]]) -> float:
    """Foreign speech seconds / total speech seconds. 0.0 when there is no speech."""
    total = sum(e - s for (s, e), _ in classified)
    if total <= 0:
        return 0.0
    foreign = sum(e - s for (s, e), is_f in classified if is_f)
    return foreign / total


def is_mostly_foreign(classified: list[tuple[Utterance, bool]], params: ForcedSegmentParams) -> bool:
    """True when more than mostly_foreign_fraction of speech is foreign — this is
    a full-transcription / mistagged-audio situation, NOT a forced-segment case.
    The orchestrator bails (emits nothing) and records the result."""
    return foreign_fraction(classified) > params.mostly_foreign_fraction


def merge_foreign_spans(
    classified: list[tuple[Utterance, bool]], params: ForcedSegmentParams
) -> list[Span]:
    """Foreign utterances -> merged Spans (absolute ms). Consecutive foreign
    spans within merge_gap_s fuse; merged spans shorter than min_span_s are
    dropped by the output floor."""
    foreign = sorted([(s, e) for (s, e), is_f in classified if is_f])
    if not foreign:
        return []
    merged: list[list[float]] = [list(foreign[0])]
    for s, e in foreign[1:]:
        if s - merged[-1][1] <= params.merge_gap_s:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    out: list[Span] = []
    for s, e in merged:
        if (e - s) >= params.min_span_s:
            out.append(Span(start_ms=int(round(s * 1000)), end_ms=int(round(e * 1000))))
    return out


def assemble_foreign_spans(
    utterances: list[Utterance], lid: LidFn, params: ForcedSegmentParams
) -> list[Span]:
    """Full pure pipeline: classify then merge. Convenience entry point."""
    return merge_foreign_spans(classify_utterances(utterances, lid, params), params)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_forced_segment_detector.py -q`
Expected: PASS (6 passed).

- [ ] **Step 5: Commit**

```bash
git add src/subarr/forced_segment.py tests/test_forced_segment_detector.py
git commit -m "feat(364): pure forced-segment detector core (classify, merge, mostly-foreign bail)"
```

---

### Task 2: Span → forced `.forced.en.srt` emitter (pure)

**Files:**
- Modify: `src/subarr/forced_segment.py`
- Test: `tests/test_forced_segment_srt.py`

Reuses `Cue` (`subtitle_readability.py:50`) + `render_srt` (`subtitle_retime.py:58`) so the SRT wire format is identical to the rest of subarr. The forced flag is carried by the **filename** (`.forced.en.srt`), which Bazarr/Plex recognise — not by cue content.

- [ ] **Step 1: Write the failing test**

```python
"""#364 slice 1 — span+text cues -> a forced .forced.en.srt string. Absolute
timing, 1..N re-indexed, multi-line preserved."""

from __future__ import annotations

from subarr.forced_segment import build_forced_srt


def test_builds_absolute_timed_reindexed_srt():
    cues = [(60000, 63000, "Hello there"), (600000, 604000, "Line one\nLine two")]
    srt = build_forced_srt(cues)
    assert srt == (
        "1\n00:01:00,000 --> 00:01:03,000\nHello there\n\n"
        "2\n00:10:00,000 --> 00:10:04,000\nLine one\nLine two\n"
    )


def test_empty_cues_render_empty_string():
    assert build_forced_srt([]) == ""


def test_blank_and_whitespace_text_lines_are_dropped():
    srt = build_forced_srt([(0, 2000, "  keep  \n\n  ")])
    assert srt == "1\n00:00:00,000 --> 00:00:02,000\nkeep\n"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_forced_segment_srt.py -q`
Expected: FAIL with `ImportError: cannot import name 'build_forced_srt'`.

- [ ] **Step 3: Write minimal implementation**

Append to `src/subarr/forced_segment.py`:

```python
def build_forced_srt(cues: list[tuple[int, int, str]]) -> str:
    """(start_ms, end_ms, text) cues -> a forced SRT string, 1..N re-indexed,
    absolute-timed. Reuses the shared Cue + render_srt so the wire format matches
    every other subarr-emitted .srt. The `.forced` marker lives in the FILENAME
    (<basename>.forced.en.srt), which Bazarr/Plex recognise — cue content is
    plain. Blank/whitespace lines are stripped so an empty cue never renders."""
    from .subtitle_readability import Cue
    from .subtitle_retime import render_srt

    built: list[Cue] = []
    for start_ms, end_ms, text in cues:
        lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
        built.append(Cue(index=0, start_ms=int(start_ms), end_ms=int(end_ms), lines=lines))
    return render_srt(built)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_forced_segment_srt.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add src/subarr/forced_segment.py tests/test_forced_segment_srt.py
git commit -m "feat(364): forced-segment SRT emitter reusing shared Cue/render_srt"
```

---

### Task 3: The gate predicate (pure)

**Files:**
- Modify: `src/subarr/forced_segment.py`
- Test: `tests/test_forced_segment_gate.py`

The cheap filters that decide which files even qualify, BEFORE any audio pass. Pure over explicitly-passed metadata so it is unit-testable; Task 8 resolves the inputs from the real stores. Inputs come from already-read code: `audio_langs` (coverage/probe `audio_lang_summary`, `media_probe.py:320`), `embedded_en` label (`english_track_summary`, `media_probe.py:293` → `'EN(forced)'` etc), `lang_class` (`AudioLangStore.get(...).lang_class`, `audio_lang_store.py:60`), a `has_forced_sidecar` bool (Task 8 checks disk), and `duration_s`.

- [ ] **Step 1: Write the failing test**

```python
"""#364 slice 1 — the gate: English-tagged, not #357-multi, no existing forced,
duration floor. Pure predicate over a file's known metadata."""

from __future__ import annotations

from subarr.forced_segment import ForcedSegmentParams, qualifies_for_forced_segment


def _ok(**over):
    base = dict(
        audio_langs=["en"],
        embedded_en=None,
        lang_class="single",
        has_forced_sidecar=False,
        duration_s=3600.0,
    )
    base.update(over)
    return qualifies_for_forced_segment(params=ForcedSegmentParams(), **base)


def test_english_single_no_forced_qualifies():
    ok, reason = _ok()
    assert ok is True
    assert reason == "ok"


def test_non_english_audio_excluded():
    ok, reason = _ok(audio_langs=["fr"])
    assert ok is False and reason == "not_english_audio"


def test_multilingual_357_excluded():
    ok, reason = _ok(lang_class="multi")
    assert ok is False and reason == "multilingual"


def test_existing_embedded_forced_english_excluded():
    ok, reason = _ok(embedded_en="EN(forced)")
    assert ok is False and reason == "existing_forced"


def test_existing_forced_sidecar_excluded():
    ok, reason = _ok(has_forced_sidecar=True)
    assert ok is False and reason == "existing_forced"


def test_too_short_excluded():
    ok, reason = _ok(duration_s=30.0)
    assert ok is False and reason == "too_short"


def test_eng_three_letter_tag_and_full_en_sub_still_qualify():
    # 'eng' counts as English; a full EN sub (EN) does NOT block a forced sub.
    ok, reason = _ok(audio_langs=["eng"], embedded_en="EN")
    assert ok is True and reason == "ok"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_forced_segment_gate.py -q`
Expected: FAIL with `ImportError: cannot import name 'qualifies_for_forced_segment'`.

- [ ] **Step 3: Write minimal implementation**

Append to `src/subarr/forced_segment.py` (add `MIN_RUNTIME_S` constant near the top params if preferred; kept local here):

```python
# Runtime floor for the gate — a trivially short clip cannot hide a foreign scene
# worth a sidecar. Named, not magic.
GATE_MIN_RUNTIME_S = 120.0


def qualifies_for_forced_segment(
    *,
    audio_langs: list[str] | None,
    embedded_en: str | None,
    lang_class: str | None,
    has_forced_sidecar: bool,
    duration_s: float | None,
    params: ForcedSegmentParams,
    min_runtime_s: float = GATE_MIN_RUNTIME_S,
) -> tuple[bool, str]:
    """Cheap pre-audio filters. Returns (qualifies, reason). A file qualifies iff:
      - its audio is English-tagged (slice 1's primary-language assumption),
      - it is NOT a #357 multilingual file (lang_class != 'multi'),
      - it has NO existing forced English sub (embedded EN(forced) or a
        .forced.en.srt sidecar) — don't redo work, don't clobber,
      - it clears the runtime floor.
    A full (non-forced) English sub does NOT disqualify — that is a different want."""
    langs = {(l or "").lower() for l in (audio_langs or [])}
    if not (langs & ({params.primary_lang} | ENGLISH_TAGS)):
        return False, "not_english_audio"
    if (lang_class or "single") == "multi":
        return False, "multilingual"
    if embedded_en == "EN(forced)" or has_forced_sidecar:
        return False, "existing_forced"
    if duration_s is not None and duration_s < min_runtime_s:
        return False, "too_short"
    return True, "ok"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_forced_segment_gate.py -q`
Expected: PASS (7 passed).

- [ ] **Step 5: Commit**

```bash
git add src/subarr/forced_segment.py tests/test_forced_segment_gate.py
git commit -m "feat(364): forced-segment gate predicate (english/not-multi/no-forced/floor)"
```

---

### Task 4: Scan-result cache — migration 028 + `ForcedSegmentScanStore`

**Files:**
- Create: `src/subarr/migrations/028_forced_segment_scans.sql`
- Create: `src/subarr/forced_segment_store.py`
- Test: `tests/test_forced_segment_store.py`

Idempotence keyed on `(canonical_path, mtime, size)`, mirroring `probe_store.py` (`get(canonical, mtime, size)` returns None on any mismatch → caller re-scans). Highest existing migration is `027_audio_lang_multilingual.sql`, so the next number is **028**.

- [ ] **Step 1: Write the failing test**

```python
"""#364 slice 1 — scan-result cache. Migration 028 applies cleanly; the store
records scanned/none/bailed keyed on (canonical_path, mtime, size), and a stale
mtime/size is a miss (re-scan)."""

from __future__ import annotations

import sqlite3

import pytest

from subarr.migrate import run_migrations
from subarr.forced_segment_store import ForcedSegmentScanStore


def test_migration_028_creates_table(tmp_path):
    db = tmp_path / "subarr.db"
    run_migrations(db)  # 001..028
    conn = sqlite3.connect(str(db))
    cols = {r[1] for r in conn.execute("PRAGMA table_info(forced_segment_scans)")}
    conn.close()
    assert {"canonical_path", "mtime", "size", "status", "n_spans", "total_ms", "scanned_at"} <= cols


def test_migration_028_idempotent(tmp_path):
    db = tmp_path / "subarr.db"
    run_migrations(db)
    assert run_migrations(db) == []


def _store(tmp_path):
    db = tmp_path / "subarr.db"
    run_migrations(db)
    return ForcedSegmentScanStore(db)


def test_hit_only_on_matching_mtime_and_size(tmp_path):
    s = _store(tmp_path)
    s.upsert(canonical_path="TV/S/ep.mkv", mtime=100.0, size=42, status="scanned", n_spans=2, total_ms=14000)
    hit = s.get("TV/S/ep.mkv", mtime=100.0, size=42)
    assert hit is not None and hit.status == "scanned" and hit.n_spans == 2 and hit.total_ms == 14000
    assert s.get("TV/S/ep.mkv", mtime=200.0, size=42) is None  # stale mtime -> miss
    assert s.get("TV/S/ep.mkv", mtime=100.0, size=99) is None  # changed size -> miss
    assert s.get("Other/x.mkv", mtime=1.0, size=1) is None


def test_upsert_replaces_prior_verdict(tmp_path):
    s = _store(tmp_path)
    s.upsert(canonical_path="TV/S/ep.mkv", mtime=1.0, size=1, status="none", n_spans=0, total_ms=0)
    s.upsert(canonical_path="TV/S/ep.mkv", mtime=2.0, size=2, status="bailed", n_spans=0, total_ms=0)
    hit = s.get("TV/S/ep.mkv", mtime=2.0, size=2)
    assert hit.status == "bailed"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_forced_segment_store.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'subarr.forced_segment_store'` (and, if the migration file is missing first, a table-not-found on the migration test).

- [ ] **Step 3a: Write the migration**

Create `src/subarr/migrations/028_forced_segment_scans.sql`:

```sql
-- #364 slice 1 — forced-segment scan-result cache (idempotence).
--
-- One row per file (keyed by canonical_path). The (mtime, size) pair makes the
-- deep foreign-scene scan idempotent: the manual walker AND the at-import hook
-- both consult this store, so an unchanged file is never re-scanned and GPU is
-- never re-burned. A changed file (new mtime/size) is a miss and re-scans.
-- Mirrors media_probe's cache-key discipline (probe_store.py).
--
-- status: 'scanned' (>=1 forced span emitted) | 'none' (qualified, no foreign
-- scene found) | 'bailed' (mostly-foreign / mistagged — recorded, nothing
-- emitted). n_spans/total_ms carry the light Aftercare-note summary.
CREATE TABLE IF NOT EXISTS forced_segment_scans (
    canonical_path  TEXT PRIMARY KEY,
    mtime           REAL,
    size            INTEGER,
    status          TEXT NOT NULL,
    n_spans         INTEGER NOT NULL DEFAULT 0,
    total_ms        INTEGER NOT NULL DEFAULT 0,
    scanned_at      REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_forced_segment_scans_status ON forced_segment_scans (status);
```

- [ ] **Step 3b: Write the store**

Create `src/subarr/forced_segment_store.py`:

```python
"""#364 slice 1 — SQLite cache for forced-segment scan results.

Keyed by canonical_path; a row is a cache HIT only when the stored (mtime, size)
still match the file on disk (mirrors probe_store.py). Schema is owned by
migrations/028_forced_segment_scans.sql — run_migrations() runs at boot before
this store is constructed, so there is no per-store init_schema(). Own
connection, WAL, lock, autocommit — background-walk writes never contend with
HTTP-request writes.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ForcedSegmentScan:
    canonical_path: str
    mtime: float | None
    size: int | None
    status: str  # scanned | none | bailed
    n_spans: int
    total_ms: int
    scanned_at: float


class ForcedSegmentScanStore:
    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False, isolation_level=None)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def get(
        self, canonical_path: str, mtime: float | None = None, size: int | None = None
    ) -> ForcedSegmentScan | None:
        """Return the cached scan only if the supplied (mtime, size) still match
        (mtime compared with a 1s tolerance, mirroring probe_store). Any mismatch
        is a miss so the caller re-scans."""
        with self._lock:
            row = self._conn.execute(
                "SELECT canonical_path, mtime, size, status, n_spans, total_ms, scanned_at "
                "FROM forced_segment_scans WHERE canonical_path = ?",
                (canonical_path,),
            ).fetchone()
        if not row:
            return None
        if mtime is not None and (row["mtime"] is None or abs(mtime - row["mtime"]) > 1):
            return None
        if size is not None and size != row["size"]:
            return None
        return ForcedSegmentScan(
            canonical_path=row["canonical_path"],
            mtime=row["mtime"],
            size=row["size"],
            status=row["status"],
            n_spans=row["n_spans"],
            total_ms=row["total_ms"],
            scanned_at=row["scanned_at"],
        )

    def upsert(
        self,
        *,
        canonical_path: str,
        mtime: float | None,
        size: int | None,
        status: str,
        n_spans: int,
        total_ms: int,
    ) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO forced_segment_scans "
                "(canonical_path, mtime, size, status, n_spans, total_ms, scanned_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(canonical_path) DO UPDATE SET "
                "  mtime=excluded.mtime, size=excluded.size, status=excluded.status, "
                "  n_spans=excluded.n_spans, total_ms=excluded.total_ms, scanned_at=excluded.scanned_at",
                (canonical_path, mtime, size, status, n_spans, total_ms, time.time()),
            )

    def summary(self) -> dict:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS n, MAX(scanned_at) AS last, "
                "COALESCE(SUM(n_spans), 0) AS spans FROM forced_segment_scans"
            ).fetchone()
        return {"total_scanned": row["n"] or 0, "last_scanned_at": row["last"], "total_spans": row["spans"] or 0}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_forced_segment_store.py -q`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add src/subarr/migrations/028_forced_segment_scans.sql src/subarr/forced_segment_store.py tests/test_forced_segment_store.py
git commit -m "feat(364): forced-segment scan-result cache (migration 028 + store)"
```

---

### Task 5: Config toggle `SUBARR_FORCED_SEGMENT_ENABLED` (off by default)

**Files:**
- Modify: `src/subarr/config.py` (`Settings` field near `:106`; `load()` near `:285`; `FIELD_ENV_VARS` `:494`; `_FIELD_COERCE` `:525`)
- Test: `tests/test_config_forced_segment.py`

Mirrors the `retime_enabled` idiom (`config.py:106`, `:285`, `test_config_retime.py`) but defaults **OFF** — the skip-English optimisation must be untouched for everyone who does not opt in. The tunable detector params live in `ForcedSegmentParams` (module constants, Task 1); only the enable toggle is config.

- [ ] **Step 1: Write the failing test**

```python
"""#364 slice 1 — SUBARR_FORCED_SEGMENT_ENABLED. OFF by default (the skip-English
optimisation stays intact); env-togglable; mirrors test_config_retime.py."""

from __future__ import annotations

import importlib

from subarr import config


def test_forced_segment_defaults_off(monkeypatch):
    monkeypatch.delenv("SUBARR_FORCED_SEGMENT_ENABLED", raising=False)
    importlib.reload(config)
    assert config.settings.forced_segment_enabled is False


def test_forced_segment_on_via_env(monkeypatch):
    monkeypatch.setenv("SUBARR_FORCED_SEGMENT_ENABLED", "1")
    importlib.reload(config)
    assert config.settings.forced_segment_enabled is True
    monkeypatch.setenv("SUBARR_FORCED_SEGMENT_ENABLED", "true")
    importlib.reload(config)
    assert config.settings.forced_segment_enabled is True


def test_forced_segment_off_via_env(monkeypatch):
    monkeypatch.setenv("SUBARR_FORCED_SEGMENT_ENABLED", "0")
    importlib.reload(config)
    assert config.settings.forced_segment_enabled is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_config_forced_segment.py -q`
Expected: FAIL with `AttributeError: 'Settings' object has no attribute 'forced_segment_enabled'`.

- [ ] **Step 3: Add the field, loader, and UI-settable wiring**

In `src/subarr/config.py`, add the field to `Settings` immediately after the `retime_enabled` field (after `config.py:106`):

```python
    # #364: opt-in "Deep-scan English files for foreign scenes" — drives the
    # forced-segment walker + the at-import hook. OFF by default so the skip-
    # English optimisation is byte-for-byte unchanged for everyone who does not
    # opt in. Set SUBARR_FORCED_SEGMENT_ENABLED=1 to enable.
    forced_segment_enabled: bool
```

In `load()`, add immediately after the `retime_enabled=` block (after `config.py:286`):

```python
        # #364: default OFF (opt-in GPU-spending pipeline).
        forced_segment_enabled=os.environ.get("SUBARR_FORCED_SEGMENT_ENABLED", "0").strip().lower()
        in ("1", "true", "yes", "on"),
```

In `FIELD_ENV_VARS` (`config.py:494`, after the `retime_enabled` entry):

```python
    "forced_segment_enabled": "SUBARR_FORCED_SEGMENT_ENABLED",
```

In `_FIELD_COERCE` (`config.py:525`, alongside the other bool toggles):

```python
    "forced_segment_enabled": _coerce_bool,
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_config_forced_segment.py -q`
Expected: PASS (3 passed).

Then guard against a positional-arg breakage in the frozen dataclass:

Run: `python -m pytest tests/test_config_retime.py tests/test_config_libraries.py -q`
Expected: PASS (existing config tests still green).

- [ ] **Step 5: Commit**

```bash
git add src/subarr/config.py tests/test_config_forced_segment.py
git commit -m "feat(364): SUBARR_FORCED_SEGMENT_ENABLED config toggle (off by default)"
```

---

### Task 6: VAD → utterances adapter + ffmpeg clip helper

**Files:**
- Modify: `src/subarr/forced_segment.py`
- Test: `tests/test_forced_segment_detector.py` (append)

Thin wrappers over the existing patterns: `vad.detect_speech_ranges(path, track)` (`vad.py:161`, returns normalized `list[(start_s, end_s)]` or `None` when VAD unavailable) and the `arena_sampler._cut_clip` ffmpeg pattern (`arena_sampler.py:103`: `-ss <start> -i <path> -t <length> -map 0:a:<track> -ar 16000 -ac 1`, `subprocess.run(..., check=True, stderr=PIPE)`). Tested with the VAD monkeypatched and the subprocess call captured.

- [ ] **Step 1: Write the failing test (append to `tests/test_forced_segment_detector.py`)**

```python
def test_detect_utterances_wraps_vad(monkeypatch):
    from subarr import forced_segment as fs

    monkeypatch.setattr(fs, "_vad_speech_ranges", lambda path, track: [(1.0, 4.0), (5.0, 6.0)])
    assert fs.detect_utterances("/media/x.mkv", track=0) == [(1.0, 4.0), (5.0, 6.0)]


def test_detect_utterances_none_when_vad_unavailable(monkeypatch):
    from subarr import forced_segment as fs

    monkeypatch.setattr(fs, "_vad_speech_ranges", lambda path, track: None)
    assert fs.detect_utterances("/media/x.mkv") == []


def test_clip_audio_builds_expected_ffmpeg_command(monkeypatch):
    from subarr import forced_segment as fs

    captured = {}

    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        captured["kw"] = kw

        class _R:
            returncode = 0

        return _R()

    monkeypatch.setattr(fs.subprocess, "run", fake_run)
    fs.clip_audio("/media/x.mkv", 12.5, 17.0, "/tmp/utt.wav", track=1)
    cmd = captured["cmd"]
    assert "-ss" in cmd and cmd[cmd.index("-ss") + 1] == "12.5"
    assert "-t" in cmd and cmd[cmd.index("-t") + 1] == "4.5"  # length = end - start
    assert "0:a:1" in cmd and "16000" in cmd and cmd[-1] == "/tmp/utt.wav"
    assert captured["kw"].get("check") is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_forced_segment_detector.py -q`
Expected: FAIL with `AttributeError: module 'subarr.forced_segment' has no attribute '_vad_speech_ranges'`.

- [ ] **Step 3: Write minimal implementation**

Append to `src/subarr/forced_segment.py`:

```python
def _vad_speech_ranges(path: str, track: int) -> "list[Utterance] | None":
    """Indirection point so tests can inject speech boundaries without a real
    silero model. Delegates to the shipped VAD (vad.detect_speech_ranges returns
    normalized, gap-merged, min-speech-filtered ranges, or None when VAD is
    unavailable — no model pulled / onnxruntime missing)."""
    from . import vad

    return vad.detect_speech_ranges(path, track=track)


def detect_utterances(fs_path: str, track: int = 0) -> list[Utterance]:
    """VAD-segment the audio into speech utterances (start_s, end_s). Returns []
    when VAD is unavailable — the orchestrator treats an empty utterance list as
    'cannot detect' and records nothing rather than guessing."""
    ranges = _vad_speech_ranges(fs_path, track)
    return list(ranges) if ranges else []


def clip_audio(fs_path: str, start_s: float, end_s: float, out_path: str, track: int = 0) -> None:
    """Extract [start_s, end_s] of audio stream `track` -> 16 kHz mono wav at
    out_path (audio-only keeps the subgen upload tiny). Mirrors the arena
    sampler's ffmpeg invocation (arena_sampler._cut_clip): -ss/-t seek+length,
    -map 0:a:N, -ar 16000 -ac 1, check=True with stderr captured. Raises
    subprocess.CalledProcessError on ffmpeg failure — the orchestrator catches
    per-utterance so one bad clip never aborts the file."""
    length = max(0.0, end_s - start_s)
    cmd = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        str(start_s),
        "-i",
        fs_path,
        "-t",
        str(length),
        "-map",
        f"0:a:{track}",
        "-ar",
        "16000",
        "-ac",
        "1",
        out_path,
    ]
    subprocess.run(cmd, check=True, stderr=subprocess.PIPE)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_forced_segment_detector.py -q`
Expected: PASS (9 passed — 6 from Task 1 plus 3 new).

- [ ] **Step 5: Commit**

```bash
git add src/subarr/forced_segment.py tests/test_forced_segment_detector.py
git commit -m "feat(364): VAD->utterances adapter and ffmpeg clip helper"
```

---

### Task 7: subgen LID + translate adapters

**Files:**
- Create: `src/subarr/forced_segment_service.py`
- Test: `tests/test_forced_segment_adapters.py`

Two async adapters bound to a `SubgenClient`. **Translate** always uploads (`asr(local_file=clip, task="translate")`, `subgen_client.py:572`) — no shared fs needed. **LID** honours the Task 0 branch: if a subgen-visible clip path is provided, call `detect_language_robust(path=...)` and derive confidence from `parse_robust_detect` (`arena.py:65`); otherwise upload via `asr(local_file=clip, task="transcribe", return_language=True)` and treat confidence as `1.0` (over-flag bias compensates for the missing probability).

- [ ] **Step 1: Write the failing test**

```python
"""#364 slice 1 — subgen LID + translate adapters. Fake subgen: /asr returns
text (+ optional X-Detected-Language), /detect_language_robust returns the
robust aggregate. No real Whisper."""

from __future__ import annotations

import httpx
import pytest

from subarr.subgen_client import SubgenClient
from subarr.forced_segment_service import subgen_lid, subgen_translate


def _subgen(handler):
    c = SubgenClient(base_url="http://subgen.test:9000")
    c._client = httpx.AsyncClient(base_url="http://subgen.test:9000", transport=httpx.MockTransport(handler))
    return c


@pytest.mark.asyncio
async def test_translate_uploads_clip_and_returns_text(tmp_path):
    clip = tmp_path / "u.wav"
    clip.write_bytes(b"wavbytes")
    seen = {}

    def h(req):
        seen["path"] = req.url.path
        seen["task"] = req.url.params.get("task")
        seen["ct"] = req.headers.get("content-type", "")
        return httpx.Response(200, text="1\n00:00:00,000 --> 00:00:02,000\nHello\n")

    c = _subgen(h)
    text = await subgen_translate(c, str(clip))
    await c.aclose()
    assert seen["path"] == "/asr" and seen["task"] == "translate"
    assert "multipart/form-data" in seen["ct"]
    assert "Hello" in text


@pytest.mark.asyncio
async def test_lid_branch_a_path_visible_uses_detect_robust(tmp_path):
    seen = {}

    def h(req):
        seen["path"] = req.url.path
        seen["param_path"] = req.url.params.get("path")
        return httpx.Response(200, json={"aggregate": {"language": "fr", "n_agreeing": 3, "n_total": 3}, "chunks": []})

    c = _subgen(h)
    lang, conf = await subgen_lid(c, str(tmp_path / "u.wav"), subgen_clip_path="/media-scratch/u.wav")
    await c.aclose()
    assert seen["path"] == "/detect_language_robust"
    assert seen["param_path"] == "/media-scratch/u.wav"
    assert lang == "fr" and conf == 1.0  # 3/3 agreeing


@pytest.mark.asyncio
async def test_lid_branch_b_upload_uses_asr_detected_language(tmp_path):
    clip = tmp_path / "u.wav"
    clip.write_bytes(b"wavbytes")
    seen = {}

    def h(req):
        seen["path"] = req.url.path
        seen["task"] = req.url.params.get("task")
        return httpx.Response(200, text="bonjour\n", headers={"X-Detected-Language": "fr"})

    c = _subgen(h)
    lang, conf = await subgen_lid(c, str(clip), subgen_clip_path=None)
    await c.aclose()
    assert seen["path"] == "/asr" and seen["task"] == "transcribe"
    assert lang == "fr" and conf == 1.0


@pytest.mark.asyncio
async def test_lid_none_confidence_when_detect_returns_nothing(tmp_path):
    def h(req):
        return httpx.Response(200, json={"aggregate": {}, "chunks": []})  # n_total 0 -> parse returns None

    c = _subgen(h)
    lang, conf = await subgen_lid(c, str(tmp_path / "u.wav"), subgen_clip_path="/media-scratch/u.wav")
    await c.aclose()
    assert lang is None and conf == 0.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_forced_segment_adapters.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'subarr.forced_segment_service'`.

- [ ] **Step 3: Write minimal implementation**

Create `src/subarr/forced_segment_service.py` (adapters only for now; the generator + walker are added in Tasks 8 and 9):

```python
"""#364 slice 1 — forced-segment orchestration + subgen adapters.

The adapters are the ONLY subgen-touching code. LID honours the Task 0 branch;
translate always uploads. Both are async and injected into the generator so the
detector and orchestration stay testable with fakes.
"""

from __future__ import annotations

import logging

from .arena import parse_robust_detect
from .subgen_client import SubgenClient, SubgenUnavailable

log = logging.getLogger(__name__)


async def subgen_lid(
    subgen: SubgenClient, clip_path: str, *, subgen_clip_path: str | None
) -> "tuple[str | None, float]":
    """Language-ID one utterance clip -> (lang|None, confidence 0..1).

    Branch A (subgen_clip_path given — the clip is on a subgen-visible mount):
    detect_language_robust(path=...) — cheapest, the spec's tier-1 choice.
    Confidence = n_agreeing / n_total from the robust aggregate.

    Branch B (no shared path): upload via asr(task='transcribe',
    return_language=True) and read the detected-language header; /asr returns no
    probability, so confidence is 1.0 and the detector's over-flag bias covers
    the uncertainty. Returns (None, 0.0) when subgen could not decide."""
    try:
        if subgen_clip_path is not None:
            resp = await subgen.detect_language_robust(subgen_clip_path)
            d = parse_robust_detect(resp)
            if not d or not d.get("language"):
                return None, 0.0
            n_tot = int(d.get("n_total") or 0)
            n_ag = int(d.get("n_agreeing") or 0)
            conf = (n_ag / n_tot) if n_tot else 0.0
            return d["language"], conf
        text, lang = await subgen.asr(
            local_file=clip_path, task="transcribe", return_language=True
        )
        return (lang or None), (1.0 if lang else 0.0)
    except SubgenUnavailable as e:
        log.warning("forced-segment LID failed for a clip: %s", e)
        return None, 0.0


async def subgen_translate(subgen: SubgenClient, clip_path: str) -> str:
    """Transcribe+translate one foreign span clip to English text (uploads the
    clip; no shared fs needed). Returns '' if subgen produced nothing."""
    try:
        return await subgen.asr(local_file=clip_path, task="translate") or ""
    except SubgenUnavailable as e:
        log.warning("forced-segment translate failed for a clip: %s", e)
        return ""
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_forced_segment_adapters.py -q`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add src/subarr/forced_segment_service.py tests/test_forced_segment_adapters.py
git commit -m "feat(364): subgen LID + translate adapters (branch A path / branch B upload)"
```

---

### Task 8: Orchestration — end-to-end pipeline with a fake subgen

**Files:**
- Modify: `src/subarr/forced_segment_service.py`
- Test: `tests/test_forced_segment_service.py`

`ForcedSegmentGenerator.process(canonical_path)`: scan-cache check → gate → VAD utterances → per-utterance clip + injected LID → merge → mostly-foreign bail → per-span clip + injected translate → absolute-timed cues → `build_forced_srt` → **path-contained, no-clobber** write via `canonical_to_fs` (`paths.py:80`) → record scan cache → return a summary. VAD/clip/LID/translate/gate-inputs are all injected so the test drives it with no audio, no ffmpeg, no subgen.

- [ ] **Step 1: Write the failing test**

```python
"""#364 slice 1 — orchestration end-to-end with injected VAD/clip/LID/translate
and a fake gate-input resolver. No audio, no ffmpeg, no real subgen."""

from __future__ import annotations

import importlib

import pytest


@pytest.fixture
def gen(subarr_env, tmp_path):
    # subarr_env sets SUBARR_MEDIA_ROOT to a tmp media root with TV/Show/ep.mkv.
    from subarr import config, paths
    from subarr.migrate import run_migrations
    from subarr.forced_segment import ForcedSegmentParams
    from subarr.forced_segment_store import ForcedSegmentScanStore
    from subarr import forced_segment_service as svc

    importlib.reload(config)
    importlib.reload(paths)
    importlib.reload(svc)
    db = config.settings.db_path
    run_migrations(db)
    store = ForcedSegmentScanStore(db)

    def make(*, utterances, lid_map, translate_map, gate=(True, "ok"), duration=3600.0):
        g = svc.ForcedSegmentGenerator(
            subgen=object(),  # unused: LID/translate are injected below
            scan_store=store,
            params=ForcedSegmentParams(min_span_s=2.0, merge_gap_s=1.0, mostly_foreign_fraction=0.6),
            vad_fn=lambda fs_path, track=0: utterances,
            clip_fn=lambda fs_path, s, e, out, track=0: None,  # no real ffmpeg
            lid_fn=lambda clip_path, span: lid_map[span],
            translate_fn=lambda clip_path, span: translate_map[span],
            gate_fn=lambda canonical: (gate[0], gate[1], duration, 42),  # (ok, reason, duration_s, size)
        )
        return g, store

    return make


@pytest.mark.asyncio
async def test_generates_forced_sidecar_for_a_foreign_scene(gen):
    from subarr.paths import canonical_to_fs

    utts = [(0.0, 60.0), (60.0, 63.0), (63.2, 66.0)]
    g, store = gen(
        utterances=utts,
        lid_map={(0.0, 60.0): ("en", 0.95), (60.0, 63.0): ("fr", 0.9), (63.2, 66.0): ("fr", 0.9)},
        translate_map={(60.0, 63.0): "Come with me", (63.2, 66.0): "Now"},
    )
    result = await g.process("TV/Show/ep.mkv")
    assert result["status"] == "scanned" and result["n_spans"] == 1
    sidecar = canonical_to_fs("TV/Show/ep.mkv").with_name("ep.forced.en.srt")
    assert sidecar.exists()
    body = sidecar.read_text(encoding="utf-8")
    assert "00:01:00,000 --> 00:01:06,000" in body  # merged 60s..66s span, absolute time
    assert "Come with me" in body and "Now" in body
    # cache records the verdict keyed on mtime/size
    assert store.get("TV/Show/ep.mkv", mtime=canonical_to_fs("TV/Show/ep.mkv").stat().st_mtime, size=42) is not None


@pytest.mark.asyncio
async def test_no_foreign_scene_writes_nothing_and_records_none(gen):
    from subarr.paths import canonical_to_fs

    utts = [(0.0, 60.0)]
    g, store = gen(utterances=utts, lid_map={(0.0, 60.0): ("en", 0.95)}, translate_map={})
    result = await g.process("TV/Show/ep.mkv")
    assert result["status"] == "none" and result["n_spans"] == 0
    assert not canonical_to_fs("TV/Show/ep.mkv").with_name("ep.forced.en.srt").exists()


@pytest.mark.asyncio
async def test_mostly_foreign_bails_without_writing(gen):
    from subarr.paths import canonical_to_fs

    utts = [(0.0, 40.0), (40.0, 80.0)]
    g, store = gen(
        utterances=utts,
        lid_map={(0.0, 40.0): ("fr", 0.9), (40.0, 80.0): ("fr", 0.9)},
        translate_map={},
    )
    result = await g.process("TV/Show/ep.mkv")
    assert result["status"] == "bailed"
    assert not canonical_to_fs("TV/Show/ep.mkv").with_name("ep.forced.en.srt").exists()


@pytest.mark.asyncio
async def test_never_clobbers_an_existing_forced_sidecar(gen):
    from subarr.paths import canonical_to_fs

    sidecar = canonical_to_fs("TV/Show/ep.mkv").with_name("ep.forced.en.srt")
    sidecar.write_text("PRE-EXISTING", encoding="utf-8")
    g, store = gen(
        utterances=[(0.0, 60.0), (60.0, 63.0)],
        lid_map={(0.0, 60.0): ("en", 0.95), (60.0, 63.0): ("fr", 0.9)},
        translate_map={(60.0, 63.0): "hi"},
        gate=(False, "existing_forced"),
    )
    result = await g.process("TV/Show/ep.mkv")
    assert result["status"] == "skipped" and result["reason"] == "existing_forced"
    assert sidecar.read_text(encoding="utf-8") == "PRE-EXISTING"


@pytest.mark.asyncio
async def test_cache_hit_skips_rescan(gen):
    utts = [(0.0, 60.0), (60.0, 63.0)]
    g, store = gen(
        utterances=utts,
        lid_map={(0.0, 60.0): ("en", 0.95), (60.0, 63.0): ("fr", 0.9)},
        translate_map={(60.0, 63.0): "hi"},
    )
    first = await g.process("TV/Show/ep.mkv")
    assert first["status"] == "scanned"
    second = await g.process("TV/Show/ep.mkv")  # unchanged file
    assert second["status"] == "cached"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_forced_segment_service.py -q`
Expected: FAIL with `AttributeError: module 'subarr.forced_segment_service' has no attribute 'ForcedSegmentGenerator'`.

- [ ] **Step 3: Write minimal implementation**

Append to `src/subarr/forced_segment_service.py`:

```python
import os
import tempfile
from pathlib import Path
from typing import Awaitable, Callable

from .forced_segment import (
    ForcedSegmentParams,
    Span,
    assemble_foreign_spans,
    build_forced_srt,
    classify_utterances,
    clip_audio,
    detect_utterances,
    is_mostly_foreign,
)
from .paths import PathOutsideRootError, canonical_to_fs

# Injectable signatures (async LID/translate take a clip path + the source span
# so the fake can key off the span; the real wiring ignores the span arg).
VadFn = Callable[..., "list[tuple[float, float]]"]
ClipFn = Callable[..., None]
LidFn = Callable[[str, "tuple[float, float]"], Awaitable["tuple[str | None, float]"]]
TranslateFn = Callable[[str, "tuple[float, float]"], Awaitable[str]]
# gate_fn(canonical) -> (qualifies, reason, duration_s, size)
GateFn = Callable[[str], "tuple[bool, str, float | None, int | None]"]


class ForcedSegmentGenerator:
    def __init__(
        self,
        *,
        subgen,
        scan_store,
        params: ForcedSegmentParams | None = None,
        vad_fn: VadFn = detect_utterances,
        clip_fn: ClipFn = clip_audio,
        lid_fn: LidFn,
        translate_fn: TranslateFn,
        gate_fn: GateFn,
        aftercare_store=None,
        subgen_scratch_prefix: str | None = None,
    ):
        self._subgen = subgen
        self._store = scan_store
        self._params = params or ForcedSegmentParams()
        self._vad = vad_fn
        self._clip = clip_fn
        self._lid = lid_fn
        self._translate = translate_fn
        self._gate = gate_fn
        self._aftercare = aftercare_store
        # When set, clips are written under a subgen-visible scratch mount so LID
        # can use the cheap path-based detect_language_robust (Task 0 Branch A).
        self._subgen_scratch_prefix = subgen_scratch_prefix

    async def process(self, canonical_path: str) -> dict:
        """Run the full pipeline for one file. Returns a summary dict
        {status, reason?, n_spans, total_ms}. status is one of: cached, skipped,
        none, bailed, scanned, error. Never raises — records + returns."""
        try:
            fs_path = canonical_to_fs(canonical_path)
        except PathOutsideRootError:
            log.warning("forced-segment: unresolvable canonical %s", canonical_path)
            return {"status": "error", "reason": "unresolvable", "n_spans": 0, "total_ms": 0}
        if not fs_path.exists():
            return {"status": "error", "reason": "missing", "n_spans": 0, "total_ms": 0}

        st = fs_path.stat()
        mtime, size = st.st_mtime, st.st_size

        # Idempotence: unchanged (path, mtime, size) is never re-scanned.
        if self._store.get(canonical_path, mtime=mtime, size=size) is not None:
            return {"status": "cached", "n_spans": 0, "total_ms": 0}

        ok, reason, _dur, _size = self._gate(canonical_path)
        if not ok:
            return {"status": "skipped", "reason": reason, "n_spans": 0, "total_ms": 0}

        # No-clobber: a forced sidecar that appeared since the gate check still
        # blocks (defence in depth over the gate's has_forced_sidecar).
        sidecar = fs_path.with_name(fs_path.stem + ".forced.en.srt")
        if sidecar.exists():
            self._store.upsert(
                canonical_path=canonical_path, mtime=mtime, size=size, status="none", n_spans=0, total_ms=0
            )
            return {"status": "skipped", "reason": "existing_forced", "n_spans": 0, "total_ms": 0}

        utterances = self._vad(str(fs_path))
        if not utterances:
            self._store.upsert(
                canonical_path=canonical_path, mtime=mtime, size=size, status="none", n_spans=0, total_ms=0
            )
            return {"status": "none", "reason": "no_speech", "n_spans": 0, "total_ms": 0}

        with tempfile.TemporaryDirectory(prefix="forced-seg-") as tmp:
            classified = await self._classify(str(fs_path), utterances, tmp)
            if is_mostly_foreign(classified, self._params):
                self._store.upsert(
                    canonical_path=canonical_path, mtime=mtime, size=size, status="bailed", n_spans=0, total_ms=0
                )
                log.info("forced-segment: %s is mostly-foreign — bailed (suspect audio lang)", canonical_path)
                return {"status": "bailed", "n_spans": 0, "total_ms": 0}

            from .forced_segment import merge_foreign_spans

            spans = merge_foreign_spans(classified, self._params)
            if not spans:
                self._store.upsert(
                    canonical_path=canonical_path, mtime=mtime, size=size, status="none", n_spans=0, total_ms=0
                )
                return {"status": "none", "n_spans": 0, "total_ms": 0}

            cues = await self._translate_spans(str(fs_path), spans, tmp)

        srt = build_forced_srt(cues)
        if not srt.strip():
            self._store.upsert(
                canonical_path=canonical_path, mtime=mtime, size=size, status="none", n_spans=0, total_ms=0
            )
            return {"status": "none", "n_spans": 0, "total_ms": 0}

        # Path-contained write (canonical_to_fs already guards traversal); atomic.
        tmp_out = sidecar.with_name(sidecar.name + ".tmp")
        tmp_out.write_text(srt, encoding="utf-8")
        os.replace(tmp_out, sidecar)
        total_ms = sum(s.duration_ms for s in spans)
        self._store.upsert(
            canonical_path=canonical_path, mtime=mtime, size=size, status="scanned",
            n_spans=len(spans), total_ms=total_ms,
        )
        self._record_aftercare_note(canonical_path, srt)
        log.info("forced-segment: wrote %s (%d scenes, %dms foreign)", sidecar.name, len(spans), total_ms)
        return {"status": "scanned", "n_spans": len(spans), "total_ms": total_ms}

    async def _classify(self, fs_path: str, utterances, tmp) -> list:
        classified = []
        for i, (s, e) in enumerate(utterances):
            clip = os.path.join(tmp, f"lid-{i}.wav")
            try:
                self._clip(fs_path, s, e, clip)
            except Exception as exc:  # noqa: BLE001 - a bad clip is over-flagged, never fatal
                log.warning("forced-segment: LID clip failed at %.1fs: %s", s, exc)
                classified.append(((s, e), True))  # over-flag on failure (completeness bias)
                continue
            subgen_path = self._to_subgen(clip)
            lang, conf = await self._lid_call(clip, subgen_path, (s, e))
            one = classify_utterances([(s, e)], lambda _u: (lang, conf), self._params)
            classified.append(one[0])
        return classified

    async def _lid_call(self, clip, subgen_path, span):
        # Real wiring passes an async adapter; the test passes an async lambda
        # keyed off the span. Both are awaited here.
        return await self._lid(clip, span)

    async def _translate_spans(self, fs_path: str, spans: "list[Span]", tmp) -> list:
        cues = []
        for i, sp in enumerate(spans):
            clip = os.path.join(tmp, f"tr-{i}.wav")
            try:
                self._clip(fs_path, sp.start_ms / 1000.0, sp.end_ms / 1000.0, clip)
            except Exception as exc:  # noqa: BLE001
                log.warning("forced-segment: translate clip failed for %s: %s", sp, exc)
                continue
            text = await self._translate(clip, (sp.start_ms / 1000.0, sp.end_ms / 1000.0))
            if text and text.strip():
                cues.append((sp.start_ms, sp.end_ms, _srt_text_to_line(text)))
        return cues

    def _to_subgen(self, clip_path: str) -> str | None:
        """Map a local clip path to a subgen-visible path (Branch A) when a
        scratch prefix is configured; else None (Branch B upload)."""
        if not self._subgen_scratch_prefix:
            return None
        return self._subgen_scratch_prefix.rstrip("/") + "/" + Path(clip_path).name

    def _record_aftercare_note(self, canonical_path: str, srt: str) -> None:
        """Light surfacing (Task 11): evaluate the generated sidecar and record
        it in aftercare with a distinct source. Best-effort — LOG on failure,
        never break the write (#416: don't swallow silently)."""
        store = getattr(self, "_aftercare", None)
        if store is None:
            return
        try:
            from .aftercare import evaluate_subtitle
            import time

            ev = evaluate_subtitle(srt, media_duration_s=None)
            store.record(
                canonical_path=canonical_path, completed_at=time.time(), evaluation=ev, source="forced-segment"
            )
        except Exception as e:  # noqa: BLE001 - aftercare note must never break generation
            log.warning("forced-segment aftercare note failed for %s: %s", canonical_path, e)


def _srt_text_to_line(text: str) -> str:
    """subgen /asr returns a full SRT for the clip; collapse its cue text into a
    single line for the merged span cue (slice 1 emits one cue per foreign span,
    not per sub-cue). Strips indices/timestamps."""
    lines = []
    for raw in (text or "").splitlines():
        s = raw.strip()
        if not s or s.isdigit() or "-->" in s:
            continue
        lines.append(s)
    return " ".join(lines)
```

Note for the implementer: the injected `lid_fn`/`translate_fn` in the test are async lambdas keyed on the span; the real wiring (Task 9) binds `subgen_lid`/`subgen_translate` via small async closures. `_srt_text_to_line` flattens subgen's per-clip SRT into the single merged-span cue.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_forced_segment_service.py -q`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add src/subarr/forced_segment_service.py tests/test_forced_segment_service.py
git commit -m "feat(364): forced-segment orchestrator (gate, VAD, LID, bail, translate, no-clobber write, cache)"
```

---

### Task 9: Manual walker (audio-audit pattern, Health-supervised)

**Files:**
- Modify: `src/subarr/forced_segment_service.py`
- Test: `tests/test_forced_segment_walker.py`

`ForcedSegmentWalker` mirrors `AudioAuditWalker` (`audio_audit.py:119`): opt-in `start(scope)`, one file at a time with `_PER_FILE_SLEEP_S` throttle, GPU-polite `_safe_busy()` yielding to live sweeps, resumable (the generator's scan-cache skips unchanged files), `WalkerState` progress, and the #157 `_run` supervision contract (clean completion → `record_success("forced-segment")`; `CancelledError` → cancelled and re-raise WITHOUT recording a failure; other outer exception → `record_failure`). Per-file errors are collected, never abort the run.

- [ ] **Step 1: Write the failing test**

```python
"""#364 slice 1 — ForcedSegmentWalker: resumable trickle, GPU-polite pause,
per-file error isolation, #157 Health record on clean completion + on cancel."""

from __future__ import annotations

import asyncio

import pytest


class _FakeGen:
    def __init__(self):
        self.calls = []

    async def process(self, canonical_path):
        self.calls.append(canonical_path)
        return {"status": "scanned", "n_spans": 1, "total_ms": 3000}


class _FakeHealth:
    def __init__(self):
        self.successes = []
        self.failures = []

    def record_success(self, name):
        self.successes.append(name)

    def record_failure(self, name, exc):
        self.failures.append((name, exc))


def _walker(gen, worklist, busy=None):
    from subarr import forced_segment_service as svc

    w = svc.ForcedSegmentWalker(generator=gen, worklist=lambda scope="library": worklist, busy_check=busy)
    return w


@pytest.mark.asyncio
async def test_walks_every_file_and_records_health(monkeypatch):
    from subarr import forced_segment_service as svc

    monkeypatch.setattr(svc, "_PER_FILE_SLEEP_S", 0)
    gen = _FakeGen()
    health = _FakeHealth()
    w = _walker(gen, ["TV/A.mkv", "TV/B.mkv"])
    w._health = health
    state = await w.start(scope="library")
    await w._task
    assert gen.calls == ["TV/A.mkv", "TV/B.mkv"]
    assert state.processed == 2 and state.found == 2 and state.status == "done"
    assert health.successes == ["forced-segment"]


@pytest.mark.asyncio
async def test_gpu_polite_pauses_while_busy(monkeypatch):
    from subarr import forced_segment_service as svc

    monkeypatch.setattr(svc, "_PER_FILE_SLEEP_S", 0)
    monkeypatch.setattr(svc, "_BUSY_SLEEP_S", 0)
    busy = {"v": True}
    gen = _FakeGen()
    w = _walker(gen, ["TV/A.mkv"], busy=lambda: busy["v"])
    task_state = await w.start(scope="library")
    await asyncio.sleep(0)
    assert gen.calls == []  # paused: no detect fired while busy
    busy["v"] = False
    await w._task
    assert gen.calls == ["TV/A.mkv"] and task_state.status == "done"


@pytest.mark.asyncio
async def test_per_file_error_isolated(monkeypatch):
    from subarr import forced_segment_service as svc

    monkeypatch.setattr(svc, "_PER_FILE_SLEEP_S", 0)

    class _Boom(_FakeGen):
        async def process(self, canonical_path):
            if canonical_path == "TV/A.mkv":
                raise RuntimeError("clip blew up")
            return await super().process(canonical_path)

    gen = _Boom()
    health = _FakeHealth()
    w = _walker(gen, ["TV/A.mkv", "TV/B.mkv"])
    w._health = health
    state = await w.start(scope="library")
    await w._task
    assert state.processed == 2 and len(state.errors) == 1
    assert state.status == "done" and health.successes == ["forced-segment"]  # run still clean
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_forced_segment_walker.py -q`
Expected: FAIL with `AttributeError: module 'subarr.forced_segment_service' has no attribute 'ForcedSegmentWalker'`.

- [ ] **Step 3: Write minimal implementation**

Append to `src/subarr/forced_segment_service.py`:

```python
import asyncio
import time as _time
from dataclasses import dataclass, field
from typing import Any

# Trickle, never burst (mirrors audio_audit._PER_FILE_SLEEP_S / _BUSY_SLEEP_S).
_PER_FILE_SLEEP_S = 1.0
_BUSY_SLEEP_S = 5.0
FORCED_SEGMENT_TASK = "forced-segment"


@dataclass
class WalkerState:
    status: str = "running"  # running | done | cancelled | error
    total: int = 0
    processed: int = 0
    found: int = 0  # files that produced >=1 forced span
    started_at: float = field(default_factory=_time.time)
    finished_at: float | None = None
    error: str | None = None
    errors: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "total": self.total,
            "processed": self.processed,
            "found": self.found,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "error": self.error,
            "errors": self.errors[:10],
        }


class ForcedSegmentWalker:
    """Opt-in, throttled, GPU-polite, resumable deep-scan walker. Resumability is
    delegated to the generator's scan cache (unchanged files return 'cached').
    Supervised on the #157 Health roster as 'forced-segment'."""

    def __init__(self, *, generator: "ForcedSegmentGenerator", worklist, busy_check=None):
        self._gen = generator
        self._worklist = worklist  # worklist(scope) -> [canonical_path, ...]
        self._busy_check = busy_check
        self._state: WalkerState | None = None
        self._task: asyncio.Task | None = None

    def get_state(self) -> WalkerState | None:
        return self._state

    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    def _resolve_worklist(self, scope: str) -> list:
        try:
            return list(self._worklist(scope) or [])
        except TypeError:
            return list(self._worklist() or [])

    async def start(self, scope: str = "library") -> WalkerState:
        if self.is_running():
            raise RuntimeError("forced-segment scan already running")
        try:
            worklist = await asyncio.to_thread(self._resolve_worklist, scope)
        except Exception as e:  # pragma: no cover - defensive
            log.warning("forced-segment: worklist resolution failed: %s", e)
            worklist = []
        state = WalkerState(total=len(worklist))
        self._state = state
        self._task = asyncio.create_task(self._run(state, worklist), name=FORCED_SEGMENT_TASK)
        return state

    async def stop(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass

    async def aclose(self) -> None:
        await self.stop()

    def _safe_busy(self) -> bool:
        try:
            return bool(self._busy_check()) if self._busy_check is not None else False
        except Exception:
            return False

    async def _run(self, state: WalkerState, worklist: list) -> None:
        try:
            for canonical_path in worklist:
                while self._safe_busy():
                    await asyncio.sleep(_BUSY_SLEEP_S)
                try:
                    result = await self._gen.process(canonical_path)
                    if (result or {}).get("status") == "scanned":
                        state.found += 1
                except asyncio.CancelledError:
                    raise
                except Exception as e:  # noqa: BLE001 - one bad file never aborts the run
                    state.errors.append({"path": canonical_path, "error": repr(e)})
                finally:
                    state.processed += 1
                await asyncio.sleep(_PER_FILE_SLEEP_S)
            state.status = "done"
            state.finished_at = _time.time()
            _h = getattr(self, "_health", None)  # #157 supervision
            if _h:
                _h.record_success(FORCED_SEGMENT_TASK)
        except asyncio.CancelledError:
            state.status = "cancelled"
            state.finished_at = _time.time()
            raise  # cancel != failure — do NOT record_failure
        except Exception as e:
            log.exception("forced-segment walk failed: %s", e)
            state.status = "error"
            state.error = repr(e)
            state.finished_at = _time.time()
            _h = getattr(self, "_health", None)
            if _h:
                _h.record_failure(FORCED_SEGMENT_TASK, e)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_forced_segment_walker.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add src/subarr/forced_segment_service.py tests/test_forced_segment_walker.py
git commit -m "feat(364): forced-segment manual walker (GPU-polite, resumable, #157-supervised)"
```

---

### Task 10: At-import hook in `completion_watcher.complete_entry`

**Files:**
- Modify: `src/subarr/completion_watcher.py` (constructor `:68`; `complete_entry` `:252`; new method near `:446`)
- Test: `tests/test_completion_forced_segment.py`

Slots beside `_run_retime`/`_run_aftercare` (`completion_watcher.py:253-254`). Best-effort, gated on `settings.forced_segment_enabled`, and **background-scheduled** so a long GPU scan never blocks the completion path. The generator re-checks the gate + cache internally, so the hook stays dumb (enabled + wired → schedule).

- [ ] **Step 1: Write the failing test**

```python
"""#364 slice 1 — at-import hook. Enabled+wired schedules a background scan;
disabled is a no-op; the hook NEVER blocks (it only schedules)."""

from __future__ import annotations

import asyncio
import importlib

import pytest


class _Entry:
    canonical_path = "TV/Show/ep.mkv"
    id = 1


class _FakeGen:
    def __init__(self):
        self.calls = []

    async def process(self, canonical_path):
        self.calls.append(canonical_path)


def _watcher():
    from subarr.completion_watcher import CompletionWatcher

    return CompletionWatcher()


@pytest.mark.asyncio
async def test_hook_schedules_when_enabled(subarr_env, monkeypatch):
    from subarr import config, completion_watcher

    monkeypatch.setenv("SUBARR_FORCED_SEGMENT_ENABLED", "1")
    importlib.reload(config)
    importlib.reload(completion_watcher)
    w = completion_watcher.CompletionWatcher()
    gen = _FakeGen()
    w._forced_segment = gen
    w._maybe_forced_segment(_Entry())
    await asyncio.sleep(0)  # let the scheduled task run
    assert gen.calls == ["TV/Show/ep.mkv"]


@pytest.mark.asyncio
async def test_hook_noop_when_disabled(subarr_env, monkeypatch):
    from subarr import config, completion_watcher

    monkeypatch.setenv("SUBARR_FORCED_SEGMENT_ENABLED", "0")
    importlib.reload(config)
    importlib.reload(completion_watcher)
    w = completion_watcher.CompletionWatcher()
    gen = _FakeGen()
    w._forced_segment = gen
    w._maybe_forced_segment(_Entry())
    await asyncio.sleep(0)
    assert gen.calls == []


@pytest.mark.asyncio
async def test_hook_noop_when_not_wired(subarr_env, monkeypatch):
    from subarr import config, completion_watcher

    monkeypatch.setenv("SUBARR_FORCED_SEGMENT_ENABLED", "1")
    importlib.reload(config)
    importlib.reload(completion_watcher)
    w = completion_watcher.CompletionWatcher()  # no _forced_segment wired
    # must not raise
    w._maybe_forced_segment(_Entry())
    await asyncio.sleep(0)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_completion_forced_segment.py -q`
Expected: FAIL with `AttributeError: 'CompletionWatcher' object has no attribute '_maybe_forced_segment'`.

- [ ] **Step 3: Write minimal implementation**

In `src/subarr/completion_watcher.py`, add the scheduling call inside `complete_entry` immediately after `self._run_aftercare(entry)` (`completion_watcher.py:254`):

```python
        self._maybe_forced_segment(entry)  # #364: best-effort background deep-scan (never blocks)
```

Then add the method next to `_run_retime` (after `completion_watcher.py:466`):

```python
    def _maybe_forced_segment(self, entry) -> None:
        """#364: if the feature is enabled and a generator is wired, schedule a
        BACKGROUND forced-segment scan for this just-completed file. The
        generator re-checks the gate + scan cache internally, so this hook only
        schedules — it NEVER blocks completion and never raises. Best-effort:
        LOG the reason on any miss (the #416 lesson — don't swallow silently)."""
        runner = getattr(self, "_forced_segment", None)
        if runner is None:
            return
        from .config import settings as _settings

        if not _settings.forced_segment_enabled:
            return
        try:
            import asyncio

            asyncio.create_task(self._forced_segment_bg(entry.canonical_path))
        except RuntimeError as e:
            log.warning("forced-segment at-import: no running loop for %s: %s", entry.canonical_path, e)

    async def _forced_segment_bg(self, canonical_path: str) -> None:
        try:
            await self._forced_segment.process(canonical_path)
        except Exception as e:  # noqa: BLE001 - at-import scan must never break completion
            log.warning("forced-segment at-import scan failed for %s: %s", canonical_path, e)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_completion_forced_segment.py -q`
Expected: PASS (3 passed).

Regression-check the completion path is intact:

Run: `python -m pytest tests/test_completion_retime.py -q`
Expected: PASS (existing completion tests still green).

- [ ] **Step 5: Commit**

```bash
git add src/subarr/completion_watcher.py tests/test_completion_forced_segment.py
git commit -m "feat(364): at-import forced-segment hook (best-effort, gated, non-blocking)"
```

---

### Task 11: Aftercare note surfacing + app wiring (walker, generator, hook, Health roster, router)

**Files:**
- Modify: `src/subarr/app.py` (task registration `:398`; wiring near `:630`)
- Create: `src/subarr/routers/forced_segment.py`
- Test: `tests/test_forced_segment_router.py`

The aftercare note itself is already emitted by `ForcedSegmentGenerator._record_aftercare_note` (Task 8, `source="forced-segment"`). This task wires the real objects into the app and exposes the walker control endpoints (mirroring `routers/audio_audit.py`), so slice 1 has its Settings-drivable control surface. The endpoint test uses the SYNC `app_with_stub` TestClient.

- [ ] **Step 1: Write the failing test**

```python
"""#364 slice 1 — walker control router. Endpoints exist and validate scope;
start/stop/get return the walker state shape. SYNC TestClient (app_with_stub)."""

from __future__ import annotations

import pytest


def test_get_forced_segment_status(app_with_stub):
    r = app_with_stub.get("/api/forced-segment")
    assert r.status_code == 200
    body = r.json()
    assert "state" in body and "summary" in body


def test_start_rejects_bad_scope(app_with_stub):
    r = app_with_stub.post("/api/forced-segment/start", params={"scope": "bogus"})
    assert r.status_code == 400


def test_start_and_stop(app_with_stub):
    r = app_with_stub.post("/api/forced-segment/start", params={"scope": "library"})
    assert r.status_code == 202
    assert r.json()["state"]["status"] in ("running", "done")
    r2 = app_with_stub.post("/api/forced-segment/stop")
    assert r2.status_code == 200
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_forced_segment_router.py -q`
Expected: FAIL with 404s (router not registered) — `assert r.status_code == 200` fails.

- [ ] **Step 3a: Create the router**

Create `src/subarr/routers/forced_segment.py`:

```python
"""#364 slice 1 — forced-segment deep-scan walker control.

POST /api/forced-segment/start   kick the opt-in, GPU-polite walker (409 if running)
POST /api/forced-segment/stop    cancel it
GET  /api/forced-segment         progress + scan-cache summary
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

router = APIRouter(prefix="/api/forced-segment", tags=["forced-segment"])

_SCOPES = ("coverage", "library")


@router.post("/start", status_code=202)
async def start_scan(request: Request, scope: str = "library") -> dict:
    if scope not in _SCOPES:
        raise HTTPException(400, detail=f"scope must be one of {_SCOPES}")
    walker = getattr(request.app.state, "forced_segment", None)
    if walker is None:
        raise HTTPException(503, detail="forced-segment walker not available")
    if walker.is_running():
        raise HTTPException(409, detail="forced-segment scan already running")
    state = await walker.start(scope=scope)
    return {"state": state.to_dict(), "scope": scope}


@router.post("/stop")
async def stop_scan(request: Request) -> dict:
    walker = getattr(request.app.state, "forced_segment", None)
    if walker is None:
        return {"state": None}
    await walker.stop()
    state = walker.get_state()
    return {"state": state.to_dict() if state is not None else None}


@router.get("")
async def get_scan(request: Request) -> dict:
    walker = getattr(request.app.state, "forced_segment", None)
    store = getattr(request.app.state, "forced_segment_store", None)
    state = walker.get_state() if walker is not None else None
    return {
        "state": state.to_dict() if state is not None else None,
        "summary": store.summary() if store is not None else {},
    }
```

- [ ] **Step 3b: Register the Health task**

In `src/subarr/app.py`, add to the task-registration tuple (after `app.py:398`, the `("audio-audit", None)` line):

```python
        # #364: opt-in deep-scan walker. expected_interval_s=None (event-driven,
        # never "stale") — sits on the unified Health roster like audio-audit.
        ("forced-segment", None),
```

- [ ] **Step 3c: Wire the store, generator, walker, and at-import hook**

In `src/subarr/app.py`, after the audio-audit walker wiring block (after `app.py:638`), add:

```python
    # #364: forced-segment deep-scan pipeline (opt-in; OFF by default). Store +
    # generator + walker + at-import hook. LID/translate bound to the subgen
    # client; Branch A (subgen-visible scratch) used when SUBARR_FORCED_SEGMENT_
    # SCRATCH_SUBGEN is set, else Branch B upload.
    from .forced_segment import ForcedSegmentParams, qualifies_for_forced_segment
    from .forced_segment_store import ForcedSegmentScanStore
    from .forced_segment_service import (
        ForcedSegmentGenerator,
        ForcedSegmentWalker,
        subgen_lid,
        subgen_translate,
    )
    from .media_probe import english_track_summary
    import os as _os

    app_.state.forced_segment_store = ForcedSegmentScanStore(settings.db_path)
    _fs_scratch_subgen = _os.environ.get("SUBARR_FORCED_SEGMENT_SCRATCH_SUBGEN") or None

    async def _fs_lid(clip_path, _span):
        subgen_path = None
        if _fs_scratch_subgen:
            from pathlib import Path as _P

            subgen_path = _fs_scratch_subgen.rstrip("/") + "/" + _P(clip_path).name
        return await subgen_lid(app_.state.subgen, clip_path, subgen_clip_path=subgen_path)

    async def _fs_translate(clip_path, _span):
        return await subgen_translate(app_.state.subgen, clip_path)

    def _fs_gate(canonical: str):
        # Resolve gate inputs from the stores already built above. Cheap: probe
        # cache (audio + duration + embedded-forced), audio-lang store (#357
        # lang_class), and a disk check for an existing .forced.en.srt sidecar.
        from .media_probe import audio_lang_summary
        from .paths import canonical_to_fs as _c2fs, PathOutsideRootError as _PORE

        pr = None
        try:
            pr = app_.state.probe_store.get(canonical)
        except Exception:
            pr = None
        audio_langs = audio_lang_summary(pr) if pr is not None else []
        embedded_en = english_track_summary(pr) if pr is not None else None
        duration_s = getattr(pr, "duration_s", None) if pr is not None else None
        size = None
        has_sidecar = False
        try:
            fs = _c2fs(canonical)
            if fs.exists():
                size = fs.stat().st_size
                has_sidecar = fs.with_name(fs.stem + ".forced.en.srt").exists()
        except (_PORE, OSError):
            pass
        lang_class = "single"
        try:
            v = app_.state.audio_lang.get(canonical.lstrip("/"))
            if v is not None:
                lang_class = getattr(v, "lang_class", "single")
        except Exception:
            lang_class = "single"
        ok, reason = qualifies_for_forced_segment(
            audio_langs=audio_langs,
            embedded_en=embedded_en,
            lang_class=lang_class,
            has_forced_sidecar=has_sidecar,
            duration_s=duration_s,
            params=ForcedSegmentParams(),
        )
        return ok, reason, duration_s, size

    app_.state.forced_segment_gen = ForcedSegmentGenerator(
        subgen=app_.state.subgen,
        scan_store=app_.state.forced_segment_store,
        params=ForcedSegmentParams(),
        lid_fn=_fs_lid,
        translate_fn=_fs_translate,
        gate_fn=_fs_gate,
        aftercare_store=getattr(app_.state, "aftercare", None),
        subgen_scratch_prefix=_fs_scratch_subgen,
    )
    app_.state.forced_segment = ForcedSegmentWalker(
        generator=app_.state.forced_segment_gen,
        worklist=lambda scope="library": [c for c, _mt in _walk_all_library_files()],
        busy_check=_audit_busy,  # reuse the arena-busy check (GPU-polite)
    )
    app_.state.forced_segment._health = app_.state.task_health  # #157 supervision
    app_.state.watcher._forced_segment = app_.state.forced_segment_gen  # at-import hook
```

Register the router where the other routers are included (search `include_router(` for `audio_audit`):

```python
    from .routers import forced_segment as forced_segment_router

    app_.include_router(forced_segment_router.router)
```

Add store teardown next to the other `.close()` calls (search for `audio_audit_store` teardown / `task_health.close()` at `app.py:931`):

```python
        try:
            app.state.forced_segment_store.close()
        except Exception:
            pass
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_forced_segment_router.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add src/subarr/app.py src/subarr/routers/forced_segment.py tests/test_forced_segment_router.py
git commit -m "feat(364): wire forced-segment store/generator/walker/hook + control router + aftercare note"
```

---

### Task 12: Settings UI toggle (frontend) — minimal, or deferred with a manual note

**Files:**
- Investigate: the Settings page bundle + `package.json` scripts (`test:frontend`, `build:frontend`, `check:frontend`).

Slice 1's only UI is a Settings toggle for "Deep-scan English files for foreign scenes" (the walker control lives on the API for now; a full page is explicitly out of scope for slice 1). The config field is already UI-settable (Task 5 added it to `FIELD_ENV_VARS` + `_FIELD_COERCE`, so `POST` to the existing settings-override endpoint persists it and `env > file > default` holds).

- [ ] **Step 1: Determine whether the Settings surface is cheap to extend**

Run: `ls C:/Projects/subarr/frontend 2>/dev/null || find C:/Projects/subarr -maxdepth 2 -name package.json -not -path '*/node_modules/*'`
Then inspect the settings component for the existing boolean-toggle pattern used by `retime_enabled` / `vad_enabled` (grep the frontend for `retime_enabled` or `vad_enabled`).

- [ ] **Step 2: Decision gate**

- **If** the settings page renders toggles from a declarative list keyed on the config field name (a one-line addition): add a `forced_segment_enabled` toggle mirroring the `retime_enabled` toggle — label "Deep-scan English files for foreign scenes (opt-in, GPU)", helper text noting it is off by default and GPU-spending. Then run the frontend gate:

  Run: `cd C:/Projects/subarr && npm run check:frontend && npm run test:frontend && npm run build:frontend`
  Expected: PASS with no bundle drift; commit the rebuilt bundle.

- **Else** (the settings page hard-codes each toggle and adding one is non-trivial / risks bundle churn): DEFER the visual toggle to a follow-up and document the manual path. The backend is fully controllable via `SUBARR_FORCED_SEGMENT_ENABLED=1` (env) or the settings-override API. Add a short note to the plan's Spec-coverage table (below) that the UI toggle is deferred with a **manual verification note**: "Enable by setting `SUBARR_FORCED_SEGMENT_ENABLED=1` and restart, or POST the override; confirm `GET /api/forced-segment` responds and `POST /api/forced-segment/start?scope=library` returns 202."

- [ ] **Step 3: Commit (only if a frontend change was made)**

```bash
git add frontend/ src/subarr/static/
git commit -m "feat(364): Settings toggle for forced-segment deep-scan"
```

If deferred, no commit — record the decision in the final report to the controller.

- [x] **DECISION (executed 2026-07-10): DEFERRED — no declarative toggle list exists.**

Investigation of `src/subarr/static/v1/home-hifi/settings.jsx` (2433 lines) found the
settings surface hard-codes each toggle as a bespoke React component wired to its own
dedicated backend endpoint (only two `<Toggle>` usages: the VAD toggle -> `/api/vad/config`
and the telemetry toggle -> its own opt-in endpoint). There is NO declarative list keyed on
config-field name that a one-liner extends, and `retime_enabled` (the plan's cited exemplar)
is not surfaced in the settings UI at all. Adding a `forced_segment_enabled` toggle would
require a new bespoke component, a generic settings-override endpoint for the UI to POST to,
and an esbuild bundle rebuild + committing the regenerated ~MB bundle — non-trivial and
bundle-churny, so it takes the plan's "Else -> DEFER" branch.

**Manual verification note (until the visual toggle lands in a follow-up):** the backend is
fully controllable without any UI. Enable by setting `SUBARR_FORCED_SEGMENT_ENABLED=1` in the
service env (or POST it through the settings-override API — Task 5 registered it in
`FIELD_ENV_VARS` + `_FIELD_COERCE`) and restart. Then confirm the control surface:
`GET /api/forced-segment` returns `{state, summary}` (200), and
`POST /api/forced-segment/start?scope=library` returns 202 with a running/done state;
`POST /api/forced-segment/stop` returns 200. With the flag unset the feature is OFF and the
at-import hook is a no-op (regression-proved by `tests/test_completion_retime.py` +
`tests/test_config_forced_segment.py`).

---

### Task 13: Final verification — full suite, ruff, bundle

**Files:** none (verification only).

- [ ] **Step 1: Run the full backend suite**

Run: `cd C:/Projects/subarr && python -m pytest -q`
Expected: PASS — all pre-existing tests plus the new forced-segment tests green. Investigate any failure to root cause (do not paper over).

- [ ] **Step 2: Lint**

Run: `cd C:/Projects/subarr && ruff format src/subarr/forced_segment.py src/subarr/forced_segment_service.py src/subarr/forced_segment_store.py src/subarr/routers/forced_segment.py src/subarr/completion_watcher.py src/subarr/config.py src/subarr/app.py tests/test_forced_segment_*.py tests/test_completion_forced_segment.py tests/test_config_forced_segment.py && ruff check src/subarr tests`
Expected: no changes needed / all checks pass. (Recall the ruff hook can strip a just-added unused import — verify imports added in Tasks 8/11 are all used.)

- [ ] **Step 3: Frontend gate (only if Task 12 touched the frontend)**

Run: `cd C:/Projects/subarr && npm run check:frontend && npm run test:frontend && npm run build:frontend`
Expected: PASS, no bundle drift.

- [ ] **Step 4: OFF-by-default regression proof**

Run: `cd C:/Projects/subarr && python -m pytest tests/test_completion_retime.py tests/test_config_forced_segment.py -q`
Expected: PASS — with the flag unset, the completion path is unchanged and `forced_segment_enabled` is False.

- [ ] **Step 5: Hand back to the controller**

Do NOT push or open a PR. Report: tasks completed, full-suite result, ruff result, the Task 0 branch decision, and the Task 12 UI decision (shipped or deferred-with-manual-note). The Tier-2 review program (writeback + detection-completeness lenses; ultra at release) runs before merge.

---

## Spec coverage (self-review)

| Slice-1 spec requirement | Task(s) |
|--------------------------|---------|
| Detector completeness — VAD-utterance-level, 100% of speech | Task 1 (classify/merge), Task 6 (VAD adapter), Task 8 (per-utterance orchestration) |
| Tier-1 LID via subgen `/detect_language_robust` (+ feasibility) | Task 0 (feasibility gate), Task 7 (LID adapter, Branch A/B) |
| Over-flag bias + confidence floor + min-duration + merge-gap | Task 1 (`ForcedSegmentParams`, classify, merge) |
| Overlap tiling for long utterances | Task 1 (`max_utterance_s`/`overlap_stride_s` params — wired hook; VAD utterances are pause-bounded so tiling is opt-in tuning) |
| Mostly-foreign bail (record, emit nothing) | Task 1 (`is_mostly_foreign`), Task 8 (`status="bailed"`) |
| Gate — English audio, not #357-multi, no existing forced, floor | Task 3 (predicate), Task 11 (`_fs_gate` resolves inputs) |
| Both triggers — manual walker + at-import hook, one toggle | Task 9 (walker), Task 10 (hook), Task 5 (single toggle) |
| Output hygiene — absolute time, `.forced.en.srt`, merge, path-contained, no-clobber | Task 2 (emitter), Task 8 (`canonical_to_fs` write + no-clobber) |
| Idempotence — `(canonical_path, mtime, size)` scan cache | Task 4 (store + migration 028), Task 8 (get/upsert) |
| OFF by default — skip-English untouched | Task 5 (default off), Task 10 (gated hook), Task 13 Step 4 (regression proof) |
| Surfacing — walker progress, Aftercare note, Health roster | Task 8 (`_record_aftercare_note`), Task 9 (state + `record_success`), Task 11 (Health register + router + summary) |
| Settings toggle (minimal) | Task 12 — DEFERRED (no declarative toggle list; manual path: `SUBARR_FORCED_SEGMENT_ENABLED=1` / settings-override API + `/api/forced-segment` control endpoints) |
| Testing — detector, SRT, gate, idempotence, orchestration, walker+hook, regression | Tasks 1–10 (each RED→GREEN), Task 13 |

## Name / type consistency (self-review)

- Detector types: `Utterance = tuple[float, float]`, `Span(start_ms, end_ms)`, `ForcedSegmentParams`, `LidFn` — defined Task 1, reused Tasks 6/8.
- Detector functions: `classify_utterances`, `foreign_fraction`, `is_mostly_foreign`, `merge_foreign_spans`, `assemble_foreign_spans` (Task 1); `build_forced_srt` (Task 2); `qualifies_for_forced_segment` (Task 3); `detect_utterances`, `clip_audio`, `_vad_speech_ranges` (Task 6). Same names used in Task 8 orchestration.
- Config flag: `forced_segment_enabled` / `SUBARR_FORCED_SEGMENT_ENABLED` — Task 5, consumed Task 10.
- Cache: `ForcedSegmentScanStore.get(canonical_path, mtime, size)` / `.upsert(...)` / `.summary()`, statuses `scanned|none|bailed` — Task 4, used Tasks 8/11.
- Service symbols: `subgen_lid`, `subgen_translate` (Task 7); `ForcedSegmentGenerator.process` (Task 8); `ForcedSegmentWalker` + `WalkerState` + `FORCED_SEGMENT_TASK="forced-segment"` (Task 9). App state keys `forced_segment_store` / `forced_segment_gen` / `forced_segment` and watcher attr `_forced_segment` — consistent across Tasks 10/11 and the router.
- Health/task name `"forced-segment"` — registered Task 11, recorded Task 9, matches `FORCED_SEGMENT_TASK`.
- Migration number **028** (next after 027) — Task 4.
