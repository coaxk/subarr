from subarr.forced_segment import (
    ForcedSegmentParams,
    assemble_windows,
    window_is_foreign,
    expand_window_verdicts,
)


def test_assemble_windows_groups_until_window_length():
    utts = [(0.0, 3.0), (4.0, 7.0), (8.0, 12.0), (20.0, 23.0), (24.0, 30.0)]
    windows = assemble_windows(utts, window_s=15.0)
    assert [w[2] for w in windows] == [[0, 1, 2], [3, 4]]
    assert windows[0][0] == 0.0 and windows[0][1] == 12.0
    assert windows[1][0] == 20.0 and windows[1][1] == 30.0


def test_assemble_windows_single_long_utterance_is_its_own_window():
    utts = [(0.0, 40.0), (41.0, 44.0)]
    windows = assemble_windows(utts, window_s=15.0)
    assert windows[0][2] == [0] and windows[0][1] == 40.0
    assert windows[1][2] == [1]


def test_window_is_foreign_gate():
    p = ForcedSegmentParams(primary_lang="en", lid_min_confidence=0.5, lid_max_english_prob=0.25)
    assert window_is_foreign("de", 0.89, 0.02, p) is True
    assert window_is_foreign("en", 0.9, 0.9, p) is False
    assert window_is_foreign("zh", 0.10, 0.05, p) is False
    assert window_is_foreign("nl", 0.6, 0.4, p) is False


def test_expand_window_verdicts_assigns_each_utterance_its_window_flag():
    utts = [(0.0, 3.0), (4.0, 7.0), (20.0, 23.0)]
    windows = [(0.0, 7.0, [0, 1]), (20.0, 23.0, [2])]
    flags = [True, False]
    classified = expand_window_verdicts(utts, windows, flags)
    assert classified == [((0.0, 3.0), True), ((4.0, 7.0), True), ((20.0, 23.0), False)]
