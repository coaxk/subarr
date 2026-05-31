"""v1.1-O Layer 4++: Audio snippet extraction for the review modal.

Two operations:

1. `find_dialog_positions(path, n=3)` — scans the audio track with ffmpeg's
   silencedetect filter, computes non-silent ranges, returns N timestamps
   that fall inside the longest non-silent regions. Avoids sampling cold
   opens, scene transitions, or music-only segments.

2. `extract_sample(path, start, duration, track=0)` — pipes a short MP3
   from the requested offset. Caller streams the bytes back to the
   browser's <audio> element.

Both are async (asyncio.subprocess) so we don't block FastAPI's event loop.

silencedetect parameters:
- noise=-30dB — anything quieter than this counts as silence
- d=1.0     — at least 1 second of continuous silence to register a range

The default 5-second sample length is enough to hear several words of
dialog reliably; we use the middle of the chosen non-silent region as
the start position to put the words in the centre.
"""
from __future__ import annotations

import asyncio
import logging
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncIterator

log = logging.getLogger(__name__)

_FFMPEG = shutil.which("ffmpeg") or "ffmpeg"
_FFPROBE = shutil.which("ffprobe") or "ffprobe"

_SILENCE_START_RE = re.compile(r"silence_start:\s*([\d.]+)")
_SILENCE_END_RE = re.compile(r"silence_end:\s*([\d.]+)")


@dataclass
class DialogPositions:
    duration_s: float
    positions: list[float]   # start times (s) — middles of longest non-silent regions
    silence_ranges: list[tuple[float, float]]   # debug / UI reasoning
    detected_at: float       # epoch
    audio_tracks: int        # count of audio streams in file


# In-memory cache keyed by (canonical_path, track) so repeat opens are
# instant. Capped at 200 entries (drop oldest by detected_at). The data
# is cheap to recompute if evicted.
_position_cache: dict[tuple[str, int], DialogPositions] = {}
_CACHE_CAP = 200


