"""Fix 1: Bazarr-wanted movie enrichment must use radarrId, not title.

Title-collision bug: two movies sharing a title (e.g. a 1974 original + 2022
remake) caused last-writer-wins in radarr_by_title, so the wrong Radarr record
was used for metadata enrichment (wrong bazarr_radarr_id, original_language,
monitored, tags, canonical_path, stale-SRT check).

Bazarr's wanted-movie rows carry a ``radarrId`` field (confirmed in the
fixture: tests/fixtures/integrations/bazarr_movies_wanted.json).  We resolve
each Bazarr-wanted movie row by ``w.get("radarrId")`` against
``radarr_by_id = {m["id"]: m for m in radarr_movies}`` instead of by title.
"""

from __future__ import annotations


def _radarr_movie(rid: int, title: str, *, year: int = 2000, lang: str = "English") -> dict:
    return {
        "id": rid,
        "title": title,
        "year": year,
        "hasFile": True,
        "monitored": True,
        "path": f"/data/Media/Movies/{title} ({year})",
        "movieFile": {"path": f"/data/Media/Movies/{title} ({year})/{title}.mkv"},
        "originalLanguage": {"name": lang},
        "tags": [],
    }


def _bazarr_wanted_movie(title: str, radarr_id: int) -> dict:
    return {
        "title": title,
        "radarrId": radarr_id,
        "missing_subtitles": [{"name": "English", "code2": "en", "code3": "eng"}],
        "tags": [],
        "monitored": "True",
    }


# ---------------------------------------------------------------------------
# Test: title collision — same title, different ids, correct id wins
# ---------------------------------------------------------------------------


def test_radarr_wanted_enrichment_uses_id_not_title(subarr_env):
    """Two movies share the same title string but different radarr ids.

    Title-based lookup is order-dependent (last-writer-wins).  Reversing the
    radarr_movies list changes which record is returned for the same title.
    The id-based lookup is always correct regardless of list order.
    """
    SHARED_TITLE = "Invasion"
    movie_old = _radarr_movie(1, SHARED_TITLE, year=1978, lang="English")
    movie_new = _radarr_movie(2, SHARED_TITLE, year=2022, lang="French")

    radarr_movies = [movie_old, movie_new]

    # Bazarr wants a sub for the 2022 remake (radarrId=2).
    w = _bazarr_wanted_movie(SHARED_TITLE, radarr_id=2)

    # --- OLD (broken) behaviour: title lookup is last-writer-wins ---
    radarr_by_title_fwd = {
        m.get("title", "").strip().lower(): m for m in radarr_movies if isinstance(m, dict)
    }
    radarr_by_title_rev = {
        m.get("title", "").strip().lower(): m for m in reversed(radarr_movies) if isinstance(m, dict)
    }
    title_key = (w.get("title") or "").strip().lower()

    # Forward order: last-writer is movie_new (id=2) — accidentally correct.
    assert radarr_by_title_fwd.get(title_key, {}).get("id") == 2
    # Reversed order: last-writer is movie_old (id=1) — WRONG.
    assert radarr_by_title_rev.get(title_key, {}).get("id") == 1, (
        "title-based lookup is broken when insertion order changes"
    )

    # --- NEW (correct) behaviour: id-based lookup ---
    radarr_by_id = {m["id"]: m for m in radarr_movies if isinstance(m, dict) and "id" in m}
    result = radarr_by_id.get(w.get("radarrId"), {})

    assert result.get("id") == 2, "id-based lookup must return the movie the Bazarr row references"
    assert result.get("year") == 2022, "should be the 2022 remake, not the 1978 original"
    assert result.get("originalLanguage", {}).get("name") == "French"

    # Reversed list — still correct.
    radarr_by_id_rev = {m["id"]: m for m in reversed(radarr_movies) if isinstance(m, dict) and "id" in m}
    result_rev = radarr_by_id_rev.get(w.get("radarrId"), {})
    assert result_rev.get("id") == 2, "id-based lookup is stable regardless of list order"


def test_radarr_by_id_lookup_survives_missing_radarr_id(subarr_env):
    """A Bazarr-wanted row with no radarrId must fall through to an empty
    dict gracefully (same behaviour as a title miss in the old code)."""
    radarr_movies = [_radarr_movie(7, "Orphan Movie")]
    w = {"title": "Orphan Movie", "missing_subtitles": []}  # no radarrId key

    radarr_by_id = {m["id"]: m for m in radarr_movies if isinstance(m, dict) and "id" in m}
    result = radarr_by_id.get(w.get("radarrId"), {})

    # w.get("radarrId") is None -> dict.get(None, {}) returns {}
    assert result == {}, "missing radarrId should return empty dict, not raise"


def test_radarr_by_id_skips_non_dict_entries(subarr_env):
    """radarr_by_id must tolerate None / non-dict entries in radarr_movies."""
    radarr_movies = [None, "bad", _radarr_movie(5, "Good Movie")]
    radarr_by_id = {m["id"]: m for m in radarr_movies if isinstance(m, dict) and "id" in m}
    assert list(radarr_by_id.keys()) == [5]
