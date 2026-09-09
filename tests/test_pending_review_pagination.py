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


# ── #494 grouped=true complete-group paging regressions ─────────────────────
# Phase 2 pins down the additive grouped contract in audio_lang.py: complete
# groups are paged (never file-capped or split), identity is library slug + media
# lane + nested Arr id (with safe canonical fallbacks), search/flag filter run
# per-row BEFORE grouping, the #493 sort decides membership/order independent of
# snapshot traversal, and the default (grouped omitted/false) response is
# byte-compatible with the pre-grouping contract.

_PENDING_KEYS_DEFAULT = (
    "count",
    "counts_by_flag",
    "flag",
    "limit",
    "offset",
    "page_count",
    "has_more",
    "items",
)


def _review_coverage_item(
    title,
    *,
    slug="",
    name=None,
    root,
    media="episode",
    sonarr_id=None,
    radarr_id=None,
    file=None,
    flag="suspect",
    episode_number=None,
):
    """A coverage-engine-shaped snapshot item (#494 P2-S1): `root` is the
    group/show-root canonical and `file` (default root + '/file.mkv') the
    per-file canonical, both relative under any `@<slug>/` head so the slug
    resolves from the canonical path exactly as a real non-default library row
    would. Nested `bazarr` ids mirror coverage_engine.to_dict(); omitting an id
    exercises the canonical-root fallback. `flag` selects suspect/unknown."""
    name = name if name is not None else (slug or "Default")
    if file is None:
        file = f"{root}/file.mkv"
    head = f"@{slug}/" if slug else ""
    item = {
        "file_canonical_path": f"{head}{file.lstrip('/')}",
        "canonical_path": f"{head}{root.lstrip('/')}",
        "title": title,
        "media_type": media,
        "episode_number": episode_number,
        "original_language": "xx",
        "audio_langs": [],
        "audio_label_notes": [],
        "audio_label_suspect": flag == "suspect",
        "audio_label_unknown": flag == "unknown",
        "library": {"slug": slug, "name": name},
        "bazarr": {"sonarr_id": sonarr_id, "radarr_id": radarr_id},
    }
    return item


def _grouped(app_with_stub, **params):
    """One grouped-mode GET; `grouped=true` is implied unless overridden."""
    q = {"grouped": "true"}
    q.update(params)
    return app_with_stub.get(PENDING, params=q).json()


def _all_group_pages(app_with_stub, limit=2):
    """Page every group with a small `limit`, concatenating the responses."""
    pages, offset = [], 0
    while True:
        body = _grouped(app_with_stub, limit=limit, offset=offset)
        pages.append(body)
        if not body["has_more"]:
            return pages
        offset += limit


def test_grouped_pages_never_split_a_group(app_with_stub):
    # A group with several rows must never be split to satisfy the group page
    # size: Alpha (5 episodes) is one group and must arrive whole even though a
    # file page of size 2 would have spread it over three pages. Request small
    # GROUP pages (limit 2 < 3 groups) and concatenate.
    shows = [("Alpha", 100, 5), ("Bravo", 101, 2), ("Charlie", 102, 3)]
    rows = []
    for title, sid, count in shows:
        for i in range(1, count + 1):
            rows.append(
                _review_coverage_item(
                    title,
                    slug="libA",
                    name="Lib A",
                    root=f"TV/{title}",
                    media="episode",
                    sonarr_id=sid,
                    file=f"TV/{title}/Season 1/e{i:02d}.mkv",
                )
            )
    app_with_stub.app.state.coverage_cache = _SnapCache(rows)

    pages = _all_group_pages(app_with_stub, limit=2)
    assert [b["group_count"] for b in pages] == [3, 3], "group_count is the total on every page"
    assert [len(b["groups"]) for b in pages] == [2, 1]

    # Every page carries WHOLE groups only: each group's flattened items are
    # exactly its file_count and all share the group's key.
    for body in pages:
        for grp in body["groups"]:
            key = grp["key"]
            grp_items = [it for it in body["items"] if it.get("group_key") == key]
            assert len(grp_items) == grp["file_count"], "group must arrive whole on its page"
            assert all(it["title"] == grp["title"] for it in grp_items)
            assert grp["canonical_root"].startswith("@libA/")

    # Concatenating pages reproduces every matching row exactly once (the Alpha
    # group's 5 rows all on one page proves no per-group file cap).
    seen = [it["file_canonical_path"] for body in pages for it in body["items"]]
    all_paths = [r["file_canonical_path"] for r in rows]
    assert sorted(seen) == sorted(all_paths), "group paging must be exhaustive"
    assert len(seen) == len(set(seen)) == 10, "no row duplicated or skipped across group pages"
    # reserved identity never leaks into serialized grouped rows
    for body in pages:
        for it in body["items"]:
            assert not any(k.startswith("_review_") for k in it), "reserved key leaked into items"


