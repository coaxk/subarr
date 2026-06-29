"""#358: canonical language table + display-name helper.

(No to_iso3 — Task 0 verified subgen's LanguageCode.from_string accepts any
form and compares enums, so 2-letter is forwarded as-is to subgen.)"""

from __future__ import annotations

from subarr.langs import WHISPER_LANGUAGES, display_name


def test_table_is_the_whisper_set_with_galician():
    # The full Whisper detection set (~100), keyed by 2-letter code.
    assert 95 <= len(WHISPER_LANGUAGES) <= 110
    assert WHISPER_LANGUAGES["gl"] == "Galician"
    assert WHISPER_LANGUAGES["ko"] == "Korean"
    # every key is a lowercase short code, every value a non-empty title-cased name
    for code, name in WHISPER_LANGUAGES.items():
        assert code == code.lower() and 2 <= len(code) <= 3
        assert name and name[0].isupper()


def test_display_name_resolves_and_falls_back():
    assert display_name("gl") == "Galician"
    assert display_name("GL") == "Galician"  # case-insensitive
    assert display_name("zz") == "zz"  # unknown → the code itself
    assert display_name("") == ""
