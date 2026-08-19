"""#451 Phase 5: advisory text-LID sanity check integration + lifecycle.

The text-LID sanity check is ADVISORY (CONTRACTS: no checker result may gate
completion, upload, provider selection, OCR, or HI policy). These tests prove
completion/upload/scan/aftercare proceed unchanged for every checker outcome and
every failure mode (WARN, INCONCLUSIVE, UNSUPPORTED, UNAVAILABLE, exceptions,
timeout, cancellation, missing sidecar, duplicate schedules), that a single
bounded structured result is stored with the aftercare signal/result model, and
that it is surfaced through the aftercare preview/history surface
(GET /api/aftercare/results) + its rendering helper.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from subarr.aftercare_store import AfterCareStore
from subarr.completion_watcher import CompletionWatcher


def _store(tmp_path):
    from subarr.migrate import run_migrations

    db = tmp_path / "a.db"
    run_migrations(db)
    return AfterCareStore(db)


def _srt(tmp_path, name="S01E01.en.srt"):
    p = tmp_path / name
    p.write_text(
        "1\n00:00:01,000 --> 00:00:03,000\nHello there.\n\n2\n00:00:04,000 --> 00:00:05,000\nGoodbye.\n",
        encoding="utf-8",
    )
    return str(p)


class _Entry:
    id = 7
    canonical_path = "TV/Show/S01E01.mkv"
    source = "subgenscan"
    task = "transcribe"
    source_language = "en"
    target_language = None
    submission_origin = "sonarr"
    webhook_event = None
    webhook_language = None
    webhook_subtitle = None
    provenance_conflict = None
    series_id = 3


class _FakeResult:
    """Stands in for text_lid.TextLanguageResult — bounded to_dict(), never the
    subtitle text."""

    def __init__(self, status, reason=None, languages=("en",), provenance=None):
        self._d = {
            "status": status,
            "reason": reason,
            "languages": languages,
            "evidence": {},
            "provenance": provenance or {},
            "checker_version": "1.0.0",
            "policy_version": "pr451-v1",
            "probabilities": {},
        }

    def to_dict(self):
        return dict(self._d)


def _watcher(store, srt_path, upload=True):
    """Build a CompletionWatcher with only the completion-flow deps stubbed so we
    can assert mark/retime/aftercare/upload/scan/plex all proceed. Returns
    (w, events, scanned, enqueued)."""
    w = CompletionWatcher.__new__(CompletionWatcher)
    w._aftercare = store
    w._lang_check_tasks = {}
    w._lang_check_semaphore = None
    w._duration_lookup = lambda p: None
    w._find_srt_sidecar = lambda p: srt_path
    events = []
    scanned = []
    enqueued = []

    w._provenance = type("P", (), {"mark_completed": lambda self, lid: events.append(("mark", lid))})()
    w._run_retime = lambda e: events.append(("retime", e.id))
    w._maybe_forced_segment = lambda e: events.append(("forced", e.id))

    async def _upload(e):
        events.append(("upload", e.id))
        return upload

    w._try_upload_to_bazarr = _upload

    async def _scan(lid, sid, cp):
        scanned.append((lid, sid, cp))

    w._trigger_bazarr_scan = _scan

    async def _plex(cp):
        events.append(("plex", cp))

    w._maybe_plex_partial_scan = _plex
    # A queue spy — complete_entry never touches it; the checker must not either.
    w._pending = type("Q", (), {"enqueue": lambda self, *a, **k: enqueued.append(a)})()
    w._events = events
    return w, events, scanned, enqueued


async def _complete(w, entry):
    await w.complete_entry(entry)
    tasks = list(getattr(w, "_lang_check_tasks", {}).values())
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


_OUTCOMES = ["PASS", "WARN", "INCONCLUSIVE", "UNSUPPORTED", "UNAVAILABLE"]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", _OUTCOMES)
async def test_completion_proceeds_and_records_each_status(tmp_path, monkeypatch, status):
    store = _store(tmp_path)
    w, events, _scanned, enqueued = _watcher(store, _srt(tmp_path))
    entry = _Entry()
    ran = {}

    async def fake_check(entry_, srt_path, identity):
        ran["status"] = status
        w._record_lang_check(
            entry_,
            _FakeResult(
                status, reason="r", languages=("en",), provenance={"origin": "sonarr", "conflict": 0}
            ),
        )

    monkeypatch.setattr(w, "_run_language_check", fake_check)
    await _complete(w, entry)

    # Completion flow ran unchanged, in order, whatever the outcome.
    assert [e[0] for e in events] == ["mark", "retime", "forced", "upload", "plex"]
    assert enqueued == []  # no requeue / queue-count change
    assert ran.get("status") == status
    rows = store.list_results(view="all", limit=10, offset=0)
    assert len(rows) == 1
    assert rows[0]["text_lang_check"]["status"] == status
    assert rows[0]["text_lang_check"]["provenance"]["origin"] == "sonarr"
    assert rows[0]["text_lang_check"]["provenance"]["conflict"] == 0


@pytest.mark.asyncio
async def test_completion_proceeds_when_checker_raises(tmp_path, monkeypatch):
    store = _store(tmp_path)
    w, events, _scanned, _enq = _watcher(store, _srt(tmp_path))

    async def raise_check(entry_, srt_path, identity):
        raise ValueError("boom")

    monkeypatch.setattr(w, "_run_language_check", raise_check)
    await _complete(w, _Entry())
    assert [e[0] for e in events] == ["mark", "retime", "forced", "upload", "plex"]
    rows = store.list_results(view="all", limit=10, offset=0)
    assert rows[0]["text_lang_check"] is None  # fail-soft: recorded nothing


@pytest.mark.asyncio
async def test_language_check_times_out_failsoft(tmp_path, monkeypatch):
    import subarr.completion_watcher as cw
    from subarr import text_lid

    store = _store(tmp_path)
    w, events, _scanned, _enq = _watcher(store, _srt(tmp_path))
    monkeypatch.setattr(cw, "LANG_CHECK_TIMEOUT_S", 0.01)

    def slow(*a, **k):
        import time

        time.sleep(0.2)  # blocks the executor thread past the 0.01s deadline
        raise AssertionError("must not surface - wait_for should have timed out")

    monkeypatch.setattr(text_lid, "check_subtitle_text", slow)
    await _complete(w, _Entry())
    assert [e[0] for e in events] == ["mark", "retime", "forced", "upload", "plex"]
    assert store.list_results(view="all", limit=10, offset=0)[0]["text_lang_check"] is None


@pytest.mark.asyncio
async def test_language_check_cancelled_failsoft(tmp_path, monkeypatch):
    store = _store(tmp_path)
    w, events, _scanned, _enq = _watcher(store, _srt(tmp_path))

    async def slow_check(entry_, srt_path, identity):
        await asyncio.sleep(3600)

    monkeypatch.setattr(w, "_run_language_check", slow_check)
    await w.complete_entry(_Entry())  # schedules; returns immediately
    tasks = list(w._lang_check_tasks.values())
    assert len(tasks) == 1
    tasks[0].cancel()
    with pytest.raises(asyncio.CancelledError):
        await tasks[0]
    assert w._lang_check_tasks == {}  # done_callback released the strong ref
    assert [e[0] for e in events] == ["mark", "retime", "forced", "upload", "plex"]


@pytest.mark.asyncio
async def test_missing_sidecar_does_not_block_completion(tmp_path):
    store = _store(tmp_path)
    w, events, _scanned, _enq = _watcher(store, srt_path=None)
    await _complete(w, _Entry())
    assert [e[0] for e in events] == ["mark", "retime", "forced", "upload", "plex"]
    assert w._lang_check_tasks == {}  # nothing scheduled without a sidecar
    assert store.list_results(view="all", limit=10, offset=0) == []  # no aftercare row either


@pytest.mark.asyncio
async def test_duplicate_schedules_coalesce(tmp_path, monkeypatch):
    store = _store(tmp_path)
    w, _events, _scanned, _enq = _watcher(store, _srt(tmp_path))

    async def slow_check(entry_, srt_path, identity):
        await asyncio.sleep(3600)

    monkeypatch.setattr(w, "_run_language_check", slow_check)
    entry = _Entry()
    w._schedule_language_check(entry, entry.canonical_path)
    w._schedule_language_check(entry, entry.canonical_path)
    w._schedule_language_check(entry, entry.canonical_path)
    assert len(w._lang_check_tasks) == 1  # one in-flight check per identity
    for t in w._lang_check_tasks.values():
        t.cancel()


@pytest.mark.asyncio
async def test_no_veto_scan_still_fires_queue_untouched(tmp_path, monkeypatch):
    # WARN outcome AND upload failure -> scan-disk still fires; no veto, no
    # requeue, no queue-count change from the checker.
    store = _store(tmp_path)
    w, events, scanned, enqueued = _watcher(store, _srt(tmp_path), upload=False)
    entry = _Entry()

    async def warn_check(entry_, srt_path, identity):
        w._record_lang_check(entry_, _FakeResult("WARN", reason="source_target_mismatch"))

    monkeypatch.setattr(w, "_run_language_check", warn_check)
    await _complete(w, entry)
    assert scanned == [(entry.id, entry.series_id, entry.canonical_path)]  # scan fired
    assert enqueued == []  # no requeue / queue-count change
    assert [e[0] for e in events] == ["mark", "retime", "forced", "upload", "plex"]


def test_no_language_gate_or_validate_language_token():
    # The repo previously deleted a validate_language gate. Phase 5 must not
    # reintroduce any language gate / veto knob. test_subtitle_tuning_router.py
    # separately guards the API surface; this guards completion_watcher.
    import subarr.completion_watcher as cw

    src = Path(cw.__file__).read_text(encoding="utf-8")
    for tok in ("language_gate", "_GATE_KEY", "validate_language"):
        assert tok not in src, f"#451 must not reintroduce {tok!r} (gate was deleted)"


# ─────────────────────────────────────────────────────────────────────────────
# preview / history rendering: GET /api/aftercare/results exposes the bounded
# structured result (status / reason / provenance) on the aftercare row.
# ─────────────────────────────────────────────────────────────────────────────


def _app_with_result(tmp_path):
    """Seed a store with one aftercare row, attach one bounded text-LID result,
    and return a TestClient exposing the aftercare router."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from subarr.aftercare import AftercareEvaluation
    from subarr.routers import aftercare as r

    db = tmp_path / "a.db"
    from subarr.migrate import run_migrations

    run_migrations(db)
    store = AfterCareStore(db)
    store.record(
        canonical_path="TV/A/e1.mkv",
        completed_at=1.0,
        evaluation=AftercareEvaluation(40.0, 10, True, {"issues": []}, {"canned_phrase_hits": 1}),
        source="subgenscan",
    )
    store.set_text_lang_check(
        "TV/A/e1.mkv",
        {
            "status": "WARN",
            "reason": "source_target_mismatch",
            "languages": ["en", "de"],
            "evidence": {},
            "provenance": {"origin": "webhook", "conflict": 1},
            "checker_version": "1.0.0",
            "policy_version": "pr451-v1",
            "probabilities": {},
        },
    )
    app = FastAPI()
    app.state.aftercare = store
    app.include_router(r.router)
    return TestClient(app)


