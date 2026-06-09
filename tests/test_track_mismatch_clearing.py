"""#170: track-mismatch rows must be clearable.

- pending-review honors a dismiss at READ time (build_coverage only bakes it
  out when it regenerates the snapshot; this endpoint reads the cached snapshot,
  so without the read-time filter a dismissed row reappears until the next walk —
  the bug Judd hit).
- bulk-dismiss endpoint clears many at once (the per-row chore was the pain).
"""

from __future__ import annotations


class _SnapStub:
    def __init__(self, items):
        self.items = items


class _SnapCache:
    def __init__(self, items):
        self._snap = _SnapStub(items)

    def request_refresh(self, *a, **k):
        pass

    def get_cached(self):
        return self._snap


_PATH = "TV/Show/Season 1/ep.mkv"
_TM_ITEM = {
    "file_canonical_path": _PATH,
    "canonical_path": _PATH,
    "default_track_mismatch": True,
    "mismatch_default_track_lang": "eng",
    "mismatch_native_track_lang": "swe",
    "mismatch_native_audio_ordinal": 1,
    "title": "Show",
}
PENDING = "/api/audio-lang/pending-review"
BULK = "/api/audio-lang/track-mismatch-dismiss-bulk"


def _has_tm(items, path):
    return any(it["flag"] == "track_mismatch" and it.get("canonical_path") == path for it in items)


def test_pending_review_honors_dismiss_live(app_with_stub):
    app = app_with_stub.app
    app.state.coverage_cache = _SnapCache([dict(_TM_ITEM)])
    # surfaces as a track-mismatch row from the cached snapshot
    assert _has_tm(app_with_stub.get(PENDING).json()["items"], _PATH)
    # dismiss it — the read-time fix means it vanishes WITHOUT a coverage rebuild
    app.state.audio_lang.dismiss_track_mismatch(_PATH)
    assert not _has_tm(app_with_stub.get(PENDING).json()["items"], _PATH)


def test_bulk_dismiss_clears_many(app_with_stub):
    app = app_with_stub.app
    paths = ["TV/A/e1.mkv", "TV/A/e2.mkv", "TV/B/e1.mkv"]
    r = app_with_stub.post(BULK, json={"file_canonical_paths": paths})
    assert r.status_code == 200
    assert r.json()["dismissed"] == 3
    assert set(paths) <= app.state.audio_lang.get_track_mismatch_dismissed_set()


def test_bulk_dismiss_ignores_empty_paths(app_with_stub):
    r = app_with_stub.post(BULK, json={"file_canonical_paths": ["", "TV/x.mkv"]})
    assert r.status_code == 200
    assert r.json()["dismissed"] == 1
