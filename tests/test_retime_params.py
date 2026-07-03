"""
tests/test_retime_params.py
============================
Unit tests for the per-language RetimeParams registry (issue #408).

Run with:  pytest tests/test_retime_params.py -v
"""

import pytest
from retime_params import (
    GLOBAL_DEFAULT,
    RetimeParams,
    all_language_params,
    get_retime_params,
)


# ---------------------------------------------------------------------------
# Exact language code look-ups
# ---------------------------------------------------------------------------

class TestExactLookup:
    def test_japanese_cps(self):
        p = get_retime_params("ja")
        assert p.target_cps == 4, "Japanese CPS should be 4 (Netflix spec)"

    def test_japanese_gap(self):
        p = get_retime_params("ja")
        assert p.min_gap_ms == 83, "Japanese gap should be 83 ms (2 frames @ 24fps)"

    def test_chinese_simplified(self):
        p = get_retime_params("zh-hans")
        assert p.target_cps == 9

    def test_chinese_traditional(self):
        p = get_retime_params("zh-hant")
        assert p.target_cps == 9

    def test_chinese_bare(self):
        p = get_retime_params("zh")
        assert p.target_cps == 9

    def test_korean(self):
        p = get_retime_params("ko")
        assert p.target_cps == 12

    def test_arabic_matches_global(self):
        p = get_retime_params("ar")
        assert p == GLOBAL_DEFAULT

    def test_hebrew_matches_global(self):
        p = get_retime_params("he")
        assert p == GLOBAL_DEFAULT


# ---------------------------------------------------------------------------
# Case insensitivity
# ---------------------------------------------------------------------------

class TestCaseInsensitive:
    @pytest.mark.parametrize("code", ["JA", "Ja", "jA", "JA-JP"])
    def test_japanese_case(self, code):
        assert get_retime_params(code).target_cps == 4

    @pytest.mark.parametrize("code", ["ZH-HANS", "zh-Hans", "ZH-hans"])
    def test_chinese_case(self, code):
        assert get_retime_params(code).target_cps == 9


# ---------------------------------------------------------------------------
# Fallback to global default
# ---------------------------------------------------------------------------

class TestFallback:
    @pytest.mark.parametrize("code", [
        None, "", "en", "fr", "de", "es", "pt", "ru", "it",
        "nl", "pl", "sv", "tr", "vi", "th", "hi", "xx-unknown",
    ])
    def test_latin_and_unknown_langs(self, code):
        p = get_retime_params(code)
        assert p == GLOBAL_DEFAULT, (
            f"Expected GLOBAL_DEFAULT for lang={code!r}, got {p}"
        )


# ---------------------------------------------------------------------------
# Base-code fallback (e.g. "zh-TW" → "zh")
# ---------------------------------------------------------------------------

class TestBaseCodeFallback:
    def test_zh_tw_falls_back_to_zh(self):
        # "zh-TW" is not in the matrix explicitly but "zh" is
        p = get_retime_params("zh-TW")
        assert p.target_cps == 9

    def test_ko_kr_falls_back_to_ko(self):
        p = get_retime_params("ko-KR")
        assert p.target_cps == 12

    def test_ja_jp_falls_back_to_ja(self):
        p = get_retime_params("ja-JP")
        assert p.target_cps == 4


# ---------------------------------------------------------------------------
# Structural invariants
# ---------------------------------------------------------------------------

class TestInvariants:
    def test_all_params_have_positive_cps(self):
        for lang, p in all_language_params().items():
            assert p.target_cps > 0, f"{lang}: target_cps must be positive"

    def test_min_cue_less_than_max_cue(self):
        for lang, p in all_language_params().items():
            assert p.min_cue_ms < p.max_cue_ms, (
                f"{lang}: min_cue_ms must be < max_cue_ms"
            )

    def test_min_gap_positive(self):
        for lang, p in all_language_params().items():
            assert p.min_gap_ms > 0, f"{lang}: min_gap_ms must be > 0"

    def test_cjk_cps_below_latin(self):
        """CJK languages must have lower CPS than the Latin default."""
        latin_cps = GLOBAL_DEFAULT.target_cps
        for lang in ("ja", "zh", "ko"):
            assert get_retime_params(lang).target_cps < latin_cps, (
                f"{lang} CPS should be < Latin default ({latin_cps})"
            )

    def test_cjk_cps_ordering(self):
        """CPS ordering must satisfy: ja <= zh <= ko (more dense → lower cap)."""
        ja = get_retime_params("ja").target_cps
        zh = get_retime_params("zh").target_cps
        ko = get_retime_params("ko").target_cps
        assert ja <= zh <= ko, f"Expected ja({ja}) ≤ zh({zh}) ≤ ko({ko})"

    def test_global_default_is_frozen(self):
        with pytest.raises((AttributeError, TypeError)):
            GLOBAL_DEFAULT.target_cps = 99  # type: ignore[misc]


# ---------------------------------------------------------------------------
# all_language_params()
# ---------------------------------------------------------------------------

class TestAllLanguageParams:
    def test_contains_sentinel(self):
        params = all_language_params()
        assert "*" in params
        assert params["*"] == GLOBAL_DEFAULT

    def test_returns_mapping(self):
        from collections.abc import Mapping
        assert isinstance(all_language_params(), Mapping)
