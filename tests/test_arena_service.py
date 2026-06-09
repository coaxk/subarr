"""#131 — ArenaService: background run lifecycle + SSE event stream.

Mirrors ScanRunner's shape (create → start → _run task → subscribe()). Driven
here by a fake CandidateRunner so the lifecycle is tested without subgen.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

import subarr
from subarr.arena import ArenaUnsupported, ConfigVariant
from subarr.arena_service import ArenaRun, ArenaService
from subarr.arena_store import ArenaStore

_ARENA_SQL = (Path(subarr.__file__).parent / "migrations" / "009_arena_runs.sql").read_text()


@pytest.fixture
def store(tmp_path):
    s = ArenaStore(tmp_path / "arena.db")
    s._conn.executescript(_ARENA_SQL)  # apply just this migration's schema
    return s


def _srt(line: str) -> str:
    return f"1\n00:00:00,000 --> 00:00:03,000\n{line}\n"


class FakeRunner:
    def __init__(self, outputs, supported=True):
        self._outputs = list(outputs)
        self._supported = supported

    async def preflight(self):
        if not self._supported:
            raise ArenaUnsupported("needs v4.10")

    async def prepare(self, media_path):
        return [{"kind": "speech", "ranges": [(0.0, 3.0)]}]  # one clip

    async def run(self, clip_idx, *, task, kwargs):
        return self._outputs.pop(0) if self._outputs else None

    async def cleanup(self):
        pass


def _service(store, outputs, supported=True) -> ArenaService:
    return ArenaService(store, build_runner=lambda run: FakeRunner(outputs, supported))


def test_create_persists_pending_run(store):
    svc = _service(store, [])
    run = svc.create("/media/clip.mkv", [ConfigVariant("a", {"beam_size": 5})], source_language="ko")
    assert run.status == "pending"
    persisted = svc.get(run.id)  # read back from SQLite
    assert persisted is not None and persisted.id == run.id
    assert persisted.media_path == "/media/clip.mkv"
    assert persisted.variants == [{"label": "a", "kwargs": {"beam_size": 5}}]
    assert persisted.source_language == "ko"


def test_save_coerces_numpy_like_scalars_in_result(store):
    # A numpy float32 leaking from the QE judge is not JSON-serializable and
    # crashed store.save → the run stayed "running" forever. The store must
    # coerce any scalar that quacks like numpy (has .item()) rather than crash.
    class FakeNpScalar:
        def __init__(self, v):
            self._v = v

        def item(self):
            return self._v

    svc = _service(store, [])
    run = svc.create("/m.mkv", [ConfigVariant("a", {})])
    run.status = "done"
    run.result = {"winner": "a", "score": FakeNpScalar(0.581)}
    store.save(run)  # must not raise
    persisted = svc.get(run.id)
    assert persisted.status == "done"
    assert persisted.result["score"] == 0.581


def test_prune_older_than_drops_old_keeps_recent(store):
    # #136 retention: append-only arena_runs grows unbounded on long-running
    # installs. prune_older_than() drops runs whose created_at predates the
    # cutoff and leaves recent ones, mirroring scan_store's age-cutoff DELETE.
    import time as _t

    now = _t.time()
    old = ArenaRun(
        id="old", media_path="/old.mkv", variants=[], status="done", created_at=now - 10 * 86400
    )  # 10 days old
    recent = ArenaRun(
        id="recent", media_path="/recent.mkv", variants=[], status="done", created_at=now - 1 * 86400
    )  # 1 day old
    store.save(old)
    store.save(recent)

    cutoff = now - 7 * 86400  # 7-day window
    deleted = store.prune_older_than(cutoff)

    assert deleted == 1
    assert store.get("old") is None  # old run pruned
    assert store.get("recent") is not None  # recent run kept


@pytest.mark.asyncio
async def test_concurrent_sweeps_serialize_one_at_a_time(store):
    # A user firing several sweeps must NOT run them all at once — each runs
    # CPU-heavy QE and N concurrent sweeps saturate the box (observed: 6
    # concurrent Nutuk sweeps pinned subarr-next + cascade-failed). Submits
    # should queue; only one processes at a time.
    gate = asyncio.Event()
    processing_started = asyncio.Event()
    started = []

    class BlockingRunner:
        def __init__(self, run):
            self.arun = run  # NOT self.run — would shadow run()

        async def preflight(self):
            pass

        async def prepare(self, media_path):
            started.append(self.arun.id)
            processing_started.set()
            await gate.wait()  # hold the single slot
            return [{"kind": "speech", "ranges": []}]

        async def run(self, clip_idx, *, task, kwargs):
            return _srt("x")

        async def cleanup(self):
            pass

    svc = ArenaService(store, build_runner=lambda run: BlockingRunner(run))
    r1 = svc.create("/a.mkv", [ConfigVariant("default", {})])
    r2 = svc.create("/b.mkv", [ConfigVariant("default", {})])
    svc.start(r1)
    svc.start(r2)

    await asyncio.wait_for(processing_started.wait(), 2)  # r1 is in the slot
    for _ in range(50):  # let r2's task reach the gate
        if svc.get(r2.id).status == "queued":
            break
        await asyncio.sleep(0.01)

    assert started == [r1.id]  # r2 has NOT started processing
    assert svc.get(r1.id).status == "running"
    assert svc.get(r2.id).status == "queued"

    gate.set()  # release; r1 finishes, r2 proceeds
    await asyncio.wait_for(asyncio.gather(svc._tasks[r1.id], svc._tasks[r2.id]), 5)
    assert started == [r1.id, r2.id]
    g1, g2 = svc.get(r1.id), svc.get(r2.id)
    assert g1.status == "done", f"r1 {g1.status} err={g1.error}"
    assert g2.status == "done", f"r2 {g2.status} err={g2.error}"


def test_aggregate_runs_by_language_groups_and_ranks():
    # #26 herd view: group done sweeps by detected language, rank recipes by
    # wins then mean composite. Pure helper — no DB.
    from subarr.arena_store import aggregate_runs_by_language, ArenaRun

    def mk(rid, lang, winner, agg):
        return ArenaRun(
            id=rid,
            media_path=f"/{rid}.mkv",
            variants=[{"label": a["label"], "kwargs": {}} for a in agg],
            source_language=lang,
            status="done",
            result={"winner": winner, "tie": False, "aggregate": agg},
        )

    runs = [
        mk(
            "k1",
            "korean",
            "noisy-robust",
            [{"label": "noisy-robust", "mean_composite": 70}, {"label": "default", "mean_composite": 50}],
        ),
        mk(
            "k2",
            "korean",
            "noisy-robust",
            [{"label": "noisy-robust", "mean_composite": 80}, {"label": "default", "mean_composite": 60}],
        ),
        mk(
            "f1",
            "french",
            "default",
            [{"label": "default", "mean_composite": 75}, {"label": "noisy-robust", "mean_composite": 40}],
        ),
    ]
    out = aggregate_runs_by_language(runs)
    by = {x["language"]: x for x in out}  # languages normalized to ISO
    assert by["ko"]["sweeps"] == 2
    assert by["ko"]["recipes"][0]["label"] == "noisy-robust"  # ranked top by wins
    ko = {r["label"]: r for r in by["ko"]["recipes"]}
    assert ko["noisy-robust"]["wins"] == 2
    assert ko["noisy-robust"]["mean_composite"] == 75.0  # (70+80)/2
    assert by["fr"]["recipes"][0]["label"] == "default"


def _lb_mk(rid, lang, agg):
    """A done sweep with one file (media_path = rid) for leaderboard tests."""
    from subarr.arena_store import ArenaRun

    return ArenaRun(
        id=rid,
        media_path=f"/{rid}.mkv",
        variants=[{"label": a["label"], "kwargs": {}} for a in agg],
        source_language=lang,
        status="done",
        result={"winner": agg[0]["label"], "tie": False, "aggregate": agg},
    )


def test_global_leaderboard_weights_languages_equally():
    # #146: rank by MEAN OF PER-LANGUAGE MEANS — heavily-swept languages must
    # NOT skew the global score. Recipe A: eng has 2 files (means 80,100 -> 90),
    # fra 1 file (60). Equal-weight global = (90+60)/2 = 75, NOT the flat
    # across-files mean (80+100+60)/3 = 80.
    from subarr.arena_store import aggregate_global_leaderboard

    runs = [
        _lb_mk("e1", "eng", [{"label": "A", "mean_composite": 80}]),
        _lb_mk("e2", "eng", [{"label": "A", "mean_composite": 100}]),
        _lb_mk("f1", "fra", [{"label": "A", "mean_composite": 60}]),
    ]
    board = aggregate_global_leaderboard(runs, min_languages=2)
    row = {r["label"]: r for r in board}["A"]
    assert row["languages_covered"] == 2
    assert row["total_files"] == 3
    assert row["global_mean"] == 75.0
    assert row["eligible"] is True
    assert row["rank"] == 1


def test_global_leaderboard_min_languages_marks_ineligible():
    # #146: a recipe seen in fewer than min_languages real languages is returned
    # but eligible=False with rank=None (still accumulating, not a verdict).
    from subarr.arena_store import aggregate_global_leaderboard

    runs = [
        _lb_mk("e1", "eng", [{"label": "A", "mean_composite": 90}]),
        _lb_mk("f1", "fra", [{"label": "A", "mean_composite": 90}]),
    ]
    board = aggregate_global_leaderboard(runs, min_languages=3)
    row = board[0]
    assert row["label"] == "A"
    assert row["languages_covered"] == 2
    assert row["eligible"] is False
    assert row["rank"] is None


def test_global_leaderboard_excludes_und():
    # #146: the "und" (undetermined) bucket is not a real language — it must not
    # count toward languages_covered nor pull the mean.
    from subarr.arena_store import aggregate_global_leaderboard

    runs = [
        _lb_mk("e1", "eng", [{"label": "A", "mean_composite": 80}]),
        _lb_mk("f1", "fra", [{"label": "A", "mean_composite": 80}]),
        _lb_mk("u1", None, [{"label": "A", "mean_composite": 10}]),  # -> und
    ]
    board = aggregate_global_leaderboard(runs, min_languages=2)
    row = board[0]
    assert row["languages_covered"] == 2
    assert row["global_mean"] == 80.0


def test_global_leaderboard_ranks_eligible_above_ineligible():
    # #146: eligible recipes sorted by score get ranks 1..k; ineligible sort last
    # with rank None. B (3 langs) outranks A (3 langs, lower score); C (1 lang)
    # is ineligible.
    from subarr.arena_store import aggregate_global_leaderboard

    runs = []
    for lang in ("eng", "fra", "deu"):
        runs.append(_lb_mk(f"a-{lang}", lang, [{"label": "A", "mean_composite": 60}]))
        runs.append(_lb_mk(f"b-{lang}", lang, [{"label": "B", "mean_composite": 90}]))
    runs.append(_lb_mk("c-eng", "eng", [{"label": "C", "mean_composite": 99}]))
    board = aggregate_global_leaderboard(runs, min_languages=3)
    ranked = [r for r in board if r["eligible"]]
    assert [r["label"] for r in ranked] == ["B", "A"]
    assert [r["rank"] for r in ranked] == [1, 2]
    c = {r["label"]: r for r in board}["C"]
    assert c["eligible"] is False and c["rank"] is None


def test_global_leaderboard_empty():
    from subarr.arena_store import aggregate_global_leaderboard

    assert aggregate_global_leaderboard([]) == []


def test_store_global_leaderboard_reads_done_runs(store):
    # #146: store wrapper applies the same done-runs filter as the herd view.
    r = _lb_mk("s1", "eng", [{"label": "A", "mean_composite": 70}])
    store.save(r)
    board = store.aggregate_global_leaderboard(min_languages=1)
    assert any(x["label"] == "A" for x in board)


def test_resolve_source_language_unanimous_mislabel():
    # The Ring: tagged Danish, Whisper heard Dutch on ALL chunks → trust Whisper
    # (override) + flag the mislabel.
    from subarr.arena_service import resolve_source_language

    det = {"language": "nl", "n_agreeing": 3, "n_total": 3, "unanimous": True, "languages_heard": ["nl"]}
    lang, src, mixed, mislabel = resolve_source_language(det, tag="da", submit=None)
    assert (lang, src) == ("nl", "whisper")
    assert mislabel is True and mixed is False


def test_resolve_source_language_bilingual_split_keeps_tag():
    # Besa: tagged Serbian, Whisper heard en/en/sr (2/3) → bilingual; keep the
    # tag as primary, flag mixed. Must NOT flip to English.
    from subarr.arena_service import resolve_source_language

    det = {
        "language": "en",
        "n_agreeing": 2,
        "n_total": 3,
        "unanimous": False,
        "languages_heard": ["en", "sr"],
    }
    lang, src, mixed, mislabel = resolve_source_language(det, tag="sr", submit=None)
    assert (lang, src) == ("sr", "tagged")
    assert mixed is True and mislabel is False


def test_resolve_source_language_no_majority_uses_tag_not_mixed():
    # Black Wedding: 1/3 (three different guesses) → Whisper confused, NOT mixed.
    # Trust the tag; no mixed flag.
    from subarr.arena_service import resolve_source_language

    det = {
        "language": "sr",
        "n_agreeing": 1,
        "n_total": 3,
        "unanimous": False,
        "languages_heard": ["sr", "fr", "nn"],
    }
    lang, src, mixed, mislabel = resolve_source_language(det, tag="no", submit=None)
    assert (lang, src) == ("no", "tagged")
    assert mixed is False and mislabel is False


def test_resolve_source_language_multitrack_suppresses_mislabel():
    # Trigger: ger+rus audio tracks, sweep transcribed the DEFAULT (German) → de
    # 3/3. The tag may name the original (ru). multitrack must SUPPRESS the
    # mislabel — that's a different track, not a mislabeled tag.
    from subarr.arena_service import resolve_source_language

    det = {"language": "de", "n_agreeing": 3, "n_total": 3, "unanimous": True, "languages_heard": ["de"]}
    lang, src, mixed, mislabel = resolve_source_language(det, tag="ru", submit=None, multitrack=True)
    assert (lang, src) == ("de", "whisper") and mislabel is False
    # same disagreement on a SINGLE-track file would (correctly) flag mislabel
    assert resolve_source_language(det, tag="ru", submit=None, multitrack=False)[3] is True


def test_resolve_source_language_submit_wins_and_no_signal():
    from subarr.arena_service import resolve_source_language

    # user-set at submit beats everything
    assert resolve_source_language(
        {"language": "en", "unanimous": True, "n_agreeing": 3, "n_total": 3, "languages_heard": ["en"]},
        tag="de",
        submit="ja",
    )[:2] == ("ja", "user")
    # nothing trustworthy + no tag → undetermined
    assert resolve_source_language(
        {
            "language": "sr",
            "n_agreeing": 1,
            "n_total": 3,
            "unanimous": False,
            "languages_heard": ["sr", "fr", "de"],
        },
        tag=None,
        submit=None,
    )[:2] == (None, None)


def test_aggregate_buckets_unknown_language_under_und():
    # A sweep that ran but whose language couldn't be resolved (Whisper
    # inconclusive AND no tagged fallback) must NOT vanish — it buckets under
    # "und" (undetermined) so it stays visible in the herd.
    from subarr.arena_store import aggregate_runs_by_language, ArenaRun

    runs = [
        ArenaRun(
            id="u1",
            media_path="/mystery.mkv",
            variants=[{"label": "default", "kwargs": {}}],
            source_language=None,
            status="done",
            result={
                "winner": "default",
                "tie": False,
                "aggregate": [{"label": "default", "mean_composite": 60}],
            },
        ),
    ]
    out = aggregate_runs_by_language(runs)
    by = {x["language"]: x for x in out}
    assert "und" in by
    assert by["und"]["sweeps"] == 1
    assert by["und"]["recipes"][0]["label"] == "default"


def test_repeated_sweeps_of_one_file_consolidate_not_skew():
    # Bulk/repeat sweeping must not let one heavily-swept file dominate its
    # language bucket. Per-file consolidation: average a file's repeats, then
    # each FILE gets one vote.
    from subarr.arena_store import aggregate_runs_by_language, ArenaRun

    def mk(rid, path, winner, nr, df):
        return ArenaRun(
            id=rid,
            media_path=path,
            variants=[{"label": "x", "kwargs": {}}],
            source_language="ko",
            status="done",
            result={
                "winner": winner,
                "tie": False,
                "aggregate": [
                    {"label": "noisy-robust", "mean_composite": nr},
                    {"label": "default", "mean_composite": df},
                ],
            },
        )

    runs = [
        mk("a", "/same.mkv", "noisy-robust", 70, 50),
        mk("b", "/same.mkv", "noisy-robust", 72, 52),
        mk("c", "/same.mkv", "noisy-robust", 74, 54),  # same file ×3
        mk("d", "/other.mkv", "default", 40, 60),  # a different file
    ]
    ko = aggregate_runs_by_language(runs)[0]
    assert ko["language"] == "ko"
    assert ko["files"] == 2 and ko["sweeps"] == 4
    rec = {r["label"]: r for r in ko["recipes"]}
    # per-file winners: same.mkv→noisy-robust, other.mkv→default → 1 each, NOT 3–1
    assert rec["noisy-robust"]["wins"] == 1 and rec["default"]["wins"] == 1
    # noisy-robust mean across files: same.mkv=(70+72+74)/3=72, other=40 → (72+40)/2=56
    assert rec["noisy-robust"]["mean_composite"] == 56.0


def test_aggregate_normalizes_language_names_to_iso():
    # Old sweeps stored full names ('korean', from the deprecated header path);
    # robust detection now returns ISO ('ko'). They must MERGE into one bucket,
    # not show as two — otherwise the herd view looks broken.
    from subarr.arena_store import aggregate_runs_by_language, ArenaRun

    def mk(rid, lang):
        return ArenaRun(
            id=rid,
            media_path="/x.mkv",
            variants=[{"label": "default", "kwargs": {}}],
            source_language=lang,
            status="done",
            result={
                "winner": "default",
                "tie": False,
                "aggregate": [{"label": "default", "mean_composite": 60}],
            },
        )

    out = aggregate_runs_by_language([mk("a", "korean"), mk("b", "ko"), mk("c", "Korean")])
    assert len(out) == 1
    assert out[0]["language"] == "ko" and out[0]["sweeps"] == 3


def test_store_aggregate_by_language_reads_done_runs(store):
    svc = _service(store, [])
    r = svc.create("/m.mkv", [ConfigVariant("default", {})])
    r.status = "done"
    r.source_language = "korean"
    r.result = {
        "winner": "default",
        "tie": False,
        "aggregate": [{"label": "default", "mean_composite": 65.0}],
    }
    store.save(r)
    out = store.aggregate_by_language()
    assert len(out) == 1 and out[0]["language"] == "ko" and out[0]["sweeps"] == 1  # normalized


def test_source_language_persists_on_update(store):
    # #23: the detected language is set AFTER create (the initial INSERT has it
    # None), on a later save() — so the UPSERT's UPDATE clause must include it,
    # else it's silently dropped (result carried 'korean' but run stayed None).
    svc = _service(store, [])
    run = svc.create("/m.mkv", [ConfigVariant("a", {})])
    assert svc.get(run.id).source_language is None
    run.source_language = "korean"
    store.save(run)  # UPDATE (ON CONFLICT) path
    assert svc.get(run.id).source_language == "korean"


@pytest.mark.asyncio
async def test_run_completes_and_persists_result(store):
    svc = _service(store, [_srt("source line"), _srt("cand a"), _srt("cand b")])
    run = svc.create("/m.mkv", [ConfigVariant("a", {}), ConfigVariant("b", {})])
    svc.start(run)
    await svc._tasks[run.id]

    persisted = svc.get(run.id)  # the persisted truth, not the in-mem object
    assert persisted.status == "done"
    # outcomes is now the progress scaffold (1 clip × (source + 2 recipes) = 3 steps)
    assert persisted.outcomes["done"] == 3 and persisted.outcomes["total"] == 3
    assert persisted.result is not None
    assert "aggregate" in persisted.result and "winner" in persisted.result and "per_clip" in persisted.result


@pytest.mark.asyncio
async def test_fanout_leg_source_labeled_track_not_user(store):
    # Cosmetic (§3.3): a multi-track fan-out leg's language is the track's tag,
    # not a user pick — the herd source must read "track", not "user".
    svc = _service(store, [_srt("source"), _srt("a")])
    run = svc.create("/m.mkv", [ConfigVariant("a", {})], source_language="de", is_track_fanout=True)
    svc.start(run)
    await svc._tasks[run.id]
    assert svc.get(run.id).result["source_language_source"] == "track"


@pytest.mark.asyncio
async def test_user_pinned_source_stays_user(store):
    # Counterpart: a genuinely user-pinned single run keeps source "user".
    svc = _service(store, [_srt("source"), _srt("a")])
    run = svc.create("/m.mkv", [ConfigVariant("a", {})], source_language="de")
    svc.start(run)
    await svc._tasks[run.id]
    assert svc.get(run.id).result["source_language_source"] == "user"


@pytest.mark.asyncio
async def test_survives_a_fresh_service_on_the_same_store(store):
    """Simulates a restart: a new ArenaService over the same store still sees
    the finished sweep — this is what makes history survive a restart + feed
    the federated tournament."""
    svc = _service(store, [_srt("src"), _srt("a")])
    run = svc.create("/m.mkv", [ConfigVariant("a", {})])
    svc.start(run)
    await svc._tasks[run.id]

    fresh = ArenaService(store, build_runner=lambda r: None)
    again = fresh.get(run.id)
    assert again is not None and again.status == "done"
    assert [r.id for r in fresh.list()] == [run.id]


@pytest.mark.asyncio
async def test_preflight_failure_persists_error(store):
    svc = _service(store, [], supported=False)
    run = svc.create("/m.mkv", [ConfigVariant("a", {})])
    svc.start(run)
    await svc._tasks[run.id]

    persisted = svc.get(run.id)
    assert persisted.status == "error"
    assert "v4.10" in persisted.error
    assert persisted.result is None


@pytest.mark.asyncio
async def test_events_streamed_to_subscriber(store):
    svc = _service(store, [_srt("src"), _srt("a"), None])
    run = svc.create("/m.mkv", [ConfigVariant("a", {}), ConfigVariant("b", {})])

    events: list[dict] = []

    async def collect():
        async for evt in svc.subscribe(run.id):
            events.append(evt)

    consumer = asyncio.create_task(collect())
    await asyncio.sleep(0)  # let the subscriber register before the run emits
    svc.start(run)
    await svc._tasks[run.id]
    await asyncio.wait_for(consumer, timeout=2)

    kinds = [e["event"] for e in events]
    assert kinds[0] == "queued"  # serialize: every run is queued before it starts
    assert kinds[1] == "start"
    assert "clip" in kinds
    assert kinds.count("step") == 3  # 1 clip × (source + 2 recipes)
    assert kinds[-1] == "done"


def test_list_is_newest_first_with_summaries(store):
    svc = _service(store, [])
    a = svc.create("/a.mkv", [ConfigVariant("x", {})])
    a.created_at = 100.0
    store.save(a)
    b = svc.create("/b.mkv", [ConfigVariant("y", {}), ConfigVariant("z", {})])
    b.created_at = 200.0
    store.save(b)
    listed = svc.list()
    assert [r.id for r in listed] == [b.id, a.id]  # newest first
    s = b.summary()
    assert s["recipe_count"] == 2 and s["status"] == "pending" and "winner" in s
    assert "result" not in s  # summaries stay light (no scorecards)


def test_reconcile_marks_interrupted_runs_errored(store):
    svc = _service(store, [])
    run = svc.create("/m.mkv", [ConfigVariant("a", {})])
    run.status = "running"
    store.save(run)  # pretend it was mid-flight at crash
    n = store.reconcile_interrupted()
    assert n == 1
    after = svc.get(run.id)
    assert after.status == "error" and "interrupted" in after.error


def test_delete_removes_run(store):
    svc = _service(store, [])
    run = svc.create("/m.mkv", [ConfigVariant("a", {})])
    assert svc.get(run.id) is not None
    assert svc.delete(run.id) is True
    assert svc.get(run.id) is None
    assert svc.delete("nope") is False
