"""#563: an audio-language verdict follows its episode when Sonarr replaces the
file (an upgrade, or `.mp4` -> `.mkv`).

Verdicts are stored against one exact path, so a replaced file had none and
subarr forwarded no override; subgen then read the new file's (wrong) tag and
skipped it. Seen live 2026-09-17 on Flics and Lolita Lobosco. A verdict now
moves to the new path, but only when:
  - its old file is really gone (two live files never share one),
  - exactly one video file for the same SxxExx exists in the series folder,
  - that file has no verdict of its own,
  - and every orphaned verdict for the episode agrees on the language.
Anything ambiguous moves nothing.
"""

from __future__ import annotations

import pytest

OLD = "TV/Flics/Season 2/Flics - S02E03 - Coup d'arret HDTV-1080p -Sonarr.mp4"
NEW = "TV/Flics/Season 2/Flics - S02E03 - Coup d'arret HDTV-1080p -Sonarr.mkv"


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


def _verdict(store, path, lang="fr", **kw):
    store.upsert(canonical_path=path, lang_code=lang, **kw)


def _paths(store):
    return set(store.all_paths())


# ---- helpers ---------------------------------------------------------------


@pytest.mark.parametrize(
    "name,key",
    [
        ("Show - S02E03 - x.mkv", (2, 3)),
        ("show.s02e03.720p.mkv", (2, 3)),
        ("Show - S1E10.mkv", (1, 10)),
        ("Show - 2026-09-08.mkv", None),
    ],
)
def test_episode_key(name, key):
    from subarr.audio_lang_store import episode_key

    assert episode_key(f"TV/Show/Season 2/{name}") == key


@pytest.mark.parametrize(
    "path,series",
    [
        ("TV/Show/Season 2/x.mkv", "TV/Show"),
        ("TV/Show/Season 02/x.mkv", "TV/Show"),
        ("TV/Show/Specials/x.mkv", "TV/Show"),
        ("TV/Show/x.mkv", "TV/Show"),
        ("TV/x.mkv", None),  # never the library root
    ],
)
def test_series_folder(path, series):
    from subarr.audio_lang_store import series_folder

    assert series_folder(path) == series


# ---- the acceptance case -----------------------------------------------------


def test_replaced_file_keeps_its_verified_language_at_queue_time(store, media):
    from subarr.audio_lang_store import resolve_audio_language_override

    _verdict(store, OLD, "fr")
    media(NEW)  # Sonarr replaced the .mp4 with a .mkv
    assert resolve_audio_language_override(store, NEW, caller="test") == "fr"
    assert _paths(store) == {NEW}  # re-keyed, so the next lookup is exact
    moved = store.get(NEW)
    assert moved.evidence["carried_from"] == OLD
    assert moved.source == "user"


def test_season_folder_rename_is_followed(store, media):
    old = "TV/Flics/Season 2/Flics - S02E03 - x.mp4"
    new = "TV/Flics/Season 02/Flics - S02E03 - x.mkv"
    _verdict(store, old, "fr")
    media(new)
    assert store.carry_over(new).lang_code == "fr"
    assert _paths(store) == {new}


# ---- safeguards -------------------------------------------------------------


def test_nothing_moves_while_the_old_file_still_exists(store, media):
    _verdict(store, OLD, "fr")
    media(OLD)
    media(NEW)
    assert store.carry_over(NEW) is None
    assert _paths(store) == {OLD}


def test_two_candidate_files_for_the_episode_move_nothing(store, media):
    _verdict(store, OLD, "fr")
    media(NEW)
    media("TV/Flics/Season 2/Flics - S02E03 - other release.mkv")
    assert store.carry_over(NEW) is None
    assert _paths(store) == {OLD}


def test_a_file_with_its_own_verdict_is_not_overwritten(store, media):
    _verdict(store, OLD, "fr")
    _verdict(store, NEW, "it")
    media(NEW)
    assert store.find_carry_over(NEW) is None  # checked directly, not only via get()
    assert store.carry_over(NEW) is None
    assert store.sweep_carry_overs() == []
    assert store.get(NEW, follow_replaced=True).lang_code == "it"
    assert _paths(store) == {OLD, NEW}


def test_a_verdict_whose_file_still_exists_is_never_moved(store, media):
    """Even when that file is not a video the successor walk counts (here a
    disc image), so the uniqueness rule alone would not stop it."""
    iso = "TV/Flics/Season 2/Flics - S02E03 - x.iso"
    _verdict(store, iso, "fr")
    media(iso)
    media(NEW)
    assert store.carry_over(NEW) is None
    assert _paths(store) == {iso}


def test_orphans_that_disagree_move_nothing(store, media):
    _verdict(store, OLD, "fr")
    _verdict(store, "TV/Flics/Season 2/Flics - S02E03 - older.avi", "it")
    media(NEW)
    assert store.carry_over(NEW) is None


