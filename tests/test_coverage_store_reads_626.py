"""#626: build_coverage's remaining synchronous SQLite reads must run off-loop.

The verification lookup and the five post-pass lookups used to hit the
audio_lang_store directly on the event loop, stalling it on a large library.
These tests pin the behaviour that must survive the move: the None-store fast
path, per-read failure fallbacks, application order, and that every read
actually runs in a worker thread.
"""

from __future__ import annotations

import asyncio
import importlib.util
import logging
from pathlib import Path

import pytest


def _on_loop() -> bool:
    try:
        asyncio.get_running_loop()
        return True
    except RuntimeError:
        return False


def _spy(calls: list[bool], real):
    def wrapper(*a, **k):
        calls.append(_on_loop())
        return real(*a, **k)

    return wrapper


# ── harness ────────────────────────────────────────────────────────────────

# tests/ is not a package, so reuse the characterization suite's mock arr
# handlers by path (the same trick test_verdict_carry_over_563 uses).
_FLICS_FILE = "TV/Flics/Season 1/Flics.S01E01.mkv"
_MOVIE_PATH = "Movies/Dune (2021)/Dune.2021.mkv"
_SEVERANCE = "TV/Severance/Season 1/Severance.S01E01.mkv"


def _mocks():
    spec = importlib.util.spec_from_file_location(
        "_coverage_characterization_mocks_626", Path(__file__).with_name("test_coverage_characterization.py")
    )
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@pytest.fixture
def als(subarr_env, tmp_path):
    from subarr.audio_lang_store import AudioLangStore
    from subarr.migrate import run_migrations

    db = tmp_path / "al626.db"
    run_migrations(db)
    return AudioLangStore(db)


@pytest.fixture
def probe_store(subarr_env, tmp_path):
    from subarr.migrate import run_migrations
    from subarr.probe_store import ProbeStore

    db = tmp_path / "probe626.db"
    run_migrations(db)
    return ProbeStore(db)


@pytest.fixture
def bundle(coverage_bundle):
    m = _mocks()
    return coverage_bundle(sonarr_handler=m._sonarr, bazarr_handler=m._bazarr, radarr_handler=m._radarr)


# The characterization dataset's Flics episodes are not Bazarr-wanted, so add
# one wanted row: a track-mismatch dismissal needs a row to apply to.
_FLICS_WANTED = {
    "sonarrSeriesId": 11,
    "sonarrEpisodeId": 1011,
    "seriesTitle": "Flics",
    "episodeTitle": "Pilot",
    "episode_number": "1x1",
    "missing_subtitles": [{"code2": "en", "name": "English"}],
}


@pytest.fixture
def mismatch_bundle(coverage_bundle):
    """The characterization dataset plus a wanted Flics episode (a French show
    whose probe says German-default / French-original, #159's mismatch case)."""
    import httpx

    m = _mocks()

    def bazarr(req):
        if req.url.path == "/api/episodes/wanted":
            data = list(m._BZ_EPS) + [_FLICS_WANTED]
            return httpx.Response(200, json={"data": data, "total": len(data)})
        return m._bazarr(req)

    return coverage_bundle(sonarr_handler=m._sonarr, bazarr_handler=bazarr, radarr_handler=m._radarr)


async def _build(bundle, store, probe_store=None):
    from subarr.coverage_engine import build_coverage

    return await build_coverage(
        bundle, use_tautulli=False, probe_store=probe_store, audio_lang_store=store, subgen_caps=None
    )


def _rows(report) -> dict[str, dict]:
    out = {}
    for it in report.to_dict()["items"]:
        p = it.get("file_canonical_path") or it.get("canonical_path")
        out[p] = it
    return out


def _seed_mismatch(probe_store):
    """A default-track mismatch on the French Flics episode: German default,
    French original to swap to (#159)."""
    from subarr.media_probe import AudioStream, ProbeResult

    probe = ProbeResult(
        canonical_path=_FLICS_FILE,
        audio=[
            AudioStream(index=1, language="ger", codec="aac", title=None, default=True),
            AudioStream(index=2, language="fre", codec="aac", title=None, default=False),
        ],
    )
    probe_store.upsert(canonical_path=_FLICS_FILE, mtime=1.0, size=1, result=probe)


