"""#357 — confident-multilingual classifier over per-chunk detection.

`parse_robust_detect` (arena.py) now surfaces `chunks_conf`: an ordered list of
(language, probability) per Whisper chunk. This module turns that into the set of
languages that are HIGH-CONFIDENCE (>=1 chunk with probability >= T), which is
what separates a genuinely multilingual file (The Beasts: 3 confident, distinct
langs) from a confused one (3 low-confidence guesses on music/silence).

Rule (design #357):
  high_conf_langs = [lang : exists a chunk (lang, p) with p is not None and p >= T]
  len >= 2  -> confident multilingual (the returned list, first-appearance order)
  len == 1  -> single language (low-conf noise chunks in other langs are ignored)
  len == 0  -> confused (empty list) -> caller falls back to the tag (unchanged)
"""

from __future__ import annotations


def classify_high_conf_langs(
    chunks_conf: list[tuple[str | None, float | None]],
    threshold: float,
) -> list[str]:
    """Return the ordered, de-duplicated list of languages that have at least
    one chunk whose probability >= threshold. 'und' and None-language chunks are
    ignored. A None probability is treated as below threshold (pre-#396 subgen)."""
    out: list[str] = []
    seen: set[str] = set()
    for lang, prob in chunks_conf or []:
        if not lang or lang == "und":
            continue
        if prob is None or prob < threshold:
            continue
        if lang in seen:
            continue
        seen.add(lang)
        out.append(lang)
    return out
