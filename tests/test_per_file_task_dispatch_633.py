"""#633: dispatch. The feeder decides the task per file and the runner sends it.

Gate and dispatch must agree, so the agreement test drives both real functions
over every row of the shared contract fixture.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

FIXTURE = Path(__file__).parent / "fixtures" / "subgen_track_choice_cases.json"
CASES = json.loads(FIXTURE.read_text(encoding="utf-8"))["cases"]


def _caps(**kw):
    base = dict(
        per_request_task=True,
        preferred_audio_languages=("en",),
        preferred_audio_languages_unreadable=(),
        force_detected_language_to=None,
        skip_audio_languages=None,
        transcribe_or_translate="transcribe",
        bypass_skip=False,
        reachable=True,
        has_batch=True,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _probe(tracks, path="TV/Show/ep.mkv"):
    from subarr.media_probe import AudioStream, ProbeResult

    return ProbeResult(
        canonical_path=path,
        audio=[
            AudioStream(index=i, language=lang, codec="aac", title=None, default=d)
            for i, (lang, d) in enumerate(tracks, start=1)
        ],
    )


class _Probes:
    def __init__(self, mapping=None, raises=False):
        self.mapping = mapping or {}
        self.raises = raises

    def get(self, canonical, *a, **k):
        if self.raises:
            raise RuntimeError("probe store down")
        return self.mapping.get(canonical)


class _Store:
    """Stands in for AudioLangStore: a verified single-language verdict."""

    def __init__(self, mapping):
        self.mapping = mapping

    def get(self, canonical, follow_replaced=False):
        lang = self.mapping.get(canonical)
        if lang is None:
            return None
        return SimpleNamespace(
            lang_code=lang, source="user", confidence=1.0, lang_class="single", evidence={}, lang_codes=[lang]
        )


def _p1(p):
    from subarr.per_file_task import subgen_track_language

    if p is None:
        return None
    return p if len(p) == 2 else subgen_track_language(p)


# ---- resolve_task_for_file ---------------------------------------------------


def test_resolve_task_for_file_translates_ja_into_en():
    from subarr.submission_language import resolve_task_for_file

    probes = _Probes({"TV/Show/ep.mkv": _probe([("jpn", True)])})
    assert (
        resolve_task_for_file(
            "TV/Show/ep.mkv",
            override=None,
            caps=_caps(),
            probe_store=probes,
            enabled=True,
            wanted=["en", "es"],
        )
        == "translate"
    )


def test_resolve_task_for_file_is_none_when_inert_or_unreadable():
    from subarr.submission_language import resolve_task_for_file

    ok = {"TV/Show/ep.mkv": _probe([("jpn", True)])}
    kw = dict(override=None, wanted=["en"])
    # feature off, no per_request_task, no list
    assert (
        resolve_task_for_file("TV/Show/ep.mkv", caps=_caps(), probe_store=_Probes(ok), enabled=False, **kw)
        is None
    )
    assert (
        resolve_task_for_file(
            "TV/Show/ep.mkv", caps=_caps(per_request_task=False), probe_store=_Probes(ok), enabled=True, **kw
        )
        is None
    )
    assert (
        resolve_task_for_file(
            "TV/Show/ep.mkv",
            caps=_caps(preferred_audio_languages=None),
            probe_store=_Probes(ok),
            enabled=True,
            **kw,
        )
        is None
    )
    # missing probe, raising probe store, no probe store: never raises
    assert (
        resolve_task_for_file("TV/Show/ep.mkv", caps=_caps(), probe_store=_Probes(), enabled=True, **kw)
        is None
    )
    assert (
        resolve_task_for_file(
            "TV/Show/ep.mkv", caps=_caps(), probe_store=_Probes(raises=True), enabled=True, **kw
        )
        is None
    )
    assert resolve_task_for_file("TV/Show/ep.mkv", caps=_caps(), probe_store=None, enabled=True, **kw) is None


def test_resolve_task_for_file_with_a_probe_of_no_audio_is_none():
    from subarr.submission_language import resolve_task_for_file

    probes = _Probes({"TV/Show/ep.mkv": _probe([])})
    assert (
        resolve_task_for_file(
            "TV/Show/ep.mkv", override=None, caps=_caps(), probe_store=probes, enabled=True, wanted=["en"]
        )
        is None
    )


def test_resolve_task_for_file_logs_one_info_line(caplog):
    from subarr.submission_language import resolve_task_for_file

    probes = _Probes({"TV/Show/ep.mkv": _probe([("jpn", True)])})
    with caplog.at_level(logging.INFO, logger="subarr.submission_language"):
        resolve_task_for_file(
            "TV/Show/ep.mkv", override=None, caps=_caps(), probe_store=probes, enabled=True, wanted=["en"]
        )
    lines = [r for r in caplog.records if "per-file task" in r.getMessage()]
    assert len(lines) == 1
    assert "translate" in lines[0].getMessage()


# ---- agreement: gate == dispatch, over the whole contract --------------------


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
@pytest.mark.parametrize("wanted", [("en",), ("es",), ("en", "es"), ("fr", "en"), ("ja", "ko")])
def test_gate_and_dispatch_agree_on_the_task(case, wanted):
    from subarr.coverage_engine import CoverageItem, _score
    from subarr.per_file_task import active_config
    from subarr.subgen_client import _parse_preferred_audio_languages
    from subarr.submission_language import resolve_task_for_file

    path = "TV/Show/ep.mkv"
    tracks = [(t, d) for t, d in case["tracks"]]
    caps = _caps(
        preferred_audio_languages=_parse_preferred_audio_languages(case["preferred_advertised"])[0],
        force_detected_language_to=_p1(case["force_detected"]),
    )
    forced = case["forced"]

    # dispatch
    dispatch = resolve_task_for_file(
        path,
        override=forced,
        caps=caps,
        probe_store=_Probes({path: _probe(tracks)}),
        enabled=True,
        wanted=list(wanted),
    )

    # gate
    cfg = active_config(True, list(wanted), caps)
    it = CoverageItem(title="Show", media_type="episode", canonical_path="TV/Show", file_canonical_path=path)
    it.missing_subtitles = list(wanted)
    it.audio_tracks = tracks
    it.audio_langs = [t for t, _ in tracks if t]
    it.verification_state = "verified"
    _score(it, {}, subgen_output_task="transcribe", per_file_task=cfg, per_file_forced=lambda p: forced)
    gate = None
    for r in it.score_reasons:
        if r.startswith("per-file task: "):
            gate = r.split(": ", 1)[1].split(" ", 1)[0]
    assert gate == dispatch


# ---- the real resolvers, through the real override resolver -------------------


def test_forced_override_is_L_in_gate_and_dispatch():
    """A verified language differing from the tracks wins at both ends, and both
    obtain it from the same resolver call."""
    from subarr.audio_lang_store import resolve_audio_language_override
    from subarr.coverage_engine import _per_file_forced_lookup
    from subarr.submission_language import resolve_for_job, resolve_task_for_file

    path = "TV/Show/ep.mkv"
    store = _Store({path: "fr"})
    caps = _caps(skip_audio_languages=("en",))
    probes = _Probes({path: _probe([("eng", True)])})
    job = SimpleNamespace(canonical_path=path, audio_language_override=None, bypass_skip=False)
    decision = resolve_for_job(job, store=store, caps=caps, probe_store=probes)
    assert decision.override == "fr"
    lookup = _per_file_forced_lookup(store, caps)
    assert lookup(path) == resolve_audio_language_override(store, path, skip_audio_languages=("en",)) == "fr"
    task = resolve_task_for_file(
        path, override=decision.override, caps=caps, probe_store=probes, enabled=True, wanted=["en"]
    )
    assert task == "translate", "fr audio, only en wanted: translate"


def test_forced_lookup_without_a_store_is_none():
    from subarr.coverage_engine import _per_file_forced_lookup

    assert _per_file_forced_lookup(None, _caps())("x") is None


def test_documented_divergence_stored_override_wins_at_dispatch():
    """KNOWN, pinned: a job queued with its own audio_language_override keeps it
    ("decided when queued"), while the gate predicted from the store's CURRENT
    verdict. The two can disagree until the job is requeued."""
    from subarr.coverage_engine import _per_file_forced_lookup
    from subarr.submission_language import resolve_for_job, resolve_task_for_file

    path = "TV/Show/ep.mkv"
    store = _Store({path: "es"})  # verdict changed to es after queueing
    caps = _caps()
    probes = _Probes({path: _probe([("jpn", True)])})
    job = SimpleNamespace(canonical_path=path, audio_language_override="fr", bypass_skip=False)
    decision = resolve_for_job(job, store=store, caps=caps, probe_store=probes)
    assert decision.override == "fr"
    dispatch = resolve_task_for_file(
        path, override=decision.override, caps=caps, probe_store=probes, enabled=True, wanted=["es"]
    )
    gate_forced = _per_file_forced_lookup(store, caps)(path)
    assert gate_forced == "es"
    gate = resolve_task_for_file(
        path, override=gate_forced, caps=caps, probe_store=probes, enabled=True, wanted=["es"]
    )
    assert dispatch is None  # fr audio, es wanted, no en: no task
    assert gate == "transcribe"  # es audio: transcribe
    assert dispatch != gate


# ---- runner: the task reaches batch(), only while the caps allow it ----------


class _FakeSubgen:
    def __init__(self):
        self.calls = []

    async def batch(self, directory, **kw):
        self.calls.append((directory, kw))
        return 200, {"walked": 1, "queued": 1, "skipped": 0, "already_in_queue": 0}


def _runner(tmp_path, caps_holder):
    from subarr.migrate import run_migrations
    from subarr.scan_runner import ScanRunner
    from subarr.scan_store import ScanStore

    db = tmp_path / "r.db"
    run_migrations(db)
    store = ScanStore(db)
    sg = _FakeSubgen()
    return (
        ScanRunner(store=store, subgen_provider=lambda: sg, caps_provider=lambda: caps_holder["caps"]),
        store,
        sg,
    )


async def _wait_done(store, scan_id):
    for _ in range(200):
        s = store.get(scan_id)
        if s and s.status in ("done", "error"):
            return s
        await asyncio.sleep(0.01)
    raise AssertionError("scan did not finish")


@pytest.mark.asyncio
async def test_runner_sends_each_path_its_own_task(tmp_path):
    holder = {"caps": _caps()}
    runner, store, sg = _runner(tmp_path, holder)
    scan = store.create(["TV/Show/a.mkv", "TV/Show/b.mkv", "TV/Show/c.mkv"], reverse=False)
    runner.start(
        scan, path_tasks={"TV/Show/a.mkv": "transcribe", "TV/Show/b.mkv": "translate", "TV/Show/c.mkv": None}
    )
    await _wait_done(store, scan.id)
    assert [c[1]["task"] for c in sg.calls] == ["transcribe", "translate", None]
    assert scan.id not in runner._path_tasks, "cleaned up with the scan"


@pytest.mark.asyncio
async def test_runner_without_path_tasks_sends_none(tmp_path):
    holder = {"caps": _caps()}
    runner, store, sg = _runner(tmp_path, holder)
    scan = store.create(["TV/Show/a.mkv"], reverse=False)
    runner.start(scan)
    await _wait_done(store, scan.id)
    assert sg.calls[0][1]["task"] is None


@pytest.mark.asyncio
async def test_caps_losing_per_request_task_mid_scan_sends_no_task(tmp_path):
    holder = {"caps": _caps()}
    runner, store, sg = _runner(tmp_path, holder)
    scan = store.create(["TV/Show/a.mkv"], reverse=False)
    holder["caps"] = _caps(per_request_task=False)  # refreshed between start and batch
    runner.start(scan, path_tasks={"TV/Show/a.mkv": "translate"})
    await _wait_done(store, scan.id)
    assert sg.calls[0][1]["task"] is None


@pytest.mark.asyncio
async def test_caps_not_yet_probed_sends_no_task(tmp_path):
    holder = {"caps": None}
    runner, store, sg = _runner(tmp_path, holder)
    scan = store.create(["TV/Show/a.mkv"], reverse=False)
    runner.start(scan, path_tasks={"TV/Show/a.mkv": "translate"})
    await _wait_done(store, scan.id)
    assert sg.calls[0][1]["task"] is None


# ---- the real feeder closure -------------------------------------------------


@contextmanager
def _settings(enabled, wanted):
    from subarr import config

    prior = (config.settings.per_file_task_enabled, config.settings.per_file_task_wanted)
    object.__setattr__(config.settings, "per_file_task_enabled", enabled)
    object.__setattr__(config.settings, "per_file_task_wanted", list(wanted))
    try:
        yield
    finally:
        object.__setattr__(config.settings, "per_file_task_enabled", prior[0])
        object.__setattr__(config.settings, "per_file_task_wanted", prior[1])


class _Rec:
    def __init__(self):
        self.batches: list[tuple[str, str | None]] = []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        if req.url.path == "/status":
            return httpx.Response(200, json={"version": "Subgen 2026.05.3 (test)"})
        if req.url.path == "/queue":
            return httpx.Response(
                200,
                json={
                    "queued": [],
                    "processing": [],
                    "queued_count": 0,
                    "processing_count": 0,
                    "idle": True,
                    "version": "test",
                },
            )
        if req.url.path == "/batch":
            self.batches.append((req.url.params.get("directory"), req.url.params.get("task")))
            return httpx.Response(
                200, json={"walked": 1, "queued": 1, "skipped": 0, "already_in_queue": 0, "no_audio": 0}
            )
        return httpx.Response(404)


_REC = _Rec()


@pytest.fixture
def rec():
    _REC.batches.clear()
    return _REC


@pytest.fixture
def client(request, rec, subarr_env):
    """app_with_stub, but with the recording subgen handler."""
    request.node.add_marker(pytest.mark.subgen(handler=rec))
    return request.getfixturevalue("app_with_stub")


def _setup(client, *, media_root, tracks_by_file, caps=None, extra_files=()):
    """Create files under media_root, probe them, set caps. Returns state."""
    st = client.app.state
    for rel in list(tracks_by_file) + list(extra_files):
        p = media_root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"")
    for rel, tracks in tracks_by_file.items():
        st.probe_store.upsert(canonical_path=rel, mtime=1.0, size=1, result=_probe(tracks, rel))
    st.subgen_caps = caps or _caps()
    return st


def _submit(client, job):
    return client.portal.call(client.app.state.queue_feeder._submit_job, job)


def _enqueue(st, path, **kw):
    return st.pending_queue.enqueue(path, source="gaps", **kw)


def _wait_batches(rec, n, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end and len(rec.batches) < n:
        time.sleep(0.02)
    time.sleep(0.05)
    return list(rec.batches)


def test_feeder_uses_the_live_settings_object(client):
    import subarr.app as app_mod
    from subarr import config

    assert app_mod.settings is config.settings


def test_feeder_sends_translate_for_a_ja_file(client, media_root):
    st = _setup(client, media_root=media_root, tracks_by_file={"TV/Show/ja.mkv": [("jpn", True)]})
    job = _enqueue(st, "TV/Show/ja.mkv")
    with _settings(True, ["en", "es"]):
        _submit(client, job)
    got = _wait_batches(_REC, 1)
    assert [t for _, t in got] == ["translate"]


def test_feeder_sends_no_task_when_the_feature_is_off(client, media_root):
    st = _setup(client, media_root=media_root, tracks_by_file={"TV/Show/ja.mkv": [("jpn", True)]})
    job = _enqueue(st, "TV/Show/ja.mkv")
    with _settings(False, ["en", "es"]):
        _submit(client, job)
    assert [t for _, t in _wait_batches(_REC, 1)] == [None]


def test_feeder_sends_no_task_when_caps_lack_per_request_task(client, media_root):
    st = _setup(
        client,
        media_root=media_root,
        tracks_by_file={"TV/Show/ja.mkv": [("jpn", True)]},
        caps=_caps(per_request_task=False),
    )
    job = _enqueue(st, "TV/Show/ja.mkv")
    with _settings(True, ["en"]):
        _submit(client, job)
    assert [t for _, t in _wait_batches(_REC, 1)] == [None]


def test_job_task_preset_is_sent_as_is(client, media_root):
    st = _setup(client, media_root=media_root, tracks_by_file={"TV/Show/ja.mkv": [("jpn", True)]})
    job = _enqueue(st, "TV/Show/ja.mkv", task="transcribe")
    assert job.task == "transcribe"
    with _settings(True, ["en"]):  # would have chosen translate
        _submit(client, job)
    assert [t for _, t in _wait_batches(_REC, 1)] == ["transcribe"]


def test_job_task_preset_is_dropped_when_caps_lack_per_request_task(client, media_root):
    st = _setup(
        client,
        media_root=media_root,
        tracks_by_file={"TV/Show/ja.mkv": [("jpn", True)]},
        caps=_caps(per_request_task=False),
    )
    job = _enqueue(st, "TV/Show/ja.mkv", task="transcribe")
    _submit(client, job)
    assert [t for _, t in _wait_batches(_REC, 1)] == [None]


def test_provenance_is_recorded_against_the_job_path(client, media_root):
    st = _setup(client, media_root=media_root, tracks_by_file={"TV/Show/ja.mkv": [("jpn", True)]})
    job = _enqueue(st, "TV/Show/ja.mkv")
    with _settings(True, ["en"]):
        _submit(client, job)
    rows = st.provenance.query_by_path("TV/Show/ja.mkv")
    assert len(rows) == 1 and rows[0].scan_id


# ---- folders ----------------------------------------------------------------


def _folder(client, media_root, files, **kw):
    return _setup(
        client, media_root=media_root, tracks_by_file={f"TV/Dir/{n}": t for n, t in files.items()}, **kw
    )


def test_uniform_folder_is_one_path_with_that_task(client, media_root):
    st = _folder(client, media_root, {"a.mkv": [("jpn", True)], "b.mkv": [("kor", True)]})
    job = _enqueue(st, "TV/Dir")
    with _settings(True, ["en"]):
        _submit(client, job)
    got = _wait_batches(_REC, 1)
    assert len(got) == 1
    assert got[0][0].rstrip("/").endswith("TV/Dir") and got[0][1] == "translate"
    assert len(st.provenance.query_by_path("TV/Dir")) == 1


def test_uniform_folder_of_unknowns_sends_the_folder_with_no_task(client, media_root):
    st = _folder(client, media_root, {"a.mkv": [(None, True)], "b.mkv": [(None, True)]})
    job = _enqueue(st, "TV/Dir")
    with _settings(True, ["en"]):
        _submit(client, job)
    got = _wait_batches(_REC, 1)
    assert len(got) == 1 and got[0][1] is None


def test_mixed_folder_is_one_scan_of_file_paths_each_with_its_own_task(client, media_root):
    st = _folder(
        client, media_root, {"a.mkv": [("eng", True)], "b.mkv": [("jpn", True)], "c.mkv": [("spa", True)]}
    )
    job = _enqueue(st, "TV/Dir")
    with _settings(True, ["en", "es"]):
        _submit(client, job)
    got = _wait_batches(_REC, 3)
    by_file = {d.rsplit("/", 1)[-1]: t for d, t in got}
    assert by_file == {"a.mkv": "transcribe", "b.mkv": "translate", "c.mkv": "transcribe"}
    # sorted, in order
    assert [d.rsplit("/", 1)[-1] for d, _ in got] == ["a.mkv", "b.mkv", "c.mkv"]
    # provenance against the FOLDER, one entry, one scan
    rows = st.provenance.query_by_path("TV/Dir")
    assert len(rows) == 1
    scan = st.scans.get(rows[0].scan_id)
    assert scan.paths == ["TV/Dir/a.mkv", "TV/Dir/b.mkv", "TV/Dir/c.mkv"]
    for _ in range(100):
        scan = st.scans.get(rows[0].scan_id)
        if scan.status == "done":
            break
        time.sleep(0.02)
    assert scan.status == "done", "a multi-path scan completes"
    assert [r.status for r in scan.results] == ["ok", "ok", "ok"]


def test_mixed_folder_marks_exactly_one_pending_job_submitted(client, media_root):
    st = _folder(client, media_root, {"a.mkv": [("eng", True)], "b.mkv": [("jpn", True)]})
    job = _enqueue(st, "TV/Dir")
    with _settings(True, ["en", "es"]):
        n = client.portal.call(st.queue_feeder.tick)
    assert n == 1
    got = _wait_batches(_REC, 2)
    assert len(got) == 2
    after = st.pending_queue.get(job.id)
    assert after is not None and after.status == "submitted"
    assert len(st.pending_queue.list_all()) == 1 if hasattr(st.pending_queue, "list_all") else True


def test_nested_subfolders_are_walked(client, media_root):
    st = _setup(
        client,
        media_root=media_root,
        tracks_by_file={
            "TV/Dir/S1/a.mkv": [("eng", True)],
            "TV/Dir/S2/deep/b.mkv": [("jpn", True)],
        },
        extra_files=("TV/Dir/notes.txt", "TV/Dir/S1/a.en.srt"),
    )
    job = _enqueue(st, "TV/Dir")
    with _settings(True, ["en"]):
        _submit(client, job)
    got = _wait_batches(_REC, 2)
    assert {d.split("TV/Dir/", 1)[-1]: t for d, t in got} == {
        "S1/a.mkv": "transcribe",
        "S2/deep/b.mkv": "translate",
    }


def test_folder_over_the_cap_is_submitted_alone_with_a_warning(client, media_root, monkeypatch, caplog):
    import subarr.submission_language as sl

    monkeypatch.setattr(sl, "PER_FILE_TASK_FOLDER_CAP", 2)
    st = _folder(
        client,
        media_root,
        {"a.mkv": [("eng", True)], "b.mkv": [("jpn", True)], "c.mkv": [("spa", True)]},
    )
    job = _enqueue(st, "TV/Dir")
    with caplog.at_level(logging.WARNING, logger="subarr.submission_language"), _settings(True, ["en", "es"]):
        _submit(client, job)
    got = _wait_batches(_REC, 1)
    assert len(got) == 1 and got[0][1] is None and got[0][0].rstrip("/").endswith("TV/Dir")
    warn = [r for r in caplog.records if r.levelno == logging.WARNING and "3" in r.getMessage()]
    assert warn, "the file count is logged at WARNING"


def test_folder_at_exactly_the_cap_is_still_planned(client, media_root, monkeypatch):
    import subarr.submission_language as sl

    monkeypatch.setattr(sl, "PER_FILE_TASK_FOLDER_CAP", 2)
    st = _folder(client, media_root, {"a.mkv": [("eng", True)], "b.mkv": [("jpn", True)]})
    job = _enqueue(st, "TV/Dir")
    with _settings(True, ["en"]):
        _submit(client, job)
    assert len(_wait_batches(_REC, 2)) == 2


def test_folder_with_no_videos_is_submitted_alone_with_no_task(client, media_root):
    st = _setup(client, media_root=media_root, tracks_by_file={}, extra_files=("TV/Empty/readme.txt",))
    job = _enqueue(st, "TV/Empty")
    with _settings(True, ["en"]):
        _submit(client, job)
    got = _wait_batches(_REC, 1)
    assert len(got) == 1 and got[0][1] is None


def test_folder_sends_no_override_so_forced_is_none_for_every_file(client, media_root):
    """A folder submission sends no per-file override, so a verified language
    must not steer the task: forced stays None to match what is sent."""
    st = _folder(client, media_root, {"a.mkv": [("jpn", True)]})
    st.audio_lang = _Store({"TV/Dir/a.mkv": "en"})
    job = _enqueue(st, "TV/Dir")
    with _settings(True, ["en"]):
        _submit(client, job)
    assert [t for _, t in _wait_batches(_REC, 1)] == ["translate"]


def test_folder_when_the_feature_is_inert_is_untouched(client, media_root):
    st = _folder(client, media_root, {"a.mkv": [("eng", True)], "b.mkv": [("jpn", True)]})
    job = _enqueue(st, "TV/Dir")
    with _settings(False, ["en"]):
        _submit(client, job)
    got = _wait_batches(_REC, 1)
    assert len(got) == 1 and got[0][1] is None and got[0][0].rstrip("/").endswith("TV/Dir")


def test_file_job_uses_the_override_being_sent(client, media_root):
    st = _setup(client, media_root=media_root, tracks_by_file={"TV/Show/e.mkv": [("eng", True)]})
    st.audio_lang = _Store({"TV/Show/e.mkv": "ja"})
    job = _enqueue(st, "TV/Show/e.mkv")
    with _settings(True, ["en"]):
        _submit(client, job)
    assert [t for _, t in _wait_batches(_REC, 1)] == ["translate"]


# ---- folders carry the job's override to EVERY path (review fix) --------------


def test_folder_under_a_declared_series_intent_plans_what_the_file_job_plans(client, media_root):
    """resolve_for_job on a FOLDER can return a series-intent override, and the
    runner sends it on every /batch of the scan, so subgen forces that language
    for each file. The plan must use the same forced language."""
    st = _folder(client, media_root, {"a.mkv": [("eng", True)]})
    st.audio_lang = _Store({"TV/Dir": "ja"})  # series declared ja; files tagged eng
    job = _enqueue(st, "TV/Dir")
    with _settings(True, ["en"]):
        _submit(client, job)
    folder_task = [t for _, t in _wait_batches(_REC, 1)]
    assert folder_task == ["translate"], "forced ja, only en wanted"

    _REC.batches.clear()
    st.audio_lang = _Store({"TV/Dir/a.mkv": "ja"})
    file_job = _enqueue(st, "TV/Dir/a.mkv")
    with _settings(True, ["en"]):
        _submit(client, file_job)
    assert [t for _, t in _wait_batches(_REC, 1)] == folder_task


def test_folder_job_carrying_a_stored_override_uses_it_for_every_file(client, media_root):
    st = _folder(client, media_root, {"a.mkv": [("eng", True)], "b.mkv": [("jpn", True)]})
    job = _enqueue(st, "TV/Dir", audio_language_override="ja")
    with _settings(True, ["en"]):
        _submit(client, job)
    got = _wait_batches(_REC, 1)
    assert len(got) == 1, "uniform once the override applies: both files translate"
    assert got[0][1] == "translate"
    assert got[0][0].rstrip("/").endswith("TV/Dir")


def test_plan_without_an_override_still_splits_the_same_folder(client, media_root):
    st = _folder(client, media_root, {"a.mkv": [("eng", True)], "b.mkv": [("jpn", True)]})
    job = _enqueue(st, "TV/Dir")
    with _settings(True, ["en"]):
        _submit(client, job)
    assert sorted(t for _, t in _wait_batches(_REC, 2)) == ["transcribe", "translate"]


# ---- the gate's forced map is built once, off the event loop (review fix) -----


class _ThreadSpyStore(_Store):
    def __init__(self, mapping):
        super().__init__(mapping)
        self.threads = []

    def get_all_as_lookup(self):
        return {}

    def get_all_sources_as_lookup(self):
        return {}

    def list_series_intents(self):
        return []

    def get_all_multi_as_lookup(self):
        return {}

    def get(self, canonical, follow_replaced=False):
        import threading

        self.threads.append(threading.get_ident())
        return super().get(canonical, follow_replaced=follow_replaced)


class _AllEntriesProbes:
    def __init__(self, probes):
        self.probes = probes

    def all_entries(self):
        return list(self.probes)

    def failed_paths(self):
        return []

    def get(self, canonical, *a, **k):
        return None


def test_forced_map_is_precomputed_and_lookup_never_touches_the_store():
    from subarr.coverage_engine import _per_file_forced_map

    store = _ThreadSpyStore({"TV/S/a.mkv": "fr"})
    lookup = _per_file_forced_map(store, _caps(), ["TV/S/a.mkv", "TV/S/b.mkv"])
    seen = len(store.threads)
    assert seen == 2
    assert lookup("TV/S/a.mkv") == "fr" and lookup("TV/S/b.mkv") is None
    assert lookup("TV/S/unknown.mkv") is None
    assert len(store.threads) == seen, "lookup is dict-backed"


@pytest.mark.asyncio
async def test_build_coverage_resolves_forced_off_loop_when_active(coverage_bundle):
    import threading

    from subarr.coverage_engine import build_coverage

    store = _ThreadSpyStore({"TV/S/a.mkv": "fr"})
    probes = _AllEntriesProbes([_probe([("eng", True)], path="TV/S/a.mkv")])
    bundle = coverage_bundle()
    with _settings(True, ["en"]):
        await build_coverage(
            bundle, use_tautulli=False, probe_store=probes, audio_lang_store=store, subgen_caps=_caps()
        )
    assert store.threads, "active config: the map was built"
    assert threading.get_ident() not in store.threads


@pytest.mark.asyncio
async def test_build_coverage_never_touches_the_store_when_inactive(coverage_bundle):
    from subarr.coverage_engine import build_coverage

    store = _ThreadSpyStore({"TV/S/a.mkv": "fr"})
    probes = _AllEntriesProbes([_probe([("eng", True)], path="TV/S/a.mkv")])
    bundle = coverage_bundle()
    with _settings(False, ["en"]):
        await build_coverage(
            bundle, use_tautulli=False, probe_store=probes, audio_lang_store=store, subgen_caps=_caps()
        )
    assert store.threads == []
