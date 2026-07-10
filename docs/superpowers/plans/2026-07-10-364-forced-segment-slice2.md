# #364 Slice 2 — Local windowed LID: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace slice 1's per-utterance subgen `/asr` language-ID with a local, no-torch silero-lang95 ONNX pass — windowed (~15 s) and confidence-gated — keeping the subgen path as the fallback and `task=translate`+`return_language` as the false-positive arbiter.

**Architecture:** A new `lid.py` model wrapper (mirrors `vad.py`: pinned-URL + SHA256 pull, checksum-verified atomic write, availability gate, onnxruntime+numpy, no torch). A new `LocalLidBackend` (`forced_segment_lid.py`) that groups VAD utterances into ~15 s windows, classifies each window once with silero, applies a confidence gate, and returns the SAME per-utterance `list[(utterance, foreign_bool)]` slice 1's downstream already consumes — so `merge_foreign_spans` / `is_mostly_foreign` / `_build_cues` are reused unchanged. `ForcedSegmentGenerator` gains an optional `local_lid` backend; when present it replaces the per-utterance subgen `_classify`, else slice-1 behaviour is byte-for-byte preserved. The translate step gains a source-language arbiter that drops spans whose detected source is the primary language.

**Tech Stack:** Python 3.11, FastAPI, onnxruntime + numpy (already baked via `[vad]`/`[qe-onnx]`), ffmpeg, pytest / pytest-asyncio. Design: `docs/superpowers/specs/2026-07-10-364-forced-segment-slice2-local-lid-design.md`.

**Pinned model facts (verified in the dev container):**
- Repo `deepghs/silero-lang95-onnx`, file `lang_classifier_95.onnx`, **SHA256 `12a91f706ad6ca5a5eba4e84a96739d077e56f121ec71db86a752f62e218a227`**.
- ONNX input node `input` shape `[batch, samples]` float32 (raw 16 kHz mono waveform, self-contained preprocessing like the VAD). Output node `output` = 95-way language logits; a second output (58-way language-group head) is ignored — request `["output"]` explicitly so the group head never runs.
- Labels: `lang_dict_95.json` maps `"<index>" -> "<iso>, <Name>"` (95 entries). **English is index 30 (`"en, English"`)**.

---

## File Structure

- **Create** `src/subarr/data/lid_lang_dict_95.json` — the 95-entry label map, committed verbatim from the pinned repo revision (no runtime download of labels).
- **Create** `src/subarr/lid.py` — silero-lang95 model management + `classify_samples()`. Mirrors `vad.py`. Pure `_softmax` / label lookup are unit-tested; the onnxruntime path is exercised by the Task 6 real-model smoke.
- **Create** `src/subarr/forced_segment_lid.py` — `LocalLidBackend`, the windowing + gate orchestration. Returns the per-utterance classified list.
- **Modify** `src/subarr/forced_segment.py` — add three `ForcedSegmentParams` fields and three PURE helpers (`assemble_windows`, `window_is_foreign`, `expand_window_verdicts`).
- **Modify** `src/subarr/forced_segment_service.py` — `subgen_translate` returns `(text, source_lang)`; `_build_cues` drops spans whose source is primary; `ForcedSegmentGenerator` accepts `local_lid` and uses it in `_run`.
- **Modify** `src/subarr/app.py` — extend the translate adapter to return the source language; construct `LocalLidBackend` and pass it to the generator when the feature is enabled and the model is available.
- **Modify** `pyproject.toml` — add the `[lid]` optional-dependency group.
- **Create/extend tests**: `tests/test_forced_segment_windowing.py`, `tests/test_lid.py`, `tests/test_forced_segment_lid.py`, and additions to `tests/test_forced_segment_service.py`.

DRY / YAGNI / TDD / frequent commits throughout. Every task ends green with a commit.

---

## Task 1: Windowing params + pure helpers