def test_grouped_equal_titles_across_libraries_remain_separate(app_with_stub):
    # Two libraries (distinct slugs) can hold same-titled, SAME Arr-id series
    # (the anime_stack collision topology); the group key must namespace by
    # library slug so they never merge.
    rows = []
    for slug in ("libA", "libB"):
        rows.append(
            _review_coverage_item(
                "The Show",
                slug=slug,
                name=f"Lib {slug[-1]}",
                root=f"TV/{slug}/Show",
                media="episode",
                sonarr_id=7,  # identical arr id in both — slug must separate
                file=f"TV/{slug}/Show/Season 1/e01.mkv",
            )
        )
        rows.append(
            _review_coverage_item(
                "The Show",
                slug=slug,
                name=f"Lib {slug[-1]}",
                root=f"TV/{slug}/Show",
                media="episode",
                sonarr_id=7,
                file=f"TV/{slug}/Show/Season 1/e02.mkv",
            )
        )
    app_with_stub.app.state.coverage_cache = _SnapCache(rows)

    body = _grouped(app_with_stub, limit=50, offset=0)
    keys = [g["key"] for g in body["groups"]]
    assert len(body["groups"]) == 2, "equal titles in two libraries must stay separate"
    assert len(set(keys)) == 2
    assert {"lib:libA|series:7", "lib:libB|series:7"} == set(keys), keys
    assert all(g["title"] == "The Show" for g in body["groups"])
    assert body["count"] == 4
    assert body["group_count"] == 2


def test_grouped_same_name_different_arr_ids_remain_separate(app_with_stub):
    # Same-name episode groups with different sonarr_ids, and same-name movie
    # groups with different radarr_ids, must each stay separate groups.
    rows = [
        _review_coverage_item(
            "Common",
            slug="libA",
            name="Lib A",
            root="TV/One",
            media="episode",
            sonarr_id=10,
            file="TV/One/Season 1/e01.mkv",
        ),
        _review_coverage_item(
            "Common",
            slug="libA",
            name="Lib A",
            root="TV/One",
            media="episode",
            sonarr_id=10,
            file="TV/One/Season 1/e02.mkv",
        ),
        _review_coverage_item(
            "Common",
            slug="libA",
            name="Lib A",
            root="TV/Two",
            media="episode",
            sonarr_id=20,
            file="TV/Two/Season 1/e01.mkv",
        ),
        _review_coverage_item(
            "SameMovie",
            slug="libA",
            name="Lib A",
            root="Movies/M1",
            media="movie",
            radarr_id=5,
            file="Movies/M1/M1.mkv",
        ),
        _review_coverage_item(
            "SameMovie",
            slug="libA",
            name="Lib A",
            root="Movies/M2",
            media="movie",
            radarr_id=6,
            file="Movies/M2/M2.mkv",
        ),
    ]
    app_with_stub.app.state.coverage_cache = _SnapCache(rows)

    body = _grouped(app_with_stub, limit=50, offset=0)
    keys = sorted(g["key"] for g in body["groups"])
    assert len(body["groups"]) == 4, "different Arr ids must never merge same-name rows"
    # two episode (series-lane) groups and two movie-lane groups
    assert keys == [
        "lib:libA|movie:5",
        "lib:libA|movie:6",
        "lib:libA|series:10",
        "lib:libA|series:20",
    ], keys
    # movie groups arrive under the movie lane with radarr identity
    groups_by_key = {g["key"]: g for g in body["groups"]}
    # the episode group (sonarr 10) holds BOTH its same-name rows
    assert groups_by_key["lib:libA|series:10"]["file_count"] == 2
    assert groups_by_key["lib:libA|series:20"]["file_count"] == 1
    assert groups_by_key["lib:libA|series:10"]["title"] == "Common"
    assert groups_by_key["lib:libA|movie:5"]["media_type"] == "movie"
    assert groups_by_key["lib:libA|movie:6"]["media_type"] == "movie"


