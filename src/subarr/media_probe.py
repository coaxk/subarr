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


@dataclass
class TrackMismatch:
    """#159: the default audio track isn't the show's original language, and a
    track that IS the original language exists. `native_audio_ordinal` is the
    1-based position among AUDIO tracks (what mkvpropedit's `track:aN` wants),
    NOT the global ffprobe stream index."""
    default_lang: str            # normalized ISO-639-1 of the current default track
    native_lang: str             # normalized ISO-639-1 of the original-language track
    native_audio_ordinal: int    # 1-based audio-track ordinal for mkvpropedit track:aN
    native_stream_index: int     # global ffprobe stream index (reference only)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def detect_default_track_mismatch(
    audio: "list[AudioStream]", original_language: str | None
) -> "TrackMismatch | None":
    """#159: detect a default-audio-track / original-language mismatch that
    causes double-translated subs (e.g. Russian show, German dub is default,
    Russian original is track 2 → Whisper transcribes the German dub).

    Fires only when ALL hold (mirrors the issue's gate):
      - original_language is known and non-English,
      - there are ≥2 separate audio streams with ≥2 distinct *tagged* languages
        (bilingual-in-one-track does NOT count — needs separate streams),
      - the default track (disposition.default, else the first audio stream) has
        a KNOWN language that differs from original_language, AND
      - a separate track tagged with original_language exists.

    Returns a TrackMismatch (carrying the 1-based audio ordinal of the native
    track for mkvpropedit) or None. Pure — no I/O."""
    from .langs import normalize_lang
    orig = normalize_lang(original_language)
    if not orig or orig in ("en", "und"):
        return None
    streams = list(audio or [])
    if len(streams) < 2:
        return None
    norm = [(a, normalize_lang(a.language)) for a in streams]
    tagged = [(a, n) for a, n in norm if n and n not in ("und",)]
    if len({n for _, n in tagged}) < 2:
        return None  # need ≥2 distinct tagged languages across separate streams
    # Default track: prefer disposition.default, else the first audio stream.
    default_stream = next((a for a in streams if a.default), streams[0])
    default_lang = normalize_lang(default_stream.language)
    # Require a KNOWN default language that differs — never guess on an untagged
    # default (that's an "unknown" case, not a confident mismatch).
    if not default_lang or default_lang == "und" or default_lang == orig:
        return None
    # First audio track tagged with the original language → the swap target.
    for ordinal, a in enumerate(streams, start=1):
        if normalize_lang(a.language) == orig:
            return TrackMismatch(
                default_lang=default_lang,
                native_lang=orig,
                native_audio_ordinal=ordinal,
                native_stream_index=int(a.index),
            )
    return None


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


# v1.1-O Layer 2: stream title parsing. Encoders default the `language` tag
# to "eng" but often write the actual language into the stream `title` —
# release groups put "VF" / "Français" / "Castellano" / "русский" etc there.
# When title disagrees with language tag, title is the stronger signal.
_TITLE_LANG_HINTS = {
    # 2-/3-letter outputs map to our ISO 639-2/B convention
    "fre": [r"\bfrench\b", r"\bfran[çc]ais\b", r"\bvf\b", r"\bvff\b", r"\bvfq\b", r"\btruefrench\b"],
    "spa": [r"\bspanish\b", r"\bespa[ñn]ol\b", r"\bcastellano\b", r"\blatino\b", r"\bspa\b"],
    "ger": [r"\bgerman\b", r"\bdeutsch\b", r"\bger\b", r"\bde\b(?:utsch)?"],
    "ita": [r"\bitalian\b", r"\bitaliano\b", r"\bita\b"],
    "rus": [r"\brussian\b", r"\brus\b", r"русск", r"русс"],
    "jpn": [r"\bjapanese\b", r"\bjap\b", r"\bjpn\b", r"日本"],
    "kor": [r"\bkorean\b", r"\bkor\b", r"한국"],
    "chi": [r"\bchinese\b", r"\bmandarin\b", r"\bcantonese\b", r"\bchi\b", r"\bzh\b", r"中文"],
    "por": [r"\bportuguese\b", r"\bportugu[êe]s\b", r"\bbrazil\b", r"\bbr\b", r"\bpor\b"],
    "dut": [r"\bdutch\b", r"\bnederlands\b", r"\bned\b", r"\bnl\b"],
    "swe": [r"\bswedish\b", r"\bsvenska\b", r"\bswe\b"],
    "nor": [r"\bnorwegian\b", r"\bnorsk\b", r"\bnor\b"],
    "dan": [r"\bdanish\b", r"\bdansk\b", r"\bdan\b"],
    "fin": [r"\bfinnish\b", r"\bsuomi\b", r"\bfin\b"],
    "pol": [r"\bpolish\b", r"\bpolski\b", r"\bpol\b"],
    "ara": [r"\barabic\b", r"\bar\b", r"عرب"],
    "hin": [r"\bhindi\b", r"\bhin\b", r"हिंदी"],
    "tur": [r"\bturkish\b", r"\btürk\b", r"\btur\b"],
    "eng": [r"\benglish\b", r"\beng\b"],
}

_TITLE_RES = {
    code: [re.compile(p, re.IGNORECASE) for p in patterns]
    for code, patterns in _TITLE_LANG_HINTS.items()
}


def parse_title_lang(title: str | None) -> str | None:
    """v1.1-O Layer 2: extract language hint from stream title. Returns
    3-letter ISO 639-2/B code or None. First-match-wins, with English
    explicitly checked LAST so a title like 'French DTS [English subs]'
    correctly resolves to French (the audio is French; English mention
    refers to the sub track in another stream)."""
    if not title:
        return None
    t = title.lower()
    # Check all non-English first
    for code, patterns in _TITLE_RES.items():
        if code == "eng":
            continue
        for p in patterns:
            if p.search(t):
                return code
    # Only return eng if nothing else matched
    for p in _TITLE_RES["eng"]:
        if p.search(t):
            return "eng"
    return None


def audio_lang_summary_with_titles(result: ProbeResult) -> tuple[list[str], list[str]]:
    """v1.1-O Layer 2: enhanced version of audio_lang_summary that uses
    stream title as a fallback signal when language tag is null/und/eng.

    Returns (langs, evidence_notes). The langs list reflects our best
    guess per stream — title wins over the tag when they disagree.

    Used by build_coverage to set audio_label_suspect when ffprobe's
    language tag is "eng" but the title clearly says otherwise."""
    langs: list[str] = []
    notes: list[str] = []
    for i, a in enumerate(result.audio):
        tag_lang = (a.language or "").lower() or None
        title_lang = parse_title_lang(a.title)
        chosen = tag_lang or "und"
        if title_lang and title_lang != tag_lang:
            # Title disagrees with tag (or tag is null) — prefer title.
            chosen = title_lang
            if tag_lang and tag_lang != "und":
                notes.append(f"track {i}: tag='{tag_lang}' but title says '{title_lang}'")
            elif tag_lang is None:
                notes.append(f"track {i}: no tag, title says '{title_lang}'")
        if chosen not in langs:
            langs.append(chosen)
    return langs, notes