**Files:**
- Modify: `src/subarr/forced_segment.py`
- Test: `tests/test_forced_segment_windowing.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_forced_segment_windowing.py
from subarr.forced_segment import (
    ForcedSegmentParams,
    assemble_windows,
    window_is_foreign,
    expand_window_verdicts,
)


def test_assemble_windows_groups_until_window_length():
    # utterances (start_s, end_s); window target 15s
    utts = [(0.0, 3.0), (4.0, 7.0), (8.0, 12.0), (20.0, 23.0), (24.0, 30.0)]
    windows = assemble_windows(utts, window_s=15.0)
    # first three fit in [0,12] (<=15 from w_start=0); next window starts at 20
    assert [w[2] for w in windows] == [[0, 1, 2], [3, 4]]
    assert windows[0][0] == 0.0 and windows[0][1] == 12.0
    assert windows[1][0] == 20.0 and windows[1][1] == 30.0


def test_assemble_windows_single_long_utterance_is_its_own_window():
    utts = [(0.0, 40.0), (41.0, 44.0)]
    windows = assemble_windows(utts, window_s=15.0)
    assert windows[0][2] == [0] and windows[0][1] == 40.0
    assert windows[1][2] == [1]


def test_window_is_foreign_gate():
    p = ForcedSegmentParams(primary_lang="en", lid_min_confidence=0.5, lid_max_english_prob=0.25)
    # confident non-English, low english prob -> foreign
    assert window_is_foreign("de", 0.89, 0.02, p) is True
    # English top -> never foreign
    assert window_is_foreign("en", 0.9, 0.9, p) is False
    # low confidence noise -> not foreign
    assert window_is_foreign("zh", 0.10, 0.05, p) is False
    # non-English but english still plausible -> not foreign
    assert window_is_foreign("nl", 0.6, 0.4, p) is False


def test_expand_window_verdicts_assigns_each_utterance_its_window_flag():
    utts = [(0.0, 3.0), (4.0, 7.0), (20.0, 23.0)]
    windows = [(0.0, 7.0, [0, 1]), (20.0, 23.0, [2])]
    flags = [True, False]
    classified = expand_window_verdicts(utts, windows, flags)
    assert classified == [((0.0, 3.0), True), ((4.0, 7.0), True), ((20.0, 23.0), False)]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_forced_segment_windowing.py -q`
Expected: FAIL — `ImportError: cannot import name 'assemble_windows'`.

- [ ] **Step 3: Add params + helpers**

In `src/subarr/forced_segment.py`, add to `ForcedSegmentParams` (after `overlap_stride_s`):

```python
    # Slice 2 — local windowed LID (silero-lang95). silero is unreliable on short
    # (<~10s) or non-speech audio, so we classify ~lid_window_s windows of speech,
    # not raw utterances, and only trust CONFIDENT non-English verdicts. These are
    # inert on the slice-1 subgen path (which still uses classify_utterances).
    lid_window_s: float = 15.0  # target window for grouping utterances (spike-verified reliable floor)
    lid_min_confidence: float = 0.5  # softmax floor for a foreign verdict (rejects silero noise)
    lid_max_english_prob: float = 0.25  # reject "foreign" if English is still this plausible
```

Add these PURE functions (near the other pure helpers, e.g. after `assemble_foreign_spans`):

```python
# --- Slice 2: windowed local-LID helpers (pure) -----------------------------
# A window is (start_s, end_s, [utterance_index, ...]).
Window = "tuple[float, float, list[int]]"


def assemble_windows(utterances: list[Utterance], window_s: float) -> list[tuple[float, float, list[int]]]:
    """Group pause-bounded speech utterances into ~window_s windows for LID.
    Greedy: a window starts at an utterance and absorbs following utterances
    while (utt.end - window_start) <= window_s; each utterance lands in exactly
    one window (no straddling). A single utterance longer than window_s is its
    own window — silero accepts variable length, and slice 2 does not tile
    (overlap tiling is out of scope, see spec S8)."""
    windows: list[tuple[float, float, list[int]]] = []
    i = 0
    n = len(utterances)
    while i < n:
        w_start = utterances[i][0]
        idxs = [i]
        j = i + 1
        while j < n and (utterances[j][1] - w_start) <= window_s:
            idxs.append(j)
            j += 1
        w_end = utterances[idxs[-1]][1]
        windows.append((w_start, w_end, idxs))
        i = j
    return windows


def window_is_foreign(
    top_lang: str | None, top_prob: float, english_prob: float, params: ForcedSegmentParams
) -> bool:
    """The slice-2 gate: a window is foreign iff the top language is non-primary
    AND confident (top_prob >= lid_min_confidence) AND English is implausible
    (english_prob <= lid_max_english_prob). Everything else is treated as primary
    — silero's low-confidence output is noise, so we do NOT over-flag on it."""
    if top_lang is None or _is_english(top_lang, params):
        return False
    if top_prob < params.lid_min_confidence:
        return False
    if english_prob > params.lid_max_english_prob:
        return False
    return True


def expand_window_verdicts(
    utterances: list[Utterance],
    windows: list[tuple[float, float, list[int]]],
    window_foreign: list[bool],
) -> list[tuple[Utterance, bool]]:
    """Assign each utterance its containing window's foreign flag, producing the
    same classified list slice-1's merge_foreign_spans / is_mostly_foreign
    consume. Utterances are returned in original order."""
    flag_by_idx: dict[int, bool] = {}
    for (_s, _e, idxs), is_foreign in zip(windows, window_foreign):
        for k in idxs:
            flag_by_idx[k] = is_foreign
    return [(utterances[k], flag_by_idx.get(k, False)) for k in range(len(utterances))]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_forced_segment_windowing.py -q`
Expected: PASS (4 tests).

- [ ] **Step 5: Lint + commit**

```bash
ruff check src/subarr/forced_segment.py tests/test_forced_segment_windowing.py
ruff format src/subarr/forced_segment.py tests/test_forced_segment_windowing.py
git add src/subarr/forced_segment.py tests/test_forced_segment_windowing.py
git commit -m "feat(#364 slice2): windowing params + pure LID-gate helpers"
```