def test_preview_history_exposes_bounded_result(tmp_path):
    client = _app_with_result(tmp_path)
    body = client.get("/api/aftercare/results?view=all").json()
    assert body["count"] == 1
    check = body["items"][0]["text_lang_check"]
    assert check is not None
    assert check["status"] == "WARN"
    assert check["reason"] == "source_target_mismatch"
    assert check["provenance"]["origin"] == "webhook"
    assert check["provenance"]["conflict"] == 1
    # The bounded result never carries full subtitle text.
    assert "text" not in check and "content" not in check


def test_preview_history_null_when_no_check(tmp_path):
    # A row with no checker result (legacy / in-flight / fail-soft) stays null.
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from subarr.aftercare import AftercareEvaluation
    from subarr.migrate import run_migrations
    from subarr.routers import aftercare as r

    db = tmp_path / "a.db"
    run_migrations(db)
    store = AfterCareStore(db)
    store.record(
        canonical_path="TV/A/e2.mkv",
        completed_at=2.0,
        evaluation=AftercareEvaluation(90.0, 10, False, {"issues": []}, {}),
        source="subgenscan",
    )
    app = FastAPI()
    app.state.aftercare = store
    app.include_router(r.router)
    body = TestClient(app).get("/api/aftercare/results?view=all").json()
    assert body["items"][0]["text_lang_check"] is None
