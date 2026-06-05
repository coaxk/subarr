"""Language-code normalization — one source of truth across the codebase.

Different sources speak different dialects of "language": Whisper/faster-whisper
returns ISO-639-1 codes ('ko') from /detect_language_robust but full names
('korean') from a single /asr pass; ffprobe audio-stream tags are ISO-639-2
('kor'); Sonarr uses English names ('Korean'). To group/compare across them
(the per-language herd view #26, and the audio-language override #90) everything
must collapse to ONE canonical form: lowercase ISO-639-1.

`normalize_lang` is intentionally forgiving — unknown inputs pass through
lowercased rather than getting dropped, so a missing map entry degrades to a
slightly-split bucket, never a crash or data loss.
"""
from __future__ import annotations

# English name → ISO-639-1. Seeded with the languages seen in testing + the
# common European/Asian set; extend as needed (unknowns pass through).
_NAME_TO_ISO = {
    "english": "en", "french": "fr", "german": "de", "spanish": "es",
    "italian": "it", "portuguese": "pt", "dutch": "nl", "swedish": "sv",
    "norwegian": "no", "norwegian nynorsk": "nn", "nynorsk": "nn", "danish": "da",
    "finnish": "fi", "icelandic": "is", "russian": "ru", "ukrainian": "uk",
    "polish": "pl", "czech": "cs", "slovak": "sk", "croatian": "hr",
    "serbian": "sr", "bulgarian": "bg", "slovenian": "sl", "greek": "el",
    "turkish": "tr", "hebrew": "he", "arabic": "ar", "persian": "fa",
    "hindi": "hi", "korean": "ko", "japanese": "ja", "chinese": "zh",
    "mandarin": "zh", "thai": "th", "vietnamese": "vi", "indonesian": "id",
    "romanian": "ro", "hungarian": "hu", "catalan": "ca",
}

# ISO-639-2/B (and a few /T) three-letter → ISO-639-1, for ffprobe audio tags.
_ISO3_TO_ISO1 = {
    "eng": "en", "fre": "fr", "fra": "fr", "ger": "de", "deu": "de",
    "spa": "es", "ita": "it", "por": "pt", "dut": "nl", "nld": "nl",
    "swe": "sv", "nor": "no", "nno": "nn", "dan": "da", "fin": "fi",
    "isl": "is", "rus": "ru", "ukr": "uk", "pol": "pl", "cze": "cs",
    "ces": "cs", "slo": "sk", "slk": "sk", "hrv": "hr", "srp": "sr",
    "bul": "bg", "slv": "sl", "gre": "el", "ell": "el", "tur": "tr",
    "heb": "he", "ara": "ar", "per": "fa", "fas": "fa", "hin": "hi",
    "kor": "ko", "jpn": "ja", "chi": "zh", "zho": "zh", "tha": "th",
    "vie": "vi", "ind": "id", "rum": "ro", "ron": "ro", "hun": "hu", "cat": "ca",
}


def normalize_lang(value: str | None) -> str | None:
    """Collapse any language label (full name, ISO-639-1, or ISO-639-2) to a
    lowercase ISO-639-1 code. Returns the input lowercased if unmapped, or None
    for falsy input. Never raises."""
    if not value:
        return value
    s = str(value).strip().lower()
    if not s or s in ("und", "unknown"):
        return s or None
    if s in _NAME_TO_ISO:
        return _NAME_TO_ISO[s]
    if len(s) == 3 and s in _ISO3_TO_ISO1:
        return _ISO3_TO_ISO1[s]
    return s  # already ISO-639-1, or unknown → pass through lowercased
