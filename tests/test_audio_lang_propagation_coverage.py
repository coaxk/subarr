"""#358: propagation resolves names from WHISPER_LANGUAGES (covers Galician +
the long tail) and degrades honestly when Sonarr can't represent a language."""

from __future__ import annotations

from subarr.routers.audio_lang import _iso_to_sonarr_name


def test_name_resolution_covers_galician_and_tail():
    # Previously absent from the curated map → fell back to the raw code.
    assert _iso_to_sonarr_name("gl") == "Galician"
    assert _iso_to_sonarr_name("glg") == "Galician"  # 3-letter also resolves
    assert _iso_to_sonarr_name("eu") == "Basque"
    # known short names still match Sonarr's spelling (existing contract)
    assert _iso_to_sonarr_name("el") == "Greek"
    assert _iso_to_sonarr_name("en") == "English"


def test_unknown_code_falls_back_to_itself():
    assert _iso_to_sonarr_name("zz") == "zz"


def test_variety_aliases_collapse_to_sonarr_parent_bucket():
    # Reconciled against the live Sonarr /language list: Sonarr has no separate
    # Nynorsk / Cantonese, so these Whisper varieties map to the parent name.
    assert _iso_to_sonarr_name("nn") == "Norwegian"
    assert _iso_to_sonarr_name("nno") == "Norwegian"  # 3-letter too
    assert _iso_to_sonarr_name("yue") == "Chinese"


def test_sonarr_unsupported_language_resolves_to_its_real_name():
    # Galician isn't in Sonarr's list; _iso_to_sonarr_name still returns the real
    # name (the honest degradation happens at the match step, not here).
    assert _iso_to_sonarr_name("gl") == "Galician"
