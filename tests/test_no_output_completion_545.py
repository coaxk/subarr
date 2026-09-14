"""#545: a file with no speech loops forever through Auto Feed.

subgen's VAD removes 100% of the audio, the transcription raises, and no
subtitle is written. subarr's completion watcher marked the job complete the
moment its path left subgen's queue, without looking for the subtitle, so the
Queue said "transcribed -- subtitle written", Coverage still saw the gap, and
the next walk queued the same file again. Every walk, forever.

The fix: a job that leaves the queue with no subtitle sidecar is recorded as
NO OUTPUT (not "written"), fires none of the write-back steps, shows as an
issue in the Queue, and is held back from auto-queue and backfill for a
cooldown.

Imports are local: conftest reloads subarr modules between tests.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock

import pytest


@dataclass
class _Caps:
    reachable: bool = True
    has_queue: bool = True
    is_subarr_subgen: bool = True


def _ledger(tmp_path):
    from subarr.migrate import run_migrations
    from subarr.provenance import ProvenanceStore

    db = tmp_path / "prov.db"
    run_migrations(db)
    return ProvenanceStore(db), db


def _age(db, ledger_id: int, seconds: float) -> None:
    con = sqlite3.connect(str(db))
    con.execute("UPDATE subs_generated SET queued_at = ? WHERE id = ?", (time.time() - seconds, ledger_id))
    con.commit()
    con.close()


def _plant(rel: str, *, srt: bool):
    from subarr.config import settings

    video = settings.media_root / rel
    video.parent.mkdir(parents=True, exist_ok=True)
    video.write_bytes(b"")
    for old in video.parent.glob(video.stem + "*.srt"):
        old.unlink()
    if srt:
        (video.parent / f"{video.stem}.en.srt").write_text(
            "1\n00:00:01,000 --> 00:00:02,000\nHi.\n\n", encoding="utf-8"
        )
    return video


def _watcher(prov):
    from subarr.completion_watcher import CompletionWatcher

    subgen = MagicMock()
    subgen.queue = AsyncMock(return_value={"queued": [], "processing": []})
    w = CompletionWatcher(subgen=subgen, bazarr=MagicMock(), provenance=prov, caps_provider=lambda: _Caps())
    fired: list[str] = []

    def rec(name, result=None):
        def _f(*a, **k):
            fired.append(name)
            return result

        return _f

    async def arec_upload(*a, **k):
        fired.append("bazarr_upload")
        return True

    async def arec_plex(*a, **k):
        fired.append("plex")

    w._run_retime = rec("retime")
    w._run_aftercare = rec("aftercare")
    w._maybe_forced_segment = rec("forced_segment")
    w._try_upload_to_bazarr = arec_upload
    w._maybe_plex_partial_scan = arec_plex
    return w, fired


def _outcome(db, ledger_id: int):
    con = sqlite3.connect(str(db))
    row = con.execute(
        "SELECT completed_at, outcome FROM subs_generated WHERE id = ?", (ledger_id,)
    ).fetchone()
    con.close()
    return row


# A fixed, realistic age. Deriving it from NO_OUTPUT_MIN_AGE_S would rescale with
# the constant, so a guard that never expires could never fail these tests
# (caught by mutation: NO_OUTPUT_MIN_AGE_S = 10**9 still passed).
TEN_MINUTES = 600


def test_the_age_guard_is_minutes_not_hours():
    from subarr.completion_watcher import NO_OUTPUT_MIN_AGE_S

    # Long enough to cover the gap between the ledger row and subgen accepting
    # /batch; short enough that a silent file is judged on the next tick or two.
    assert 30 <= NO_OUTPUT_MIN_AGE_S <= 300


# ─── the watcher ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_job_that_left_the_queue_with_no_subtitle_is_no_output_and_fires_nothing(tmp_path):
    prov, db = _ledger(tmp_path)
    rel = "TV/Tom and Jerry/Season 1950/Tom and Jerry - S1950E09 - Casanova Cat.mkv"
    _plant(rel, srt=False)
    lid = prov.record(canonical_path=rel, scan_id="s1", series_id=7)
    _age(db, lid, TEN_MINUTES)

    w, fired = _watcher(prov)
    await w._pass_pending()

    completed_at, outcome = _outcome(db, lid)
    assert completed_at is not None, "the job left subgen's queue; it must not stay pending forever"
    assert outcome == "no_output"
    assert fired == [], f"no subtitle exists, so nothing may be written back: {fired}"


@pytest.mark.asyncio
async def test_a_young_job_without_a_subtitle_stays_pending(tmp_path):
    # Between recording the ledger row and subgen accepting /batch, the path is
    # in neither queue. Judging it then would suppress a file that is about to
    # transcribe normally.
    prov, db = _ledger(tmp_path)
    rel = "TV/Show/Season 1/Show - S01E01.mkv"
    _plant(rel, srt=False)
    lid = prov.record(canonical_path=rel, scan_id="s2")

    w, fired = _watcher(prov)
    await w._pass_pending()

    assert _outcome(db, lid) == (None, None)
    assert fired == []


@pytest.mark.asyncio
async def test_job_with_a_subtitle_is_written_and_runs_the_full_flow(tmp_path):
    prov, db = _ledger(tmp_path)
    rel = "TV/Show/Season 1/Show - S01E02.mkv"
    _plant(rel, srt=True)
    lid = prov.record(canonical_path=rel, scan_id="s3", series_id=9)

    w, fired = _watcher(prov)
    await w._pass_pending()

    completed_at, outcome = _outcome(db, lid)
    assert completed_at is not None
    assert outcome == "written"
    assert "aftercare" in fired and "bazarr_upload" in fired and "plex" in fired


@pytest.mark.asyncio
async def test_a_directory_job_keeps_the_existing_completion(tmp_path):
    # A season-folder job has no single sidecar to look for; it must behave
    # exactly as before rather than be misjudged as no output.
    from subarr.config import settings

    prov, db = _ledger(tmp_path)
    rel = "TV/Folder Show/Season 2"
    (settings.media_root / rel).mkdir(parents=True, exist_ok=True)
    lid = prov.record(canonical_path=rel, scan_id="s4")
    _age(db, lid, TEN_MINUTES)

    w, fired = _watcher(prov)
    await w._pass_pending()

    completed_at, outcome = _outcome(db, lid)
    assert completed_at is not None
    assert outcome is None
    assert "aftercare" in fired


# ─── the ledger ──────────────────────────────────────────────────────────────


def test_no_output_paths_reflect_the_latest_completion_per_path(tmp_path):
    prov, db = _ledger(tmp_path)
    a, b = "TV/A/S01E01.mkv", "TV/B/S01E01.mkv"

    first = prov.record(canonical_path=a, scan_id="x")
    prov.mark_completed(first, outcome="no_output")
    prov.mark_completed(prov.record(canonical_path=b, scan_id="y"), outcome="no_output")
    # b was later transcribed successfully: it must no longer be held back.
    prov.mark_completed(prov.record(canonical_path=b, scan_id="z"), when=time.time() + 1, outcome="written")

    assert prov.no_output_paths_since(time.time() - 3600) == {a}


def test_no_output_paths_respect_the_window(tmp_path):
    prov, _ = _ledger(tmp_path)
    lid = prov.record(canonical_path="TV/Old/S01E01.mkv", scan_id="o")
    prov.mark_completed(lid, when=time.time() - 30 * 86400, outcome="no_output")
    assert prov.no_output_paths_since(time.time() - 7 * 86400) == set()


def test_bazarr_retry_ignores_no_output_jobs(tmp_path):
    prov, _ = _ledger(tmp_path)
    lid = prov.record(canonical_path="TV/C/S01E01.mkv", scan_id="c", series_id=3)
    prov.mark_completed(lid, outcome="no_output")
    assert prov.completed_without_bazarr() == []


def test_ledger_entries_carry_the_outcome(tmp_path):
    prov, _ = _ledger(tmp_path)
    lid = prov.record(canonical_path="TV/D/S01E01.mkv", scan_id="d")
    prov.mark_completed(lid, outcome="no_output")
    [entry] = prov.query_by_path("TV/D/S01E01.mkv")
    assert entry.outcome == "no_output"
    assert entry.to_dict()["outcome"] == "no_output"


# ─── the Queue page ──────────────────────────────────────────────────────────


def test_queue_labels_a_no_output_job_as_an_issue_not_subtitle_written():
    from subarr.routers.queue import _path_outcome_chip
    from subarr.scan_store import PATH_STATUS_OK

    rel = "TV/Tom and Jerry/Season 1950/Casanova Cat.mkv"
    chip = _path_outcome_chip(
        PATH_STATUS_OK,
        {"queued": 1},
        None,
        canonical_path=rel,
        completed_paths={rel},
        no_output_paths={rel},
    )
    assert chip["category"] == "error"
    assert chip["label"] == "no subtitle"
    assert "subtitle written" not in chip["detail"]


def test_queue_still_labels_a_written_job_completed():
    from subarr.routers.queue import _path_outcome_chip
    from subarr.scan_store import PATH_STATUS_OK

    rel = "TV/Show/Season 1/Show - S01E02.mkv"
    chip = _path_outcome_chip(
        PATH_STATUS_OK, {"queued": 1}, None, canonical_path=rel, completed_paths={rel}, no_output_paths=set()
    )
    assert chip["label"] == "completed"


# ─── auto-queue and backfill hold the file back ─────────────────────────────


def _episode_item(path: str):
    import dataclasses

    from subarr.coverage_engine import CoverageItem

    item = CoverageItem(
        media_type="episode",
        title="Tom and Jerry",
        episode_number="1950x9",
        original_language="Korean",  # English is denied by the default rules
        monitored=True,
        tags=[],
        canonical_path="TV/Tom and Jerry",
        has_sub_on_disk=False,
        bazarr_episode_id=1,
        score=900,
        verification_state="verified",
    )
    return dataclasses.replace(item, file_canonical_path=path)


def test_evaluate_holds_back_a_file_whose_last_attempt_produced_nothing():
    from subarr.auto_queue import evaluate
    from subarr.schedule_store import MODE_AUTO_RULES, AutoQueueRules

    held = "TV/Tom and Jerry/Season 1950/Casanova Cat.mkv"
    fine = "TV/Tom and Jerry/Season 1950/Other.mkv"
    rules = AutoQueueRules(mode=MODE_AUTO_RULES, min_score=0, settle_minutes=0)
    decisions = evaluate(
        [_episode_item(held), _episode_item(fine)], rules, in_flight_paths=set(), suppressed_paths={held}
    )
    by_path = {d.item.file_canonical_path: d for d in decisions}
    assert by_path[held].action == "skip"
    assert "no subtitle" in by_path[held].reason
    assert by_path[fine].action == "queue"


class _Stop(Exception):
    pass


@pytest.mark.asyncio
async def test_the_coverage_walk_passes_the_no_output_set_to_evaluate(monkeypatch):
    # Computed-but-never-consumed guard: the cooldown only works if the walk
    # actually hands it to evaluate.
    import subarr.scheduler as sched_mod
    from subarr.schedule_store import AutoQueueRules

    captured = {}

    def fake_evaluate(items, rules, **kw):
        captured.update(kw)
        raise _Stop

    monkeypatch.setattr(sched_mod, "evaluate", fake_evaluate)
    monkeypatch.setattr(sched_mod, "build_coverage", AsyncMock(return_value=MagicMock(items=[])))

    s = sched_mod.Scheduler.__new__(sched_mod.Scheduler)
    s._schedule = MagicMock()
    s._schedule.get_rules.return_value = AutoQueueRules()
    s._schedule.get_schedule.return_value = MagicMock(probe_roots="")
    s._probe_walker = None
    s._bundle_provider = lambda: MagicMock()
    s._caps_provider = lambda: None
    s._provenance = MagicMock()
    s._provenance.pending.return_value = []
    s._provenance.no_output_paths_since.return_value = {"TV/Silent/S01E01.mkv"}
    s._pending_queue = MagicMock()
    s._pending_queue.active_paths.return_value = set()
    s._maybe_poke_bazarr_for_stale_disk = AsyncMock(return_value={})

    with pytest.raises(_Stop):
        await s._run_coverage_walk_locked()
    assert captured.get("suppressed_paths") == {"TV/Silent/S01E01.mkv"}


def test_backfill_passes_the_no_output_set(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import subarr.backfill as backfill_mod
    from subarr.routers import queue as queue_router
    from subarr.schedule_store import AutoQueueRules

    captured = {}

    def fake_eligible(items, rules, **kw):
        captured.update(kw)
        return []

    monkeypatch.setattr(backfill_mod, "eligible_backfill_items", fake_eligible)

    app = FastAPI()
    app.include_router(queue_router.router)
    app.state.coverage_cache = MagicMock()
    app.state.coverage_cache.get_cached.return_value = MagicMock(items=[])
    app.state.schedule = MagicMock()
    app.state.schedule.get_rules.return_value = AutoQueueRules()
    app.state.pending_queue = MagicMock()
    app.state.pending_queue.active_paths.return_value = set()
    app.state.provenance = MagicMock()
    app.state.provenance.pending.return_value = []
    app.state.provenance.no_output_paths_since.return_value = {"TV/Silent/S01E01.mkv"}

    with TestClient(app) as c:
        r = c.post("/api/queue/backfill")
    assert r.status_code == 200, r.text
    assert captured.get("suppressed_paths") == {"TV/Silent/S01E01.mkv"}