def test_grouped_no_arr_identity_uses_canonical_root_fallback(app_with_stub):
    # Rows with NO Arr identity must group via the canonical root and separate
    # across roots — never collapse on a bare title.
    rows = [
        # Same title, no sonarr_id, in the SAME library but DIFFERENT roots.
        _review_coverage_item(
            "Fallback",
            slug="libA",
            name="Lib A",
            root="TV/One",
            media="episode",
            file="TV/One/Season 1/e01.mkv",
        ),
        _review_coverage_item(
            "Fallback",
            slug="libA",
            name="Lib A",
            root="TV/One",
            media="episode",
            file="TV/One/Season 1/e02.mkv",
        ),
        _review_coverage_item(
            "Fallback",
            slug="libA",
            name="Lib A",
            root="TV/Two",
            media="episode",
            file="TV/Two/Season 1/e01.mkv",
        ),
        # A movie with no radarr id likewise falls back to its canonical root.
        _review_coverage_item(
            "MovieNoId",
            slug="libA",
            name="Lib A",
            root="Movies/M",
            media="movie",
            file="Movies/M/M.mkv",
        ),
    ]
    app_with_stub.app.state.coverage_cache = _SnapCache(rows)

    body = _grouped(app_with_stub, limit=50, offset=0)
    keys = sorted(g["key"] for g in body["groups"])
    # Three groups: both no-id rows of TV/One (same root) merge; TV/Two and the
    # movie are separate.
    assert body["group_count"] == 3, keys
    assert keys == [
        "lib:libA|movie:root:@libA/Movies/M",
        "lib:libA|series:root:@libA/TV/One",
        "lib:libA|series:root:@libA/TV/Two",
    ], keys
    # group keys are never bare titles
    assert all(g["key"] != g["title"] for g in body["groups"])
    by_key = {g["key"]: g for g in body["groups"]}
    assert by_key["lib:libA|series:root:@libA/TV/One"]["file_count"] == 2


def test_grouped_search_filters_rows_before_grouping(app_with_stub):
    # Search matches individual rows BEFORE grouping: a would-be group where only
    # SOME episodes match must return a group whose file_count/items describe
    # only the matching rows, while `count` stays the matching-file total.
    rows = [
        _review_coverage_item(
            "Mixed",
            slug="libA",
            name="Lib A",
            root="TV/Mixed",
            media="episode",
            sonarr_id=99,
            file=f"TV/Mixed/Season 1/S01E0{i}.mkv",
            episode_number=f"S01E0{i}",
        )
        for i in range(1, 5)
    ]
    # A second, fully-non-matching group must vanish under search.
    rows.append(
        _review_coverage_item(
            "Other",
            slug="libA",
            name="Lib A",
            root="TV/Other",
            media="episode",
            sonarr_id=200,
            file="TV/Other/Season 1/S01E01.mkv",
            episode_number="S01E01",
        )
    )
    app_with_stub.app.state.coverage_cache = _SnapCache(rows)

    body = _grouped(app_with_stub, search="S01E02", limit=50, offset=0)
    assert body["count"] == 1, "`count` stays the matching-file total"
    assert body["group_count"] == 1, "only groups with a matching row appear"
    (grp,) = body["groups"]
    assert grp["key"] == "lib:libA|series:99"
    assert grp["file_count"] == 1, "file_count = matching rows actually in items"
    assert [it["file_canonical_path"] for it in body["items"]] == ["@libA/TV/Mixed/Season 1/S01E02.mkv"]
    # the non-matching group is entirely absent (no empty groups)
    assert all(g["title"] != "Other" for g in body["groups"])


