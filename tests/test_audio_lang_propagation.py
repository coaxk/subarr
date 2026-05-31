"""v1.1.1 #219 closer — Sonarr language propagation.

When the user verifies a file's audio language in subarr, optionally PUT
the verified language back to Sonarr's per-episodeFile record. Bazarr
syncs from Sonarr → re-evaluates "audio matches subs" against the now-
correct foreign-language audio → episode appears in /wanted.

This is the architectural payoff for the bazarr-blind defense: not just
"see what Bazarr can't", but "make Bazarr able to see it".
"""
from __future__ import annotations

import pytest


def test_iso_code_to_sonarr_name_maps_common_languages():
    from subarr.routers.audio_lang import _iso_to_sonarr_name
    assert _iso_to_sonarr_name("en") == "English"
    assert _iso_to_sonarr_name("eng") == "English"
    assert _iso_to_sonarr_name("fr") == "French"
    assert _iso_to_sonarr_name("fre") == "French"
    assert _iso_to_sonarr_name("fra") == "French"
    assert _iso_to_sonarr_name("ger") == "German"
    assert _iso_to_sonarr_name("jpn") == "Japanese"
    # Mixed case + whitespace handled
    assert _iso_to_sonarr_name(" FR ") == "French"
    assert _iso_to_sonarr_name("EN") == "English"
    # Unknown code passes through (Sonarr will reject with a clear error
    # rather than us silently mistranslating to e.g. English)
    assert _iso_to_sonarr_name("zz") == "zz"
