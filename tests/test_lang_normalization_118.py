"""#118: ISO language-code normalization across coverage detection.

The same language appears as en/eng/en-US, de/ger/deu across Sonarr, Bazarr,
subgen sidecars, and probe tags. Coverage comparisons must normalize all
variants or they raise phantom gaps (a present .ger.srt not matched against a
'de' wanted) or miss Bazarr-blind rows (en-US audio on a foreign show).
"""

from __future__ import annotations

from subarr.langs import normalize_lang
from subarr.coverage_engine import _stale_for_episode, _audio_metadata_looks_mislabeled


def _stale(sidecar_name: str, wanted: list[str]) -> bool:
    return _stale_for_episode(
        sonarr_episode_id=1,
        ep_file_paths={10: "/tv/Show/Show.S01E01.mkv"},
        sonarr_eps_by_id={1: {"episodeFileId": 10}},
        series_srt_paths=[f"/tv/Show/{sidecar_name}"],
        episode_number="1",
        missing_subs=wanted,
    )[0]


# ── GAP A: stale-sidecar match must normalize 639-2/B + /T → 639-1 ──────────


def test_bibliographic_and_terminological_sidecar_satisfy_two_letter_wanted():
    # A German sidecar (.ger 639-2/B or .deu 639-2/T) satisfies a 'de' wanted.
    assert _stale("Show.S01E01.deu.srt", ["de"]) is True
    assert _stale("Show.S01E01.ger.srt", ["de"]) is True


def test_eng_sidecar_satisfies_en_wanted():
    # .eng.srt must satisfy 'en' — the [:2] truncation made 'eng' != 'en'.
    assert _stale("Show.S01E01.eng.srt", ["en"]) is True


def test_two_letter_sidecar_still_matches():  # regression guard
    assert _stale("Show.S01E01.de.srt", ["de"]) is True


def test_unrelated_language_is_not_stale():  # control
    assert _stale("Show.S01E01.fr.srt", ["de"]) is False


# ── langs.normalize_lang: strip region/script suffixes ─────────────────────


def test_normalize_strips_region_and_script_tags():
    assert normalize_lang("en-US") == "en"
    assert normalize_lang("pt-BR") == "pt"
    assert normalize_lang("zh-Hans") == "zh"
    assert normalize_lang("en") == "en"  # unchanged
    assert normalize_lang("ger") == "de"  # still maps 639-2/B
    assert normalize_lang("und") == "und"  # preserved


# ── GAP B: Bazarr-blind mislabel signature catches region-tagged English ───


def test_region_tagged_english_audio_flagged_mislabeled():
    assert _audio_metadata_looks_mislabeled(["en-US"]) is True
    assert _audio_metadata_looks_mislabeled(["eng"]) is True


def test_genuine_foreign_audio_not_flagged():  # control
    assert _audio_metadata_looks_mislabeled(["de"]) is False
    assert _audio_metadata_looks_mislabeled(["ger"]) is False


def test_normalize_galician_358():
    # #358: Galician (As bestas) — picker sends ISO-639-2 'glg', Whisper says 'gl'
    from subarr.langs import normalize_lang

    assert normalize_lang("glg") == "gl"
    assert normalize_lang("gal") == "gl"
    assert normalize_lang("galician") == "gl"
    assert normalize_lang("gl") == "gl"


def test_normalize_zxx_no_linguistic_content_358():
    # #358: zxx = no linguistic content (Junk Head gibberish) — must stay 'zxx'
    from subarr.langs import normalize_lang

    assert normalize_lang("zxx") == "zxx"
