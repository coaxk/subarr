"""#475: forced sidecars must be named so Bazarr can actually see them.

Measured 2026-08-31 against a real Bazarr 1.6.0 and Plex 1.43.3, with both
files present on disk and two scan-disk cycles:

    Movie.en.forced.srt   Bazarr: SEEN, forced=True     Plex: seen, forced
    Movie.forced.en.srt   Bazarr: INVISIBLE             Plex: seen, forced

subarr wrote the second form. Bazarr is the tool subarr's entire coverage model
reads from, so every forced sidecar subarr has ever produced was invisible to
it. Bazarr's own issue #1516 independently states the expected format as
`.eng.forced.srt` (language code first, then forced).

So this is a defect, not a preference, and the fix is the DEFAULT rather than
an option: keeping the old form as default would preserve a file Bazarr cannot
read.

Detection stays liberal and accepts BOTH orders. Anyone who enabled #364 before
this has `.forced.en.srt` files on disk; subarr must keep recognising them or
it would regenerate duplicates beside them. Write one form, read either.
"""

from __future__ import annotations

from subarr.forced_segment import forced_sidecar_name, is_forced_sidecar_for


def test_we_now_write_the_bazarr_visible_form():
    """Language code immediately before .srt is what Bazarr parses."""
    assert forced_sidecar_name("Movie (2019)", "en") == "Movie (2019).en.forced.srt"


def test_the_language_is_not_hardcoded_to_english():
    assert forced_sidecar_name("Movie", "es") == "Movie.es.forced.srt"


def test_detection_accepts_the_new_form():
    assert is_forced_sidecar_for("Movie.en.forced.srt", "Movie", "en") is True


def test_detection_STILL_accepts_the_legacy_form():
    """The half that prevents duplicates.

    Anyone who ran #364 before this fix has .forced.en.srt on disk. If subarr
    stopped recognising it, the no-clobber gate would miss it and generate a
    second forced sidecar in the new convention beside the old one -- exactly
    the mixed-naming mess the reporter opened #475 to escape.
    """
    assert is_forced_sidecar_for("Movie.forced.en.srt", "Movie", "en") is True


def test_a_plain_sub_is_not_a_forced_sidecar():
    assert is_forced_sidecar_for("Movie.en.srt", "Movie", "en") is False


def test_a_different_language_does_not_match():
    assert is_forced_sidecar_for("Movie.es.forced.srt", "Movie", "en") is False
    assert is_forced_sidecar_for("Movie.forced.es.srt", "Movie", "en") is False


def test_a_sidecar_for_a_different_file_does_not_match():
    assert is_forced_sidecar_for("Other.en.forced.srt", "Movie", "en") is False


def test_three_letter_codes_are_accepted_both_ways():
    """subgen can emit ISO_639_2_B (eng) depending on
    SUBTITLE_LANGUAGE_NAMING_TYPE, so both widths must resolve."""
    assert is_forced_sidecar_for("Movie.eng.forced.srt", "Movie", "en") is True
    assert is_forced_sidecar_for("Movie.forced.eng.srt", "Movie", "en") is True


def test_non_srt_is_rejected():
    assert is_forced_sidecar_for("Movie.en.forced.idx", "Movie", "en") is False
