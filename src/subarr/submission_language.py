"""What to tell subgen about a file's audio language, decided when a job is
SUBMITTED (#573, #570).

#573: only the manual queue paths (Queue requeue, Gaps "queue", Aftercare
re-run) ever looked up the verified language. The scheduler's auto-queue,
backfill and folder scans enqueued with no override, the feeder forwarded the
job's empty override, and a file the user had verified as French but whose
track is tagged English was skipped by `SKIP_IF_AUDIO_LANGUAGES=eng` every time
it was queued automatically. Deciding at submit covers every producer in one
place, and a verdict set after the job was queued still applies.

#570: a MULTILINGUAL verdict sends no override by design (subgen self-detects
per chunk), but subgen then falls back to the file's tag, and a bilingual file
tagged `eng` (Maximilian: German and French) is skipped although the user has
said what it is. For those files, and only when it is safe, subarr sends
`bypass_skip` instead, so subgen transcribes with its own language detection.

`bypass_skip` switches off EVERY subgen skip rule for the request, including
"a subtitle already exists". So it is sent only when all of these hold:
  - the verdict for the file is multilingual;
  - the connected subgen advertises `bypass_skip`;
  - its advertised skip list would refuse this file on the TAGGED audio
    language (the only skip this is meant to get past), which needs the probe;
  - no full (not forced) text subtitle in the language subgen would write is
    already next to the file.
Anything that cannot be established leaves the decision at "no override".
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from .langs import normalize_lang
from .log_safe import scrub

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class SubmissionLanguage:
    override: str | None
    bypass_skip: bool
    reason: str


def _norm(code: Any) -> str:
    raw = str(code or "").strip().lower()
    return normalize_lang(raw) or raw


def _tagged_audio_languages(probe_store: Any, canonical: str) -> set[str] | None:
    """Normalized language tags on the file's audio streams, or None when the
    file has not been probed (we cannot tell what subgen will read)."""
    if probe_store is None:
        return None
    try:
        probe = probe_store.get(canonical)
    except Exception:  # noqa: BLE001 - an unreadable probe means "cannot tell"
        return None
    if probe is None:
        return None
    return {_norm(a.language) for a in (getattr(probe, "audio", None) or []) if a.language}


def _output_languages(caps: Any, verdict: Any) -> set[str]:
    """Languages subgen could write for this job. Translate mode always writes
    English (or whatever SUBTITLE_LANGUAGE_NAME names the file); transcribe
    mode writes the audio's own language, which for a multilingual file is any
    of the verdict's languages. Unknown mode: both, to stay conservative."""
    mode = (getattr(caps, "transcribe_or_translate", None) or "").strip().lower()
    name = _norm(getattr(caps, "subtitle_language_name", None) or "en")
    spoken = {_norm(c) for c in (getattr(verdict, "lang_codes", None) or []) if c}
    if mode == "translate":
        return {name, "en"}
    if mode == "transcribe":
        return spoken
    return spoken | {name, "en"}


def _has_full_text_sidecar(canonical: str, languages: set[str]) -> bool:
    """A full (not forced) .srt next to the file in any of `languages`."""
    from .paths import PathOutsideRootError, canonical_to_fs, srt_sidecar_names

    try:
        video = canonical_to_fs(canonical)
    except PathOutsideRootError:
        return True  # cannot look, so assume it exists: never bypass blind
    stem = video.stem
    for name in srt_sidecar_names(video.parent, stem):
        tail = name[len(stem) : -len(".srt")]
        tokens = [t.lower() for t in tail.split(".") if t]
        if "forced" in tokens:
            continue
        if any(_norm(t) in languages for t in tokens):
            return True
    return False


def resolve_submission(
    store: Any,
    canonical: str,
    *,
    caps: Any = None,
    probe_store: Any = None,
    caller: str = "feeder",
) -> SubmissionLanguage:
    """Decide the override / bypass for one file. Never raises."""
    from .audio_lang_store import resolve_audio_language_override

    if store is None:
        return SubmissionLanguage(None, False, "no verdict store")
    skip_list = getattr(caps, "skip_audio_languages", None)
    override = resolve_audio_language_override(
        store, canonical, caller=caller, log=log, skip_audio_languages=skip_list
    )
    if override:
        return SubmissionLanguage(override, False, f"override {override} from the verified language")

    # The resolver moved any replaced-file verdict here (#563), so this is exact.
    verdict = store.get(canonical)
    if verdict is None:
        return SubmissionLanguage(None, False, "no verified language")
    if getattr(verdict, "lang_class", "single") != "multi":
        return SubmissionLanguage(None, False, "verified language not forwarded (see the resolver log)")

    def no(reason: str) -> SubmissionLanguage:
        log.info("%s: no bypass for %s: multilingual verdict, but %s", caller, scrub(canonical), reason)
        return SubmissionLanguage(None, False, f"multilingual: {reason}")

    if not getattr(caps, "bypass_skip", False):
        return no("the connected subgen does not advertise bypass_skip")
    skip = {_norm(c) for c in (skip_list or ()) if c}
    if not skip:
        return no("subgen skips no audio language, so it will not refuse the file")
    tagged = _tagged_audio_languages(probe_store, canonical)
    if tagged is None:
        return no("the file has not been analyzed, so its tagged audio language is unknown")
    refused_on = sorted(tagged & skip)
    if not refused_on:
        return no(f"its tagged audio ({sorted(tagged) or 'none'}) is not on subgen's skip list")
    outputs = _output_languages(caps, verdict)
    if _has_full_text_sidecar(canonical, outputs):
        return no("a subtitle in the language subgen would write already exists")
    log.info(
        "%s: bypass_skip for %s: multilingual verdict %s, file tagged %s, which subgen skips",
        caller,
        scrub(canonical),
        getattr(verdict, "lang_codes", None),
        refused_on,
    )
    return SubmissionLanguage(None, True, f"bypass: multilingual, tagged {','.join(refused_on)}")


