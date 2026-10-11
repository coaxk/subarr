"""Caption suffixes are not the subtitle language.

Plex and Bazarr append `sdh`, `cc`, or `hi` after the language code
(`Movie.en.sdh.srt`, `Movie.es.hi.srt`). `_langs_in_sidecars` kept the last
2-3 letter token, so those files were reported as language `sdh` / `cc` / `hi`.
Coverage then queued them, and subgen rejected the job because it already
reads the language token as English or Spanish.

`hi` is also ISO 639-1 Hindi. It is a hearing-impaired marker only when
another language code shares the trailing suffix cluster. A bare
`Movie.hi.srt` stays Hindi, including when the title itself contains a
dotted token that is also a language code (`Mr. Title.hi.srt`, where `mr`
is Marathi).
"""

from __future__ import annotations

import pytest

from subarr.coverage_engine import _lang_token_from_srt_name, _langs_in_sidecars
from subarr.routers.browse import _lang_from_srt_name


class TestCaptionSuffixIsNotTheLanguage:
    @pytest.mark.parametrize(
        "name,expected",
        [
            ("Movie.en.sdh.srt", {"en"}),
            ("Movie.es.sdh.srt", {"es"}),
            ("Movie.es.hi.srt", {"es"}),
            ("Movie.en.hi.srt", {"en"}),
            ("Movie.eng.sdh.srt", {"eng"}),
            ("Movie.en.cc.srt", {"en"}),
            ("Movie.en.sdh.alass.srt", {"en"}),
            ("Movie.es.hi.alass.srt", {"es"}),
            ("Movie.en.hi.sdh.srt", {"en"}),
            ("Mr. Title.es.hi.srt", {"es"}),
        ],
    )
    def test_a_caption_suffix_resolves_to_the_language_in_front(self, name, expected):
        assert _langs_in_sidecars([name]) == expected
        assert _lang_from_srt_name(name) == _lang_token_from_srt_name(name)
        assert _lang_from_srt_name(name) == next(iter(expected))

    @pytest.mark.parametrize(
        "name,expected",
        [
            ("Movie.hi.srt", {"hi"}),
            ("Movie.hin.srt", {"hin"}),
            ("Movie.hi.sdh.srt", {"hi"}),
            ("Mr. Title.hi.srt", {"hi"}),
            ("The.hi.srt", {"hi"}),
        ],
    )
    def test_hindi_without_another_language_stays_hindi(self, name, expected):
        assert _langs_in_sidecars([name]) == expected
        assert _lang_from_srt_name(name) == next(iter(expected))

    def test_a_full_sidecar_beside_an_sdh_sidecar_is_one_language(self):
        got = _langs_in_sidecars(
            [
                "Movie.en.srt",
                "Movie.en.sdh.srt",
                "Movie.es.sdh.srt",
            ]
        )
        assert got == {"en", "es"}

    def test_a_forced_sidecar_is_still_not_coverage(self):
        assert _langs_in_sidecars(["Show - S01E01 - Title.en.forced.srt"]) == set()
        assert _langs_in_sidecars(["Show - S01E01 - Title.en.sdh.forced.srt"]) == set()

    @pytest.mark.parametrize("name", ["Movie.sdh.srt", "Movie.cc.srt"])
    def test_a_caption_marker_with_no_language_is_undefined(self, name):
        assert _langs_in_sidecars([name]) == {"und"}
        assert _lang_from_srt_name(name) == "und"
