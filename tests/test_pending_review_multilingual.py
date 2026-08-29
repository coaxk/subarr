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


# ── #406 server-side search + pagination regression coverage ────────────────


def _suspect_rows(n, prefix="TV/Bulk", title="Ep"):
    """Build `n` distinct suspect rows (each → a pending row) for exercising
    search + offset/limit beyond the historical 200-row cap."""
    return [
        {
            "file_canonical_path": f"{prefix}/e{i:03d}.mkv",
            "canonical_path": f"{prefix}/e{i:03d}.mkv",
            "title": f"{title}{i:03d}",
            "episode_number": i,
            "audio_label_suspect": True,
        }
        for i in range(n)
    ]


def test_classification_completes_full_set_before_slicing(app_with_stub):
    # Classification must run over the COMPLETE pending set (not just the first
    # 200) so rows beyond the old boundary are reachable via offset and count
    # reflects the true total.
    app = app_with_stub.app
    app.state.coverage_cache = _SnapCache(_suspect_rows(250))

    default = app_with_stub.get(PENDING).json()
    assert default["count"] == 250  # full classified total
    assert len(default["items"]) == 200  # historical cap preserved as default limit

    deep = app_with_stub.get(PENDING, params={"limit": 50, "offset": 240}).json()
    assert deep["count"] == 250
    assert len(deep["items"]) == 10  # 250-240
    assert [it["canonical_path"] for it in deep["items"]] == [
        f"TV/Bulk/e{i:03d}.mkv" for i in range(240, 250)
    ]
    assert any(it["canonical_path"] == "TV/Bulk/e249.mkv" for it in deep["items"])


def test_search_case_insensitive_across_supported_fields(app_with_stub):
    app = app_with_stub.app
    app.state.coverage_cache = _SnapCache(
        [
            {
                "file_canonical_path": "TV/ShowA/e1.mkv",
                "canonical_path": "TV/ShowA/e1.mkv",
                "title": "TheSpecialOne",
                "episode_number": 7,
                "audio_label_suspect": True,
            },
            {
                "file_canonical_path": "TV/ShowB/e2.mkv",
                "canonical_path": "TV/ShowB/e2.mkv",
                "title": "Plain",
                "episode_number": 42,
                "audio_label_suspect": True,
            },
            {
                "file_canonical_path": "TV/ShowC/e3.mkv",
                "canonical_path": "TV/ShowC/e3.mkv",
                "title": "Other",
                "episode_number": 9,
                "audio_label_suspect": True,
            },
        ]
    )
    # title — mixed case
    body = app_with_stub.get(PENDING, params={"search": "thespecialone"}).json()
    assert body["count"] == 1
    assert body["items"][0]["title"] == "TheSpecialOne"
    # episode number
    body = app_with_stub.get(PENDING, params={"search": "42"}).json()
    assert body["count"] == 1
    assert body["items"][0]["episode_number"] == 42
    # canonical/file path, mixed case
    body = app_with_stub.get(PENDING, params={"search": "showb"}).json()
    assert body["count"] == 1
    assert body["items"][0]["canonical_path"] == "TV/ShowB/e2.mkv"
    body = app_with_stub.get(PENDING, params={"search": "E3.MKV"}).json()
    assert body["count"] == 1
    assert body["items"][0]["canonical_path"] == "TV/ShowC/e3.mkv"


def test_search_reaches_rows_beyond_historical_cap(app_with_stub):
    app = app_with_stub.app
    rows = _suspect_rows(250)
    rows[249]["title"] = "NeedleInHaystack"
    app.state.coverage_cache = _SnapCache(rows)

    body = app_with_stub.get(PENDING, params={"search": "needleinhaystack"}).json()
    assert body["count"] == 1
    assert body["items"][0]["canonical_path"] == "TV/Bulk/e249.mkv"


def test_flag_filter_is_global_and_reports_matching_counts(app_with_stub):
    rows = _suspect_rows(205)
    rows[200]["audio_label_suspect"] = False
    rows[200]["audio_label_unknown"] = True
    app_with_stub.app.state.coverage_cache = _SnapCache(rows)

    body = app_with_stub.get(PENDING, params={"flag": "unknown", "limit": 5}).json()
    assert body["count"] == 1
    assert body["items"][0]["canonical_path"] == "TV/Bulk/e200.mkv"
    assert body["counts_by_flag"]["suspect"] == 204
    assert body["counts_by_flag"]["unknown"] == 1