---

## Task 2: Baked label map + `lid.py` model wrapper

**Files:**
- Create: `src/subarr/data/lid_lang_dict_95.json`
- Create: `src/subarr/lid.py`
- Test: `tests/test_lid.py` (create)

- [ ] **Step 1: Commit the label map verbatim**

Download the exact file from the pinned repo revision and commit it (no runtime label download). In the dev container this file is already in the HF cache; copy it out, or fetch:

```bash
python - <<'PY'
import json, urllib.request, pathlib
url = "https://huggingface.co/deepghs/silero-lang95-onnx/resolve/main/lang_dict_95.json"
data = json.loads(urllib.request.urlopen(url).read())
assert len(data) == 95, len(data)
assert data["30"].startswith("en,"), data["30"]  # English is index 30
pathlib.Path("src/subarr/data").mkdir(parents=True, exist_ok=True)
pathlib.Path("src/subarr/data/lid_lang_dict_95.json").write_text(
    json.dumps(data, ensure_ascii=False, indent=0), encoding="utf-8"
)
print("wrote", len(data), "labels")
PY
```

- [ ] **Step 2: Write the failing test**

```python
# tests/test_lid.py
import numpy as np
import pytest
from subarr import lid


def test_labels_loaded_english_index_30():
    labels = lid.load_labels()
    assert len(labels) == 95
    assert labels[30] == "en"  # code only, lowercased


def test_softmax_topk_picks_max_and_english_prob():
    labels = lid.load_labels()
    logits = np.full(95, -5.0, dtype=np.float32)
    logits[30] = 5.0  # English dominant
    top_lang, top_prob, en_prob = lid._verdict_from_logits(logits, labels)
    assert top_lang == "en"
    assert top_prob > 0.99
    assert en_prob == pytest.approx(top_prob)


def test_verdict_reports_english_prob_even_when_foreign_wins():
    labels = lid.load_labels()
    logits = np.full(95, -5.0, dtype=np.float32)
    de = labels.index("de")
    logits[de] = 4.0
    logits[30] = 2.0  # English second
    top_lang, top_prob, en_prob = lid._verdict_from_logits(logits, labels)
    assert top_lang == "de"
    assert 0.0 < en_prob < top_prob


def test_pull_model_verifies_checksum(tmp_path, monkeypatch):
    monkeypatch.setenv("SUBARR_LID_MODEL_PATH", str(tmp_path / "m.onnx"))
    good = b"fake-onnx-bytes"
    import hashlib
    monkeypatch.setattr(lid, "MODEL_SHA256", hashlib.sha256(good).hexdigest())
    res = lid.pull_model(_fetch=lambda url: good)
    assert res["status"] == "downloaded"
    # tamper -> rejected, not persisted
    monkeypatch.setattr(lid, "MODEL_SHA256", "0" * 64)
    (tmp_path / "m.onnx").unlink()
    with pytest.raises(ValueError):
        lid.pull_model(_fetch=lambda url: good)
    assert not (tmp_path / "m.onnx").exists()
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/test_lid.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'subarr.lid'`.

- [ ] **Step 4: Implement `lid.py`**