# ── a. audio_lang_store is None: no store reads, no thread hop ─────────────


def test_none_store_takes_no_store_read_or_thread_hop(bundle, monkeypatch):
    from subarr import coverage_engine as ce

    hopped: list = []
    real_to_thread = ce.asyncio.to_thread

    async def spy(fn, *a, **k):
        hopped.append(fn)
        return await real_to_thread(fn, *a, **k)

    monkeypatch.setattr(ce.asyncio, "to_thread", spy)

    report = asyncio.run(_build(bundle, None))

    assert report.to_dict()["items"], "premise: the mock dataset still builds rows"
    assert ce._build_verification_lookup not in hopped, "None store hopped a thread for the lookup"
    assert ce._read_post_pass_lookups not in hopped, "None store hopped a thread for the post-pass"
    # The report is still sorted by score (the last step of the build).
    scores = [i["score"] for i in report.to_dict()["items"]]
    assert scores == sorted(scores, reverse=True)


# ── b. per-read failure handling ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_failing_multi_lookup_does_not_stop_the_other_post_passes(
    mismatch_bundle, als, probe_store, monkeypatch, caplog
):
    from subarr import coverage_engine as ce

    _seed_mismatch(probe_store)
    als.dismiss_track_mismatch(_FLICS_FILE)
    als.ignore_title(_MOVIE_PATH)

    def boom():
        raise RuntimeError("no multi lookup today")

    monkeypatch.setattr(als, "get_all_multi_as_lookup", boom)
    applied: list = []
    real_apply = ce._apply_multilingual_verifications

    def spy_apply(items, multi):
        applied.append(multi)
        return real_apply(items, multi)

    monkeypatch.setattr(ce, "_apply_multilingual_verifications", spy_apply)

    with caplog.at_level(logging.WARNING, logger="subarr.coverage_engine"):
        report = await _build(mismatch_bundle, als, probe_store=probe_store)

    rows = _rows(report)
    assert any(
        "multilingual lookup failed; skipping suppression this build" in r.message for r in caplog.records
    )
    assert applied == [None], "the failed map must still reach the suppression pass as None"
    # The ignored title was still dropped and the dismissal still cleared.
    assert _MOVIE_PATH not in rows
    assert report.to_dict()["sources"]["coverage"]["ignored_titles"] == 1
    assert rows[_FLICS_FILE]["default_track_mismatch"] is False


@pytest.mark.asyncio
async def test_failing_carried_lookup_skips_notes_but_applies_the_rest(bundle, als, monkeypatch, caplog):
    # Severance is the dataset's Bazarr-wanted episode — a row always exists.
    als.upsert(
        canonical_path=_SEVERANCE,
        lang_code="en",
        evidence={"carried_from": "TV/Severance/Season 1/Severance.S01E01.mp4"},
    )
    als.ignore_title(_MOVIE_PATH)

    def boom():
        raise RuntimeError("no carried lookup today")

    monkeypatch.setattr(als, "get_carried_lookup", boom)

    with caplog.at_level(logging.WARNING, logger="subarr.coverage_engine"):
        report = await _build(bundle, als)

    rows = _rows(report)
    assert any(
        "carried-verdict lookup failed; skipping notes this build" in r.message for r in caplog.records
    )
    assert not any("carried over" in n for n in rows[_SEVERANCE]["audio_label_notes"])
    # A later read still applied.
    assert _MOVIE_PATH not in rows


