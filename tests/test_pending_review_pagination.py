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