```python
# src/subarr/lid.py
"""#364 slice 2 — silero-lang95 spoken language identification.

Mirrors vad.py exactly: a pinned-URL + SHA256 model pulled on opt-in and
verified before it is written, run on bare onnxruntime + numpy (NO torch). The
95-language label map is committed (data/lid_lang_dict_95.json), not downloaded.
Never raises on a missing/broken model — callers fall back to the subgen LID.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from functools import lru_cache
from pathlib import Path

log = logging.getLogger(__name__)

_MODEL_ENV = "SUBARR_LID_MODEL_PATH"
# Immutable revision (pin the repo commit sha at integration, not a moving
# branch). SHA256 is the real guarantee — verified before the file is written.
MODEL_URL = "https://huggingface.co/deepghs/silero-lang95-onnx/resolve/main/lang_classifier_95.onnx"
MODEL_SHA256 = "12a91f706ad6ca5a5eba4e84a96739d077e56f121ec71db86a752f62e218a227"

_LABELS_FILE = Path(__file__).with_name("data") / "lid_lang_dict_95.json"


@lru_cache(maxsize=1)
def load_labels() -> list[str]:
    """Index-ordered ISO codes (lowercased), parsed from the committed label
    map ('<idx>' -> '<iso>, <Name>'). load_labels()[30] == 'en'."""
    raw = json.loads(_LABELS_FILE.read_text(encoding="utf-8"))
    n = len(raw)
    labels = [""] * n
    for k, v in raw.items():
        labels[int(k)] = str(v).split(",")[0].strip().lower()
    return labels


def model_target_path() -> Path:
    """Read/write location for the pulled model. Beside the DB (persisted
    volume) by default; overridable via SUBARR_LID_MODEL_PATH / SUBARR_LID_DIR."""
    override = os.environ.get(_MODEL_ENV)
    if override:
        return Path(override)
    base = os.environ.get("SUBARR_LID_DIR")
    if not base:
        db = os.environ.get("SUBARR_DB_PATH", "/data/subarr.db")
        base = str(Path(db).parent / "lid")
    return Path(base) / "lang_classifier_95.onnx"


def _model_path() -> str | None:
    p = model_target_path()
    return str(p) if p.is_file() else None


def runtime_present() -> bool:
    try:
        import onnxruntime  # noqa: F401
    except Exception:
        return False
    return True


def _default_fetch(url: str) -> bytes:
    import httpx

    r = httpx.get(url, follow_redirects=True, timeout=120.0)
    r.raise_for_status()
    return r.content


def pull_model(force: bool = False, *, _fetch=None) -> dict:
    """Download the pinned model, verify SHA256, write atomically. Idempotent;
    never persists a checksum-mismatched file. `_fetch` injectable for tests."""
    if not MODEL_SHA256:
        raise RuntimeError("silero-lang95 checksum is not pinned; refusing to pull")
    target = model_target_path()
    if target.is_file() and not force:
        if hashlib.sha256(target.read_bytes()).hexdigest() == MODEL_SHA256:
            return {"status": "present", "path": str(target)}
    data = (_fetch or _default_fetch)(MODEL_URL)
    digest = hashlib.sha256(data).hexdigest()
    if digest != MODEL_SHA256:
        raise ValueError(f"silero-lang95 checksum mismatch: got {digest}")
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(target)
    return {"status": "downloaded", "path": str(target), "sha256": digest}


def lid_available() -> bool:
    """True iff onnxruntime AND the model file are present. Never raises."""
    return runtime_present() and _model_path() is not None


def ensure_available() -> bool:
    """Best-effort make the model available (pull if runtime present + missing).
    Returns lid_available() afterwards; logs and returns False on failure so the
    caller falls back to subgen LID."""
    if not runtime_present():
        return False
    try:
        pull_model()
    except Exception as e:  # noqa: BLE001 - offline / fetch failure degrades to subgen
        log.warning("forced-segment: silero-lang95 pull failed (%s); using subgen LID", e)
    return lid_available()


def _verdict_from_logits(logits, labels: list[str]) -> "tuple[str, float, float]":
    """Pure: 95-way logits -> (top_lang_iso, top_prob, english_prob) via softmax."""
    import numpy as np

    v = np.asarray(logits, dtype=np.float64)
    e = np.exp(v - v.max())
    probs = e / e.sum()
    top = int(probs.argmax())
    en = labels.index("en")
    return labels[top], float(probs[top]), float(probs[en])


# silero-lang95 ONNX contract: input 'input' [batch, samples] float32 (16 kHz
# mono waveform); output 'output' = 95-way logits. A second 58-way group head is
# ignored — request only 'output' so it never runs.
_SR = 16000


def classify_samples(samples) -> "tuple[str, float, float] | None":
    """Classify a 16 kHz mono float32 waveform -> (top_lang, top_prob,
    english_prob), or None when unavailable / on any inference error."""
    if not lid_available():
        return None
    try:
        import numpy as np
        import onnxruntime as ort

        sess = ort.InferenceSession(_model_path(), providers=["CPUExecutionProvider"])
        x = np.asarray(samples, dtype=np.float32).reshape(1, -1)
        (logits,) = sess.run(["output"], {"input": x})
        return _verdict_from_logits(logits[0], load_labels())
    except Exception:  # noqa: BLE001 - degrade to None -> caller falls back
        log.warning("silero-lang95 inference failed", exc_info=True)
        return None
```

- [ ] **Step 5: Run tests + verify pass**

Run: `python -m pytest tests/test_lid.py -q`
Expected: PASS (4 tests).

- [ ] **Step 6: Lint + commit**

```bash
ruff check src/subarr/lid.py tests/test_lid.py
ruff format src/subarr/lid.py tests/test_lid.py
git add src/subarr/lid.py tests/test_lid.py src/subarr/data/lid_lang_dict_95.json
git commit -m "feat(#364 slice2): silero-lang95 model wrapper (mirrors vad.py) + baked labels"
```

Note for the implementer: confirm `src/subarr/data/*.json` is packaged. `pyproject.toml` likely already includes package data (the qe-onnx path ships weights via HF cache, not the repo, so check `[tool.setuptools.package-data]` / `include-package-data`). If `data/lid_lang_dict_95.json` is not picked up by the wheel, add it to package-data in this task and note it.

---

## Task 3: `LocalLidBackend`

