"""#364 LID threshold validation -- pure sweep/metrics core.

Reuses the production predicate forced_segment.window_is_foreign, so the sweep
measures exactly what the live detector does. No file/network I/O here.
"""

from __future__ import annotations

from subarr import lid_tune


def _records():
    # 2 english + 2 foreign windows, chosen so that at (min_conf=0.5, max_en=0.25):
    #   - english/en high-conf     -> not flagged (true negative)
    #   - english/de low-en-prob   -> flagged     (FALSE POSITIVE)
    #   - foreign/de confident     -> flagged     (true positive)
    #   - foreign/ja under-conf    -> not flagged (miss)
    return [
        {"truth": "english", "lang": "english", "top_lang": "en", "top_prob": 0.90, "english_prob": 0.90},
        {"truth": "english", "lang": "english", "top_lang": "de", "top_prob": 0.60, "english_prob": 0.10},
        {"truth": "foreign", "lang": "deu", "top_lang": "de", "top_prob": 0.80, "english_prob": 0.05},
        {"truth": "foreign", "lang": "jpn", "top_lang": "ja", "top_prob": 0.40, "english_prob": 0.05},
    ]


def test_evaluate_counts_fp_and_tp():
    cell = lid_tune.evaluate(_records(), 0.5, 0.25)
    assert cell.n_english == 2
    assert cell.n_foreign == 2
    assert cell.false_positives == 1
    assert cell.true_positives == 1
    assert cell.fp_rate == 0.5
    assert cell.recall == 0.5


def test_evaluate_tighter_thresholds_drop_the_false_positive():
    # english/de record has english_prob=0.10; max_en=0.05 rejects it -> no FP.
    cell = lid_tune.evaluate(_records(), 0.5, 0.05)
    assert cell.false_positives == 0


def test_evaluate_lower_confidence_floor_recovers_the_missed_foreign():
    # foreign/ja has top_prob=0.40; min_conf=0.4 now catches it -> recall 2/2.
    cell = lid_tune.evaluate(_records(), 0.4, 0.25)
    assert cell.true_positives == 2
    assert cell.recall == 1.0


def test_sweep_grid_shape():
    cells = lid_tune.sweep(_records(), [0.4, 0.5], [0.2, 0.25, 0.3])
    assert len(cells) == 6


def test_per_language_recall_groups_foreign_only():
    plr = lid_tune.per_language_recall(_records(), 0.5, 0.25)
    assert plr["deu"] == (1, 1)  # flagged
    assert plr["jpn"] == (0, 1)  # missed
    assert "english" not in plr


def test_recommend_maximises_recall_within_fp_budget_conservative_tiebreak():
    cells = [
        lid_tune.ThresholdCell(0.4, 0.30, 100, 100, 20, 90),  # fp_rate 0.20 -> over budget
        lid_tune.ThresholdCell(0.5, 0.25, 100, 100, 8, 70),  # fp 0.08 recall 0.70
        lid_tune.ThresholdCell(0.6, 0.20, 100, 100, 5, 70),  # fp 0.05 recall 0.70 -> conservative winner
    ]
    best = lid_tune.recommend(cells, max_fp_rate=0.10)
    assert best.min_conf == 0.6 and best.max_en == 0.20


def test_recommend_returns_none_when_nothing_meets_budget():
    cells = [lid_tune.ThresholdCell(0.5, 0.25, 100, 100, 30, 90)]  # fp 0.30
    assert lid_tune.recommend(cells, max_fp_rate=0.10) is None


def test_select_audio_stream_prefers_tag_match():
    streams = [{"index": 0, "lang": "eng"}, {"index": 1, "lang": "jpn"}]
    assert lid_tune.select_audio_stream(streams, {"jpn"}) == 1


def test_select_audio_stream_single_stream_fallback():
    assert lid_tune.select_audio_stream([{"index": 2, "lang": None}], {"jpn"}) == 2


def test_select_audio_stream_ambiguous_returns_none():
    streams = [{"index": 0, "lang": "eng"}, {"index": 1, "lang": "fra"}]
    assert lid_tune.select_audio_stream(streams, {"jpn"}) is None


def test_format_report_is_a_string_with_key_sections():
    records = _records()
    cells = lid_tune.sweep(records, [0.5], [0.25])
    rep = lid_tune.format_report(records, cells, default=(0.5, 0.25), rec_cell=cells[0])
    assert isinstance(rep, str)
    assert "fp_rate" in rep.lower() or "false" in rep.lower()
    assert "recall" in rep.lower()
