"""Tests for GET /api/queue (subgen v4.2 proxy)."""

from __future__ import annotations

import httpx
import pytest


def test_queue_response_includes_arena_active(app_with_stub):
    """GET /api/queue must include arena_active reflecting in-flight sweep count."""
    # Monkey-patch the arena on app state to report one in-flight sweep.
    app_with_stub.app.state.arena.inflight_count = lambda: 1
    r = app_with_stub.get("/api/queue")
    assert r.status_code == 200
    assert r.json()["arena_active"] == 1


def test_queue_arena_active_zero_when_idle(app_with_stub):
    """arena_active defaults to 0 when no sweeps are running."""
    app_with_stub.app.state.arena.inflight_count = lambda: 0
    r = app_with_stub.get("/api/queue")
    assert r.status_code == 200
    assert r.json()["arena_active"] == 0


def test_queue_idle_default(app_with_stub):
    r = app_with_stub.get("/api/queue")
    assert r.status_code == 200
    body = r.json()
    assert body["idle"] is True
    assert body["queued"] == []
    assert body["processing"] == []
    assert body["queued_count"] == 0


def _busy_handler(req: httpx.Request) -> httpx.Response:
    if req.url.path == "/queue":
        return httpx.Response(
            200,
            json={
                "queued": [{"path": "/media/library/TV/A/file2.mkv", "type": "transcribe"}],
                "processing": [{"path": "/media/library/TV/A/file1.mkv", "type": "transcribe"}],
                "queued_count": 1,
                "processing_count": 1,
                "idle": False,
                "version": "2026.05.3",
            },
        )
    return httpx.Response(404)


@pytest.mark.subgen(handler=_busy_handler)
def test_queue_busy(app_with_stub):
    r = app_with_stub.get("/api/queue")
    assert r.status_code == 200
    body = r.json()
    assert body["idle"] is False
    assert body["queued_count"] == 1
    assert body["processing"][0]["type"] == "transcribe"


def test_queue_idle_skips_docker_progress_read(app_with_stub):
    """Perf (dogfood 2026-06-20): with nothing processing, /api/queue must NOT
    do the slow docker logs read (recent_progress) — there's no progress to
    merge into an empty list, and the read was costing ~2s per poll when idle."""
    calls = []

    async def _spy(*a, **k):
        calls.append((a, k))
        return {}

    app_with_stub.app.state.docker.recent_progress = _spy
    r = app_with_stub.get("/api/queue")
    assert r.status_code == 200
    assert r.json()["processing"] == []
    assert calls == []  # docker logs read skipped when idle


@pytest.mark.subgen(handler=_busy_handler)
def test_queue_busy_still_reads_progress(app_with_stub):
    """When something IS processing, recent_progress is fetched to merge live %."""
    calls = []

    async def _spy(*a, **k):
        calls.append((a, k))
        return {}

    app_with_stub.app.state.docker.recent_progress = _spy
    r = app_with_stub.get("/api/queue")
    assert r.status_code == 200
    assert len(calls) == 1  # fetched once because a job is processing


def _arena_mixed_handler(req: httpx.Request) -> httpx.Response:
    # subgen /queue with one real library job (/media/...) and one tuning-lab
    # arena sweep, which runs via /asr UPLOAD mode → a subgen temp upload path
    # (NOT under the media prefix). The arena job must NOT count toward the main
    # queue or appear in its lists.
    if req.url.path == "/queue":
        return httpx.Response(
            200,
            json={
                "queued": [],
                "processing": [
                    {"path": "/media/library/TV/A/file1.mkv", "type": "transcribe"},
                    {"path": "/tmp/subgen-upload-abc123.wav", "type": "transcribe"},
                ],
                "queued_count": 0,
                "processing_count": 2,
                "idle": False,
                "version": "2026.05.3",
            },
        )
    return httpx.Response(404)


@pytest.mark.subgen(handler=_arena_mixed_handler)
def test_queue_excludes_arena_asr_upload_jobs(app_with_stub):
    body = app_with_stub.get("/api/queue").json()
    # Only the /media library job survives — the /tmp arena upload is dropped
    # from both the list and the count (count must match what the page renders).
    assert body["processing_count"] == 1
    paths = [t["path"] for t in body["processing"]]
    assert paths == ["/media/library/TV/A/file1.mkv"]
    assert not any("subgen-upload" in p for p in paths)


def _one_queued_handler(req: httpx.Request) -> httpx.Response:
    if req.url.path == "/queue":
        return httpx.Response(
            200,
            json={
                "queued": [{"path": "/media/library/TV/Dup/dupfile.mkv", "type": "transcribe"}],
                "processing": [],
                "queued_count": 1,
                "processing_count": 0,
                "idle": False,
                "version": "2026.05.3",
            },
        )
    return httpx.Response(404)


@pytest.mark.subgen(handler=_one_queued_handler)
def test_inflight_queued_path_not_duplicated_in_history(app_with_stub):
    """A just-submitted file is live in subgen's queue AND has an OK
    ('subgen queued 1') scan_store row. It must appear ONLY in the live
    queued section, not also in history/Recently-done. Regression for the
    double-listing where 40+ queued files showed in both places."""
    from subarr.scan_store import PATH_STATUS_OK

    c = app_with_stub
    store = c.app.state.scans
    scan = store.create(["TV/Dup/dupfile.mkv"], reverse=False)
    scan.results[0].status = PATH_STATUS_OK
    scan.results[0].subgen_body = {"walked": 1, "queued": 1}
    store.save(scan)

    body = c.get("/api/queue").json()
    assert any("dupfile.mkv" in t["path"] for t in body["queued"])
    assert not any("dupfile.mkv" in h["path"] for h in body["history"]), (
        "in-flight queued path must not also appear in history"
    )