**Files:**
- Create: `src/subarr/forced_segment_lid.py`
- Test: `tests/test_forced_segment_lid.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_forced_segment_lid.py
import asyncio
from subarr.forced_segment import ForcedSegmentParams
from subarr.forced_segment_lid import LocalLidBackend


def _run(coro):
    return asyncio.run(coro)


def test_local_backend_flags_confident_foreign_window():
    # Two windows: first foreign (de, confident), second English.
    utts = [(0.0, 4.0), (5.0, 9.0), (30.0, 34.0)]
    params = ForcedSegmentParams(lid_window_s=15.0, lid_min_confidence=0.5, lid_max_english_prob=0.25)
    # fake window classifier keyed by window start
    def fake_classify(samples):
        return fake_classify.queue.pop(0)
    fake_classify.queue = [("de", 0.9, 0.02), ("en", 0.95, 0.95)]
    clips = []
    def fake_clip(fs, s, e, out, track=0):
        clips.append((s, e))  # record window spans; write nothing
    def fake_read(_path):
        return [0.0]  # samples unused by fake_classify

    be = LocalLidBackend(params=params, classify_fn=fake_classify, clip_fn=fake_clip, read_fn=fake_read)
    classified = _run(be.classify("/x.mkv", utts, tmp="/tmp"))
    assert classified == [((0.0, 4.0), True), ((5.0, 9.0), True), ((30.0, 34.0), False)]
    assert clips == [(0.0, 9.0), (30.0, 34.0)]  # one clip per window, spanning its utterances


def test_local_backend_over_flags_on_clip_failure():
    utts = [(0.0, 4.0)]
    params = ForcedSegmentParams()
    def boom(*a, **k):
        raise RuntimeError("ffmpeg died")
    be = LocalLidBackend(
        params=params, classify_fn=lambda s: ("en", 0.9, 0.9), clip_fn=boom, read_fn=lambda p: [0.0]
    )
    classified = _run(be.classify("/x.mkv", utts, tmp="/tmp"))
    assert classified == [((0.0, 4.0), True)]  # hard failure -> over-flag (completeness bias)


def test_local_backend_none_verdict_over_flags():
    # classify returns None (model vanished mid-run) -> over-flag the window
    utts = [(0.0, 4.0)]
    be = LocalLidBackend(
        params=ForcedSegmentParams(), classify_fn=lambda s: None, clip_fn=lambda *a, **k: None, read_fn=lambda p: [0.0]
    )
    classified = _run(be.classify("/x.mkv", utts, tmp="/tmp"))
    assert classified == [((0.0, 4.0), True)]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_forced_segment_lid.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'subarr.forced_segment_lid'`.

- [ ] **Step 3: Implement `forced_segment_lid.py`**

```python
# src/subarr/forced_segment_lid.py
"""#364 slice 2 — the local windowed LID backend.

Groups VAD utterances into ~lid_window_s windows, classifies each window ONCE
with silero-lang95 (local, no subgen round-trip), applies the confidence gate,
and returns the per-utterance classified list slice-1's merge/bail already
consume. Selected by ForcedSegmentGenerator when the model is available; the
subgen per-utterance path is the fallback.
"""

from __future__ import annotations

import asyncio
import logging
import os
import wave
from typing import Callable

from . import lid as lid_module
from .forced_segment import (
    ForcedSegmentParams,
    Utterance,
    assemble_windows,
    expand_window_verdicts,
    window_is_foreign,
)

log = logging.getLogger(__name__)


def _read_wav_f32(path: str):
    """Read a 16 kHz mono int16 wav -> float32 [-1, 1] numpy array."""
    import numpy as np

    with wave.open(path) as w:
        frames = w.readframes(w.getnframes())
    return np.frombuffer(frames, np.int16).astype(np.float32) / 32768.0


class LocalLidBackend:
    """classify(fs_path, utterances, tmp) -> list[(utterance, foreign_bool)].
    clip_fn/read_fn/classify_fn are injected so the logic is testable without
    ffmpeg or the real model. A window whose clip or inference fails is
    over-flagged (completeness bias), matching slice-1's clip-failure handling."""

    def __init__(
        self,
        *,
        params: ForcedSegmentParams,
        classify_fn: Callable[..., "tuple[str, float, float] | None"] | None = None,
        clip_fn: Callable[..., None] | None = None,
        read_fn: Callable[[str], object] | None = None,
    ):
        self._params = params
        self._classify = classify_fn or lid_module.classify_samples
        if clip_fn is None:
            from .forced_segment import clip_audio

            clip_fn = clip_audio
        self._clip = clip_fn
        self._read = read_fn or _read_wav_f32

    async def classify(self, fs_path: str, utterances: list[Utterance], tmp) -> list:
        windows = assemble_windows(utterances, self._params.lid_window_s)
        flags: list[bool] = []
        for i, (w_start, w_end, _idxs) in enumerate(windows):
            clip = os.path.join(tmp, f"lidwin-{i}.wav")
            try:
                # Off-loop (#364): ffmpeg + onnxruntime are blocking.
                await asyncio.to_thread(self._clip, fs_path, w_start, w_end, clip)
                samples = await asyncio.to_thread(self._read, clip)
                verdict = await asyncio.to_thread(self._classify, samples)
            except Exception as exc:  # noqa: BLE001 - a bad window is suspect, never fatal
                log.warning("forced-segment: local LID failed for window %.1f-%.1fs: %s", w_start, w_end, exc)
                flags.append(True)  # over-flag on hard failure
                continue
            if verdict is None:
                flags.append(True)  # model unavailable mid-run -> over-flag
                continue
            top_lang, top_prob, en_prob = verdict
            flags.append(window_is_foreign(top_lang, top_prob, en_prob, self._params))
        return expand_window_verdicts(utterances, windows, flags)
```

