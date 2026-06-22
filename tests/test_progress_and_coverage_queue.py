"""Tests for the v1.1 batch 2 additions:

- docker_client._PROGRESS_RE parses subgen's progress log lines.
- /api/queue merges per-processing-task progress when docker_ops reports any.
- POST /api/coverage/queue resolves a sonarr_episode_id to a single .mkv
  file path via sonarr.episode + sonarr.episode_file, NOT the series dir.
"""

from __future__ import annotations

import httpx
import pytest


# ───── progress regex ───────────────────────────────────────────────────────


def test_progress_regex_parses_subgen_lines():
    from subarr.docker_client import _PROGRESS_RE

    line = (
        "INFO:root:[ Cette nuit-là - S01E02 - TBA WEBDL-72.. ]  78% "
        "| 2040/2610 s [06:43<01:52,  5.06s/s] | Jobs: 1 processing, 0 queued"
    )
    m = _PROGRESS_RE.search(line)
    assert m, "expected progress regex to match"
    d = m.groupdict()
    assert d["name"] == "Cette nuit-là - S01E02 - TBA WEBDL-72"
    assert d["pct"] == "78"
    assert d["cur"] == "2040"
    assert d["tot"] == "2610"
    assert d["elapsed"] == "06:43"
    assert d["eta"] == "01:52"
    assert d["speed"] == "5.06"


def test_progress_regex_short_filename_no_truncation():
    from subarr.docker_client import _PROGRESS_RE

    line = "INFO:root:[ Foo - S01E01.mkv ]  5% | 100/2000 s [00:20<06:30,  4.94s/s] | Jobs:"
    m = _PROGRESS_RE.search(line)
    assert m
    assert m.groupdict()["name"] == "Foo - S01E01.mkv"


# ───── /api/queue with progress merge ────────────────────────────────────


def _subgen_busy(req: httpx.Request) -> httpx.Response:
    if req.url.path == "/queue":
        return httpx.Response(
            200,
            json={
                "queued": [],
                "processing": [
                    {
                        "path": "/media/TV/Cette nuit-là/Season 1/Cette nuit-là - S01E02 - TBA WEBDL-720p.mkv",
                        "type": "transcribe",
                    }
                ],
                "queued_count": 0,
                "processing_count": 1,
                "idle": False,
                "version": "2026.05.3",
            },
        )
    return httpx.Response(404)


@pytest.mark.subgen(handler=_subgen_busy)
@pytest.mark.docker_stub(
    progress_map={
        "Cette nuit-là - S01E02 - TBA WEBDL-72": {
            "pct": 78,
            "cur_s": 2040.0,
            "tot_s": 2610.0,
            "elapsed": "06:43",
            "eta": "01:52",
            "speed_s_per_s": 5.06,
        },
    }
)
def test_queue_merges_progress_into_processing_rows(app_with_stub):
    r = app_with_stub.get("/api/queue")
    assert r.status_code == 200
    body = r.json()
    proc = body["processing"][0]
    assert "progress" in proc, f"expected progress merged, got {proc}"
    assert proc["progress"]["pct"] == 78
    assert proc["progress"]["elapsed"] == "06:43"


@pytest.mark.subgen(handler=_subgen_busy)
def test_queue_no_progress_when_docker_silent(app_with_stub):
    r = app_with_stub.get("/api/queue")
    proc = r.json()["processing"][0]
    assert "progress" not in proc


# ───── POST /api/coverage/queue resolves to single file ─────────────────────


def _sonarr_resolver_handler(req: httpx.Request) -> httpx.Response:
    path = req.url.path
    if path == "/api/v3/system/status":
        return httpx.Response(200, json={"version": "4.0.17.2967"})
    if path == "/api/v3/episode/9001":
        return httpx.Response(
            200,
            json={
                "id": 9001,
                "seriesId": 42,
                "seasonNumber": 1,
                "episodeNumber": 3,
                "title": "Pilot",
                "hasFile": True,
                "episodeFileId": 4242,
            },
        )
    if path == "/api/v3/episodefile/4242":
        return httpx.Response(
            200,
            json={
                "id": 4242,
                "seriesId": 42,
                "path": "/data/Media/TV/Foreign Drama/Season 1/Foreign.Drama.S01E03.mkv",
            },
        )
    return httpx.Response(404)