def _down_handler(req: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("subgen unreachable", request=req)


@pytest.mark.subgen(handler=_down_handler)
def test_queue_history_only_when_subgen_down(app_with_stub):
    # v1.1.1 Featured Queue: when subgen is unreachable we no longer 503.
    # The queue endpoint serves a history-only view so the user can still
    # inspect/requeue/clean up past submissions while subgen is down.
    # The subgen failure is surfaced via the `subgen_error` field.
    r = app_with_stub.get("/api/queue")
    assert r.status_code == 200
    body = r.json()
    assert body["processing"] == []
    assert body["queued"] == []
    assert "subgen_error" in body
    assert "subgen" in body["subgen_error"].lower()
    assert "history" in body
    assert "history_counts" in body


# ── #queue-cancel: subgen-space path must pass through verbatim ──────────
# Regression: the cancel endpoint used to strip("/") + canonicalise the
# path as if it were subarr-relative. But /api/queue returns subgen's OWN
# path space (e.g. /media/...), and subgen matches its queue by exact path
# — stripping the leading slash made every cancel fail "not in queue".

_CANCEL_SEEN = {}


def _cancel_handler(req):
    if req.url.path == "/queue/cancel":
        _CANCEL_SEEN["path"] = req.url.params.get("path")
        return httpx.Response(200, json={"cancelled": True, "path": _CANCEL_SEEN["path"]})
    # keep boot + GET /queue happy
    return httpx.Response(200, json={"active": None, "queue": []})


@pytest.mark.subgen(handler=_cancel_handler)
def test_cancel_passes_subgen_path_verbatim(app_with_stub):
    import dataclasses
    from subarr.subgen_client import SubgenCapabilities

    c = app_with_stub
    base = c.app.state.subgen_caps or SubgenCapabilities()
    c.app.state.subgen_caps = dataclasses.replace(base, queue_cancel=True)
    _CANCEL_SEEN.clear()

    full = "/media/TV/Innato/Season 1/Innate - S01E06 - x.mkv"
    r = c.post("/api/queue/cancel", json={"path": full})
    assert r.status_code == 200, r.text
    assert r.json().get("cancelled") is True
    # The exact subgen path (leading slash + all) must reach subgen.
    assert _CANCEL_SEEN["path"] == full


def test_queue_history_is_cached_within_ttl(app_with_stub, monkeypatch):
    """#336: history is stale-while-revalidate. The first /api/queue builds it
    (cold cache); a second call within the TTL serves the cached view without
    rebuilding (the NAS-walking build is what made the page slow)."""
    import subarr.routers.queue as q

    calls = {"n": 0}
    orig = q._build_history_view

    def counting(scans, live_paths, completed_paths=None):
        calls["n"] += 1
        return orig(scans, live_paths, completed_paths)

    monkeypatch.setattr(q, "_build_history_view", counting)
    assert app_with_stub.get("/api/queue").status_code == 200
    assert app_with_stub.get("/api/queue").status_code == 200
    assert calls["n"] == 1  # built once; second call served the cache


def test_empty_status_surfaces_as_file_removed():
    """#336: subgen 404 / walked==0 (file gone from disk) must NOT vanish into
    the hidden 'pending' fallback — it surfaces as a visible 'file removed' skip."""
    import subarr.routers.queue as q
    from subarr.scan_store import PATH_STATUS_EMPTY

    chip = q._path_outcome_chip(PATH_STATUS_EMPTY, None, None)
    assert chip["category"] == "skipped"
    assert chip["label"] == "file removed"
    assert "no longer on disk" in chip["detail"]
    # benign skip_reason routes it to Recently-done, not the Issues bucket
    assert chip["skip_reason"] == "file_removed"


def test_ok_relabels_to_completed_when_transcription_done():
    """#346: an OK scan row whose canonical path is in completed_paths (subgen
    finished it) shows 'completed', not the stale hand-off 'queued' label."""
    import subarr.routers.queue as q
    from subarr.scan_store import PATH_STATUS_OK

    path = "TV/Show/Season 1/Show - S01E01.mkv"
    # not yet completed → still 'queued'
    pending = q._path_outcome_chip(
        PATH_STATUS_OK, {"queued": 1}, None, canonical_path=path, completed_paths=set()
    )
    assert pending["label"] == "queued"
    # completion recorded → 'completed'
    done = q._path_outcome_chip(
        PATH_STATUS_OK, {"queued": 1}, None, canonical_path=path, completed_paths={path}
    )
    assert done["label"] == "completed"
    assert done["category"] == "ok"


def test_cancelled_scan_is_benign_interrupted():
    """#351 follow-up: a scan cancelled by an app restart is NOT a subgen
    failure — it surfaces as a benign 'interrupted' skip (Recently-done), not the
    'silent fails' Issues bucket. A real error still shows as failed."""
    import subarr.routers.queue as q
    from subarr.scan_store import PATH_STATUS_ERROR

    chip = q._path_outcome_chip(PATH_STATUS_ERROR, None, "cancelled")
    assert chip["category"] == "skipped"
    assert chip["label"] == "interrupted"
    assert chip["skip_reason"] == "interrupted"
    err = q._path_outcome_chip(PATH_STATUS_ERROR, None, "subgen exploded")
    assert err["category"] == "error" and err["label"] == "failed"