async def _ffprobe_duration_and_tracks(path: str) -> tuple[float, int]:
    """Returns (duration_seconds, audio_track_count)."""
    proc = await asyncio.create_subprocess_exec(
        _FFPROBE, "-v", "error",
        "-show_entries", "format=duration:stream=index,codec_type",
        "-of", "default=noprint_wrappers=1",
        path,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    out, _ = await proc.communicate()
    text = out.decode("utf-8", "replace")
    duration = 0.0
    audio_count = 0
    for line in text.splitlines():
        if line.startswith("duration="):
            try:
                duration = float(line.split("=", 1)[1])
            except (ValueError, IndexError):
                pass
        elif line.startswith("codec_type=audio"):
            audio_count += 1
    return duration, audio_count


async def _silencedetect(path: str, track: int = 0,
                         noise_db: float = -30.0, min_silence: float = 1.0,
                         scan_timeout_s: float = 15.0,
                         scan_window_s: float = 1200.0) -> list[tuple[float, float]]:
    """Returns list of (silence_start, silence_end) tuples.
    Skips silence shorter than min_silence seconds.

    #210 speedup: prior to this we decoded the full audio track at native
    sample rate / stereo, which was 30+s on a 45-min file. Two changes:
      1. -t scan_window_s: cap at the first 20 min. Any TV episode fits
         entirely; only feature-length movies get a partial scan, and
         even there the first 20 min has plenty of non-silent ranges to
         spread 3 samples across.
      2. -ar 8000 -ac 1 downsamples to mono 8kHz before silencedetect.
         silencedetect only needs dB envelope — quality loss is
         irrelevant. Cuts decode CPU ~10x.
    Combined: ~30s → ~2-3s on a 45-min episode.
    Bumped default timeout 90s → 15s so the modal fails fast and falls
    back to even-spaced positions rather than hanging."""
    cmd = [
        _FFMPEG, "-hide_banner", "-nostdin",
        "-t", f"{scan_window_s:.0f}",
        "-i", path,
        "-map", f"0:a:{track}",
        "-ar", "8000", "-ac", "1",
        "-af", f"silencedetect=noise={noise_db}dB:d={min_silence}",
        "-f", "null", "-",
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=scan_timeout_s)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except Exception:
            pass
        return []
    text = stderr.decode("utf-8", "replace")
    starts: list[float] = []
    ends: list[float] = []
    for line in text.splitlines():
        m = _SILENCE_START_RE.search(line)
        if m:
            try:
                starts.append(float(m.group(1)))
            except ValueError:
                pass
            continue
        m = _SILENCE_END_RE.search(line)
        if m:
            try:
                ends.append(float(m.group(1)))
            except ValueError:
                pass
    # Pair them up; trailing silence_start without a matching end gets a
    # synthetic end at file duration handled by caller (we just truncate
    # the unmatched start here).
    n = min(len(starts), len(ends))
    return list(zip(starts[:n], ends[:n]))


def _non_silent_ranges(duration: float, silence: list[tuple[float, float]]
                       ) -> list[tuple[float, float]]:
    """Compute speech ranges as the complement of silence ranges within
    [0, duration]. Silence list is assumed sorted ascending by start."""
    if not silence:
        # No silence detected = whole file is "speech".
        return [(0.0, duration)] if duration > 0 else []
    silence = sorted(silence, key=lambda t: t[0])
    out: list[tuple[float, float]] = []
    cursor = 0.0
    for s_start, s_end in silence:
        if s_start > cursor + 0.1:
            out.append((cursor, s_start))
        cursor = max(cursor, s_end)
    if duration > cursor + 0.1:
        out.append((cursor, duration))
    return out


def _pick_positions(non_silent: list[tuple[float, float]], n: int,
                    sample_len: float = 5.0) -> list[float]:
    """Pick N start positions from the longest non-silent ranges,
    ordered to spread across the runtime (so user samples beginning /
    middle / end-ish rather than all from one cluster)."""
    if not non_silent:
        return []
    # Sort by length desc, take top N candidates, then re-sort by start
    # so the UI sample buttons appear in temporal order.
    sized = sorted(non_silent, key=lambda r: r[1] - r[0], reverse=True)[:n * 2]
    sized.sort(key=lambda r: r[0])
    out: list[float] = []
    for start, end in sized:
        if len(out) >= n:
            break
        length = end - start
        if length < 0.5:
            continue
        # Put sample window in the middle of the range, but back off
        # half-sample_len from the end so we don't overrun.
        mid = start + length / 2.0
        candidate = max(start, mid - sample_len / 2.0)
        if candidate + sample_len > end + 0.5:
            candidate = max(start, end - sample_len)
        out.append(round(candidate, 2))
    return out


async def find_dialog_positions(path: str, track: int = 0, n: int = 3,
                                use_cache: bool = True) -> DialogPositions:
    import time
    key = (path, track)
    if use_cache and key in _position_cache:
        return _position_cache[key]
    duration, audio_count = await _ffprobe_duration_and_tracks(path)
    silence = await _silencedetect(path, track=track) if duration > 0 else []
    # #210: silencedetect only scanned the first scan_window_s. Cap the
    # range we hand to the picker so it doesn't treat the unscanned tail
    # as one giant non-silent block and bias all samples there.
    scanned = min(duration, 1200.0) if duration > 0 else 0.0
    non_silent = _non_silent_ranges(scanned, silence)
    positions = _pick_positions(non_silent, n=n)
    # Fallback: if silencedetect found nothing usable (e.g. very short
    # file or all-music), just spread N points across the runtime.
    if not positions and duration > 5:
        step = duration / (n + 1)
        positions = [round(step * (i + 1), 2) for i in range(n)]
    result = DialogPositions(
        duration_s=round(duration, 2),
        positions=positions,
        silence_ranges=[(round(s, 2), round(e, 2)) for s, e in silence[:20]],
        detected_at=time.time(),
        audio_tracks=audio_count,
    )
    if use_cache:
        if len(_position_cache) >= _CACHE_CAP:
            # Evict oldest
            oldest_key = min(_position_cache, key=lambda k: _position_cache[k].detected_at)
            _position_cache.pop(oldest_key, None)
        _position_cache[key] = result
    return result


async def extract_sample(path: str, start_s: float, duration_s: float = 5.0,
                         track: int = 0) -> AsyncIterator[bytes]:
    """Yield MP3 bytes for the requested audio window. Caller streams
    to client. Uses libmp3lame at 64k for size; browsers play it natively.

    Track is the audio stream index (0 = first audio track)."""
    cmd = [
        _FFMPEG, "-hide_banner", "-nostdin",
        "-ss", f"{max(0.0, start_s):.2f}",
        "-i", path,
        "-t", f"{max(0.5, duration_s):.2f}",
        "-vn",
        "-map", f"0:a:{track}",
        "-c:a", "libmp3lame",
        "-b:a", "64k",
        "-f", "mp3",
        "-",
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        while True:
            chunk = await proc.stdout.read(8192)
            if not chunk:
                break
            yield chunk
    finally:
        try:
            await proc.wait()
        except Exception:
            pass
