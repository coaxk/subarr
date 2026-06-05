"""#90 slice 3 — POST /api/audio-lang/detect (on-demand Whisper language verify)."""
from __future__ import annotations

import httpx
import pytest


def _detect_stub(req: httpx.Request) -> httpx.Response:
    if req.url.path == "/status":
        return httpx.Response(200, json={"version": "Subgen 2026.05.3 (test)"})
    if req.url.path == "/queue":
        return httpx.Response(200, json={
            "queued": [], "processing": [],
            "capabilities": {"robust_language_detection": True},
        })
    if req.url.path == "/detect_language_robust":
        return httpx.Response(200, json={
            "aggregate": {"language": "ko", "n_agreeing": 3, "n_total": 3, "min_probability": 0.9},
        })
    return httpx.Response(404, json={"detail": "stub: unhandled"})


@pytest.mark.subgen(handler=_detect_stub)
def test_detect_stores_whisper_language(app_with_stub):
    r = app_with_stub.post("/api/audio-lang/detect", json={"canonical_path": "TV/Show/ep.mkv"})
    assert r.status_code == 200
    body = r.json()
    assert body["detected"] is True and body["lang_code"] == "ko"
    # persisted as a whisper-sourced verification
    listed = app_with_stub.get("/api/audio-lang/verifications").json()["verifications"]
    row = next((v for v in listed if v["canonical_path"] == "TV/Show/ep.mkv"), None)
    assert row is not None and row["source"] == "whisper" and row["lang_code"] == "ko"


def test_detect_blocked_without_robust_capability(app_with_stub):
    # default stub /queue advertises no robust_language_detection → 503
    r = app_with_stub.post("/api/audio-lang/detect", json={"canonical_path": "x.mkv"})
    assert r.status_code == 503