def test_orphans_that_agree_move_the_most_recent(store, media, monkeypatch):
    import time

    older = "TV/Flics/Season 2/Flics - S02E03 - older.avi"
    t = [1000.0]
    monkeypatch.setattr(time, "time", lambda: t[0])
    _verdict(store, older, "fr", evidence={"n": 1})
    t[0] = 2000.0
    _verdict(store, OLD, "fr", evidence={"n": 2})
    media(NEW)
    moved = store.carry_over(NEW)
    assert moved.evidence["carried_from"] == OLD and moved.evidence["n"] == 2
    assert _paths(store) == {older, NEW}


def test_a_different_episode_is_not_matched(store, media):
    _verdict(store, "TV/Flics/Season 2/Flics - S02E04 - x.mp4", "fr")
    media(NEW)
    assert store.carry_over(NEW) is None


def test_a_different_series_is_not_matched(store, media):
    _verdict(store, "TV/Other/Season 2/Other - S02E03 - x.mp4", "fr")
    media(NEW)
    assert store.carry_over(NEW) is None


def test_a_tokenless_file_moves_nothing(store, media):
    _verdict(store, "TV/Daily/Season 1/Daily - 2026-09-07.mp4", "fr")
    new = "TV/Daily/Season 1/Daily - 2026-09-07.mkv"
    media(new)
    assert store.carry_over(new) is None


def test_an_unreadable_series_folder_moves_nothing(store, media):
    """If the share is not mounted every old file looks gone; that must not
    be read as a replacement."""
    _verdict(store, OLD, "fr")
    assert store.carry_over(NEW) is None  # NEW not on disk, series folder absent
    assert _paths(store) == {OLD}


def test_a_multilingual_verdict_moves_intact(store, media):
    _verdict(store, OLD, "de", lang_class="multi", lang_codes=["de", "fr"])
    media(NEW)
    moved = store.carry_over(NEW)
    assert moved.lang_class == "multi" and moved.lang_codes == ["de", "fr"]


def test_plain_get_does_not_touch_the_filesystem(store, media):
    """Only the queue-time override opts in; bulk readers stay exact."""
    _verdict(store, OLD, "fr")
    media(NEW)
    assert store.get(NEW) is None
    assert _paths(store) == {OLD}


def test_exact_verdict_still_beats_a_series_intent(store, media):
    _verdict(store, OLD, "fr")
    store.set_series_intent(series_prefix="TV/Flics/", lang_code="it")
    media(NEW)
    assert store.get(NEW, follow_replaced=True).lang_code == "fr"


# ---- sweep -------------------------------------------------------------------


def test_sweep_moves_every_unambiguous_orphan_once(store, media):
    _verdict(store, OLD, "fr")
    lolita_old = "TV/Lolita/Season 2/Lolita - S02E06 - Last act.mp4"
    lolita_new = "TV/Lolita/Season 2/Lolita - S02E06 - Last act.mkv"
    _verdict(store, lolita_old, "it")
    kept = "TV/Kept/Season 1/Kept - S01E01.mkv"
    _verdict(store, kept, "de")
    media(NEW)
    media(lolita_new)
    media(kept)
    moved = store.sweep_carry_overs()
    assert sorted(moved) == sorted([(OLD, NEW), (lolita_old, lolita_new)])
    assert _paths(store) == {NEW, lolita_new, kept}
    assert store.sweep_carry_overs() == []  # idempotent


def test_carried_lookup_names_the_previous_file(store, media):
    _verdict(store, OLD, "fr")
    media(NEW)
    store.carry_over(NEW)
    assert store.get_carried_lookup() == {NEW: OLD}


# ---- coverage note ----------------------------------------------------------


def test_coverage_row_says_the_language_was_carried_over():
    from subarr.coverage_engine import CoverageItem, _annotate_carried_verdicts

    it = CoverageItem(media_type="episode", title="Flics", canonical_path="TV/Flics", file_canonical_path=NEW)
    other = CoverageItem(
        media_type="episode", title="X", canonical_path="TV/X", file_canonical_path="TV/X/a.mkv"
    )
    _annotate_carried_verdicts([it, other], {NEW: OLD})
    assert any("carried over" in n and "Sonarr.mp4" in n for n in it.audio_label_notes)
    assert other.audio_label_notes == []


# ---- end to end: requeue forwards the override ------------------------------


def test_requeue_of_a_replaced_file_forwards_the_verified_language(app_with_stub, media_root, monkeypatch):
    """The issue's acceptance: the replaced file is queued with the verified
    language as its override. The feeder is held so the job stays inspectable."""
    from subarr.app import app

    monkeypatch.setattr(app.state.queue_feeder, "kick", lambda *a, **k: None)
    old = "TV/Flics/Season 2/Flics - S02E03 - x.mp4"
    new = "TV/Flics/Season 2/Flics - S02E03 - x.mkv"
    (media_root / new).parent.mkdir(parents=True, exist_ok=True)
    (media_root / new).write_bytes(b"x")
    app.state.audio_lang.upsert(canonical_path=old, lang_code="fr")
    r = app_with_stub.post("/api/queue/requeue", json={"path": new})
    assert r.status_code == 202, r.text
    jobs = [j for j in app.state.pending_queue.list() if j.canonical_path == new]
    assert jobs and jobs[0].audio_language_override == "fr"
    assert app.state.audio_lang.get(new).evidence["carried_from"] == old