def resolve_for_job(job: Any, *, store: Any, caps: Any, probe_store: Any) -> SubmissionLanguage:
    """The decision to submit a pending job with. A job that already carries an
    explicit decision (an override, or bypass_skip) keeps it; any other job is
    resolved now, whichever producer queued it."""
    if getattr(job, "audio_language_override", None) or getattr(job, "bypass_skip", False):
        return SubmissionLanguage(
            getattr(job, "audio_language_override", None),
            bool(getattr(job, "bypass_skip", False)),
            "decided when queued",
        )
    try:
        return resolve_submission(store, job.canonical_path, caps=caps, probe_store=probe_store)
    except Exception as e:  # noqa: BLE001 - a lookup failure must not block the submit
        log.warning("feeder: language lookup failed for %s: %s", scrub(job.canonical_path), e)
        return SubmissionLanguage(None, False, "lookup failed")


_LANGUAGE_NAMES = {
    "en": "English", "fr": "French", "de": "German", "it": "Italian", "es": "Spanish", "pt": "Portuguese",
    "nl": "Dutch", "sv": "Swedish", "da": "Danish", "no": "Norwegian", "fi": "Finnish", "pl": "Polish",
    "ru": "Russian", "sr": "Serbian", "hr": "Croatian", "bg": "Bulgarian", "cs": "Czech", "el": "Greek",
    "he": "Hebrew", "tr": "Turkish", "ja": "Japanese", "ko": "Korean", "zh": "Chinese", "hi": "Hindi",
    "ar": "Arabic", "is": "Icelandic", "hu": "Hungarian", "ro": "Romanian", "uk": "Ukrainian",
}  # fmt: skip


def _lang_name(code: str) -> str:
    return _LANGUAGE_NAMES.get(code, code)


def explain_audio_language_skip(canonical: str, *, store: Any, caps: Any, probe_store: Any) -> dict | None:
    """#569: say why subgen skipped a file for its audio language, when subarr
    can tell, and what to do about it.

    subgen's /batch reply only counts skips, so a skipped row read "reason not
    in /batch response". subarr knows the file's TAGGED audio language (the
    probe), the connected subgen's skip list, and the user's verdict, which is
    enough to name the audio-language skip and the fix. Returns None when the
    tag does not explain the skip (the caller keeps its generic text).

    `skip_reason` is `audio_lang_expected` when the skip is correct (the user
    verified a language this subgen is set to skip, so there is nothing to do;
    the Queue page files it under Recently done), else `audio_lang`.
    """
    skip = {_norm(c) for c in (getattr(caps, "skip_audio_languages", None) or ()) if c}
    if not skip:
        return None
    tagged = _tagged_audio_languages(probe_store, canonical)
    if not tagged:
        return None
    hit = sorted(tagged & skip)
    if not hit:
        return None
    tag = ", ".join(_lang_name(c) for c in hit)
    lead = f"skipped: the audio is tagged {tag}, and this subgen skips {tag} audio."
    try:
        verdict = store.get(canonical) if store is not None else None
    except Exception:  # noqa: BLE001 - explaining must never break the Queue page
        verdict = None
    if verdict is None:
        return {
            "skip_reason": "audio_lang",
            "label": "skipped: audio language",
            "detail": f"{lead} If the audio is really another language, verify it in Review "
            "(or set a series language rule) and requeue.",
        }
    if getattr(verdict, "lang_class", "single") == "multi":
        if getattr(caps, "bypass_skip", False):
            nxt = "Requeue it: subarr asks subgen to bypass this skip for multilingual files."
        else:
            nxt = "This subgen cannot bypass the skip; a subarr-subgen with patch v4.23 or later can transcribe it."
        return {"skip_reason": "audio_lang", "label": "skipped: audio language", "detail": f"{lead} {nxt}"}
    lang = _norm(getattr(verdict, "lang_code", None))
    if lang in skip:
        return {
            "skip_reason": "audio_lang_expected",
            "label": f"skipped: {_lang_name(lang)} audio",
            "detail": f"{lead} You verified the audio as {_lang_name(lang)}, so this skip is expected.",
        }
    return {
        "skip_reason": "audio_lang",
        "label": "skipped: audio language",
        "detail": f"{lead} You verified it as {_lang_name(lang)}; requeue it and subarr sends that "
        "language, so subgen transcribes instead of skipping.",
    }


__all__ = ["SubmissionLanguage", "explain_audio_language_skip", "resolve_for_job", "resolve_submission"]
