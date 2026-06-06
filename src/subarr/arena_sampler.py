"""#131 — build a representative SAMPLE clip for a tuning sweep.

Running recipes on a whole 90-min episode is both hopeless (slow) and useless
for discrimination: validated in the Tier-B work (data/tournament), on CLEAN
dialogue every config near-ties — they only separate on the **hallucination
bait** (silence / music after speech), where bad configs invent lines and the
silence/repeat judges punish them. `clean_film` looped to 5 while `noisy_robust`
scored 69 *only because* the clip had a music tail.

So we deliberately assemble a short clip that MIXES strata:
  1. dense speech         — transcription quality
  2. a speech→silence boundary — the transition where hallucination starts
  3. a long silence/music stretch — pure hallucination bait

…concatenated into one ~75s clip. Picking "densest speech" (the obvious move)
would hide the exact failure mode the lab exists to find — hence the strata.

`select_windows` is pure + unit-tested; `build_sample` shells ffmpeg + VAD
(needs the runtime, live-verified).
"""
from __future__ import annotations

import logging
import subprocess
import tempfile
from pathlib import Path

from . import vad

log = logging.getLogger(__name__)

WINDOW_S = 30.0          # length of each strata window (matches Tier-B: all clips were 30s)
MAX_WINDOWS = 3          # speech + boundary + silence
_MIN_SILENCE_GAP = 6.0   # only treat a gap this long as a "silence" strata


def _clamp_start(start: float, duration: float, win: float) -> float:
    return round(max(0.0, min(start, max(0.0, duration - win))), 2)


def select_windows(ranges: list[tuple[float, float]], duration: float,
                   win: float = WINDOW_S) -> list[dict]:
    """Pick up to 3 strata window starts: dense-speech, speech→silence
    boundary, and the longest silence/music gap. Pure (no I/O) so it's
    unit-testable. De-dups windows whose starts are within half a window.

    Returns a list of {start, len, kind}. Falls back to a single window at 0
    when there's no usable speech (all-music/undetected audio)."""
    picks: list[dict] = []

    def _add(start, kind):
        if start is None:
            return
        s = _clamp_start(start, duration, win)
        if any(abs(s - p["start"]) < win / 2 for p in picks):
            return  # too close to an existing window — would overlap
        picks.append({"start": s, "len": win, "kind": kind})

    if not ranges:
        return [{"start": 0.0, "len": min(win * MAX_WINDOWS, duration), "kind": "fallback"}]

    # 1. dense speech — the most substantial speech region.
    _add(vad.select_speech_window(ranges, duration, target_len=win), "speech")

    # 2. boundary — tail of the largest speech region + the silence that
    #    follows it (speech leads ~8s in, then the gap). This is where a
    #    hallucinating config keeps "talking" into the silence.
    start, end = max(ranges, key=lambda r: r[1] - r[0])
    _add(end - (win - 8.0), "boundary")

    # 3. silence/music — centre a window on the LONGEST non-speech gap
    #    (between ranges, or the lead-in / tail). Pure hallucination bait.
    gaps: list[tuple[float, float]] = []
    ordered = sorted(ranges, key=lambda r: r[0])
    if ordered[0][0] > 0:
        gaps.append((0.0, ordered[0][0]))
    for a, b in zip(ordered, ordered[1:]):
        gaps.append((a[1], b[0]))
    if ordered[-1][1] < duration:
        gaps.append((ordered[-1][1], duration))
    if gaps:
        gs, ge = max(gaps, key=lambda g: g[1] - g[0])
        if ge - gs >= _MIN_SILENCE_GAP:
            _add((gs + ge) / 2.0 - win / 2.0, "silence")

    return picks[:MAX_WINDOWS]


def _duration_s(path: str) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", path],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=True,
    ).stdout.decode().strip()
    return float(out)


def _cut_clip(fs_path: str, start: float, length: float, out_path: str, track: int = 0) -> None:
    """Extract one window → 16 kHz mono wav (audio-only keeps the upload tiny).
    `track` selects the Nth audio stream (0-based) for multi-track files (an
    original + a dub get swept separately, each from its own track)."""
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
           "-ss", str(start), "-i", fs_path, "-t", str(length),
           "-map", f"0:a:{track}", "-ar", "16000", "-ac", "1", out_path]
    subprocess.run(cmd, check=True, stderr=subprocess.PIPE)


def build_samples(fs_path: str, *, out_dir: str | None = None, track: int = 0) -> list[dict]:
    """VAD `fs_path` and cut each strata window into its OWN 30s clip.

    Returns a list of {path, kind, ranges} — one SEPARATE clip per stratum
    (dense-speech / boundary / silence). The arena judges each clip
    independently and aggregates: this is the validated Tier-B methodology —
    a config is only trustworthy if it wins ACROSS separate clips, not on one.
    Caller owns the clip files (delete each when done).

    `ranges` are computed on each cut clip (its own timeline) so the
    tournament's silence/coverage judges line up with that clip's audio.
    """
    raw = vad.detect_speech_ranges(fs_path)
    ranges = vad.normalize_speech_ranges(raw) if raw else []
    duration = _duration_s(fs_path)
    windows = select_windows(ranges, duration)
    log.info("arena sampler: %s → %d separate strata clips (%s)", Path(fs_path).name,
             len(windows), ", ".join(w["kind"] for w in windows))

    base = Path(out_dir or tempfile.gettempdir())
    key = abs(hash((fs_path, duration, track))) % 10**8   # track in key so multi-track clips don't collide
    clips: list[dict] = []
    for i, w in enumerate(windows):
        clip_path = str(base / f"arena-sample-{key}-t{track}-{i}-{w['kind']}.wav")
        _cut_clip(fs_path, w["start"], w["len"], clip_path, track=track)
        clip_ranges = vad.detect_speech_ranges(clip_path) or []
        clips.append({"path": clip_path, "kind": w["kind"], "ranges": clip_ranges})
    return clips
