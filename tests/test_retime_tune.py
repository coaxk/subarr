"""#359: pure re-timer parameter sweep over an SRT corpus."""

from __future__ import annotations

from subarr.retime_tune import param_grid, retime_sweep
from subarr.subtitle_retime import RetimeParams

# One hot sub (40 cps cue + a micro-cue) and one comfortable sub.
_HOT = (
    "1\n00:00:00,000 --> 00:00:02,000\n"
    "This is a very long translated line that crams far too many characters\n\n"
    "2\n00:00:20,000 --> 00:00:20,300\nhi\n"
)
_CALM = (
    "1\n00:00:00,000 --> 00:00:03,000\nHello there.\n\n2\n00:00:04,000 --> 00:00:07,000\nGeneral Kenobi.\n"
)


def test_param_grid_is_target_cps_x_min_cue():
    grid = param_grid()
    assert len(grid) == 9
    assert RetimeParams(target_cps=17.0, min_cue_ms=1000, min_gap_ms=100, max_cue_ms=7000) in grid
    assert all(p.min_gap_ms == 100 and p.max_cue_ms == 7000 for p in grid)


def test_sweep_has_baseline_first_then_one_row_per_combo():
    rows = retime_sweep([_HOT, _CALM], param_grid())
    assert rows[0].params is None  # baseline (no re-timing)
    assert len(rows) == 1 + len(param_grid())
    assert all(r.subs == 2 for r in rows)


def test_sweep_reduces_critical_cps_and_micro_cues_vs_baseline():
    rows = retime_sweep([_HOT, _CALM], param_grid())
    baseline = rows[0]
    treated = [r for r in rows if r.params is not None]
    # every combo should reduce (or hold) %over-critical and micro-cues, and add screen time.
    assert any(r.pct_over_critical < baseline.pct_over_critical for r in treated)
    assert all(r.micro_cues <= baseline.micro_cues for r in treated)
    assert all(r.too_long <= baseline.too_long for r in treated)  # cap prevents over-long
    changed = [r for r in treated if r.subs_changed > 0]
    assert changed and all(r.mean_added_ms > 0 for r in changed)


def test_sweep_leaves_comfortable_only_corpus_essentially_unchanged():
    rows = retime_sweep([_CALM], param_grid())
    baseline = rows[0]
    for r in rows[1:]:
        assert r.subs_changed == 0
        assert r.median_cps == baseline.median_cps


def test_sweep_lower_target_cps_reduces_median_more():
    grid = [
        RetimeParams(target_cps=20.0, min_cue_ms=1000, min_gap_ms=100, max_cue_ms=7000),
        RetimeParams(target_cps=15.0, min_cue_ms=1000, min_gap_ms=100, max_cue_ms=7000),
    ]
    rows = retime_sweep([_HOT], grid)
    at20 = next(r for r in rows if r.params and r.params.target_cps == 20.0)
    at15 = next(r for r in rows if r.params and r.params.target_cps == 15.0)
    assert at15.median_cps <= at20.median_cps  # aim lower → extend more → lower CPS


def test_corpus_from_dir_reads_srts_and_skips_sync_variants(tmp_path):
    from subarr.retime_tune import corpus_from_dir

    (tmp_path / "a.en.srt").write_text(_CALM, encoding="utf-8")
    (tmp_path / "a.en.ffsubsync.srt").write_text(_CALM, encoding="utf-8")  # subsyncarr variant
    (tmp_path / "a.en.alass.srt").write_text(_CALM, encoding="utf-8")
    (tmp_path / "junk.txt").write_text("nope", encoding="utf-8")
    corpus = corpus_from_dir(str(tmp_path))
    names = sorted(n for n, _ in corpus)
    assert names == ["a.en.srt"]  # variants + non-srt excluded


def test_original_sidecar_prefers_plain_and_excludes_engine_suffix(tmp_path):
    from subarr.retime_tune import _original_sidecar

    video = tmp_path / "Show - S01E01.mkv"
    video.write_text("x")
    (tmp_path / "Show - S01E01.en.srt").write_text(_CALM, encoding="utf-8")
    (tmp_path / "Show - S01E01.en.ffsubsync.srt").write_text(_CALM, encoding="utf-8")
    got = _original_sidecar(video)
    assert got is not None and got.name == "Show - S01E01.en.srt"


def test_corpus_from_ledger_gathers_original_and_guards_replaced(tmp_path):
    import sqlite3

    from subarr.retime_tune import corpus_from_ledger

    # temp DB with the two tables we read.
    db = tmp_path / "s.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE subs_generated (canonical_path TEXT, completed_at REAL)")
    conn.execute(
        "CREATE TABLE aftercare_results (id INTEGER PRIMARY KEY, canonical_path TEXT, cue_count INTEGER)"
    )
    conn.execute("INSERT INTO subs_generated VALUES (?, ?)", ("TV/Keep/ep.mkv", 123.0))
    conn.execute("INSERT INTO subs_generated VALUES (?, ?)", ("TV/Replaced/ep.mkv", 124.0))
    conn.execute("INSERT INTO subs_generated VALUES (?, ?)", ("TV/Pending/ep.mkv", None))  # not completed
    conn.commit()

    keep_dir = tmp_path / "keep"
    keep_dir.mkdir()
    (keep_dir / "ep.en.srt").write_text(_CALM, encoding="utf-8")  # 2 cues
    repl_dir = tmp_path / "repl"
    repl_dir.mkdir()
    (repl_dir / "ep.en.srt").write_text(_CALM, encoding="utf-8")  # 2 cues on disk...
    # aftercare recorded 9 cues when subarr made it → on-disk (2) mismatches → replaced.
    conn.execute(
        "INSERT INTO aftercare_results (canonical_path, cue_count) VALUES (?, ?)", ("TV/Replaced/ep.mkv", 9)
    )
    conn.execute(
        "INSERT INTO aftercare_results (canonical_path, cue_count) VALUES (?, ?)", ("TV/Keep/ep.mkv", 2)
    )
    conn.commit()
    conn.close()

    def _resolve(canon: str):
        return {"TV/Keep/ep.mkv": keep_dir / "ep.mkv", "TV/Replaced/ep.mkv": repl_dir / "ep.mkv"}.get(canon)

    corpus = corpus_from_ledger(str(db), resolve=_resolve)
    paths = [p for p, _ in corpus]
    assert paths == ["TV/Keep/ep.mkv"]  # completed + cue_count matches; Replaced skipped, Pending excluded


def test_format_report_ranks_and_shows_baseline():
    from subarr.retime_tune import format_report, retime_sweep

    rows = retime_sweep([_HOT, _CALM], param_grid())
    report = format_report(rows)
    assert "baseline" in report.lower()
    assert "median_cps" in report or "median" in report.lower()
    # a line per row (baseline + combos)
    assert report.count("target_cps=") >= 1
