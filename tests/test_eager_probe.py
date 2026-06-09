"""PR-C: eager-probe of unprobed (Bazarr-wanted) files.

The probe-gate hides every row whose file hasn't been probed yet
(verification_state != "verified"). The scheduled walk only probes the
user's configured probe_roots — so if a wanted file lives outside those
roots (or probe_roots is unset), its row stays "unprobed" forever and the
gap list is silently empty. Eager-probe closes that gap: after each
coverage build we probe exactly the unprobed rows' files, regardless of
probe_roots config.
"""

from __future__ import annotations

import asyncio

import pytest


# ---- target selection -------------------------------------------------


def test_eager_probe_targets_picks_only_unprobed_deduped():
    from subarr.coverage_cache import eager_probe_targets

    items = [
        {"canonical_path": "TV/A/a.mkv", "verification_state": "verified"},
        {"canonical_path": "TV/B/b.mkv", "verification_state": "unprobed"},
        {"canonical_path": "TV/B/b.mkv", "verification_state": "unprobed"},  # dup
        {"canonical_path": "TV/C/c.mkv", "verification_state": "probe_failed"},
        {"canonical_path": "", "verification_state": "unprobed"},  # no path
        {"verification_state": "unprobed"},  # missing key
    ]
    # only B is unprobed-with-path; probe_failed is excluded (already tried)
    assert eager_probe_targets(items) == ["TV/B/b.mkv"]


def test_eager_probe_targets_caps():
    from subarr.coverage_cache import eager_probe_targets

    items = [{"canonical_path": f"TV/X/{i}.mkv", "verification_state": "unprobed"} for i in range(50)]
    assert len(eager_probe_targets(items, cap=10)) == 10


def test_eager_probe_targets_skips_non_file_paths():
    """Rows whose canonical_path is a show/series FOLDER (the coverage
    engine couldn't resolve an episode file at build time) have nothing to
    probe. Probing the folder just errors ("not a file") and manufactures
    false 'couldn't analyze' rows — so they must be excluded."""
    from subarr.coverage_cache import eager_probe_targets

    items = [
        {"canonical_path": "TV/Klovn", "verification_state": "unprobed"},  # folder
        {"canonical_path": "TV/Klovn/", "verification_state": "unprobed"},  # folder w/ slash
        {"canonical_path": "TV/Show/S01E01.mkv", "verification_state": "unprobed"},  # real file
        {"canonical_path": "Movies/Film (2024)", "verification_state": "unprobed"},  # folder
        {"canonical_path": "Movies/Film (2024)/film.mp4", "verification_state": "unprobed"},  # file
    ]
    assert eager_probe_targets(items) == ["TV/Show/S01E01.mkv", "Movies/Film (2024)/film.mp4"]


# ---- ProbeWalker.probe_paths -----------------------------------------


@pytest.fixture
def walker(subarr_env, tmp_path):
    from subarr.migrate import run_migrations
    from subarr.probe_store import ProbeStore
    from subarr.probe_walker import ProbeWalker

    db = tmp_path / "probe.db"
    run_migrations(db)  # schema is migration-owned now (no init_schema)
    store = ProbeStore(db)
    return ProbeWalker(store), store


def _make_file(canonical: str, data: bytes = b"data"):
    from subarr.config import settings

    p = settings.media_root / canonical
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p


def _stub_probe(monkeypatch, duration=123.0):
    from subarr import probe_walker as pw
    from subarr.media_probe import ProbeResult

    async def fake_probe(path, timeout_s=30.0):
        pr = ProbeResult(canonical_path="")  # caller fills canonical_path
        pr.duration_s = duration
        return pr

    monkeypatch.setattr(pw, "probe", fake_probe)


async def _drain(state):
    while state.status == "running":
        await asyncio.sleep(0.01)
    return state


def test_probe_paths_probes_listed_files(walker, monkeypatch):
    wk, store = walker
    _make_file("TV/A/a.mkv")
    _make_file("TV/B/b.mkv")
    _stub_probe(monkeypatch)

    async def go():
        return await _drain(await wk.probe_paths(["TV/A/a.mkv", "TV/B/b.mkv"]))

    state = asyncio.run(go())
    assert state.status == "done"
    assert state.probed == 2
    assert store.get("TV/A/a.mkv") is not None
    assert store.get("TV/B/b.mkv") is not None


