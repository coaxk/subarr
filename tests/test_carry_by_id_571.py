"""#571: carry a verdict by Sonarr/Radarr id, for files with no SxxExx token.

#563 carries a verdict onto a replaced file by matching the episode's `SxxExx`
token. A date-named episode (`Daily Show - 2026-09-08.mkv`, #561) has no token,
and a movie has nothing to match at all, so their verdicts died with the old
file and only a series rule covered them.

Each verdict now stores the Sonarr episode id / Radarr movie id it was recorded
against. Carry-over matches on that id, which needs no filename token and no
API call: the ids come from the coverage snapshot subarr already caches, passed
in as a plain mapping so the store stays offline.

Scope is ONE LIBRARY (the `@slug` head): the same Sonarr id in two instances
means two different files.
"""

from __future__ import annotations

import pytest

MOVIE_OLD = "Movies/Perez (2014)/Perez (2014) DVD.avi"
MOVIE_NEW = "Movies/Perez (2014)/Perez (2014) Bluray-1080p.mkv"
DAILY_OLD = "TV/Daily Show/Season 2026/Daily Show - 2026-09-08 HDTV-720p.mkv"
DAILY_NEW = "TV/Daily Show/Season 2026/Daily Show - 2026-09-08 WEBDL-1080p.mkv"


@pytest.fixture
def store(subarr_env, tmp_path):
    from subarr.audio_lang_store import AudioLangStore
    from subarr.migrate import run_migrations

    db = tmp_path / "al.db"
    run_migrations(db)
    return AudioLangStore(db)


@pytest.fixture
def media(media_root):
    def put(canonical: str):
        p = media_root / canonical
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x")
        return p

    return put


def _ids(**kw):
    """The mapping the coverage snapshot supplies: path -> (sonarr, radarr)."""
    return kw


# ── the id is recorded with the verdict ──────────────────────────────


def test_a_verdict_remembers_the_episode_id_it_was_recorded_against(store):
    store.upsert(canonical_path=DAILY_OLD, lang_code="fr", sonarr_episode_id=4242)
    assert store.get(DAILY_OLD).sonarr_episode_id == 4242


def test_a_verdict_remembers_the_movie_id(store):
    store.upsert(canonical_path=MOVIE_OLD, lang_code="fr", radarr_movie_id=77)
    assert store.get(MOVIE_OLD).radarr_movie_id == 77


def test_a_verdict_without_an_id_still_works(store):
    store.upsert(canonical_path=MOVIE_OLD, lang_code="fr")
    v = store.get(MOVIE_OLD)
    assert v.sonarr_episode_id is None and v.radarr_movie_id is None


# ── carrying by id ───────────────────────────────────────────────────


def test_a_date_named_episode_follows_its_replacement(store, media):
    media(DAILY_NEW)  # the old file is gone; the new one is on disk
    store.upsert(canonical_path=DAILY_OLD, lang_code="fr", sonarr_episode_id=4242)
    moved = store.sweep_carry_overs(path_ids={DAILY_NEW: (4242, None)})
    assert moved == [(DAILY_OLD, DAILY_NEW)]
    assert store.get(DAILY_NEW).lang_code == "fr"
    assert store.get(DAILY_OLD) is None


def test_a_movie_follows_its_replacement(store, media):
    media(MOVIE_NEW)
    store.upsert(canonical_path=MOVIE_OLD, lang_code="it", radarr_movie_id=77)
    assert store.sweep_carry_overs(path_ids={MOVIE_NEW: (None, 77)}) == [(MOVIE_OLD, MOVIE_NEW)]
    assert store.get(MOVIE_NEW).lang_code == "it"


def test_the_carried_verdict_keeps_its_id(store, media):
    media(MOVIE_NEW)
    store.upsert(canonical_path=MOVIE_OLD, lang_code="it", radarr_movie_id=77)
    store.sweep_carry_overs(path_ids={MOVIE_NEW: (None, 77)})
    assert store.get(MOVIE_NEW).radarr_movie_id == 77


