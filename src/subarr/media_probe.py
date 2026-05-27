"""ffprobe wrapper + structured embedded-sub / audio extraction.

One canonical call per file:
    ffprobe -v error -show_entries
        format=duration:
        stream=index,codec_type,codec_name:
        stream_tags=language,title:
        stream_disposition=default,forced,comment,hearing_impaired
    -of json <path>

We parse the streams list, splitting audio + subtitle. Both 2-letter (ISO
639-1) and 3-letter (ISO 639-2/B) language tags appear in practice, so we
normalise to lowercase strings without remapping.

Subtitle stream classification:
- 'sdh' iff disposition.hearing_impaired OR title matches SDH/CC/hearing
  patterns. Bazarr/jellyfin/etc are inconsistent about disposition flags
  so the title is the more reliable signal in practice.
- 'forced' from disposition.forced OR title containing 'forced'.
- 'commentary' from disposition.comment OR title containing 'commentary'.

We deliberately keep this defensive: when in doubt, the helper functions
classify a sub as 'not usable' rather than 'usable' so we don't suppress
a Bazarr-wanted gap based on a false-positive embedded match.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


# 3-letter ISO 639-2/B → 2-letter equivalents we care about. Subgen uses
# 'eng'; some media tag 'en'. Both should match 'is this English?'.
ENGLISH_TAGS = {"en", "eng"}

_SDH_PAT = re.compile(r"\b(SDH|HI|hearing[\s-]?impaired|CC|closed[\s-]?caption|deaf)\b", re.I)
_FORCED_PAT = re.compile(r"\bforced\b", re.I)
_COMMENTARY_PAT = re.compile(r"\bcommentary\b|\bdirector'?s?\b", re.I)


@dataclass
class AudioStream:
    index: int
    language: str | None
    codec: str | None
    title: str | None
    default: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SubtitleStream:
    index: int
    language: str | None
    codec: str | None
    title: str | None
    default: bool
    forced: bool
    sdh: bool
    commentary: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ProbeResult:
    canonical_path: str
    duration_s: float | None = None
    audio: list[AudioStream] = field(default_factory=list)
    subtitles: list[SubtitleStream] = field(default_factory=list)
    raw_error: str | None = None
    probed_at: float = 0.0
    cached: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "canonical_path": self.canonical_path,
            "duration_s": self.duration_s,
            "audio": [a.to_dict() for a in self.audio],
            "subtitles": [s.to_dict() for s in self.subtitles],
            "raw_error": self.raw_error,
            "probed_at": self.probed_at,
            "cached": self.cached,
        }


class ProbeError(RuntimeError):
    pass


def _classify_subtitle(stream: dict) -> SubtitleStream:
    disp = stream.get("disposition") or {}
    tags = stream.get("tags") or {}
    title = tags.get("title") or ""
    return SubtitleStream(
        index=int(stream.get("index", -1)),
        language=(tags.get("language") or "").lower() or None,
        codec=stream.get("codec_name"),
        title=title or None,
        default=bool(disp.get("default")),
        forced=bool(disp.get("forced")) or bool(_FORCED_PAT.search(title)),
        sdh=bool(disp.get("hearing_impaired")) or bool(_SDH_PAT.search(title)),
        commentary=bool(disp.get("comment")) or bool(_COMMENTARY_PAT.search(title)),
    )


def _classify_audio(stream: dict) -> AudioStream:
    disp = stream.get("disposition") or {}
    tags = stream.get("tags") or {}
    return AudioStream(
        index=int(stream.get("index", -1)),
        language=(tags.get("language") or "").lower() or None,
        codec=stream.get("codec_name"),
        title=tags.get("title") or None,
        default=bool(disp.get("default")),
    )


def parse_ffprobe_json(canonical_path: str, payload: dict) -> ProbeResult:
    result = ProbeResult(canonical_path=canonical_path)
    fmt = payload.get("format") or {}
    try:
        result.duration_s = float(fmt.get("duration")) if fmt.get("duration") else None
    except (TypeError, ValueError):
        result.duration_s = None
    for stream in (payload.get("streams") or []):
        kind = stream.get("codec_type")
        if kind == "audio":
            result.audio.append(_classify_audio(stream))
        elif kind == "subtitle":
            result.subtitles.append(_classify_subtitle(stream))
    return result


async def probe(path: Path, timeout_s: float = 30.0) -> ProbeResult:
    """Run ffprobe on `path`. Raises ProbeError on subprocess failure.
    `path` is a filesystem Path on subarr's mount."""
    if not path.exists():
        raise ProbeError(f"not found: {path}")
    if not path.is_file():
        raise ProbeError(f"not a file: {path}")

    args = [
        "ffprobe", "-v", "error",
        "-show_entries",
        "format=duration:"
        "stream=index,codec_type,codec_name:"
        "stream_tags=language,title:"
        "stream_disposition=default,forced,comment,hearing_impaired",
        "-of", "json",
        str(path),
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as e:
        raise ProbeError(f"ffprobe not installed: {e}") from e

    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise ProbeError(f"ffprobe timeout after {timeout_s}s: {path}")

    if proc.returncode != 0:
        raise ProbeError(
            f"ffprobe exit {proc.returncode}: {stderr.decode(errors='replace')[:300]}"
        )

    try:
        payload = json.loads(stdout.decode("utf-8", errors="replace") or "{}")
    except json.JSONDecodeError as e:
        raise ProbeError(f"ffprobe returned non-json: {e}") from e

    # Note: canonical_path will be filled in by the caller (it has the
    # canonical form; we only have a filesystem Path here).
    return parse_ffprobe_json("", payload)


# ─────────────────────────── classification helpers ─────────────────────────


def has_usable_embedded_english(result: ProbeResult) -> bool:
    """True if at least one subtitle stream is English AND not forced AND
    not commentary. SDH counts as usable per user feedback 2026-05-27:
    'I can play the SDH track in VLC, it IS English' — so the demote-
    score and the Bazarr rescan trigger apply to SDH too.

    Forced subs (only translate non-English dialogue snippets) and
    director's commentary tracks remain non-usable — those genuinely
    aren't full subtitles for the show."""
    for s in result.subtitles:
        if (s.language or "").lower() in ENGLISH_TAGS \
                and not s.forced and not s.commentary:
            return True
    return False


def has_forced_or_commentary_english(result: ProbeResult) -> bool:
    """True if an English sub exists but ONLY as forced or commentary —
    Coverage rows in this state get demoted but not fully suppressed.
    (Renamed from has_forced_or_sdh_english — SDH no longer counts here.)"""
    has_english = any((s.language or "").lower() in ENGLISH_TAGS for s in result.subtitles)
    if not has_english:
        return False
    return not has_usable_embedded_english(result)


# Back-compat shim: keep the old name for any callers we missed; points at
# the new partial check. Will be removed in a later cleanup.
has_forced_or_sdh_english = has_forced_or_commentary_english


def english_track_summary(result: ProbeResult) -> str | None:
    """Short label for the UI: 'EN' / 'EN(SDH)' / 'EN(forced)' /
    'EN(commentary)' / None.

    'EN(SDH)' is preserved as metadata so the Library Probe tab can show
    which tracks include hearing-impaired markers, but coverage scoring
    treats 'EN' and 'EN(SDH)' identically (see _score in coverage_engine).
    """
    if has_usable_embedded_english(result):
        # Prefer the clean label if there's a non-SDH English track;
        # otherwise note SDH so the UI is honest about what's there.
        for s in result.subtitles:
            if (s.language or "").lower() in ENGLISH_TAGS \
                    and not s.forced and not s.commentary and not s.sdh:
                return "EN"
        for s in result.subtitles:
            if (s.language or "").lower() in ENGLISH_TAGS \
                    and not s.forced and not s.commentary and s.sdh:
                return "EN(SDH)"
        return "EN"
    for s in result.subtitles:
        if (s.language or "").lower() in ENGLISH_TAGS:
            if s.forced:
                return "EN(forced)"
            if s.commentary:
                return "EN(commentary)"
    return None


def audio_lang_summary(result: ProbeResult) -> list[str]:
    """List of audio languages (lowercase, deduped, ordered) for UI display."""
    seen: list[str] = []
    for a in result.audio:
        lang = (a.language or "").lower() or "und"
        if lang not in seen:
            seen.append(lang)
    return seen
