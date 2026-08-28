"""#458: image-based subtitle tracks were counted as subtitle coverage.

Reported on r/radarr: a file with an embedded VobSub track is skipped as
"already has subtitles", but a bitmap cannot be read as text, searched,
restyled, retimed, or fed to anything downstream. The user had patched it
locally with a text-codec allowlist.

We deny the image codecs instead of allowing the text ones. The image set is
small and closed; the text set has a long tail (microdvd, subviewer, stl,
jacosub, sami, mpl2) and every format missed from an allowlist recreates this
exact bug for someone else. Denying also fails SAFE: an unknown new image
codec over-counts coverage, which is visible, rather than silently skipping
files, which is not.
"""

from __future__ import annotations

from subarr.media_probe import (
    IMAGE_SUBTITLE_CODECS,
    ProbeResult,
    SubtitleStream,
    has_usable_embedded_english,
    is_image_subtitle_codec,
)


def _sub(codec: str, *, lang: str = "eng", forced: bool = False, commentary: bool = False):
    return SubtitleStream(
        index=0,
        language=lang,
        codec=codec,
        title=None,
        default=False,
        forced=forced,
        sdh=False,
        commentary=commentary,
    )


def _result(*subs) -> ProbeResult:
    pr = ProbeResult(canonical_path="TV/Show/ep.mkv")
    pr.subtitles.extend(subs)
    return pr


def test_the_image_codecs_we_deny():
    # Verified 2026-08-28 against `ffmpeg -decoders` in the subgen-next image:
    # these four are the ONLY bitmap subtitle codecs ffmpeg knows, so the deny
    # set is complete by construction rather than by recollection.
    for c in ("hdmv_pgs_subtitle", "dvd_subtitle", "dvb_subtitle", "xsub"):
        assert c in IMAGE_SUBTITLE_CODECS, f"{c} must be denied"
        assert is_image_subtitle_codec(c) is True


def test_text_codecs_are_not_denied_including_the_long_tail():
    # ffmpeg also decodes pjs, realtext, vplayer and subviewer1 as text -- four
    # more formats the reporter's 7-entry allowlist would have misfiled as
    # images. The tail is long, which is the whole argument for denying.
    # The reporter's allowlist was {srt, ass, mov_text, subrip, webvtt, text,
    # ssa}. Anything outside it would have been wrongly treated as an image
    # sub. Denying instead means these all keep working with no extra config.
    for c in (
        "subrip",
        "ass",
        "ssa",
        "mov_text",
        "webvtt",
        "text",
        "microdvd",
        "subviewer",
        "stl",
        "jacosub",
        "sami",
        "mpl2",
    ):
        assert is_image_subtitle_codec(c) is False, f"{c} is text, must not be denied"


def test_unknown_codec_is_treated_as_text_not_image():
    # Fail-safe direction: an unrecognised codec over-counts coverage (visible)
    # rather than silently queueing a transcription for a file that has subs.
    # dvb_teletext and eia_608 are the sharp cases: both LOOK broadcast-y but
    # ffmpeg decodes both to text (libzvbi / cc_dec), so denying them would
    # have thrown away real subtitles.
    assert is_image_subtitle_codec("dvb_teletext") is False
    assert is_image_subtitle_codec("eia_608") is False
    assert is_image_subtitle_codec("some_future_text_format") is False
    assert is_image_subtitle_codec(None) is False


def test_a_pgs_english_track_is_not_usable_coverage():
    # The exact reported case, in Blu-ray flavour.
    assert has_usable_embedded_english(_result(_sub("hdmv_pgs_subtitle"))) is False


def test_a_vobsub_english_track_is_not_usable_coverage():
    # The exact reported case, verbatim: "I have VobSub embedded".
    assert has_usable_embedded_english(_result(_sub("dvd_subtitle"))) is False


def test_a_text_english_track_is_still_usable():
    assert has_usable_embedded_english(_result(_sub("subrip"))) is True


def test_a_text_track_alongside_an_image_track_still_counts():
    # Common on Blu-ray rips: PGS plus an SRT. The text one is real coverage.
    assert has_usable_embedded_english(_result(_sub("hdmv_pgs_subtitle"), _sub("subrip"))) is True


