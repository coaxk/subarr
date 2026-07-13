from subarr.multilang_tune import multilang_sweep, prob_distribution, t_grid, format_report


def test_sweep_multilingual_count_monotonic_non_increasing_in_t():
    corpus = [
        [["gl", 0.94], ["es", 0.88], ["fr", 0.71]],  # multilingual until T passes 0.71
        [["en", 0.97], ["en", 0.95]],  # single
        [["de", 0.20], ["it", 0.18]],  # confused (never >= a real T)
    ]
    grid = [0.3, 0.5, 0.75, 0.9]
    rows = multilang_sweep(corpus, grid)
    counts = [r.multilingual for r in rows]
    assert counts == sorted(counts, reverse=True)  # non-increasing in T
    assert rows[0].multilingual == 1  # T=0.3: the gl/es/fr file
    # fr(0.71) dropping at T=0.75 still leaves gl(0.94)+es(0.88) >= T, so the file
    # stays multilingual (>=2) until es also drops -- that only happens at T=0.9.
    assert rows[grid.index(0.9)].multilingual == 0  # T=0.9: only gl(0.94) survives -> single


def test_prob_distribution_quantiles():
    corpus = [[["gl", 0.9], ["es", 0.8]], [["en", 0.6], ["en", 0.4]]]
    d = prob_distribution(corpus)
    assert d["n"] == 4 and d["min"] == 0.4 and d["max"] == 0.9


def test_report_renders_and_handles_empty_corpus():
    assert "empty" in format_report(multilang_sweep([], t_grid()), prob_distribution([])).lower()
    txt = format_report(
        multilang_sweep([[["gl", 0.9], ["es", 0.8]]], [0.5]), prob_distribution([[["gl", 0.9], ["es", 0.8]]])
    )
    assert "T=0.50" in txt and "multilingual=1" in txt