@pytest.mark.asyncio
async def test_coverage_build_shows_the_carried_over_note(coverage_bundle, tmp_path):
    """Wiring: a full build reads the carried lookup and notes the row."""
    from subarr.audio_lang_store import AudioLangStore
    from subarr.coverage_engine import build_coverage
    from subarr.migrate import run_migrations

    # Reuse the characterization suite's mock Sonarr/Bazarr/Radarr. Loaded by
    # path: tests/ is not a package, so a `tests.` import only works when the
    # repo root happens to be on sys.path.
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        "_coverage_characterization_mocks", Path(__file__).with_name("test_coverage_characterization.py")
    )
    mocks = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mocks)
    _bazarr, _radarr, _sonarr = mocks._bazarr, mocks._radarr, mocks._sonarr

    db = tmp_path / "wiring.db"
    run_migrations(db)
    als = AudioLangStore(db)
    current = "TV/Severance/Season 1/Severance.S01E01.mkv"
    als.upsert(
        canonical_path=current,
        lang_code="en",
        evidence={"carried_from": "TV/Severance/Season 1/Severance.S01E01.mp4"},
    )
    bundle = coverage_bundle(sonarr_handler=_sonarr, bazarr_handler=_bazarr, radarr_handler=_radarr)
    report = await build_coverage(
        bundle, use_tautulli=False, probe_store=None, audio_lang_store=als, subgen_caps=None
    )
    rows = [i for i in report.to_dict()["items"] if i.get("file_canonical_path") == current]
    assert rows, "the Severance row should resolve to its file"
    assert any("carried over" in n and "Severance.S01E01.mp4" in n for n in rows[0]["audio_label_notes"])


# ---- the manual orphan prune carries before it deletes ----------------------


def test_orphan_prune_carries_a_replaced_verdict_instead_of_deleting_it(app_with_stub, media_root):
    c = app_with_stub
    als = c.app.state.audio_lang
    present = [f"TV/Keep/Season 1/Keep - S01E0{i}.mkv" for i in range(1, 7)]
    for rel in present + [NEW]:
        (media_root / rel).parent.mkdir(parents=True, exist_ok=True)
        (media_root / rel).write_bytes(b"x")
    for rel in present:
        als.upsert(canonical_path=rel, lang_code="en")
    als.upsert(canonical_path=OLD, lang_code="fr")

    dry = c.get("/api/admin/db/orphans").json()
    assert OLD in dry["missing"] and dry["carried_over"] == 0
    assert als.get(OLD) is not None  # the dry run moves nothing

    body = c.post("/api/admin/db/orphans/prune").json()
    assert body["carried_over"] == 1
    assert als.get(OLD) is None
    assert als.get(NEW).lang_code == "fr"  # moved, not deleted


# ---- coverage refresh runs the sweep, throttled -----------------------------


def test_coverage_refresh_sweeps_at_most_every_interval(subarr_env, tmp_path, monkeypatch):
    import asyncio

    from subarr import coverage_cache as cc
    from subarr import coverage_engine
    from subarr.migrate import run_migrations

    class _Report:
        def to_dict(self):
            return {"items": [], "totals": {}, "sources": {}}

    async def fake_build(*a, **k):
        return _Report()

    monkeypatch.setattr(coverage_engine, "build_coverage", fake_build)
    db = tmp_path / "cov.db"
    run_migrations(db)
    cache = cc.CoverageCache(db)

    calls = []

    class _Store:
        def sweep_carry_overs(self):
            calls.append(1)
            return [("a", "b")]

    now = [10_000.0]
    monkeypatch.setattr(cc.time, "time", lambda: now[0])

    def refresh():
        asyncio.run(cache.refresh(bundle=None, probe_store=None, audio_lang_store=_Store()))

    refresh()
    refresh()  # same instant: throttled
    assert len(calls) == 1
    now[0] += cc.CARRY_SWEEP_INTERVAL_S
    refresh()
    assert len(calls) == 2


def test_a_failing_sweep_does_not_block_the_build(subarr_env, tmp_path, monkeypatch):
    import asyncio

    from subarr import coverage_cache as cc
    from subarr import coverage_engine
    from subarr.migrate import run_migrations

    built = []

    class _Report:
        def to_dict(self):
            return {"items": [], "totals": {}, "sources": {}}

    async def fake_build(*a, **k):
        built.append(1)
        return _Report()

    monkeypatch.setattr(coverage_engine, "build_coverage", fake_build)
    db = tmp_path / "cov.db"
    run_migrations(db)
    cache = cc.CoverageCache(db)

    class _Store:
        def sweep_carry_overs(self):
            raise OSError("share went away")

    asyncio.run(cache.refresh(bundle=None, probe_store=None, audio_lang_store=_Store()))
    assert built == [1]