def test_image_codec_does_not_rescue_a_forced_track():
    # Forced was already excluded; adding the codec check must not change that.
    assert has_usable_embedded_english(_result(_sub("subrip", forced=True))) is False


def test_a_non_english_image_track_is_still_irrelevant():
    assert has_usable_embedded_english(_result(_sub("dvd_subtitle", lang="fra"))) is False


def test_codec_is_optional_and_missing_codec_does_not_crash():
    # ffprobe can omit codec_name on a malformed container.
    assert has_usable_embedded_english(_result(_sub(None))) is True


# --- UI honesty: the label must say an image track EXISTS ------------------
# Without this, english_track_summary() falls through every branch and returns
# None, so a file with a perfectly real VobSub English track renders as "no
# English track at all". That is worse than the original bug: the user cannot
# tell a file with unusable subs from a file with none, and would reasonably
# think subarr failed to read the container.

from subarr.media_probe import english_track_summary, has_forced_or_commentary_english


def test_image_only_english_is_labelled_not_dropped():
    assert english_track_summary(_result(_sub("hdmv_pgs_subtitle"))) == "EN(image)"


def test_image_only_english_counts_as_partial_coverage_not_absent():
    # Partial => the Coverage row is demoted but still visible, same treatment
    # as forced. Silently absent would hide the file from the user entirely.
    assert has_forced_or_commentary_english(_result(_sub("dvd_subtitle"))) is True


def test_a_real_text_track_still_labels_clean():
    assert english_track_summary(_result(_sub("subrip"))) == "EN"


def test_text_track_wins_the_label_over_an_image_track():
    assert english_track_summary(_result(_sub("hdmv_pgs_subtitle"), _sub("subrip"))) == "EN"


def test_forced_still_wins_over_image_so_existing_labels_do_not_regress():
    s = _sub("dvd_subtitle", forced=True)
    assert english_track_summary(_result(s)) == "EN(forced)"


def test_no_english_at_all_is_still_none():
    assert english_track_summary(_result(_sub("dvd_subtitle", lang="fra"))) is None


# --- Coverage scoring: demote, and be honest that subgen will skip it -------
# An image-only English file is the SAME trap #79 solved for forced subs:
# subgen's has_internal_subtitle_in_language() sees a subtitle stream tagged
# "eng" and skips the file, whatever the codec. So the gap is real but
# UN-FILLABLE today. Presenting it as an ordinary actionable gap dangles work
# the user can click forever with nothing happening.


def _item(embedded_en=None):
    from subarr.coverage_engine import CoverageItem

    return CoverageItem(
        media_type="movie",
        title="A DVD Rip",
        canonical_path="Movies/A DVD Rip",
        embedded_en=embedded_en,
        audio_langs=["eng"],
    )


def test_image_only_en_is_demoted_to_partial_not_treated_as_no_subs():
    from subarr.coverage_engine import _score

    it = _item("EN(image)")
    _score(it, {}, ignore_forced_subtitles=True)
    assert any("partial coverage" in r for r in it.score_reasons)


def test_image_only_en_says_subgen_will_skip_it():
    from subarr.coverage_engine import _score

    it = _item("EN(image)")
    _score(it, {}, ignore_forced_subtitles=True)
    assert any("skip" in r.lower() for r in it.score_reasons), (
        "an un-fillable gap must say so, per the #79 precedent"
    )


def test_image_only_en_is_not_satisfied_by_the_ignore_forced_cap():
    # The forced cap governs FORCED subs. It must not accidentally make an
    # image-only file look actionable.
    from subarr.coverage_engine import _score

    for cap in (True, False):
        it = _item("EN(image)")
        _score(it, {}, ignore_forced_subtitles=cap)
        assert any("skip" in r.lower() for r in it.score_reasons)


def test_full_english_is_still_satisfied_and_not_demoted():
    from subarr.coverage_engine import _score

    it = _item("EN")
    _score(it, {}, ignore_forced_subtitles=True)
    assert not any("partial coverage" in r for r in it.score_reasons)
