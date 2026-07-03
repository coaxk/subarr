"""#406: pending-review surfaces AUTO-classified multilingual rows (store
source == 'auto-high-conf-multi') as flag='multilingual' with the lang_codes
set, so they are visible + correctable. User-confirmed multi (source=='user')
is settled and must NOT re-enter the lane.

Sync TestClient tests mirroring tests/test_track_mismatch_clearing.py — the
pending-review endpoint reads app.state.coverage_cache's cached snapshot, so we
seed a stub snapshot and seed the real AudioLangStore with .upsert().
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


PENDING = "/api/audio-lang/pending-review"

_AUTO_PATH = "Movies/TheBeasts.mkv"
_USER_PATH = "Movies/Roma.mkv"
_SUSPECT_PATH = "TV/Show/Season 1/ep.mkv"


def _multi_item(path):
    # Mirrors a real snapshot row for a multilingual verdict: audio_source is
    # 'multilingual' and suspect is suppressed for BOTH auto and user rows —
    # which is exactly why the endpoint must key on the STORE source, not this.
    return {
        "file_canonical_path": path,
        "canonical_path": path,
        "title": path.split("/")[-1],
        "audio_source": "multilingual",
        "audio_label_suspect": False,
        "audio_label_unknown": False,
        "audio_langs": ["gl", "es"],
    }


def _suspect_item(path):
    return {
        "file_canonical_path": path,
        "canonical_path": path,
        "title": "Show",
        "audio_label_suspect": True,
    }


def test_auto_multilingual_surfaces_with_lang_codes(app_with_stub):
    app = app_with_stub.app
    store = app.state.audio_lang
    # AUTO multilingual verdict — store source is auto-high-conf-multi.
    store.upsert(
        canonical_path=_AUTO_PATH,
        lang_code="gl",
        source="auto-high-conf-multi",
        lang_class="multi",
        lang_codes=["gl", "es"],
    )
    app.state.coverage_cache = _SnapCache([_multi_item(_AUTO_PATH)])

    items = app_with_stub.get(PENDING).json()["items"]
    row = next((it for it in items if it.get("canonical_path") == _AUTO_PATH), None)
    assert row is not None, "auto-multi row must appear in the lane"
    assert row["flag"] == "multilingual"
    assert row["lang_codes"] == ["gl", "es"]


def test_user_confirmed_multilingual_is_not_surfaced(app_with_stub):
    app = app_with_stub.app
    store = app.state.audio_lang
    # USER-confirmed multilingual — settled, must never re-enter the lane.
    store.upsert(
        canonical_path=_USER_PATH,
        lang_code="es",
        source="user",
        lang_class="multi",
        lang_codes=["es", "en"],
    )
    app.state.coverage_cache = _SnapCache([_multi_item(_USER_PATH)])

    items = app_with_stub.get(PENDING).json()["items"]
    assert not any(it.get("canonical_path") == _USER_PATH for it in items)


def test_suspect_row_unaffected(app_with_stub):
    app = app_with_stub.app
    app.state.coverage_cache = _SnapCache([_suspect_item(_SUSPECT_PATH)])
    items = app_with_stub.get(PENDING).json()["items"]
    row = next((it for it in items if it.get("canonical_path") == _SUSPECT_PATH), None)
    assert row is not None
    assert row["flag"] == "suspect"


def test_track_mismatch_wins_over_auto_multilingual(app_with_stub):
    # A row that is BOTH an auto-multi verdict AND a default-track mismatch must
    # surface as track_mismatch (FIRST precedence), not multilingual — the two
    # are orthogonal and track-mismatch needs the swap/dismiss action.
    app = app_with_stub.app
    app.state.audio_lang.upsert(
        canonical_path=_AUTO_PATH,
        lang_code="gl",
        source="auto-high-conf-multi",
        lang_class="multi",
        lang_codes=["gl", "es"],
    )
    item = _multi_item(_AUTO_PATH)
    item["default_track_mismatch"] = True
    app.state.coverage_cache = _SnapCache([item])

    items = app_with_stub.get(PENDING).json()["items"]
    row = next((it for it in items if it.get("canonical_path") == _AUTO_PATH), None)
    assert row is not None
    assert row["flag"] == "track_mismatch"


def test_auto_multilingual_bazarr_synthetic_none_file_path(app_with_stub):
    # Bazarr-synthetic / series-level rows have file_canonical_path=None and are
    # stored under canonical_path — the branch must fall back to the canonical key
    # for BOTH the source check and the lang_codes lookup.
    app = app_with_stub.app
    app.state.audio_lang.upsert(
        canonical_path=_AUTO_PATH,
        lang_code="gl",
        source="auto-high-conf-multi",
        lang_class="multi",
        lang_codes=["gl", "es"],
    )
    item = _multi_item(_AUTO_PATH)
    item["file_canonical_path"] = None  # synthetic row, keyed only on canonical
    app.state.coverage_cache = _SnapCache([item])

    items = app_with_stub.get(PENDING).json()["items"]
    row = next((it for it in items if it.get("canonical_path") == _AUTO_PATH), None)
    assert row is not None
    assert row["flag"] == "multilingual"
    assert row["lang_codes"] == ["gl", "es"]
