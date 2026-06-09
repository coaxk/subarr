"""#90 — Whisper-verified audio language (ground truth, not metadata).

Coverage's audio-language columns come from ffprobe tags + Sonarr originalLanguage
+ Tautulli/Plex hints — none of which LISTEN to the audio, so a mistagged file
(tagged `rus` but actually Korean, and Sonarr agreeing) sails through every
cross-check. The only thing that breaks the tie is hearing the audio.

`verify_audio_language` does exactly that: robust multi-chunk detection via subgen
`/detect_language_robust` (samples the MIDDLE of the file, majority vote — skips
the intro/credits/silence where single-pass Whisper hallucinates `en`/`nynorsk`),
normalizes to ISO-639-1, and persists a whisper-sourced verification. It returns
None — and stores nothing — when there's no real majority, so we never record a
guess. (Slice 1: detect + persist. The classification override + UI are #90
slices 2–3.)
"""

from __future__ import annotations

import logging

from .langs import normalize_lang
from .paths import canonical_to_subgen_batch

log = logging.getLogger(__name__)


async def verify_audio_language(
    subgen, store, canonical_path: str, *, to_subgen=canonical_to_subgen_batch, min_agreement: float = 0.5
):
    """Detect + persist a file's spoken language. Returns (iso_lang, confidence)
    on a clear majority, else None (nothing stored). Gate the caller on
    capabilities.robust_language_detection; this degrades to None on any error."""
    try:
        resp = await subgen.detect_language_robust(to_subgen(canonical_path))
    except Exception as e:  # subgen down / 404 on vanilla → no verification
        log.debug("audio-language verify failed for %s: %s", canonical_path, e)
        return None
    agg = (resp or {}).get("aggregate") or {}
    lang = normalize_lang(agg.get("language"))
    n_agree = agg.get("n_agreeing") or 0
    n_total = agg.get("n_total") or 0
    if not lang or lang == "und" or not n_total or (n_agree / n_total) < min_agreement:
        return None  # no real majority — never store a guess
    confidence = round(n_agree / n_total, 2)
    store.upsert(
        canonical_path=canonical_path, lang_code=lang, source="whisper", confidence=confidence, evidence=resp
    )
    return (lang, confidence)