def test_the_carry_is_recorded_in_the_evidence(store, media):
    media(MOVIE_NEW)
    store.upsert(canonical_path=MOVIE_OLD, lang_code="it", radarr_movie_id=77)
    store.sweep_carry_overs(path_ids={MOVIE_NEW: (None, 77)})
    assert store.get(MOVIE_NEW).evidence.get("carried_from") == MOVIE_OLD


def test_a_verdict_moves_when_the_folder_was_renamed(store, media):
    """The id is the identity, so a rename inside the same library still carries
    — which is the whole point of matching on it rather than on the path."""
    renamed = "Movies/Perez. (2014) {tmdb-1}/Perez. (2014) Bluray-1080p.mkv"
    media(renamed)
    store.upsert(canonical_path=MOVIE_OLD, lang_code="it", radarr_movie_id=77)
    assert store.sweep_carry_overs(path_ids={renamed: (None, 77)}) == [(MOVIE_OLD, renamed)]


# ── when it must refuse ──────────────────────────────────────────────


def test_it_refuses_when_the_old_file_still_exists(store, media):
    media(MOVIE_OLD)
    media(MOVIE_NEW)
    store.upsert(canonical_path=MOVIE_OLD, lang_code="it", radarr_movie_id=77)
    assert store.sweep_carry_overs(path_ids={MOVIE_NEW: (None, 77)}) == []


def test_it_refuses_when_the_id_now_has_two_files(store, media):
    other = "Movies/Perez (2014)/Perez (2014) WEBRip-720p.mkv"
    media(MOVIE_NEW)
    media(other)
    store.upsert(canonical_path=MOVIE_OLD, lang_code="it", radarr_movie_id=77)
    assert store.sweep_carry_overs(path_ids={MOVIE_NEW: (None, 77), other: (None, 77)}) == []


def test_it_refuses_when_the_new_file_already_has_its_own_verdict(store, media):
    media(MOVIE_NEW)
    store.upsert(canonical_path=MOVIE_OLD, lang_code="it", radarr_movie_id=77)
    store.upsert(canonical_path=MOVIE_NEW, lang_code="fr")
    assert store.sweep_carry_overs(path_ids={MOVIE_NEW: (None, 77)}) == []
    assert store.get(MOVIE_NEW).lang_code == "fr"


def test_it_refuses_across_libraries(two_libraries, tmp_path):
    """The same Sonarr id in a second instance is a different file (#161).

    Uses a REAL second library: with an unconfigured slug the path fails to
    resolve and the carry would be refused for that reason instead, which would
    make this test pass without the library check existing at all.
    """
    from subarr.audio_lang_store import AudioLangStore
    from subarr.migrate import run_migrations

    db = tmp_path / "xlib.db"
    run_migrations(db)
    st = AudioLangStore(db)

    # The successor exists, on disk, in library 'disk2', carrying the same id.
    (two_libraries / "Movies" / "Perez (2014)").mkdir(parents=True, exist_ok=True)
    (two_libraries / "Movies" / "Perez (2014)" / "Perez (2014) Bluray-1080p.mkv").write_bytes(b"x")
    other_lib = "@disk2/Movies/Perez (2014)/Perez (2014) Bluray-1080p.mkv"

    st.upsert(canonical_path=MOVIE_OLD, lang_code="it", radarr_movie_id=77)
    assert st.sweep_carry_overs(path_ids={other_lib: (None, 77)}) == []
    assert st.get(MOVIE_OLD).lang_code == "it"


def test_a_sonarr_id_never_matches_a_radarr_id(store, media):
    media(MOVIE_NEW)
    store.upsert(canonical_path=DAILY_OLD, lang_code="fr", sonarr_episode_id=77)
    assert store.sweep_carry_overs(path_ids={MOVIE_NEW: (None, 77)}) == []


def test_a_verdict_with_no_id_is_left_to_the_token_match(store, media):
    media(MOVIE_NEW)
    store.upsert(canonical_path=MOVIE_OLD, lang_code="it")
    assert store.sweep_carry_overs(path_ids={MOVIE_NEW: (None, 77)}) == []


