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

    def clear_track_mismatch_for(self, file_canonical_path):
        n = 0
        for it in self._snap.items:
            if it.get("file_canonical_path") == file_canonical_path and it.get("default_track_mismatch"):
                it["default_track_mismatch"] = False
                n += 1
        return n


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


# ── #310 bulk swap ───────────────────────────────────────────────────────────

SWAP_BULK = "/api/audio-lang/track-mismatch-swap-bulk"


class _CountingCache(_SnapCache):
    def __init__(self, items):
        super().__init__(items)
        self.refreshes = 0

    def request_refresh(self, *a, **k):
        self.refreshes += 1


class _FakeProbe:
    def __init__(self):
        self.audio = [object(), object()]  # 2 audio tracks
        self.canonical_path = None


class _FakeMismatch:
    native_audio_ordinal = 1
    native_lang = "swe"
    default_lang = "eng"


def _wire_swap_mocks(monkeypatch, *, mismatch=_FakeMismatch()):
    """Stub the heavy swap deps (probe + mkvpropedit + fs resolve) so the bulk
    endpoint is exercised without touching real media."""
    import subarr.media_probe as mp
    import subarr.track_swap as ts
    from subarr.routers import audio_lang as al

    async def fake_probe(_path):
        return _FakeProbe()

    async def fake_swap(_fs, _ordinal, _ordinals):
        return None

    monkeypatch.setattr(mp, "probe", fake_probe)
    monkeypatch.setattr(mp, "detect_default_track_mismatch", lambda _audio, _lang: mismatch)
    monkeypatch.setattr(ts, "swap_default_audio_track", fake_swap)
    monkeypatch.setattr(al, "_resolve_canonical_to_fs", lambda c: "/fake/" + c)


def _tm_items(*paths):
    return [
        {**_TM_ITEM, "file_canonical_path": p, "canonical_path": p, "original_language": "swe"} for p in paths
    ]


def test_bulk_swap_swaps_many_with_one_refresh(app_with_stub, monkeypatch):
    """#310: swapping a show's worth of track-mismatches must loop server-side
    and refresh the coverage cache ONCE — not once per heavy per-file swap."""
    p1, p2 = "TV/Show/Season 1/a.mkv", "TV/Show/Season 1/b.mkv"
    cache = _CountingCache(_tm_items(p1, p2))
    app_with_stub.app.state.coverage_cache = cache
    _wire_swap_mocks(monkeypatch)

    r = app_with_stub.post(SWAP_BULK, json={"file_canonical_paths": [p1, p2]})
    assert r.status_code == 200
    assert r.json()["swapped"] == 2
    assert cache.refreshes == 1  # ONE batch refresh, not per-file


def test_bulk_swap_records_per_file_failures_and_continues(app_with_stub, monkeypatch):
    """#310: a non-.mkv (or unflagged) file in the batch is recorded as failed
    but does not abort the rest."""
    good, bad = "TV/Show/Season 1/a.mkv", "TV/Show/Season 1/note.txt"
    cache = _CountingCache(_tm_items(good))  # only the .mkv is a flagged row
    app_with_stub.app.state.coverage_cache = cache
    _wire_swap_mocks(monkeypatch)

    r = app_with_stub.post(SWAP_BULK, json={"file_canonical_paths": [good, bad]})
    assert r.status_code == 200
    body = r.json()
    assert body["swapped"] == 1
    assert len(body["failed"]) == 1
    assert body["failed"][0]["file_canonical_path"] == bad
