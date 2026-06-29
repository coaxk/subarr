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