def test_the_token_match_still_works_without_any_ids(store, media):
    """#563 must not regress: the sweep is called with no mapping at all."""
    old = "TV/Flics/Season 2/Flics - S02E03 - Coup HDTV-1080p.mp4"
    new = "TV/Flics/Season 2/Flics - S02E03 - Coup HDTV-1080p.mkv"
    media(new)
    store.upsert(canonical_path=old, lang_code="fr")
    assert store.sweep_carry_overs() == [(old, new)]


# ── the back-fill ────────────────────────────────────────────────────


def test_backfill_gives_an_existing_verdict_its_id(store, media):
    media(MOVIE_OLD)
    store.upsert(canonical_path=MOVIE_OLD, lang_code="it")
    report = store.backfill_ids({MOVIE_OLD: (None, 77)})
    assert report["updated"] == 1
    assert store.get(MOVIE_OLD).radarr_movie_id == 77


def test_backfill_is_idempotent(store, media):
    media(MOVIE_OLD)
    store.upsert(canonical_path=MOVIE_OLD, lang_code="it")
    store.backfill_ids({MOVIE_OLD: (None, 77)})
    assert store.backfill_ids({MOVIE_OLD: (None, 77)})["updated"] == 0


def test_backfill_never_overwrites_an_id_already_recorded(store, media):
    media(MOVIE_OLD)
    store.upsert(canonical_path=MOVIE_OLD, lang_code="it", radarr_movie_id=77)
    store.backfill_ids({MOVIE_OLD: (None, 999)})
    assert store.get(MOVIE_OLD).radarr_movie_id == 77


def test_backfill_skips_a_verdict_whose_file_is_already_gone(store):
    """Nothing on disk to confirm the mapping is about THIS file, so the id
    would be a guess."""
    store.upsert(canonical_path=MOVIE_OLD, lang_code="it")
    report = store.backfill_ids({MOVIE_OLD: (None, 77)})
    assert report["updated"] == 0
    assert report["skipped_file_missing"] == 1
    assert store.get(MOVIE_OLD).radarr_movie_id is None


def test_backfill_reports_what_it_did(store, media):
    media(MOVIE_OLD)
    media(DAILY_OLD)
    store.upsert(canonical_path=MOVIE_OLD, lang_code="it")
    store.upsert(canonical_path=DAILY_OLD, lang_code="fr")
    report = store.backfill_ids({MOVIE_OLD: (None, 77), DAILY_OLD: (4242, None)})
    assert report["updated"] == 2
    assert report["considered"] == 2


# ── the snapshot mapping the sweep is fed ────────────────────────────


class _Snap:
    def __init__(self, items):
        self.items = items


def _row(path, media_type="episode", **bazarr):
    return {"file_canonical_path": path, "media_type": media_type, "bazarr": bazarr}


def test_an_episode_row_maps_to_its_episode_id():
    from subarr.coverage_cache import snapshot_path_ids

    snap = _Snap([_row(DAILY_NEW, sonarr_id=9, episode_id=4242)])
    assert snapshot_path_ids(snap) == {DAILY_NEW: (4242, None)}


def test_a_movie_row_maps_to_its_radarr_id():
    from subarr.coverage_cache import snapshot_path_ids

    assert snapshot_path_ids(_Snap([_row(MOVIE_NEW, "movie", radarr_id=77)])) == {MOVIE_NEW: (None, 77)}


def test_a_row_with_no_file_on_disk_is_not_mapped():
    """A wanted-but-absent episode has nothing to key a verdict to."""
    from subarr.coverage_cache import snapshot_path_ids

    row = _row(DAILY_NEW, episode_id=4242)
    row["file_canonical_path"] = None
    assert snapshot_path_ids(_Snap([row])) == {}


def test_a_row_with_no_id_is_not_mapped():
    from subarr.coverage_cache import snapshot_path_ids

    assert snapshot_path_ids(_Snap([_row(DAILY_NEW, sonarr_id=9)])) == {}


