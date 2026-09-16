"""#558: a video with [brackets] in its name never had its subtitle found.

`_find_srt_sidecar` listed candidates with `parent.glob(f"{stem}*.srt")`. In a
glob, `[HEVC+x265 Priority]` is a CHARACTER CLASS, not literal text, so the
pattern could not match the file's own name: with
`Sea Shadows - S01E01 ... [HEVC+x265 Priority] -Freek911.en.srt` on disk the
lookup returned None. Since v1.1 that silently skipped retime, aftercare and the
direct Bazarr upload for every such file; since 2.7.7 (#545) it also recorded
the job as "no subtitle", held the file back for 7 days and skipped all
write-back. 759 of 4,364 videos in one real library have `[` in the name.

The same pattern language can also match the WRONG file: `Show [a]*.srt` matches
`Show a.en.srt`, a sibling that belongs to a different video.

Imports are local: conftest reloads subarr modules between tests.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock

import pytest

REPORTED = (
    "TV/Sea Shadows/Season 1/Sea Shadows - S01E01 - Episode 1 HDTV-2160p [HEVC+x265 Priority] -Freek911.mkv"
)
SRT = "1\n00:00:01,000 --> 00:00:02,000\nHi.\n\n"


@dataclass
class _Caps:
    reachable: bool = True
    has_queue: bool = True
    is_subarr_subgen: bool = True


def _stem(rel):
    return rel.rsplit("/", 1)[-1].rsplit(".", 1)[0]


def _plant(rel: str, *, srt_names=()):
    """Create the video and exactly the named sidecars. No glob anywhere:
    the helper must not share the bug it is testing."""
    from subarr.config import settings

    video = settings.media_root / rel
    video.parent.mkdir(parents=True, exist_ok=True)
    video.write_bytes(b"")
    for n in srt_names:
        (video.parent / n).write_text(SRT, encoding="utf-8")
    return video


def _ledger(tmp_path):
    from subarr.migrate import run_migrations
    from subarr.provenance import ProvenanceStore

    db = tmp_path / "prov.db"
    run_migrations(db)
    return ProvenanceStore(db), db


def _row(db, ledger_id):
    con = sqlite3.connect(str(db))
    r = con.execute("SELECT completed_at, outcome FROM subs_generated WHERE id = ?", (ledger_id,)).fetchone()
    con.close()
    return r


def _force(db, ledger_id, *, completed_at, outcome, queued_ago=600):
    con = sqlite3.connect(str(db))
    con.execute(
        "UPDATE subs_generated SET completed_at = ?, outcome = ?, queued_at = ? WHERE id = ?",
        (completed_at, outcome, time.time() - queued_ago, ledger_id),
    )
    con.commit()
    con.close()


def _watcher(prov):
    from subarr.completion_watcher import CompletionWatcher

    subgen = MagicMock()
    subgen.queue = AsyncMock(return_value={"queued": [], "processing": []})
    w = CompletionWatcher(subgen=subgen, bazarr=MagicMock(), provenance=prov, caps_provider=lambda: _Caps())
    fired: list[str] = []

    def rec(name):
        def _f(*a, **k):
            fired.append(name)

        return _f

    async def up(*a, **k):
        fired.append("bazarr_upload")
        return True

    async def plex(*a, **k):
        fired.append("plex")

    w._run_retime = rec("retime")
    w._run_aftercare = rec("aftercare")
    w._maybe_forced_segment = rec("forced_segment")
    w._try_upload_to_bazarr = up
    w._maybe_plex_partial_scan = plex
    w._pass_retry_bazarr_notify = AsyncMock()
    return w, fired


def _bare_watcher():
    from subarr.completion_watcher import CompletionWatcher

    return CompletionWatcher.__new__(CompletionWatcher)


# --- the lookup ---------------------------------------------------------------


def test_finds_the_sidecar_of_the_reported_bracketed_file(subarr_env):
    video = _plant(REPORTED, srt_names=[f"{_stem(REPORTED)}.en.srt"])
    found = _bare_watcher()._find_srt_sidecar(REPORTED)
    assert found is not None, "the .en.srt is on disk; brackets in the name must not hide it"
    assert found.endswith(f"{video.stem}.en.srt")


def test_a_pattern_look_alike_sibling_is_not_taken_for_this_videos_subtitle(subarr_env):
    # As a glob, "Show [a]*.srt" matches "Show a.en.srt", which is another video's file.
    rel = "TV/Show/Season 1/Show [a].mkv"
    _plant(rel, srt_names=["Show a.en.srt"])
    _plant("TV/Show/Season 1/Show a.mkv")
    assert _bare_watcher()._find_srt_sidecar(rel) is None


@pytest.mark.parametrize(
    "name",
    [
        "Movie (2024) [1080p] {tmdb-123}.mkv",
        "Movie ]closing first[.mkv",
        "Movie [!neg].mkv",
    ],
)
def test_other_bracket_shapes_are_literal_too(subarr_env, name):
    rel = f"Movies/X/{name}"
    _plant(rel, srt_names=[f"{_stem(rel)}.en.srt"])
    assert _bare_watcher()._find_srt_sidecar(rel) is not None


# --- the watcher --------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_bracketed_job_with_a_subtitle_is_written_and_runs_the_full_flow(subarr_env, tmp_path):
    prov, db = _ledger(tmp_path)
    _plant(REPORTED, srt_names=[f"{_stem(REPORTED)}.en.srt"])
    lid = prov.record(canonical_path=REPORTED, scan_id="s1", series_id=7)

    w, fired = _watcher(prov)
    await w._pass_pending()

    _, outcome = _row(db, lid)
    assert outcome == "written", "the subtitle exists; this is not a no-output job"
    for step in ("retime", "aftercare", "bazarr_upload", "plex"):
        assert step in fired, f"{step} was skipped for a bracketed file: {fired}"


@pytest.mark.asyncio
async def test_a_job_wrongly_recorded_as_no_output_heals_once_its_subtitle_is_found(subarr_env, tmp_path):
    # Installs on 2.7.7/2.7.8 already hold rows like this; they must recover on
    # upgrade rather than sit under Issues for the 7-day hold.
    prov, db = _ledger(tmp_path)
    _plant(REPORTED, srt_names=[f"{_stem(REPORTED)}.en.srt"])
    lid = prov.record(canonical_path=REPORTED, scan_id="s1", series_id=7)
    first_completion = time.time() - 3600
    _force(db, lid, completed_at=first_completion, outcome="no_output")

    w, fired = _watcher(prov)
    await w._tick()

    completed_at, outcome = _row(db, lid)
    assert outcome == "written"
    assert completed_at == pytest.approx(first_completion), "the original completion time is history; keep it"
    for step in ("retime", "aftercare", "bazarr_upload", "plex"):
        assert step in fired, f"the skipped write-back must run now: missing {step} in {fired}"
    assert REPORTED not in prov.no_output_paths_since(0), "a healed file must no longer be held back"

    fired.clear()
    await w._tick()
    assert fired == [], f"a healed row must not be healed again every tick: {fired}"


@pytest.mark.asyncio
async def test_a_genuine_no_output_row_is_left_alone(subarr_env, tmp_path):
    prov, db = _ledger(tmp_path)
    rel = "TV/Tom and Jerry/Season 1950/Tom and Jerry - S1950E09 [Silent].mkv"
    _plant(rel)  # no sidecar: the job really produced nothing
    lid = prov.record(canonical_path=rel, scan_id="s2")
    done = time.time() - 3600
    _force(db, lid, completed_at=done, outcome="no_output")

    w, fired = _watcher(prov)
    await w._tick()

    completed_at, outcome = _row(db, lid)
    assert outcome == "no_output"
    assert completed_at == pytest.approx(done)
    assert fired == []
    assert rel in prov.no_output_paths_since(0)


@pytest.mark.asyncio
async def test_healing_only_looks_inside_the_hold_window(subarr_env, tmp_path):
    # Older rows no longer hold anything back; re-running their write-back
    # weeks later would be surprising. Leave them.
    from subarr.provenance import NO_OUTPUT_COOLDOWN_S

    prov, db = _ledger(tmp_path)
    _plant(REPORTED, srt_names=[f"{_stem(REPORTED)}.en.srt"])
    lid = prov.record(canonical_path=REPORTED, scan_id="s3")
    old = time.time() - NO_OUTPUT_COOLDOWN_S - 3600
    _force(db, lid, completed_at=old, outcome="no_output")

    w, fired = _watcher(prov)
    await w._tick()

    assert _row(db, lid)[1] == "no_output"
    assert fired == []


# --- the tuning tool shares the lookup shape ------------------------------------


def test_retime_tune_finds_the_original_sidecar_of_a_bracketed_file(subarr_env):
    from subarr.retime_tune import _original_sidecar

    video = _plant(REPORTED, srt_names=[f"{_stem(REPORTED)}.subgen.large-v3.eng.srt"])
    found = _original_sidecar(video)
    assert found is not None and found.name.endswith(".subgen.large-v3.eng.srt")
