"""A forced sidecar is not full subtitle coverage.

subarr handles this meticulously for EMBEDDED tracks: `has_usable_embedded_english`
excludes forced, coverage reports `EN(forced)`, and #79 built a whole
`forced_only_subgen_will_skip` machinery around it. Sidecars were never given the
same treatment. `_langs_in_sidecars` returned `en` for `<stem>.en.forced.srt`,
so a forced sidecar counted as full English coverage.

⚠️ It is a CLOSED LOOP, and #475 made it likely rather than theoretical. subarr's
own forced-segment feature (#364) WRITES `.en.forced.srt`, and #475 made that the
default naming two days ago. So subarr writes a forced sidecar, then counts it as
English coverage, then refuses to queue the full transcription the user wanted:

    "An English subtitle already exists on disk -- not queued
     (subgen would skip it)."

Same conceptual bug fixed on the subgen side in coaxk/subarr-subgen v4.24 and
reported upstream as McCloudS/subgen#358. This is the subarr half of it.

⚠️ The parser is otherwise healthy and was NOT the problem. Run over 3,820 real
sidecar filenames from a live library it produced 3,612 `en`, correct
`da`/`fi`/`no`/`sv`, 13 `und` and no false positives. Only the forced dimension
was missing.
"""

from __future__ import annotations

import pytest

from subarr.coverage_engine import _langs_in_sidecars


class TestForcedSidecarsAreNotCoverage:
    @pytest.mark.parametrize(
        "name",
        [
            "Show - S01E01 - Title.en.forced.srt",  # what subarr writes since #475
            "Show - S01E01 - Title.forced.en.srt",  # what it wrote before #475
            "Show - S01E01 - Title.eng.forced.srt",  # 3-letter code
            "Show - S01E01 - Title.en.forced.alass.srt",  # re-synced by a tool
            "Show - S01E01 - Title.EN.FORCED.srt",  # case
        ],
    )
    def test_a_forced_sidecar_contributes_no_language(self, name):
        # Not 'und' either: it is not an unknown language, it is a known
        # language in a form that does not constitute coverage. Returning 'und'
        # would leak into the "only sidecar is untagged" branch downstream.
        assert _langs_in_sidecars([name]) == set()

    def test_a_full_sidecar_still_counts(self):
        assert _langs_in_sidecars(["Show - S01E01 - Title.en.srt"]) == {"en"}

    def test_a_full_sidecar_beside_a_forced_one_still_counts(self):
        # The common real state once #364 has run: both exist. The full one is
        # coverage; the forced one must not mask it or add noise.
        got = _langs_in_sidecars(
            [
                "Show - S01E01 - Title.en.srt",
                "Show - S01E01 - Title.en.forced.srt",
            ]
        )
        assert got == {"en"}

    def test_a_forced_sidecar_in_another_language_is_also_excluded(self):
        assert _langs_in_sidecars(["Show - S01E01 - Title.de.forced.srt"]) == set()

    def test_a_title_containing_the_word_forced_is_not_affected(self):
        # ⚠️ The reason this checks TOKENS and not a substring. A real episode
        # in the test library is literally titled "Forced Labour"; a naive
        # `"forced" in name` would silently stop counting its subtitles.
        assert _langs_in_sidecars(["A French Village - S05E01 - Forced Labour WEBDL-1080p -SbR.en.srt"]) == {
            "en"
        }


class TestUnchangedBehaviour:
    """The parser was healthy apart from the forced dimension. These pin the
    shapes measured across 3,820 real filenames so the fix cannot regress them."""

    @pytest.mark.parametrize(
        "name,expected",
        [
            ("The Roots Of Evil - S01E03 - TBA WEBDL-1080p -GER.en.srt", {"en"}),
            ("The Roots Of Evil - S01E03 - TBA WEBDL-1080p -GER.en.alass.srt", {"en"}),
            ("The Halcyon - S01E02 - Episode 2 WEBDL-1080p -DKV.da.srt", {"da"}),
            ("The Halcyon - S01E05 - Episode 5 WEBDL-1080p -DKV.no.alass.srt", {"no"}),
            ("Show - S01E01 - Title.srt", {"und"}),
        ],
    )
    def test_real_world_names_parse_as_before(self, name, expected):
        assert _langs_in_sidecars([name]) == expected

    def test_a_non_srt_is_still_ignored(self):
        assert _langs_in_sidecars(["Show - S01E01 - Title.en.ass"]) == set()