def test_an_episode_is_never_keyed_by_its_series_id():
    """Every episode of a show shares the series id: keying on it would make
    them all look like the same thing and carry verdicts between episodes."""
    from subarr.coverage_cache import snapshot_path_ids

    a = "TV/Daily Show/Season 2026/Daily Show - 2026-09-08.mkv"
    b = "TV/Daily Show/Season 2026/Daily Show - 2026-09-09.mkv"
    snap = _Snap([_row(a, sonarr_id=9, episode_id=1), _row(b, sonarr_id=9, episode_id=2)])
    assert snapshot_path_ids(snap) == {a: (1, None), b: (2, None)}


def test_an_empty_snapshot_maps_to_nothing():
    from subarr.coverage_cache import snapshot_path_ids

    assert snapshot_path_ids(None) == {}
    assert snapshot_path_ids(_Snap([])) == {}


# ── the seam: verifying through the API records the id ───────────────


def test_verifying_records_the_id_from_the_snapshot(app_with_stub):
    """The server resolves the id itself, so an older UI that sends none still
    produces a carry-able verdict."""
    from types import SimpleNamespace

    app = app_with_stub.app
    app.state.coverage_cache._cached = SimpleNamespace(items=[_row(DAILY_NEW, sonarr_id=9, episode_id=4242)])
    r = app_with_stub.post(
        "/api/audio-lang/verifications", json={"canonical_path": DAILY_NEW, "lang_code": "fr"}
    )
    assert r.status_code == 200
    assert app.state.audio_lang.get(DAILY_NEW).sonarr_episode_id == 4242


def test_verifying_still_works_with_no_snapshot(app_with_stub):
    app = app_with_stub.app
    app.state.coverage_cache._cached = None
    r = app_with_stub.post(
        "/api/audio-lang/verifications", json={"canonical_path": DAILY_NEW, "lang_code": "fr"}
    )
    assert r.status_code == 200
    assert app.state.audio_lang.get(DAILY_NEW).sonarr_episode_id is None


def test_re_verifying_without_an_id_keeps_the_one_already_recorded(store):
    """Review re-verifies by path. If that erased the id, a verdict would lose
    its carry-ability the moment the user corrected it."""
    store.upsert(canonical_path=MOVIE_OLD, lang_code="it", radarr_movie_id=77)
    store.upsert(canonical_path=MOVIE_OLD, lang_code="fr")
    v = store.get(MOVIE_OLD)
    assert (v.lang_code, v.radarr_movie_id) == ("fr", 77)

    # ...and the same for an episode: both columns are kept, not just one.
    store.upsert(canonical_path=DAILY_OLD, lang_code="fr", sonarr_episode_id=4242)
    store.upsert(canonical_path=DAILY_OLD, lang_code="de")
    e = store.get(DAILY_OLD)
    assert (e.lang_code, e.sonarr_episode_id) == ("de", 4242)


def test_it_refuses_when_the_snapshot_names_a_file_that_is_not_there(store):
    """The snapshot is up to 30 minutes old: the successor it names may itself
    have been replaced, and carrying onto a path with no file would strand the
    verdict where nothing can ever match it."""
    store.upsert(canonical_path=MOVIE_OLD, lang_code="it", radarr_movie_id=77)
    assert store.sweep_carry_overs(path_ids={MOVIE_NEW: (None, 77)}) == []
    assert store.get(MOVIE_OLD).lang_code == "it"


def test_backfill_does_not_overwrite_an_id_recorded_while_it_runs(store, media, monkeypatch):
    """The race the WHERE clause exists for: the verdict is read as having no
    id, then a verification records one before the UPDATE lands. Driven here by
    verifying from inside the file-exists check, which runs in exactly that
    window."""
    from subarr import paths as paths_mod

    media(MOVIE_OLD)
    store.upsert(canonical_path=MOVIE_OLD, lang_code="it")
    real = paths_mod.canonical_to_fs
    fired = []

    def racing(canonical):
        if canonical == MOVIE_OLD and not fired:
            fired.append(True)
            store.upsert(canonical_path=MOVIE_OLD, lang_code="it", radarr_movie_id=77)
        return real(canonical)

    monkeypatch.setattr(paths_mod, "canonical_to_fs", racing)
    store.backfill_ids({MOVIE_OLD: (None, 999)})
    assert fired, "the race window never opened; the test proves nothing"
    assert store.get(MOVIE_OLD).radarr_movie_id == 77