def test_probe_paths_skips_already_cached(walker, monkeypatch):
    wk, store = walker
    p = _make_file("TV/A/a.mkv")
    st = p.stat()
    from subarr.media_probe import ProbeResult

    store.upsert(
        canonical_path="TV/A/a.mkv",
        mtime=st.st_mtime,
        size=st.st_size,
        result=ProbeResult(canonical_path="TV/A/a.mkv"),
    )
    _stub_probe(monkeypatch)

    async def go():
        return await _drain(await wk.probe_paths(["TV/A/a.mkv"]))

    state = asyncio.run(go())
    assert state.cached_hits == 1
    assert state.probed == 0


def test_probe_paths_records_missing_file_as_error(walker, monkeypatch):
    wk, store = walker
    _stub_probe(monkeypatch)

    async def go():
        return await _drain(await wk.probe_paths(["TV/Ghost/missing.mkv"]))

    state = asyncio.run(go())
    assert state.status == "done"
    assert state.processed == 1
    assert state.probed == 0
    assert len(state.errors) == 1


def test_probe_paths_dedupes_concurrent_walk(walker, monkeypatch):
    wk, store = walker
    _make_file("TV/A/a.mkv")

    from subarr import probe_walker as pw
    from subarr.media_probe import ProbeResult

    async def slow_probe(path, timeout_s=30.0):
        await asyncio.sleep(0.05)
        return ProbeResult(canonical_path="")

    monkeypatch.setattr(pw, "probe", slow_probe)

    async def go():
        s1 = await wk.probe_paths(["TV/A/a.mkv"])
        s2 = await wk.probe_paths(["TV/A/a.mkv"])  # should reuse s1, not start a 2nd
        same = s1.id == s2.id
        await _drain(s1)
        return same

    assert asyncio.run(go()) is True


# ---- CoverageCache.refresh eager-probe trigger -----------------------


class _FakeReport:
    def __init__(self, items):
        self._items = items

    def to_dict(self):
        return {"items": self._items, "totals": {"items": len(self._items)}, "sources": {}}


def _patch_build(monkeypatch, items):
    from subarr import coverage_engine

    async def fake_build(*a, **k):
        return _FakeReport(items)

    monkeypatch.setattr(coverage_engine, "build_coverage", fake_build)


def test_refresh_fires_eager_probe_for_unprobed(subarr_env, tmp_path, monkeypatch):
    from subarr.coverage_cache import CoverageCache

    _patch_build(
        monkeypatch,
        [
            {"canonical_path": "TV/A/a.mkv", "verification_state": "unprobed", "media_type": "episode"},
            {"canonical_path": "TV/B/b.mkv", "verification_state": "verified", "media_type": "episode"},
        ],
    )

    calls = []

    class FakeWalker:
        async def probe_paths(self, paths, **k):
            calls.append(list(paths))

            class _S:
                id = "x"
                status = "done"

            return _S()

    from subarr.migrate import run_migrations

    db = tmp_path / "cov.db"
    run_migrations(db)
    cache = CoverageCache(db)
    asyncio.run(
        cache.refresh(
            bundle=None,
            probe_store=None,
            audio_lang_store=None,
            probe_walker=FakeWalker(),
        )
    )
    assert calls == [["TV/A/a.mkv"]]


def test_refresh_no_walker_does_not_crash(subarr_env, tmp_path, monkeypatch):
    from subarr.coverage_cache import CoverageCache

    _patch_build(
        monkeypatch,
        [
            {"canonical_path": "TV/A/a.mkv", "verification_state": "unprobed", "media_type": "episode"},
        ],
    )
    from subarr.migrate import run_migrations

    db = tmp_path / "cov.db"
    run_migrations(db)
    cache = CoverageCache(db)
    # No probe_walker passed — back-compat path, must just store + return.
    snap = asyncio.run(cache.refresh(bundle=None, probe_store=None, audio_lang_store=None))
    assert snap.item_count == 1


def test_eager_probe_targets_prefers_file_canonical_path():
    """Folder-row fix: when the Sonarr episode-file path is propagated onto
    file_canonical_path, eager-probe must target THAT (a real video file),
    not the folder-level canonical_path (which the file-only guard skips)."""
    from subarr.coverage_cache import eager_probe_targets

    items = [
        {
            "canonical_path": "TV/Klovn",
            "file_canonical_path": "TV/Klovn/Season 1/S01E01.mkv",
            "verification_state": "unprobed",
        },
        {"canonical_path": "TV/ILied", "verification_state": "unprobed"},  # folder only → skipped
    ]
    assert eager_probe_targets(items) == ["TV/Klovn/Season 1/S01E01.mkv"]
