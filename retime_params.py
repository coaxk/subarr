"""
retime_params.py
================
Per-language RetimeParams registry for the SRT re-timer (issue #408).

Usage
-----
    from retime_params import get_retime_params

    params = get_retime_params(lang="ja")   # → RetimeParams(target_cps=4, …)
    params = get_retime_params(lang="fr")   # → global default (17 CPS)
    params = get_retime_params(lang=None)   # → global default

The matrix was derived from:
  - Netflix Timed Text Style Guides (per-language CPS caps)
  - EBU R25 / BBC Subtitle Guidelines
  - Issue #359 sweep results (1 801 cues; global best = 17 CPS / 0 new overlaps)
  - Issue #408 stratified sweep (see scripts/retime_tune_stratified.py)

If the per-language corpus sweep confirms the matrix, this module ships
as-is.  If the sweep finds corpus-level differences, update the values
here and re-run the tests.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class RetimeParams:
    """Immutable timing parameters for the SRT re-timer."""

    target_cps: float
    """Characters per second ceiling.  Cues exceeding this are extended."""

    min_cue_ms: int
    """Minimum cue display duration in milliseconds."""

    min_gap_ms: int
    """Minimum silence gap between consecutive cues in milliseconds."""

    max_cue_ms: int
    """Maximum cue display duration in milliseconds."""

    def __repr__(self) -> str:          # pragma: no cover
        return (
            f"RetimeParams("
            f"target_cps={self.target_cps}, "
            f"min_cue_ms={self.min_cue_ms}, "
            f"min_gap_ms={self.min_gap_ms}, "
            f"max_cue_ms={self.max_cue_ms})"
        )


# ---------------------------------------------------------------------------
# Global default (shipped in PR #404; validated on 1 801 real subs)
# ---------------------------------------------------------------------------

GLOBAL_DEFAULT = RetimeParams(
    target_cps=17,
    min_cue_ms=1000,
    min_gap_ms=100,
    max_cue_ms=7000,
)
"""
Baseline RetimeParams for Latin, Cyrillic, Greek and all unrecognised codes.
Reduced critical-CPS cues from 22.9 % → 5.2 % with zero new overlaps on
the global 1 801-cue sweep (#359).
"""


# ---------------------------------------------------------------------------
# Per-language matrix
# ---------------------------------------------------------------------------
#
# Rationale for each language:
#
#  ja  (Japanese)
#      Netflix cap: 4 CPS.  Full-width kanji/kana; no word spaces;
#      each character carries 1–2 morphemes.  Standard JP broadcast
#      gap is 2 frames @ 24 fps ≈ 83 ms.
#
#  zh  (Chinese — Simplified & Traditional share the same cap)
#      Netflix cap: 9 CPS.  Full-width CJK; high information density.
#      A 16-char Chinese line ≈ 40+ Latin chars of meaning.
#
#  ko  (Korean)
#      Netflix cap: 12 CPS.  Hangul syllable blocks; denser than Latin
#      but spacing exists between words, so slightly faster than Chinese.
#
#  ar  (Arabic)
#      No Netflix-published CPS; EBU-equivalent is ~17 CPS.
#      Characters are alphabetic (not syllabic/logographic), joined
#      cursively — reading speed comparable to Latin scripts.
#      Keeping GLOBAL_DEFAULT target_cps; min_gap_ms unchanged.
#
#  he  (Hebrew)
#      Same reasoning as Arabic.  17 CPS / same timing defaults.
#
# All other language codes (en, fr, de, es, pt, ru, …) fall through to
# GLOBAL_DEFAULT (17 CPS), consistent with the global sweep result.
#
# NOTE: min_cue_ms, min_gap_ms, max_cue_ms are **not** changed for CJK
# because the #359 sweep showed timing wins are param-invariant — only
# target_cps matters materially.  The Japanese min_gap_ms of 83 ms is a
# broadcast convention, not a quality optimisation.

_LANG_MATRIX: dict[str, RetimeParams] = {
    "ja": RetimeParams(
        target_cps=4,
        min_cue_ms=1000,
        min_gap_ms=83,      # 2 frames @ 24 fps; JP broadcast convention
        max_cue_ms=7000,
    ),
    "zh": RetimeParams(
        target_cps=9,
        min_cue_ms=1000,
        min_gap_ms=100,
        max_cue_ms=7000,
    ),
    "zh-hans": RetimeParams(   # alias — same params as zh
        target_cps=9,
        min_cue_ms=1000,
        min_gap_ms=100,
        max_cue_ms=7000,
    ),
    "zh-hant": RetimeParams(   # alias — same params as zh
        target_cps=9,
        min_cue_ms=1000,
        min_gap_ms=100,
        max_cue_ms=7000,
    ),
    "ko": RetimeParams(
        target_cps=12,
        min_cue_ms=1000,
        min_gap_ms=100,
        max_cue_ms=7000,
    ),
    # RTL languages — CPS matches Latin default; listed explicitly so
    # callers can see we considered them.
    "ar": GLOBAL_DEFAULT,
    "he": GLOBAL_DEFAULT,
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_retime_params(lang: str | None) -> RetimeParams:
    """
    Return the RetimeParams appropriate for *lang*.

    Lookup order:
      1. Exact match on the full code (e.g. "zh-hans").
      2. Match on the base code (e.g. "zh" for "zh-TW").
      3. GLOBAL_DEFAULT for everything else.

    Parameters
    ----------
    lang:
        BCP-47 / ISO 639-1 language code (case-insensitive), or None.

    Returns
    -------
    RetimeParams
    """
    if not lang:
        return GLOBAL_DEFAULT
    lc = lang.lower()
    if lc in _LANG_MATRIX:
        return _LANG_MATRIX[lc]
    base = lc.split("-")[0]
    if base in _LANG_MATRIX:
        return _LANG_MATRIX[base]
    return GLOBAL_DEFAULT


def all_language_params() -> Mapping[str, RetimeParams]:
    """
    Return a read-only view of the full per-language matrix
    (including the ``"*"`` sentinel for the global default).
    """
    out = dict(_LANG_MATRIX)
    out["*"] = GLOBAL_DEFAULT
    return out