def test_grouped_flag_filter_filters_rows_before_grouping(app_with_stub):
    # Flag filter also runs per-row BEFORE grouping: a mixed-flag group yields
    # only its matching rows; a group whose rows ALL fail the filter disappears.
    rows = []
    # "Mixed" has 2 suspect + 2 unknown episodes, all one sonarr group.
    for flag, i in (("suspect", 1), ("suspect", 2), ("unknown", 3), ("unknown", 4)):
        rows.append(
            _review_coverage_item(
                "Mixed",
                slug="libA",
                name="Lib A",
                root="TV/Mixed",
                media="episode",
                sonarr_id=99,
                file=f"TV/Mixed/Season 1/e{i}.mkv",
                flag=flag,
            )
        )
    # "AllSuspect" is entirely suspect -> must not appear when filtering unknown.
    rows.append(
        _review_coverage_item(
            "AllSuspect",
            slug="libA",
            name="Lib A",
            root="TV/AS",
            media="episode",
            sonarr_id=100,
            file="TV/AS/Season 1/e1.mkv",
            flag="suspect",
        )
    )
    app_with_stub.app.state.coverage_cache = _SnapCache(rows)

    body = _grouped(app_with_stub, flag="unknown", limit=50, offset=0)
    assert body["count"] == 2, "`count` = total matching files after the flag filter"
    assert body["group_count"] == 1, "all-suspect group must vanish under flag=unknown"
    (grp,) = body["groups"]
    assert grp["key"] == "lib:libA|series:99"
    assert grp["file_count"] == 2, "file_count = only the rows that passed the filter"
    assert sorted(it["file_canonical_path"] for it in body["items"]) == [
        "@libA/TV/Mixed/Season 1/e3.mkv",
        "@libA/TV/Mixed/Season 1/e4.mkv",
    ]


def test_grouped_order_is_deterministic_across_traversal_orders(app_with_stub):
    # Seeding identical content in two traversal orders must yield identical
    # group membership/order, row order within groups, totals, and flattened
    # paths — the #493 sort (not snapshot order) decides, and paging stays stable.
    def build(traversal):
        rows = [
            _review_coverage_item(
                "Alpha",
                slug="libA",
                name="Lib A",
                root="TV/Alpha",
                media="episode",
                sonarr_id=2,
                file=f"TV/Alpha/Season 1/e{i}.mkv",
            )
            for i in (3, 1, 2)
        ]
        rows.append(
            _review_coverage_item(
                "Zulu Movie",
                slug="libA",
                name="Lib A",
                root="Movies/Zulu",
                media="movie",
                radarr_id=50,
                file="Movies/Zulu/Zulu.mkv",
            )
        )
        # two same-title episode groups with different sonarr ids (title tie)
        for sid, root in ((30, "TV/Beta"), (31, "TV/Beta2")):
            rows.append(
                _review_coverage_item(
                    "beta show",
                    slug="libA",
                    name="Lib A",
                    root=root,
                    media="episode",
                    sonarr_id=sid,
                    file=f"{root}/Season 1/e1.mkv",
                )
            )
        return rows if traversal == "forward" else list(reversed(rows))

    def collect(rows):
        app_with_stub.app.state.coverage_cache = _SnapCache(rows)
        pages = _all_group_pages(app_with_stub, limit=2)
        return (
            [g["key"] for b in pages for g in b["groups"]],
            [g["file_count"] for b in pages for g in b["groups"]],
            [it["file_canonical_path"] for b in pages for it in b["items"]],
            pages[0]["count"],
            pages[0]["group_count"],
        )

    fwd = collect(build("forward"))
    rev = collect(build("reversed"))
    assert rev == fwd, "snapshot traversal order must not change grouped output"
    keys, file_counts, paths, count, group_count = fwd
    # Alpha(3) + two same-name "beta show" groups + one movie = 4 groups / 6 rows
    assert len(keys) == group_count == 4, (keys, group_count)
    assert sum(file_counts) == len(paths) == count == 6, "exhaustive and duplicate-free"
    assert len(set(paths)) == len(paths)
    # the two "beta show" groups stay distinct and the movie lands last (movie lane)
    assert len(set(keys)) == 4