@pytest.mark.asyncio
async def test_failing_carried_annotation_does_not_block_the_build(bundle, als, monkeypatch, caplog):
    # The pre-#626 try covered the annotation as well as the read: a note must
    # never block the build, whichever half of it fails.
    from subarr import coverage_engine as ce

    als.ignore_title(_MOVIE_PATH)

    def boom(items, carried):
        raise RuntimeError("annotation broke")

    monkeypatch.setattr(ce, "_annotate_carried_verdicts", boom)

    with caplog.at_level(logging.WARNING, logger="subarr.coverage_engine"):
        report = await _build(bundle, als)

    assert any(
        "carried-verdict lookup failed; skipping notes this build" in r.message for r in caplog.records
    )
    assert _MOVIE_PATH not in _rows(report), "the passes after the notes must still run"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failing",
    ["get_mixed_dismissed_set", "get_track_mismatch_dismissed_set", "get_ignored_titles_set"],
)
async def test_dismissal_read_failure_degrades_to_empty_without_a_log(
    mismatch_bundle, als, probe_store, monkeypatch, caplog, failing
):
    _seed_mismatch(probe_store)
    als.dismiss_track_mismatch(_FLICS_FILE)
    als.ignore_title(_MOVIE_PATH)

    def boom():
        raise RuntimeError("no dismissal lookup today")

    monkeypatch.setattr(als, failing, boom)

    with caplog.at_level(logging.WARNING, logger="subarr.coverage_engine"):
        report = await _build(mismatch_bundle, als, probe_store=probe_store)

    rows = _rows(report)
    # A failing set read is silent (as before) …
    assert not any("lookup failed" in r.message for r in caplog.records)
    # … and the OTHER reads still took effect.
    if failing != "get_ignored_titles_set":
        assert _MOVIE_PATH not in rows
    if failing != "get_track_mismatch_dismissed_set":
        assert rows[_FLICS_FILE]["default_track_mismatch"] is False


# ── c. application order is unchanged ──────────────────────────────────────


@pytest.mark.asyncio
async def test_post_pass_application_order_is_unchanged(mismatch_bundle, als, probe_store, monkeypatch):
    from subarr import coverage_engine as ce

    _seed_mismatch(probe_store)
    als.dismiss_track_mismatch(_FLICS_FILE)
    als.ignore_title(_MOVIE_PATH)

    order: list[str] = []

    def _record(name):
        real = getattr(ce, name)

        def wrapper(*a, **k):
            order.append(name)
            return real(*a, **k)

        monkeypatch.setattr(ce, name, wrapper)

    for name in (
        "_refine_audio_sources",
        "_apply_multilingual_verifications",
        "_annotate_carried_verdicts",
        "_flag_mixed_language_series",
        "_drop_ignored_titles",
    ):
        _record(name)

    report = await _build(mismatch_bundle, als, probe_store=probe_store)

    assert order == [
        "_refine_audio_sources",
        "_apply_multilingual_verifications",
        "_annotate_carried_verdicts",
        "_flag_mixed_language_series",
        "_drop_ignored_titles",
    ]
    # Sorting by score is still the very last step.
    scores = [i["score"] for i in report.to_dict()["items"]]
    assert scores == sorted(scores, reverse=True)


# ── d. the verification lookup has no error net ────────────────────────────


@pytest.mark.asyncio
async def test_verification_lookup_failure_propagates(bundle, als, monkeypatch):
    def boom():
        raise RuntimeError("boom")

    monkeypatch.setattr(als, "get_all_as_lookup", boom)

    with pytest.raises(RuntimeError, match="boom"):
        await _build(bundle, als)


# ── e. every read runs off the event loop ──────────────────────────────────

_READS = (
    "get_all_as_lookup",
    "get_all_sources_as_lookup",
    "list_series_intents",
    "get_all_multi_as_lookup",
    "get_carried_lookup",
    "get_mixed_dismissed_set",
    "get_track_mismatch_dismissed_set",
    "get_ignored_titles_set",
)


@pytest.mark.asyncio
@pytest.mark.parametrize("name", _READS)
async def test_store_read_runs_off_the_event_loop(bundle, als, monkeypatch, name):
    calls: list[bool] = []
    monkeypatch.setattr(als, name, _spy(calls, getattr(als, name)))

    await _build(bundle, als)

    assert calls, f"{name} was never called"
    assert not any(calls), f"{name} ran on the event loop"