- [ ] **Step 4: Run tests + verify pass**

Run: `python -m pytest tests/test_forced_segment_lid.py -q`
Expected: PASS (3 tests).

- [ ] **Step 5: Lint + commit**

```bash
ruff check src/subarr/forced_segment_lid.py tests/test_forced_segment_lid.py
ruff format src/subarr/forced_segment_lid.py tests/test_forced_segment_lid.py
git add src/subarr/forced_segment_lid.py tests/test_forced_segment_lid.py
git commit -m "feat(#364 slice2): LocalLidBackend (windowed silero classify + gate)"
```

---

## Task 4: Translate-arbiter (drop false-positive spans)

**Files:**
- Modify: `src/subarr/forced_segment_service.py`
- Test: `tests/test_forced_segment_service.py` (extend)

Context: `subgen_translate` currently returns `str`. Slice 2 makes it return `(text, source_lang)` (subgen `/asr task=translate` + `return_language`, which subgen already computes — slice-1's `subgen_lid` read it the same way). `_build_cues` then DROPS any span whose detected source is the primary language (silero false-positive). `TranslateFn` and the app adapter change accordingly. This applies to both backends and is a safe improvement on the subgen path too (it resolves a flag/translate disagreement toward emitting nothing rather than a bogus English cue).

- [ ] **Step 1: Write the failing tests**

```python
# add to tests/test_forced_segment_service.py
import asyncio
from subarr import forced_segment_service as svc
from subarr.forced_segment import Span, ForcedSegmentParams


def test_build_cues_drops_span_whose_source_is_primary(gen_factory):
    # translate returns (text, source_lang); a Spanish span is kept, an English
    # (false-positive) span is dropped.
    async def fake_translate(clip, span):
        # span passed as (start_s, end_s); key off start
        s0 = span[0]
        return ("Hola.", "es") if s0 < 10 else ("hello there", "en")

    g = gen_factory(translate_fn=fake_translate)
    spans = [Span(0, 4000), Span(20000, 24000)]
    cues = asyncio.run(g._build_cues("/x.mkv", spans, "/tmp"))
    texts = [t for _s, _e, t in cues]
    assert any("Hola" in t for t in texts)
    assert not any("hello there" in t for t in texts)  # en source dropped


def test_subgen_translate_returns_text_and_lang(monkeypatch):
    class FakeSubgen:
        async def asr(self, *, local_file, task, return_language=False):
            assert task == "translate" and return_language is True
            return ("translated text", "es")
    text, lang = asyncio.run(svc.subgen_translate(FakeSubgen(), "/clip.wav"))
    assert text == "translated text" and lang == "es"
```

Add a `gen_factory` fixture to the test module if one is not already present, constructing a `ForcedSegmentGenerator` with fakes (reuse the existing `gen`/fixture wiring already in this file; the factory just lets a test override `translate_fn`). The implementer wires it to match the file's existing fixture style.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_forced_segment_service.py -q -k "arbiter or translate_returns or drops_span"`
Expected: FAIL — `subgen_translate` returns a str (not a tuple); `_build_cues` keeps the en span.

- [ ] **Step 3: Implement the arbiter**

In `src/subarr/forced_segment_service.py`:

1. Change `subgen_translate`:

```python
async def subgen_translate(subgen: SubgenClient, clip_path: str) -> "tuple[str, str | None]":
    """Transcribe+translate one foreign span clip to English text AND report the
    detected SOURCE language (subgen computes it before translating; free here).
    The source language is the arbiter: a span whose source is the primary
    language was a local-LID false positive and is dropped upstream. Returns
    ('', None) if subgen produced nothing."""
    try:
        text, lang = await subgen.asr(local_file=clip_path, task="translate", return_language=True)
        return (text or ""), (lang or None)
    except SubgenUnavailable as e:
        log.warning("forced-segment translate failed for a clip: %s", e)
        return "", None
```

2. Update the `TranslateFn` type:

```python
TranslateFn = Callable[
    [str, "tuple[float, float]"],
    "Awaitable[tuple[str, str | None]] | tuple[str, str | None]",
]
```

3. In `_build_cues`, replace the translate call + text guard with the arbiter. The generator needs `_is_english` — import it at top: `from .forced_segment import _is_english` (add to the existing forced_segment import block). Then:

```python
            text, source_lang = await _maybe_await(
                self._translate(clip, (sp.start_ms / 1000.0, sp.end_ms / 1000.0))
            )
            # Arbiter: silero over-flags; if subgen's translate detected the source
            # as the primary language, this span was a false positive — drop it.
            if source_lang is not None and _is_english(source_lang, self._params):
                log.info("forced-segment: span %s source=%s == primary — dropped (LID false positive)", sp, source_lang)
                continue
            if not (text and text.strip()):
                continue
            for c in parse_srt(text):
                ...
```

- [ ] **Step 4: Run tests to verify pass**

Run: `python -m pytest tests/test_forced_segment_service.py -q`
Expected: PASS. Fix any slice-1 tests that asserted the old `subgen_translate` str return or passed a str-returning `translate_fn` fake — update those fakes to return `(text, None)` (None source = "unknown", never dropped, preserving slice-1 behaviour).

- [ ] **Step 5: Lint + commit**

```bash
ruff check src/subarr/forced_segment_service.py tests/test_forced_segment_service.py
ruff format src/subarr/forced_segment_service.py tests/test_forced_segment_service.py
git add src/subarr/forced_segment_service.py tests/test_forced_segment_service.py
git commit -m "feat(#364 slice2): translate-arbiter drops spans whose source is primary"
```

---

## Task 5: Wire `local_lid` into the generator + app + `[lid]` extra

**Files:**
- Modify: `src/subarr/forced_segment_service.py` (generator `__init__` + `_run`)
- Modify: `src/subarr/app.py` (translate adapter returns lang; construct + inject `LocalLidBackend`)
- Modify: `pyproject.toml` (`[lid]` extra)
- Test: `tests/test_forced_segment_service.py` (extend)

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_forced_segment_service.py
def test_generator_uses_local_lid_when_present(gen_factory):
    calls = {"local": 0, "subgen_classify": 0}

    class FakeLocal:
        async def classify(self, fs_path, utterances, tmp):
            calls["local"] += 1
            # flag everything English -> 'none' outcome, but proves the path ran
            return [(u, False) for u in utterances]

    g = gen_factory(local_lid=FakeLocal())
    # patch VAD to yield utterances + gate to pass; reuse the file's helpers
    # (the implementer wires this to the existing fixture that stubs vad/gate/store)
    import asyncio
    asyncio.run(g.process("lib::/x.mkv"))
    assert calls["local"] == 1  # local backend used, per-utterance subgen _classify NOT called
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_forced_segment_service.py -q -k local_lid`
Expected: FAIL — `__init__() got an unexpected keyword argument 'local_lid'`.

- [ ] **Step 3: Generator changes**

In `ForcedSegmentGenerator.__init__`, add a parameter and store it:

```python
        local_lid=None,
```
```python
        self._local_lid = local_lid  # slice-2 windowed backend; None -> slice-1 subgen _classify
```

In `_run`, replace the single `classified = await self._classify(...)` line inside the `with tempfile.TemporaryDirectory(...)` block:

```python
            if self._local_lid is not None:
                classified = await self._local_lid.classify(str(fs_path), utterances, tmp)
            else:
                classified = await self._classify(str(fs_path), utterances, tmp)
```

- [ ] **Step 4: Run generator test + full service tests**

Run: `python -m pytest tests/test_forced_segment_service.py -q`
Expected: PASS (local path exercised; all slice-1 tests still green since `local_lid` defaults to None).

- [ ] **Step 5: App wiring**

In `src/subarr/app.py`, at the forced-segment wiring block (where `ForcedSegmentGenerator` is constructed):

1. Extend the translate adapter to return `(text, lang)`. Find `_fs_translate` (currently returns text) and change it to call `subgen_translate` (now returns the tuple) and return the tuple verbatim. If the adapter currently unwraps to a str, stop unwrapping.

2. Build the local backend when the feature is enabled and the model can be made available, and pass it in:

```python
from . import lid as _lid
from .forced_segment_lid import LocalLidBackend

_local_lid = None
if settings.forced_segment_enabled and _lid.ensure_available():
    _local_lid = LocalLidBackend(params=ForcedSegmentParams())
    log.info("forced-segment: local silero-lang95 LID active")
elif settings.forced_segment_enabled:
    log.info("forced-segment: silero-lang95 unavailable — using subgen LID fallback")

forced_segment_gen = ForcedSegmentGenerator(
    ...,               # existing args unchanged
    local_lid=_local_lid,
)
```

Keep `subgen_scratch_prefix=None` (unchanged from slice 1) — the subgen LID stays Branch-B upload for the fallback path.

- [ ] **Step 6: `[lid]` extra in `pyproject.toml`**

Add after the `qe-onnx` group:

```toml
# #364 slice 2: local spoken-LID (silero-lang95). onnxruntime + numpy only
# (already present via [vad]) — NO torch. The ~ small model is pulled lazily +
# checksum-verified on first use; the 95-language label map is committed. Baked
# into the image like [vad].
lid = [
    "onnxruntime>=1.16,<2.0",
    "numpy>=1.24,<3.0",
]
```

If the Dockerfile installs extras explicitly (grep for `[vad]`/`[qe-onnx]` in the Dockerfile), add `lid` alongside them so the runtime is baked. Note the finding in the commit body if the Dockerfile already installs `.[all]` or similar.

- [ ] **Step 7: Run affected tests + verify app imports**

```bash
python -m pytest tests/test_forced_segment_service.py tests/test_forced_segment_lid.py tests/test_lid.py tests/test_forced_segment_windowing.py -q
python -c "import subarr.app"  # app wiring imports cleanly
```
Expected: PASS; app imports without error.

- [ ] **Step 8: Lint + commit**

```bash
ruff check src/subarr/forced_segment_service.py src/subarr/app.py
ruff format src/subarr/forced_segment_service.py src/subarr/app.py
git add src/subarr/forced_segment_service.py src/subarr/app.py pyproject.toml
git commit -m "feat(#364 slice2): wire LocalLidBackend into generator + app; add [lid] extra"
```

---

## Task 6: Full suite, OFF-by-default guarantee, real-model smoke

**Files:**
- Test: `tests/test_forced_segment_service.py` (extend), plus a documented manual smoke.

- [ ] **Step 1: OFF-by-default regression test**

Assert that with `local_lid=None` (the default when the feature/model is off) the generator behaves exactly as slice 1 — the per-utterance subgen `_classify` path runs and produces an identical outcome for a fixture file. If such a test already exists from slice 1, confirm it still passes unchanged; otherwise add one asserting `_classify` is used when `local_lid is None`.

- [ ] **Step 2: Run the full suite**

Run: `python -m pytest -q`
Expected: All green (slice-1 count + the new slice-2 tests; 0 failures). Investigate any full-suite-only flake against the conftest module-reload gotcha (`reference_subarr-test-module-reload` — reload only what a test needs).

- [ ] **Step 3: ruff gate**

Run: `ruff check . && ruff format --check .`
Expected: clean (heredoc-appended tests must be `ruff format`ted — see the banked ruff-hook lesson).

- [ ] **Step 4: Real-model smoke (manual, dev container, reuses the spike harness)**

On `subarr-next` (:9923, branch checked out via bind mount), with `SUBARR_FORCED_SEGMENT_ENABLED=1` and the model pulled:

```bash
# a) model pulls + a clear-English window stays UNFLAGGED (English clip)
# b) the known-German film at 3600s FLAGS de and produces a .forced.en.srt
# c) per-window classify latency stays sub-second (spike measured 100-320ms)
```

Drive `ForcedSegmentGenerator.process()` (or `POST /api/forced-segment/start` on a tiny scoped worklist) against:
- the English episode used in the slice-1 smoke → expect `status: none` (no foreign scene, nothing written), and
- `All Quiet on the Western Front (2022)` (German audio) → expect a `.forced.en.srt` with German scenes translated, source-language arbiter NOT dropping them.

Record the outcome (statuses, cue counts, timing) inline; do not commit any generated `.srt`.

- [ ] **Step 5: Finish the branch**

Use superpowers:finishing-a-development-branch (open a PR; this is Tier-2, ultra-review deferred to the release cut per the standing decision).

---

## Self-Review (run before dispatching)

- **Spec coverage:** model choice (silero-lang95) → Task 2; windowing ≥15s → Tasks 1, 3; confidence + english-prob gate → Task 1 (`window_is_foreign`); decided-verdict contract (no double-gating) → Task 3 returns the classified list directly, bypassing `classify_utterances`; translate-arbiter → Task 4; fallback to subgen when model absent → Task 5 (`local_lid=None`), Task 6 Step 1; `[lid]` packaging → Task 5; OFF-by-default → Task 6; real-model smoke incl. the German film → Task 6.
- **Placeholder scan:** none — model URL/SHA256, node names, English index, and the label-map source are all concrete. `lid_min_confidence`/`lid_max_english_prob` defaults (0.5 / 0.25) are set from the spike and finalised in the Task 6 smoke.
- **Type consistency:** `assemble_windows` returns `(start, end, [idx])`; `expand_window_verdicts` consumes that shape; `LocalLidBackend.classify` returns `list[(utterance, bool)]` = the same type `merge_foreign_spans`/`is_mostly_foreign` consume; `subgen_translate`/`TranslateFn`/`_build_cues` all use `(text, source_lang)`; `classify_samples`/`_verdict_from_logits` return `(lang, top_prob, english_prob)` used by `window_is_foreign`.

---

## Execution Handoff

Two options: **(1) Subagent-Driven (recommended)** — fresh subagent per task, two-stage review between tasks; **(2) Inline Execution** — batch with checkpoints. Recommend option 1 given the slice-1 precedent.