def test_default_mode_has_no_grouped_fields(app_with_stub):
    # A request WITHOUT grouped= must keep the exact pre-grouping key set and row
    # contract: no group_count/groups, no group_key, no reserved identity leak —
    # even when the seeded rows carry library + nested Arr identity.
    rows = [
        _review_coverage_item(
            "Alpha",
            slug="libA",
            name="Lib A",
            root="TV/Alpha",
            media="episode",
            sonarr_id=2,
            file="TV/Alpha/Season 1/e1.mkv",
        ),
        _review_coverage_item(
            "Alpha",
            slug="libA",
            name="Lib A",
            root="TV/Alpha",
            media="episode",
            sonarr_id=2,
            file="TV/Alpha/Season 1/e2.mkv",
        ),
        _review_coverage_item(
            "Movie",
            slug="libA",
            name="Lib A",
            root="Movies/Movie",
            media="movie",
            radarr_id=5,
            file="Movies/Movie/Movie.mkv",
        ),
    ]
    app_with_stub.app.state.coverage_cache = _SnapCache(rows)

    body = app_with_stub.get(PENDING).json()
    assert tuple(sorted(body)) == tuple(sorted(_PENDING_KEYS_DEFAULT)), sorted(body)
    for it in body["items"]:
        assert "group_key" not in it, "default rows carry no group linkage"
        assert not any(k.startswith("_review_") for k in it), "reserved key leaked in default mode"
    assert body["count"] == 3


def test_grouped_false_explicit_matches_absent(app_with_stub):
    # grouped=false (explicit) must behave byte-identically to grouped omitted.
    rows = [
        _review_coverage_item(
            "Alpha",
            slug="libA",
            name="Lib A",
            root="TV/Alpha",
            media="episode",
            sonarr_id=2,
            file=f"TV/Alpha/Season 1/e{i}.mkv",
        )
        for i in (1, 2, 3)
    ]
    app_with_stub.app.state.coverage_cache = _SnapCache(rows)

    absent = app_with_stub.get(PENDING, params={"limit": 2, "offset": 0}).json()
    explicit = app_with_stub.get(PENDING, params={"grouped": "false", "limit": 2, "offset": 0}).json()
    assert absent == explicit, "grouped=false must be identical to grouped omitted"
    assert absent["count"] == 3
    assert len(absent["items"]) == 2
    assert tuple(sorted(absent)) == tuple(sorted(_PENDING_KEYS_DEFAULT))


def test_default_mode_keeps_file_offset_limit_semantics(app_with_stub):
    # Default (non-group) callers page FILES: a single group's rows may be spread
    # across file pages and offset/limit slice rows, not groups.
    rows = [
        _review_coverage_item(
            "Show",
            slug="libA",
            name="Lib A",
            root="TV/Show",
            media="episode",
            sonarr_id=9,
            file=f"TV/Show/Season 1/e{i}.mkv",
        )
        for i in range(1, 5)
    ]
    app_with_stub.app.state.coverage_cache = _SnapCache(rows)

    page1 = app_with_stub.get(PENDING, params={"limit": 2, "offset": 0}).json()
    page2 = app_with_stub.get(PENDING, params={"limit": 2, "offset": 2}).json()
    assert page1["count"] == page2["count"] == 4
    assert [it["file_canonical_path"] for it in page1["items"]] == [
        "@libA/TV/Show/Season 1/e1.mkv",
        "@libA/TV/Show/Season 1/e2.mkv",
    ]
    # rows 3-4 land on a SECOND page, though they belong to the SAME group as 1-2
    assert [it["file_canonical_path"] for it in page2["items"]] == [
        "@libA/TV/Show/Season 1/e3.mkv",
        "@libA/TV/Show/Season 1/e4.mkv",
    ]
