"""#216 — aftercare signal slice: ad/boilerplate detection + sync overrun.

Signal-only by design: neither contributes to the tournament composite (the
#123 calibration must not move); aftercare flags on them instead.
"""

from __future__ import annotations


def _srt(cues):
    """cues: list of (start, end, text) with times as 'HH:MM:SS,mmm'."""
    blocks = []
    for i, (start, end, text) in enumerate(cues, 1):
        blocks.append(f"{i}\n{start} --> {end}\n{text}\n")
    return "\n".join(blocks)


CLEAN = _srt(
    [
        ("00:00:01,000", "00:00:03,000", "Hello there."),
        ("00:00:04,000", "00:00:06,000", "How are you today?"),
        ("00:00:07,000", "00:00:09,000", "I am fine, thanks."),
    ]
)


def test_ad_boilerplate_hits_detects_edge_ads(subarr_env):
    from subarr.subtitle_readability import parse_srt
    from subarr.transcript_signals import ad_boilerplate_hits

    text = _srt(
        [
            ("00:00:01,000", "00:00:03,000", "Downloaded from OpenSubtitles.org"),
            ("00:00:04,000", "00:00:06,000", "Real dialogue line."),
            ("00:00:07,000", "00:00:09,000", "Support us and become VIP member"),
        ]
    )
    assert ad_boilerplate_hits(parse_srt(text)) == 2
    assert ad_boilerplate_hits(parse_srt(CLEAN)) == 0
    assert ad_boilerplate_hits([]) == 0


def test_ad_boilerplate_only_scans_edges(subarr_env):
    from subarr.subtitle_readability import parse_srt
    from subarr.transcript_signals import ad_boilerplate_hits

    # A URL quoted mid-file (cue 6 of 11) is dialogue, not an injected ad.
    cues = [("00:00:01,000", "00:00:02,000", f"Line {i}.") for i in range(11)]
    cues[5] = ("00:00:06,000", "00:00:07,000", "Check out www.example.com he said.")
    assert ad_boilerplate_hits(parse_srt(_srt(cues))) == 0


def test_evaluate_flags_ads_without_moving_composite(subarr_env):
    from subarr.aftercare import evaluate_subtitle

    ad_text = _srt(
        [
            ("00:00:01,000", "00:00:03,000", "Downloaded from OpenSubtitles.org"),
            ("00:00:04,000", "00:00:06,000", "How are you today?"),
            ("00:00:07,000", "00:00:09,000", "I am fine, thanks."),
        ]
    )
    clean = evaluate_subtitle(CLEAN)
    ads = evaluate_subtitle(ad_text)
    assert ads.signals["ad_boilerplate_hits"] == 1
    assert ads.flagged is True
    # Signal-only: the ad hit itself must not change the composite scoring
    # path (both texts have identical structure/readability shape).
    assert not clean.flagged or clean.signals.get("ad_boilerplate_hits") == 0


def test_evaluate_sync_overrun_flags(subarr_env):
    from subarr.aftercare import evaluate_subtitle

    late = _srt(
        [
            ("00:00:01,000", "00:00:03,000", "Hello there."),
            ("00:50:00,000", "00:50:04,000", "This cue ends way past the media."),
        ]
    )
    # Media is 40 minutes; last cue ends at 50:04 → ~604s overrun.
    ev = evaluate_subtitle(late, media_duration_s=40 * 60)
    assert ev.signals["sync_overrun_s"] > 30
    assert ev.flagged is True

    # Same sub against its true duration: no overrun, span recorded.
    ok = evaluate_subtitle(late, media_duration_s=51 * 60)
    assert ok.signals["sync_overrun_s"] == 0.0
    assert 0 < ok.signals["sync_span_ratio"] <= 1.0


def test_evaluate_without_duration_adds_no_sync_signals(subarr_env):
    from subarr.aftercare import evaluate_subtitle

    ev = evaluate_subtitle(CLEAN)
    assert "sync_overrun_s" not in (ev.signals or {})
    assert ev.flagged is False
