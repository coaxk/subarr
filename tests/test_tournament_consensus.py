"""#65 — cross-config CONSENSUS judge (the no-ground-truth pseudo-reference).

The other judges score each entrant in isolation, so they can't catch a fluent,
well-formed, but DIVERGENT output (e.g. a unique mistranslation or a hallucinated
end-card no other config produced). The consensus judge uses the agreement ACROSS
entrants as a pseudo-reference: build a majority vocabulary, then score each
entrant on precision (does it only say agreed things?) and recall (does it cover
the agreed content?). It also reports a clip-level agreement score so a tournament
on genuinely ambiguous audio can be auto-flagged for human review.

No ground truth: consensus IS the reference. Robust to translation wording/timing
variance — it compares content-word sets, not cue alignment.
"""
from __future__ import annotations


def _t():
    from subarr import tournament as t
    return t


# Three configs agree on the content; a fourth adds a unique hallucinated
# end-card (the "amara.org" failure) on top of the agreed content.
AGREE_A = (
    "1\n00:00:00,000 --> 00:00:02,000\nThe storm will last seventy two hours.\n\n"
    "2\n00:00:02,000 --> 00:00:04,000\nCivil protection issued precautionary rules.\n"
)
AGREE_B = (
    "1\n00:00:00,000 --> 00:00:02,000\nThe storm lasts seventy two hours.\n\n"
    "2\n00:00:02,000 --> 00:00:04,000\nCivil protection has issued precautionary rules.\n"
)
AGREE_C = (
    "1\n00:00:00,000 --> 00:00:02,000\nThe storm will last seventy two hours total.\n\n"
    "2\n00:00:02,000 --> 00:00:04,000\nProtection issued some precautionary rules.\n"
)
HALLUC_ENDCARD = (
    "1\n00:00:00,000 --> 00:00:02,000\nThe storm will last seventy two hours.\n\n"
    "2\n00:00:02,000 --> 00:00:04,000\nCivil protection issued precautionary rules.\n\n"
    "3\n00:00:04,000 --> 00:00:06,000\nSubtitles by the Amara dot org community forever.\n"
)


def test_consensus_report_present_per_entrant_and_clip():
    t = _t()
    res = t.run_tournament([
        t.Entrant(label="a", srt_text=AGREE_A),
        t.Entrant(label="b", srt_text=AGREE_B),
        t.Entrant(label="c", srt_text=AGREE_C),
    ])
    for sc in res.scorecards:
        assert sc.consensus is not None
        assert 0.0 <= sc.consensus["precision"] <= 1.0
        assert 0.0 <= sc.consensus["recall"] <= 1.0
        assert 0.0 <= sc.consensus["f1"] <= 1.0
    # clip-level agreement is exposed for the human-review flag
    assert res.clip_agreement is not None
    assert 0.0 <= res.clip_agreement <= 1.0


def test_divergent_hallucination_scores_lower_consensus_precision():
    """An entrant that adds content no other config agrees on (a hallucinated
    end-card) must have LOWER consensus precision than the agreeing entrants."""
    t = _t()
    res = t.run_tournament([
        t.Entrant(label="agree_a", srt_text=AGREE_A),
        t.Entrant(label="agree_b", srt_text=AGREE_B),
        t.Entrant(label="halluc", srt_text=HALLUC_ENDCARD),
    ])
    cards = {sc.entrant_label: sc for sc in res.scorecards}
    assert cards["halluc"].consensus["precision"] < cards["agree_a"].consensus["precision"]
    # ...and the agreeing entrant must outrank the diverging hallucination
    assert res.winner_label in ("agree_a", "agree_b")
    assert cards["halluc"].composite < cards["agree_a"].composite


def test_dropout_scores_lower_consensus_recall():
    """An entrant that drops most of the agreed content (looped/collapsed to a
    couple of words) must have LOWER consensus recall than the full ones."""
    t = _t()
    dropout = "1\n00:00:00,000 --> 00:00:04,000\nstorm storm\n"
    res = t.run_tournament([
        t.Entrant(label="full_a", srt_text=AGREE_A),
        t.Entrant(label="full_b", srt_text=AGREE_B),
        t.Entrant(label="dropout", srt_text=dropout),
    ])
    cards = {sc.entrant_label: sc for sc in res.scorecards}
    assert cards["dropout"].consensus["recall"] < cards["full_a"].consensus["recall"]


def test_high_agreement_clip_flagged_higher_than_divergent_clip():
    """The clip-level agreement score must be higher when entrants say the same
    thing than when they wildly diverge (the human-review flag)."""
    t = _t()
    agree = t.run_tournament([
        t.Entrant(label="a", srt_text=AGREE_A),
        t.Entrant(label="b", srt_text=AGREE_B),
        t.Entrant(label="c", srt_text=AGREE_C),
    ])
    diverge = t.run_tournament([
        t.Entrant(label="x", srt_text="1\n00:00:00,000 --> 00:00:02,000\napple orchard harvest\n"),
        t.Entrant(label="y", srt_text="1\n00:00:00,000 --> 00:00:02,000\nsubmarine periscope depth\n"),
        t.Entrant(label="z", srt_text="1\n00:00:00,000 --> 00:00:02,000\nviolin concerto tempo\n"),
    ])
    assert agree.clip_agreement > diverge.clip_agreement


def test_single_entrant_has_no_consensus_penalty():
    """With one entrant there is no consensus to form — it must not be penalised,
    and clip_agreement is undefined (None)."""
    t = _t()
    res = t.run_tournament([t.Entrant(label="only", srt_text=AGREE_A)])
    sc = res.scorecards[0]
    assert res.clip_agreement is None
    # composite must equal the pre-consensus quality path (no consensus penalty);
    # we assert it's not dragged down by a phantom consensus miss.
    assert sc.composite > 50
