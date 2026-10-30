"""#633: choose transcribe vs translate PER FILE. Pure: no I/O, no config.

subgen's global TRANSCRIBE_OR_TRANSLATE cannot serve a mixed-language library.
A user who wants en + es subtitles needs:

    source audio | Whisper task | output
    en           | transcribe   | en
    es           | transcribe   | es
    ja           | translate    | en

subgen accepts a per-request `task` already. What subarr lacked was the audio
language subgen will actually use. subgen picks it in this order (upstream
`choose_transcribe_language`):

  1. the forced language (subarr's audio_language_override)
  2. FORCE_DETECTED_LANGUAGE_TO
  3. PREFERRED_AUDIO_LANGUAGES, in list order, against the file's tracks
  4. the track flagged default

`resolve_source_language` mirrors that order; subarr-subgen v4.38 advertises
steps 2 and 3. The contract with subgen is the shared fixture
tests/fixtures/subgen_track_choice_cases.json, byte-identical in both repos.

Vocabulary: W is the user's wanted subtitle languages, L the source language
(None = unknown), `task(L, W)` as in `choose_task`.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from .langs import UPSTREAM_ISO639_2_TO_1, WHISPER_LANGUAGES, normalize_lang

Task = Literal["transcribe", "translate"]


def subgen_track_language(raw_tag: str | None) -> str | None:
    """The ISO 639-1 code subgen derives from one audio track's language tag.

    Mirrors upstream `LanguageCode.from_iso_639_2`: a code comes back ONLY when
    `raw_tag` is exactly a three-letter ISO 639-2/T or /B code upstream knows.
    Upstream compares case-sensitively and does not strip. Two-letter tags
    (`en`), `und`, empty, None and unknown codes all give None.

    Deliberately NOT `normalize_lang`: that accepts two-letter codes and names,
    so subgen and subarr would disagree on a file tagged `en`.

    KNOWN DIVERGENCE, documented and not fixed: subarr's probe store lowercases
    tags (media_probe.py), so a file tagged `ENG` reads as `en` here while
    subgen sees an unknown track. Such tags are rare, and fixing it would mean
    storing raw tags in the probe store.
    """
    if not raw_tag:
        return None
    return UPSTREAM_ISO639_2_TO_1.get(raw_tag)


def known_language(value: Any) -> str | None:
    """`value` as a lowercase ISO 639-1 code (or Whisper's 3-letter `haw` /
    `yue`), or None when it is not a language this codebase can name."""
    if not isinstance(value, str):
        return None
    code = normalize_lang(value)
    if not code or code == "und":
        return None
    return code if code in WHISPER_LANGUAGES else None


def resolve_source_language(
    tracks: Sequence[tuple[str | None, bool]],
    *,
    forced: str | None,
    force_detected: str | None,
    preferred: Sequence[str | None],
) -> str | None:
    """The audio language subgen will use for one file, or None if unknown.

    `tracks` are (raw ffprobe tag, default flag) in ffprobe stream order.
    `forced` and `force_detected` are ISO 639-1 codes (callers pass
    `normalize_lang(x)`, and an unmappable value as None).
    """
    if forced:
        return forced
    if force_detected:
        return force_detected
    track_langs = [subgen_track_language(tag) for tag, _default in tracks]
    for p in preferred:
        if p not in track_langs:
            # No track matches this entry (a None entry matches only an
            # unknown-language track), so look at the next one.
            continue
        if p is not None:
            return p
        # A None entry matched an unknown-language track. Upstream treats the
        # falsy LanguageCode.NONE as "no preferred match" and falls through to
        # the default track, which skips every LATER entry too.
        break
    for tag, default in tracks:
        if default:
            return subgen_track_language(tag)
    return None


def choose_task(source: str | None, wanted: Collection[str]) -> Task | None:
    """transcribe when the source is wanted, translate (to English) when it is
    not but English is, otherwise None (send no task; subgen's global applies)."""
    if source is None:
        return None
    if source in wanted:
        return "transcribe"
    if "en" in wanted:
        return "translate"
    return None


def producible_for_task(task: Task, source: str | None) -> set[str]:
    """The subtitle languages one job with this task can write."""
    if task == "translate":
        return {"en"}
    return {source} if source else set()


@dataclass(frozen=True)
class PerFileTaskConfig:
    wanted: frozenset[str]
    preferred: tuple[str | None, ...]
    force_detected: str | None


def normalize_wanted(wanted: Iterable[Any] | None) -> list[str]:
    """Wanted codes: normalized, deduplicated, order-preserving; empty and
    `und` entries dropped. Unknown codes are kept (callers that must reject
    them use `known_language`)."""
    out: list[str] = []
    for w in wanted or ():
        code = normalize_lang(w) if isinstance(w, str) else None
        if code and code != "und" and code not in out:
            out.append(code)
    return out


def active_config(
    settings_enabled: bool, wanted: Iterable[Any] | None, caps: Any
) -> PerFileTaskConfig | None:
    """The per-file configuration, or None when the feature is inert.

    Non-None only when ALL hold: the setting is on, W is non-empty after
    normalization, subgen accepts a per-request task, subgen advertised its
    preferred audio languages, and every advertised entry was readable. There
    is no fallback to `eng` for an unadvertised list: guessing it would predict
    a track subgen may never pick.
    """
    if not settings_enabled or caps is None:
        return None
    norm = normalize_wanted(wanted)
    if not norm:
        return None
    if not getattr(caps, "per_request_task", False):
        return None
    preferred = getattr(caps, "preferred_audio_languages", None)
    if preferred is None:
        return None
    if getattr(caps, "preferred_audio_languages_unreadable", ()):
        return None
    if getattr(caps, "force_detected_language_unreadable", None):
        return None
    return PerFileTaskConfig(
        wanted=frozenset(norm),
        preferred=tuple(preferred),
        force_detected=getattr(caps, "force_detected_language_to", None) or None,
    )


__all__ = [
    "PerFileTaskConfig",
    "active_config",
    "choose_task",
    "known_language",
    "normalize_wanted",
    "producible_for_task",
    "resolve_source_language",
    "subgen_track_language",
]
