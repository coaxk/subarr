"""#123 — the QE/adequacy judge folded into the tournament composite.

When a source transcript is present, the validated LaBSE adequacy signal
dominates the composite (structural quality still gates failure modes). QE is
monkeypatched here so the fold is tested deterministically without the model.
"""

from __future__ import annotations

# two structurally-clean, single-cue outputs — equal on every structural judge,
# so ONLY the QE adequacy can separate them.
FAITHFUL = "1\n00:00:00,000 --> 00:00:03,000\nThe reactor is overheating.\n"
OFF_TOPIC = "1\n00:00:00,000 --> 00:00:03,000\nThe weather is nice today.\n"
SPEECH = [(0.0, 3.0)]


def test_qe_adequacy_makes_the_faithful_translation_win(monkeypatch):
    from subarr import qe, tournament as t

    def fake_qe(source, hyp, **kw):
        return 0.95 if "reactor" in hyp else 0.30

    monkeypatch.setattr(qe, "qe_adequacy", fake_qe)
    res = t.run_tournament(
        [
            t.Entrant(label="off", srt_text=OFF_TOPIC, source_text="SRC", speech_ranges=SPEECH),
            t.Entrant(label="faithful", srt_text=FAITHFUL, source_text="SRC", speech_ranges=SPEECH),
        ]
    )
    assert res.winner_label == "faithful"
    fc = next(s for s in res.scorecards if s.entrant_label == "faithful")
    assert fc.qe_adequacy == 0.95
    assert fc.signals["qe_adequacy"] == 0.95


def test_no_source_text_skips_qe(monkeypatch):
    from subarr import qe, tournament as t

    # would fire (high) if called — but no source_text means QE must be skipped
    monkeypatch.setattr(qe, "qe_adequacy", lambda *a, **k: 0.99)
    res = t.run_tournament([t.Entrant(label="x", srt_text=FAITHFUL, speech_ranges=SPEECH)])
    sc = res.scorecards[0]
    assert sc.qe_adequacy is None
    assert sc.signals["qe_adequacy"] is None


def test_qe_unavailable_is_graceful(monkeypatch):
    from subarr import qe, tournament as t

    # embedder unavailable → qe_adequacy returns None → structural-only composite
    monkeypatch.setattr(qe, "qe_available", lambda: False)
    res = t.run_tournament(
        [
            t.Entrant(label="a", srt_text=FAITHFUL, source_text="SRC", speech_ranges=SPEECH),
        ]
    )
    assert res.scorecards[0].qe_adequacy is None
    assert not res.scorecards[0].disqualified
