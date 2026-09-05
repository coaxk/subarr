"""#448: Review truncated at 200 rows with no way to reach the rest.

The endpoint returned `count` = the true total but `items` = pending[:200], so
the UI could tell you there were 538 rows and then hand you 200 of them, with
no offset to page past the cliff. Reported by a user with a library well over
that size.

Harness mirrors tests/test_pending_review_multilingual.py.
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


def _suspect(i: int) -> dict:
    p = f"TV/Show/Season 1/ep{i:04d}.mkv"
    return {
        "file_canonical_path": p,
        "canonical_path": p,
        "title": "Show",
        "episode_number": f"S01E{i:04d}",
        "audio_label_suspect": True,
    }


def _seed(app_with_stub, n: int):
    app_with_stub.app.state.coverage_cache = _SnapCache([_suspect(i) for i in range(n)])


def test_count_is_the_true_total_not_the_page_size(app_with_stub):
    _seed(app_with_stub, 450)
    body = app_with_stub.get(PENDING).json()
    assert body["count"] == 450, "count must report every pending row"


def test_rows_past_the_old_200_cliff_are_reachable(app_with_stub):
    # The actual bug: row 300 existed and could not be fetched at all.
    _seed(app_with_stub, 450)
    body = app_with_stub.get(f"{PENDING}?limit=100&offset=300").json()
    paths = [it["canonical_path"] for it in body["items"]]
    assert paths, "offset past 200 must return rows"
    assert "TV/Show/Season 1/ep0300.mkv" in paths


def test_paging_covers_every_row_exactly_once(app_with_stub):
    _seed(app_with_stub, 450)
    seen: list[str] = []
    for off in range(0, 450, 100):
        page = app_with_stub.get(f"{PENDING}?limit=100&offset={off}").json()["items"]
        seen.extend(it["canonical_path"] for it in page)
    assert len(seen) == 450
    assert len(set(seen)) == 450, "no row may be duplicated or skipped across pages"


def test_offset_beyond_the_end_is_empty_not_an_error(app_with_stub):
    _seed(app_with_stub, 10)
    body = app_with_stub.get(f"{PENDING}?limit=50&offset=999").json()
    assert body["items"] == []
    assert body["count"] == 10, "count still reports the total"


def test_order_is_stable_across_pages(app_with_stub):
    # Paging over an unstable order silently drops and repeats rows.
    _seed(app_with_stub, 300)
    first = app_with_stub.get(f"{PENDING}?limit=50&offset=0").json()["items"]
    again = app_with_stub.get(f"{PENDING}?limit=50&offset=0").json()["items"]
    assert [i["canonical_path"] for i in first] == [i["canonical_path"] for i in again]


def test_default_request_still_works_for_existing_callers(app_with_stub):
    # Back-compat: no params must keep returning a sane page.
    _seed(app_with_stub, 450)
    body = app_with_stub.get(PENDING).json()
    assert body["count"] == 450
    assert 0 < len(body["items"]) <= 450


# ── Server-side Review ordering (#448/#449 follow-on) regressions ───────────
# Phase 2 pins down `_sort_pending_review_rows` in audio_lang.py: media rank
# (only media_type == "movie" is rank 1/last; every missing / episode / show /
# legacy / unknown value is TV), case-insensitive casefold title order, and a
# deterministic canonical-identity tie-break that ignores snapshot traversal.


def _review_item(title, media_type, path):
    """A suspect snapshot item -> exactly one pending Review row. `media_type`
    None omits the key (models 'missing/unknown'); 'movie' sorts after every
    TV-ish value. `path` is both the file and canonical identity."""
    item = {
        "file_canonical_path": path,
        "canonical_path": path,
        "title": title,
        "audio_label_suspect": True,
    }
    if media_type is not None:
        item["media_type"] = media_type
    return item


def test_mixed_media_and_case_insensitive_title_ordering(app_with_stub):
    # TV/episode plus missing/legacy-media rows all sort BEFORE movies, and
    # titles are alphabetical case-insensitively within each bucket — even when
    # the snapshot traversal order is deliberately unsorted (movies interleaved
    # with TV, titles out of order, mixed case).
    rows = [
        _review_item("mango", "movie", "Movies/mango.mkv"),
        _review_item("Zulu", "episode", "TV/Ep/Zulu.mkv"),
        _review_item("Hotel", "movie", "Movies/Hotel.mkv"),
        _review_item("India", "movie", "Movies/India.mkv"),
        _review_item("apple", "episode", "TV/Ep/apple.mkv"),
        _review_item("Bravo", "show", "TV/Show/Bravo.mkv"),
        _review_item("echo", "series", "TV/Legacy/echo.mkv"),  # legacy media value
        _review_item("Delta", None, "TV/Season/Delta.mkv"),  # missing media_type
    ]
    app_with_stub.app.state.coverage_cache = _SnapCache(rows)

    items = app_with_stub.get(PENDING, params={"limit": 100, "offset": 0}).json()["items"]
    got = [it["canonical_path"] for it in items]
    assert got == [
        # TV bucket (rank 0) first — casefold-alphabetical:
        "TV/Ep/apple.mkv",  # lowercase 'a' sorts before uppercase-leading titles
        "TV/Show/Bravo.mkv",
        "TV/Season/Delta.mkv",  # missing media_type -> TV
        "TV/Legacy/echo.mkv",  # legacy "series" value -> TV
        "TV/Ep/Zulu.mkv",
        # then all movies (rank 1), casefold-alphabetical:
        "Movies/Hotel.mkv",
        "Movies/India.mkv",
        "Movies/mango.mkv",
    ]
    # Explicit case-insensitivity proofs (raw ASCII ordering would differ):
    # 'Z'(90) < 'a'(97), so a case-sensitive sort would place Zulu before apple…
    assert got.index("TV/Ep/apple.mkv") < got.index("TV/Ep/Zulu.mkv")
    # …and a lowercase-leading title must not sink behind uppercase-leading ones.
    assert got.index("TV/Ep/apple.mkv") == 0
    # Media rank beats title: movie "Hotel" still trails every TV row incl. "Zulu".
    assert got.index("TV/Ep/Zulu.mkv") < got.index("Movies/Hotel.mkv")


def test_equal_titles_break_deterministically_by_canonical_identity(app_with_stub):
    # Rows that normalize to the SAME title ("the show") must order by canonical
    # identity ascending — never by snapshot traversal order. The traversal is
    # seeded in the OPPOSITE (descending-identity) order to prove the tie-break
    # (not the input order) decides; a synthetic row with a None file path
    # exercises the canonical_path fallback.
    rows = [
        _review_item("THE SHOW", None, "ZZZ.mkv"),
        {
            # Bazarr-synthetic: file_canonical_path is None, keyed only on canonical.
            "file_canonical_path": None,
            "canonical_path": "MMM.mkv",
            "title": "The Show",
            "audio_label_suspect": True,
        },
        _review_item("the show", None, "AAA.mkv"),
    ]
    app_with_stub.app.state.coverage_cache = _SnapCache(rows)

    items = app_with_stub.get(PENDING, params={"limit": 100, "offset": 0}).json()["items"]
    got = [it["canonical_path"] for it in items]
    # Identities ascend AAA.mkv < MMM.mkv < ZZZ.mkv, independent of the ZZZ/MMM/AAA
    # traversal order above.
    assert got == ["AAA.mkv", "MMM.mkv", "ZZZ.mkv"]
    assert got != ["ZZZ.mkv", "MMM.mkv", "AAA.mkv"], "must not follow snapshot order"


def test_server_side_order_is_stable_across_small_pages(app_with_stub):
    # The authoritative order must be applied BEFORE offset/limit slicing, so
    # page membership is deterministic: paging a mixed set in small pages and
    # concatenating must reproduce the full sorted order (disjoint + exhaustive).
    tv_titles = ["apple", "Bravo", "Charlie", "delta", "EchoEp", "foxtrot", "GolfShow", "hotel"]
    mv_titles = [
        "AppleMovie",
        "bananaMovie",
        "CherryMovie",
        "deltaMovie",
        "EchoMovie",
        "figMovie",
        "GrapeMovie",
        "honeyMovie",
    ]
    tv_rows = [
        _review_item(t, {1: "show", 3: None, 5: "series"}.get(i, "episode"), f"TV/{t}.mkv")
        for i, t in enumerate(tv_titles)
    ]
    mv_rows = [_review_item(t, "movie", f"Movies/{t}.mkv") for t in mv_titles]
    # Traversal deliberately unsorted: movies (which must sort last) are fed
    # first and the TV rows are reversed, so input order is nowhere near output.
    rows = mv_rows + list(reversed(tv_rows))
    app_with_stub.app.state.coverage_cache = _SnapCache(rows)

    expected = [f"TV/{t}.mkv" for t in tv_titles] + [f"Movies/{m}.mkv" for m in mv_titles]
    seen: list[str] = []
    for off in range(0, len(expected), 4):
        page = app_with_stub.get(PENDING, params={"limit": 4, "offset": off}).json()["items"]
        seen += [it["canonical_path"] for it in page]
    assert seen == expected
    assert len(seen) == len(expected)
    assert len(set(seen)) == len(expected), "no row duplicated or skipped across pages"