@pytest.fixture
def coverage_queue_media_root():
    """Plant the resolved file on disk so canonical_to_fs passes the
    target.exists() check."""
    from subarr.config import settings

    folder = settings.media_root / "TV" / "Foreign Drama" / "Season 1"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "Foreign.Drama.S01E03.mkv").write_bytes(b"")
    yield


@pytest.mark.integrations_stub(sonarr_handler=_sonarr_resolver_handler)
def test_coverage_queue_resolves_episode_to_file(app_with_stub, coverage_queue_media_root):
    r = app_with_stub.post("/api/coverage/queue", json={"sonarr_episode_id": 9001})
    assert r.status_code == 202, r.json()
    body = r.json()
    # The resolved canonical is the SINGLE file, not the series dir.
    assert body["canonical_path"] == "TV/Foreign Drama/Season 1/Foreign.Drama.S01E03.mkv"
    assert body["is_file"] is True
    assert body["resolved_via"] == "sonarr_episode_id=9001"


def _sonarr_no_file_handler(req: httpx.Request) -> httpx.Response:
    if req.url.path == "/api/v3/episode/777":
        return httpx.Response(200, json={"id": 777, "hasFile": False, "episodeFileId": 0})
    return _sonarr_resolver_handler(req)


@pytest.mark.integrations_stub(sonarr_handler=_sonarr_no_file_handler)
def test_coverage_queue_404_when_episode_has_no_file(app_with_stub):
    r = app_with_stub.post("/api/coverage/queue", json={"sonarr_episode_id": 777})
    assert r.status_code == 404
    assert "no episodeFileId" in r.json()["detail"]


def test_coverage_queue_falls_back_to_canonical_path(app_with_stub, coverage_queue_media_root):
    """Movies: Bazarr's wanted payload doesn't expose a per-file id, so the
    button posts canonical_path directly and we skip Sonarr resolution."""
    r = app_with_stub.post(
        "/api/coverage/queue",
        json={
            "canonical_path": "TV/Foreign Drama/Season 1/Foreign.Drama.S01E03.mkv",
        },
    )
    assert r.status_code == 202
    assert r.json()["resolved_via"] == "canonical_path"


def test_coverage_queue_400_when_nothing_supplied(app_with_stub):
    r = app_with_stub.post("/api/coverage/queue", json={})
    assert r.status_code == 400


# ─── v1.1.1 #224: audio_language_override pre-flight skip-language check ────


_BATCH_PARAMS_CAPTURE: list[dict[str, str]] = []


def _capturing_subgen_handler(req: httpx.Request) -> httpx.Response:
    """Subgen stub that records every /batch call's query params so the test
    can assert audio_language_override was (or wasn't) forwarded."""
    if req.url.path == "/status":
        return httpx.Response(
            200,
            json={
                "version": "Subgen 2026.05.3, stable-ts 0.7.0, faster-whisper 1.0.3 (test)",
            },
        )
    if req.url.path == "/queue":
        # Advertise the v4.3 capability so subgen_client surfaces it.
        return httpx.Response(
            200,
            json={
                "queued": [],
                "processing": [],
                "queued_count": 0,
                "processing_count": 0,
                "idle": True,
                "version": "test",
                "subarr_subgen_patch_rev": "v4.3",
                "capabilities": {"audio_language_override": True},
            },
        )
    if req.url.path == "/batch":
        _BATCH_PARAMS_CAPTURE.append(dict(req.url.params))
        return httpx.Response(
            200,
            json={
                "walked": 1,
                "queued": 1,
                "skipped": 0,
                "already_in_queue": 0,
                "no_audio": 0,
                "pending_language_detect": 0,
                "path": req.url.params.get("directory"),
                "reverse": False,
            },
        )
    return httpx.Response(404, json={"detail": "stub: unhandled"})