def test_offset_limit_slicing_is_stable(app_with_stub):
    app = app_with_stub.app
    n = 20
    app.state.coverage_cache = _SnapCache(_suspect_rows(n))

    page = app_with_stub.get(PENDING, params={"limit": 5, "offset": 5}).json()
    assert page["count"] == n
    assert [it["canonical_path"] for it in page["items"]] == [f"TV/Bulk/e{i:03d}.mkv" for i in range(5, 10)]

    # offset past end -> empty items, truthful count (empty-last-page recovery)
    tail = app_with_stub.get(PENDING, params={"limit": 5, "offset": 20}).json()
    assert tail["count"] == n
    assert tail["items"] == []

    # paging step-by-step covers the whole set exactly once (disjoint, exhaustive)
    seen = []
    offset = 0
    while True:
        p = app_with_stub.get(PENDING, params={"limit": 6, "offset": offset}).json()
        seen += [it["canonical_path"] for it in p["items"]]
        if not p["items"]:
            break
        offset += 6
    assert seen == [f"TV/Bulk/e{i:03d}.mkv" for i in range(n)]


def test_count_is_matching_total_not_page_length(app_with_stub):
    app = app_with_stub.app
    # every 3rd row shares the searchable title "Zebra" (i = 0,3,6,9 → 4 rows)
    app.state.coverage_cache = _SnapCache(
        [
            {
                "file_canonical_path": f"TV/X/e{i}.mkv",
                "canonical_path": f"TV/X/e{i}.mkv",
                "title": "Zebra" if i % 3 == 0 else "Plain",
                "episode_number": i,
                "audio_label_suspect": True,
            }
            for i in range(10)
        ]
    )
    body = app_with_stub.get(PENDING, params={"search": "ZEBRA", "limit": 1, "offset": 0}).json()
    assert body["count"] == 4  # matching total, NOT the page length
    assert len(body["items"]) == 1

    full = app_with_stub.get(PENDING, params={"search": "zebra", "limit": 100, "offset": 0}).json()
    assert full["count"] == 4
    assert len(full["items"]) == 4


def test_precedence_and_verified_skip_hold_with_search_and_pagination(app_with_stub):
    # track_mismatch > auto-multi > verified-skip > suspect/unknown must survive
    # when search + pagination are in play (search reaches all via a shared path
    # substring; precedence must still win).
    app = app_with_stub.app
    store = app.state.audio_lang
    store.upsert(
        canonical_path="TV/Prec/multi.mkv",
        lang_code="gl",
        source="auto-high-conf-multi",
        lang_class="multi",
        lang_codes=["gl", "es"],
    )
    store.upsert(canonical_path="TV/Prec/verified.mkv", lang_code="en", source="user", lang_class="single")
    app.state.coverage_cache = _SnapCache(
        [
            {
                "file_canonical_path": "TV/Prec/multi.mkv",
                "canonical_path": "TV/Prec/multi.mkv",
                "title": "MultiEp",
                "audio_source": "multilingual",
                "audio_label_suspect": False,
                "audio_label_unknown": False,
                "audio_langs": ["gl", "es"],
            },
            {
                "file_canonical_path": "TV/Prec/verified.mkv",
                "canonical_path": "TV/Prec/verified.mkv",
                "title": "VerifiedEp",
                "audio_label_suspect": True,
            },
            {
                "file_canonical_path": "TV/Prec/tm.mkv",
                "canonical_path": "TV/Prec/tm.mkv",
                "title": "TmEp",
                "default_track_mismatch": True,
                "audio_label_suspect": True,
            },
            {
                "file_canonical_path": "TV/Prec/suspect.mkv",
                "canonical_path": "TV/Prec/suspect.mkv",
                "title": "SuspectEp",
                "audio_label_suspect": True,
            },
            {
                "file_canonical_path": "TV/Prec/unknown.mkv",
                "canonical_path": "TV/Prec/unknown.mkv",
                "title": "UnknownEp",
                "audio_label_unknown": True,
            },
        ]
    )
    body = app_with_stub.get(PENDING, params={"search": "Prec", "limit": 50, "offset": 0}).json()
    flags = {it["canonical_path"]: it["flag"] for it in body["items"]}
    assert flags["TV/Prec/tm.mkv"] == "track_mismatch"  # first precedence
    assert flags["TV/Prec/multi.mkv"] == "multilingual"  # auto-multi surfaced
    assert "TV/Prec/verified.mkv" not in flags  # settled -> skipped
    assert flags["TV/Prec/suspect.mkv"] == "suspect"
    assert flags["TV/Prec/unknown.mkv"] == "unknown"
    assert body["count"] == 4


def test_pending_review_validates_query_params(app_with_stub):
    app = app_with_stub.app
    app.state.coverage_cache = _SnapCache([_suspect_item(_SUSPECT_PATH)])
    assert app_with_stub.get(PENDING, params={"limit": 0}).status_code == 422
    assert app_with_stub.get(PENDING, params={"limit": 501}).status_code == 422
    assert app_with_stub.get(PENDING, params={"offset": -1}).status_code == 422
    assert app_with_stub.get(PENDING, params={"search": "x" * 201}).status_code == 422
    # empty search string is allowed
    assert app_with_stub.get(PENDING, params={"search": ""}).status_code == 200


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
