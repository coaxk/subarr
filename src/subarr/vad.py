"""#110 / #65 — silero speech detection + window selection.

Why this exists: [`audio_sampler.py`](audio_sampler.py) locates listen/clip
windows with ffmpeg `silencedetect`, which finds *silence*. Its complement
("non-silent") is NOT speech — it includes music, action SFX, ambience. On
real libraries that means the picker frequently lands on a loud music sting
with no dialogue (the observed "~90% of review clips have no voice").

silero VAD detects actual SPEECH, so the window can be placed inside it.
This module is split deliberately:

  - `select_speech_window(...)` — PURE placement logic (no model, no I/O),
    fully unit-tested. Given speech ranges, pick where the window goes.
  - `detect_speech_ranges(...)` — silero/onnxruntime I/O, behind an
    availability gate; added in the next step.

silero is an OPT-IN dependency (onboarding checkbox; pulled on demand or via
the `subarr[vad]` extra). When it is unavailable, callers fall back to the
existing silencedetect behaviour — never a hard failure.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)

# Range = (start_s, end_s)
Range = tuple[float, float]

# silero model is pulled on opt-in; path overridable for tests / bake-in.
_MODEL_ENV = "SUBARR_VAD_MODEL_PATH"


def select_speech_window(
    speech_ranges: list[Range],
    duration_s: float,
    target_len: float = 12.0,
) -> float | None:
    """Return the start offset (s) for a ``target_len`` window placed inside
    the most substantial speech region, clamped to ``[0, duration-target]``.

    Returns ``None`` when there is no detected speech (caller falls back to
    the silencedetect picker)."""
    if not speech_ranges:
        return None
    # The region with the most speech is the safest bet for "hear dialogue".
    start, end = max(speech_ranges, key=lambda r: r[1] - r[0])
    mid = (start + end) / 2.0
    candidate = mid - target_len / 2.0
    upper = max(0.0, duration_s - target_len)
    candidate = max(0.0, min(candidate, upper))
    return round(candidate, 2)


def normalize_speech_ranges(
    ranges: list[Range],
    gap_merge: float = 0.3,
    min_speech: float = 0.4,
) -> list[Range]:
    """Clean silero's raw speech timestamps: sort, merge ranges separated by
    a gap <= ``gap_merge`` (brief pauses inside one utterance), then drop
    anything shorter than ``min_speech`` (single-word blips / false fires)."""
    if not ranges:
        return []
    ordered = sorted(ranges, key=lambda r: r[0])
    merged: list[list[float]] = [list(ordered[0])]
    for start, end in ordered[1:]:
        if start - merged[-1][1] <= gap_merge:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(s, e) for s, e in merged if e - s >= min_speech]


def _model_path() -> str | None:
    """Resolve the silero ONNX model: explicit env override, else the
    on-demand cache under the config dir. Returns None if not present."""
    override = os.environ.get(_MODEL_ENV)
    if override and Path(override).is_file():
        return override
    default = Path(os.environ.get("SUBARR_CONFIG_DIR", "/config")) / "vad" / "silero_vad.onnx"
    return str(default) if default.is_file() else None


def vad_available() -> bool:
    """True iff the silero runtime (onnxruntime) AND the model are present.
    Never raises — callers fall back to silencedetect when False."""
    try:
        import onnxruntime  # noqa: F401
    except Exception:
        return False
    return _model_path() is not None


def detect_speech_ranges(path: str, track: int = 0) -> list[Range] | None:
    """Return normalized speech ranges (s) via silero VAD, or ``None`` when
    VAD is unavailable so the caller falls back to silencedetect. Never
    raises on a missing/broken model — degrades to None."""
    if not vad_available():
        return None
    try:
        raw = _run_silero(path, track=track)
    except Exception:  # model/runtime/decoding failure → graceful fallback
        log.warning("silero VAD failed for %s; falling back", path, exc_info=True)
        return None
    return normalize_speech_ranges(raw)


def _probs_to_ranges(
    probs: list[float],
    window_s: float,
    threshold: float = 0.5,
) -> list[Range]:
    """Pure: per-window speech probabilities → contiguous speech ranges (s).
    Window ``i`` covers ``[i*window_s, (i+1)*window_s)``; consecutive
    above-threshold windows fuse into one range."""
    ranges: list[Range] = []
    run_start: int | None = None
    for i, p in enumerate(probs):
        if p >= threshold:
            if run_start is None:
                run_start = i
        elif run_start is not None:
            ranges.append((run_start * window_s, i * window_s))
            run_start = None
    if run_start is not None:
        ranges.append((run_start * window_s, len(probs) * window_s))
    return ranges


# silero v5/v6 ONNX contract (16 kHz): 512-sample window (~32 ms), recurrent
# [2,1,128] state reset per file, int64 sample-rate scalar.
_SR = 16000
_WINDOW = 512
_WINDOW_S = _WINDOW / _SR
# Cap the scan like silencedetect does (#210): the first 20 min holds plenty
# of speech to place a window / stratify clips; keeps the per-window loop bounded.
_SCAN_WINDOW_S = 1200.0


def _decode_pcm_f32(path: str, track: int) -> "object":
    """ffmpeg → 16 kHz mono float32 PCM as a numpy array. ffmpeg is already a
    hard dependency of the audio sampler, so no new system requirement."""
    import shutil
    import subprocess

    import numpy as np

    ffmpeg = shutil.which("ffmpeg") or "ffmpeg"
    cmd = [
        ffmpeg, "-hide_banner", "-nostdin",
        "-t", f"{_SCAN_WINDOW_S:.0f}",
        "-i", path,
        "-map", f"0:a:{track}",
        "-ac", "1", "-ar", str(_SR),
        "-f", "f32le", "-",
    ]
    out = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=True).stdout
    return np.frombuffer(out, dtype="<f4")


def _run_silero(path: str, track: int = 0) -> list[Range]:
    """Live-verified I/O: decode audio, run the silero ONNX model windowed,
    return raw speech ranges (caller normalizes). No torch — onnxruntime +
    numpy only. Unit-tested logic lives in `_probs_to_ranges`."""
    import numpy as np
    import onnxruntime as ort

    model = _model_path()
    if model is None:
        raise RuntimeError("silero model not available")
    audio = _decode_pcm_f32(path, track)
    sess = ort.InferenceSession(model, providers=["CPUExecutionProvider"])
    state = np.zeros((2, 1, 128), dtype=np.float32)
    sr = np.array(_SR, dtype=np.int64)
    probs: list[float] = []
    n = len(audio)
    for off in range(0, n - _WINDOW + 1, _WINDOW):
        chunk = audio[off:off + _WINDOW].reshape(1, _WINDOW).astype(np.float32)
        out, state = sess.run(["output", "stateN"], {"input": chunk, "state": state, "sr": sr})
        probs.append(float(out[0][0]))
    return _probs_to_ranges(probs, _WINDOW_S)