@pytest.mark.subgen(handler=_capturing_subgen_handler)
def test_coverage_queue_forwards_audio_language_override_when_verified(
    app_with_stub,
    coverage_queue_media_root,
):
    """When the user has verified the file's audio is non-English via the
    review queue, coverage/queue must forward audio_language_override=<lang>
    to subgen so SKIP_IF_AUDIO_LANGUAGES=eng doesn't silently drop it."""
    _BATCH_PARAMS_CAPTURE.clear()
    canonical = "TV/Foreign Drama/Season 1/Foreign.Drama.S01E03.mkv"
    # Plant the verification — non-English, so the override should fire.
    app_with_stub.app.state.audio_lang.upsert(
        canonical_path=canonical,
        lang_code="fre",
        source="manual_review",
        confidence=1.0,
        verified_by="test",
    )

    r = app_with_stub.post("/api/coverage/queue", json={"canonical_path": canonical})
    assert r.status_code == 202, r.json()

    # #66/#116 s6: coverage/queue routes through the pending queue. The resolved
    # override rides on the pending job; the feeder forwards it to subgen at
    # drain time. So assert it's stored on the job, not POSTed immediately.
    jobs = [j for j in app_with_stub.app.state.pending_queue.list() if j.canonical_path == canonical]
    assert jobs, "coverage/queue did not enqueue a pending job"
    assert jobs[0].audio_language_override == "fre"


@pytest.mark.subgen(handler=_capturing_subgen_handler)
def test_coverage_queue_omits_override_for_english_verifications(
    app_with_stub,
    coverage_queue_media_root,
):
    """English-verified files don't need the bypass — SKIP_IF_AUDIO_LANGUAGES=eng
    would correctly skip them anyway. Override stays absent."""
    _BATCH_PARAMS_CAPTURE.clear()
    canonical = "TV/Foreign Drama/Season 1/Foreign.Drama.S01E03.mkv"
    app_with_stub.app.state.audio_lang.upsert(
        canonical_path=canonical,
        lang_code="eng",
        source="manual_review",
        confidence=1.0,
        verified_by="test",
    )

    r = app_with_stub.post("/api/coverage/queue", json={"canonical_path": canonical})
    assert r.status_code == 202

    jobs = [j for j in app_with_stub.app.state.pending_queue.list() if j.canonical_path == canonical]
    assert jobs, "coverage/queue did not enqueue a pending job"
    assert jobs[0].audio_language_override is None


@pytest.mark.subgen(handler=_capturing_subgen_handler)
def test_coverage_queue_omits_override_when_no_verification(
    app_with_stub,
    coverage_queue_media_root,
):
    """No audio_lang_store entry → no override forwarded. Pre-flight is opt-in
    only for confirmed user verifications, not heuristic guesses."""
    _BATCH_PARAMS_CAPTURE.clear()
    canonical = "TV/Foreign Drama/Season 1/Foreign.Drama.S01E03.mkv"

    r = app_with_stub.post("/api/coverage/queue", json={"canonical_path": canonical})
    assert r.status_code == 202

    jobs = [j for j in app_with_stub.app.state.pending_queue.list() if j.canonical_path == canonical]
    assert jobs, "coverage/queue did not enqueue a pending job"
    assert jobs[0].audio_language_override is None


# ─── #317 Slice B: transcribe-a-full-sub-anyway (ignore_forced) ────────────


@pytest.mark.subgen(handler=_capturing_subgen_handler)
def test_coverage_queue_carries_ignore_forced_when_requested(
    app_with_stub,
    coverage_queue_media_root,
):
    """The 'transcribe a full sub anyway' action sends ignore_forced=true; it
    must ride on the pending job so the feeder forwards ?ignore_forced=true to
    subgen's /batch (subgen otherwise skips a forced-only file)."""
    canonical = "TV/Foreign Drama/Season 1/Foreign.Drama.S01E03.mkv"

    r = app_with_stub.post(
        "/api/coverage/queue",
        json={"canonical_path": canonical, "ignore_forced": True},
    )
    assert r.status_code == 202, r.json()

    jobs = [j for j in app_with_stub.app.state.pending_queue.list() if j.canonical_path == canonical]
    assert jobs, "coverage/queue did not enqueue a pending job"
    assert jobs[0].ignore_forced is True


@pytest.mark.subgen(handler=_capturing_subgen_handler)
def test_coverage_queue_ignore_forced_defaults_false(
    app_with_stub,
    coverage_queue_media_root,
):
    """A normal gap queue (no ignore_forced) leaves the job's flag False so the
    file goes through subgen's standard skip logic."""
    canonical = "TV/Foreign Drama/Season 1/Foreign.Drama.S01E03.mkv"

    r = app_with_stub.post("/api/coverage/queue", json={"canonical_path": canonical})
    assert r.status_code == 202

    jobs = [j for j in app_with_stub.app.state.pending_queue.list() if j.canonical_path == canonical]
    assert jobs, "coverage/queue did not enqueue a pending job"
    assert jobs[0].ignore_forced is False
