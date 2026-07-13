"""#155 phase 2 — library-wide audio-language audit walker + store.

Covers the store (upsert/get/list_findings + resumable-by-mtime), the
parse_robust_detect extraction, and the walker's classification + GPU-polite
pause. The walker is driven by a fake subgen (canned per-path detect responses)
and a fake worklist — same style as the arena tests' injected runner/judge.
"""

from __future__ import annotations

import asyncio

import pytest

from subarr.arena import parse_robust_detect


def _store(tmp_path):
    # Mirror the app: migrations own the schema (010_audio_audit.sql).
    from subarr.migrate import run_migrations
    from subarr.audio_audit_store import AudioAuditStore

    db = tmp_path / "a.db"
    run_migrations(db)
    return AudioAuditStore(db)


# ── store ─────────────────────────────────────────────────────────────────


def test_upsert_and_get(tmp_path):
    s = _store(tmp_path)
    s.upsert(
        canonical_path="TV/X/a.mkv",
        tag_lang="da",
        detected_lang="nl",
        status="mislabel",
        languages_heard=["nl"],
        n_agreeing=3,
        n_total=3,
        mtime=100.0,
    )
    f = s.get("TV/X/a.mkv")
    assert f is not None
    assert f.tag_lang == "da" and f.detected_lang == "nl"
    assert f.status == "mislabel"
    assert f.languages_heard == ["nl"]
    assert f.n_agreeing == 3 and f.n_total == 3
    assert f.mtime == 100.0
    assert f.checked_at > 0


def test_upsert_roundtrips_track_languages(tmp_path):
    s = _store(tmp_path)
    s.upsert(
        canonical_path="TV/X/mt.mkv",
        tag_lang="de",
        detected_lang="de",
        status="multitrack",
        languages_heard=["de"],
        n_agreeing=3,
        n_total=3,
        mtime=1.0,
        track_languages=["de", "ru"],
    )
    f = s.get("TV/X/mt.mkv")
    assert f.track_languages == ["de", "ru"]
    # default when omitted = empty list (not None) in to_dict
    s.upsert(
        canonical_path="TV/X/a.mkv",
        tag_lang="en",
        detected_lang="en",
        status="agrees",
        languages_heard=[],
        n_agreeing=3,
        n_total=3,
        mtime=1.0,
    )
    assert s.get("TV/X/a.mkv").to_dict()["track_languages"] == []


def test_upsert_round_trips_chunks_conf(tmp_path):
    s = _store(tmp_path)
    s.upsert(
        canonical_path="lib::/a.mkv",
        tag_lang="en",
        detected_lang="gl",
        status="multilingual",
        languages_heard=["gl", "es"],
        n_agreeing=2,
        n_total=3,
        mtime=1.0,
        track_languages=None,
        chunks_conf=[("gl", 0.94), ("es", 0.88), ("fr", 0.71)],
    )
    f = s.get("lib::/a.mkv")
    assert f.chunks_conf == [["gl", 0.94], ["es", 0.88], ["fr", 0.71]]  # JSON round-trip -> lists
    assert f.to_dict()["chunks_conf"] == [["gl", 0.94], ["es", 0.88], ["fr", 0.71]]


def test_chunks_conf_defaults_to_none_when_absent(tmp_path):
    s = _store(tmp_path)
    s.upsert(
        canonical_path="lib::/b.mkv",
        tag_lang="en",
        detected_lang="en",
        status="agrees",
        languages_heard=["en"],
        n_agreeing=3,
        n_total=3,
        mtime=1.0,
        track_languages=None,
    )
    assert s.get("lib::/b.mkv").chunks_conf is None


def test_upsert_replaces_existing(tmp_path):
    s = _store(tmp_path)
    s.upsert(
        canonical_path="TV/X/a.mkv",
        tag_lang="da",
        detected_lang=None,
        status="undetermined",
        languages_heard=[],
        n_agreeing=0,
        n_total=0,
        mtime=100.0,
    )
    s.upsert(
        canonical_path="TV/X/a.mkv",
        tag_lang="da",
        detected_lang="nl",
        status="mislabel",
        languages_heard=["nl"],
        n_agreeing=3,
        n_total=3,
        mtime=200.0,
    )
    f = s.get("TV/X/a.mkv")
    assert f.status == "mislabel" and f.mtime == 200.0
    assert len(s.all()) == 1


def test_list_findings_only_actionable_newest_first(tmp_path):
    s = _store(tmp_path)
    s.upsert(
        canonical_path="agrees.mkv",
        tag_lang="en",
        detected_lang="en",
        status="agrees",
        languages_heard=["en"],
        n_agreeing=3,
        n_total=3,
        mtime=1.0,
    )
    s.upsert(
        canonical_path="mislabel.mkv",
        tag_lang="da",
        detected_lang="nl",
        status="mislabel",
        languages_heard=["nl"],
        n_agreeing=3,
        n_total=3,
        mtime=1.0,
    )
    s.upsert(
        canonical_path="bilingual.mkv",
        tag_lang="sr",
        detected_lang="sr",
        status="bilingual",
        languages_heard=["en", "sr"],
        n_agreeing=2,
        n_total=3,
        mtime=1.0,
    )
    findings = s.list_findings()
    paths = [f.canonical_path for f in findings]
    assert "agrees.mkv" not in paths
    assert set(paths) == {"mislabel.mkv", "bilingual.mkv"}
    # newest checked_at first — bilingual was upserted last
    assert paths[0] == "bilingual.mkv"


def test_count_by_status_and_clear(tmp_path):
    s = _store(tmp_path)
    s.upsert(
        canonical_path="a.mkv",
        tag_lang="en",
        detected_lang="en",
        status="agrees",
        languages_heard=[],
        n_agreeing=3,
        n_total=3,
        mtime=1.0,
    )
    s.upsert(
        canonical_path="b.mkv",
        tag_lang="da",
        detected_lang="nl",
        status="mislabel",
        languages_heard=["nl"],
        n_agreeing=3,
        n_total=3,
        mtime=1.0,
    )
    assert s.count_by_status() == {"agrees": 1, "mislabel": 1}
    s.clear()
    assert s.all() == []
    assert s.count_by_status() == {}


# ── parse_robust_detect (extracted shared parser) ───────────────────────────


def test_parse_robust_detect_unanimous():
    resp = {
        "aggregate": {"language": "nl", "n_agreeing": 3, "n_total": 3},
        "chunks": [{"language": "nl"}, {"language": "nl"}, {"language": "nl"}],
    }
    d = parse_robust_detect(resp)
    assert d["language"] == "nl" and d["unanimous"] is True
    assert d["languages_heard"] == ["nl"]


def test_parse_robust_detect_none_when_no_votes():
    assert parse_robust_detect({"aggregate": {"n_total": 0}}) is None
    assert parse_robust_detect(None) is None


def test_parse_robust_detect_drops_und():
    resp = {
        "aggregate": {"language": "und", "n_agreeing": 1, "n_total": 3},
        "chunks": [{"language": "en"}, {"language": "und"}, {"language": "sr"}],
    }
    d = parse_robust_detect(resp)
    assert d["language"] is None
    assert d["languages_heard"] == ["en", "sr"]  # und filtered out


# ── walker fakes ────────────────────────────────────────────────────────────


class _FakeSubgen:
    """Canned per-subgen-path detect responses. Records each path it was
    asked to detect (so we can assert the busy-pause fired no detects)."""

    def __init__(self, by_path: dict):
        self._by_path = by_path
        self.calls: list[str] = []

    async def detect_language_robust(self, path):
        self.calls.append(path)
        return self._by_path.get(path)


class _FakeAudio:
    def __init__(self, language):
        self.language = language


class _FakeProbe:
    def __init__(self, langs):
        self.audio = [_FakeAudio(l) for l in langs]


class _FakeProbeStore:
    def __init__(self, by_path: dict):
        self._by_path = by_path

    def get(self, canonical_path, *a, **k):
        return self._by_path.get(canonical_path)


def _unanimous(lang, n=3):
    return {
        "aggregate": {"language": lang, "n_agreeing": n, "n_total": n},
        "chunks": [{"language": lang}] * n,
    }


def _split(langs):
    # e.g. ["en","en","sr"] → plurality en, 2/3, two distinct heard
    from collections import Counter

    c = Counter(langs)
    top, n = c.most_common(1)[0]
    return {
        "aggregate": {"language": top, "n_agreeing": n, "n_total": len(langs)},
        "chunks": [{"language": l} for l in langs],
    }


def _identity(path):
    return path  # to_subgen passthrough for tests


@pytest.mark.asyncio
async def test_walker_classifies_mislabel_bilingual_agrees(tmp_path):
    from subarr.audio_audit import AudioAuditWalker

    s = _store(tmp_path)
    subgen = _FakeSubgen(
        {
            "mislabel.mkv": _unanimous("nl"),  # tagged da, heard nl 3/3 → mislabel
            "bilingual.mkv": _split(["en", "en", "sr"]),  # tagged sr, real 2nd lang
            "agrees.mkv": _unanimous("en"),  # tagged en, heard en → agrees
            "confused.mkv": _split(["en", "sr", "fr"]),  # 1/1/1 no majority, tagged en
        }
    )
    worklist = [
        ("mislabel.mkv", "da", 10.0),
        ("bilingual.mkv", "sr", 10.0),
        ("agrees.mkv", "en", 10.0),
        ("confused.mkv", "en", 10.0),
    ]
    w = AudioAuditWalker(subgen, s, worklist=lambda: worklist, to_subgen=_identity)
    state = await w.start()
    await w._task  # drive to completion
    assert state.status == "done"
    assert state.processed == 4
    assert s.get("mislabel.mkv").status == "mislabel"
    assert s.get("bilingual.mkv").status == "bilingual"
    assert s.get("agrees.mkv").status == "agrees"
    # 1/1/1 split: not unanimous, n_agreeing 1, tag wins → not mislabel/mixed
    assert s.get("confused.mkv").status in ("agrees", "confused")
    assert state.found == 2  # mislabel + bilingual


@pytest.mark.asyncio
async def test_walker_persists_chunks_conf(tmp_path):
    """#407 Task 2: _audit_one passes parse_robust_detect's chunks_conf through
    to the store, so per-chunk probabilities survive the audit walk (not just
    the store layer, which Task 1 already covers)."""
    from subarr.audio_audit import AudioAuditWalker

    s = _store(tmp_path)
    resp = {
        "aggregate": {"language": "gl", "n_agreeing": 1, "n_total": 3},
        "chunks": [
            {"language": "gl", "probability": 0.94},
            {"language": "es", "probability": 0.88},
            {"language": "fr", "probability": 0.71},
        ],
    }
    subgen = _FakeSubgen({"multi.mkv": resp})
    worklist = [("multi.mkv", "en", 10.0)]
    w = AudioAuditWalker(subgen, s, worklist=lambda: worklist, to_subgen=_identity)
    await (await w.start(), w._task)[-1]
    f = s.get("multi.mkv")
    assert f is not None
    assert f.chunks_conf == [["gl", 0.94], ["es", 0.88], ["fr", 0.71]]


class _FakeLangStore:
    """Records Tier 2 auto-verifications the walker writes."""

    def __init__(self):
        self.rows: dict = {}

    def upsert(
        self, *, canonical_path, lang_code, source="user", confidence=1.0, verified_by=None, evidence=None
    ):
        self.rows[canonical_path] = {
            "lang_code": lang_code,
            "source": source,
            "confidence": confidence,
            "evidence": evidence,
        }


@pytest.mark.asyncio
async def test_walker_tier2_writes_whisper_robust_on_mislabel(tmp_path):
    """Tier 2 feedback: a unanimous mislabel writes a `whisper-robust`
    verification (conf 0.7) so coverage + the override gate can use it — but
    bilingual stays advisory (no writeback), and risky JA/KO/ZH are written
    BELOW the override threshold so a wrong auto-guess can't change output."""
    from subarr.audio_audit import AudioAuditWalker

    s = _store(tmp_path)
    lang = _FakeLangStore()
    subgen = _FakeSubgen(
        {
            "mislabel.mkv": _unanimous("nl"),  # tagged da → mislabel nl
            "risky.mkv": _unanimous("ja"),  # tagged en → mislabel ja (risky)
            "bi.mkv": _split(["en", "en", "sr"]),  # bilingual → advisory, NO writeback
            "agree.mkv": _unanimous("en"),  # agrees → no writeback
        }
    )
    worklist = [
        ("mislabel.mkv", "da", 10.0),
        ("risky.mkv", "en", 10.0),
        ("bi.mkv", "sr", 10.0),
        ("agree.mkv", "en", 10.0),
    ]
    w = AudioAuditWalker(subgen, s, worklist=lambda: worklist, audio_lang=lang, to_subgen=_identity)
    await (await w.start(), w._task)[-1]
    assert lang.rows["mislabel.mkv"]["source"] == "whisper-robust"
    assert lang.rows["mislabel.mkv"]["lang_code"] == "nl"
    assert lang.rows["mislabel.mkv"]["confidence"] == 0.7
    # risky language written below the 0.5 override gate (display-only)
    assert lang.rows["risky.mkv"]["confidence"] < 0.5
    # bilingual + agrees never auto-write
    assert "bi.mkv" not in lang.rows
    assert "agree.mkv" not in lang.rows


@pytest.mark.asyncio
async def test_walker_start_passes_scope_when_supported(tmp_path):
    """start(scope=...) is forwarded to a scope-aware worklist, and falls back
    cleanly for a legacy zero-arg worklist (the test fakes)."""
    from subarr.audio_audit import AudioAuditWalker

    s = _store(tmp_path)
    seen = {}

    def worklist(scope="coverage"):
        seen["scope"] = scope
        return []

    subgen = _FakeSubgen({})
    w = AudioAuditWalker(subgen, s, worklist=worklist, to_subgen=_identity)
    await (await w.start(scope="library"), w._task)[-1]
    assert seen["scope"] == "library"


@pytest.mark.asyncio
async def test_walker_undetermined_when_no_detection(tmp_path):
    from subarr.audio_audit import AudioAuditWalker

    s = _store(tmp_path)
    subgen = _FakeSubgen({"x.mkv": None})  # detect unavailable
    w = AudioAuditWalker(subgen, s, worklist=lambda: [("x.mkv", "en", 10.0)], to_subgen=_identity)
    await (await w.start(), w._task)[-1]
    # no tag-vs-heard signal, nothing detected → undetermined
    assert s.get("x.mkv").status == "undetermined"


@pytest.mark.asyncio
async def test_walker_multitrack_from_probe(tmp_path):
    from subarr.audio_audit import AudioAuditWalker

    s = _store(tmp_path)
    subgen = _FakeSubgen({"mt.mkv": _unanimous("ru")})  # heard ru, tagged de
    probe = _FakeProbeStore({"mt.mkv": _FakeProbe(["de", "ru"])})  # 2 tracks
    w = AudioAuditWalker(
        subgen, s, worklist=lambda: [("mt.mkv", "de", 10.0)], probe_store=probe, to_subgen=_identity
    )
    await (await w.start(), w._task)[-1]
    # ≥2 distinct track langs → multitrack suppresses mislabel
    finding = s.get("mt.mkv")
    assert finding.status == "multitrack"
    assert finding.status != "mislabel"
    # the actual TRACK languages are stored (so the UI shows "DE + RU", not the
    # single language heard in the one track the walker listened to)
    assert finding.track_languages == ["de", "ru"]


@pytest.mark.asyncio
async def test_walker_resumable_skips_matching_mtime(tmp_path):
    from subarr.audio_audit import AudioAuditWalker

    s = _store(tmp_path)
    # Pre-seed: already audited at mtime 10.0.
    s.upsert(
        canonical_path="seen.mkv",
        tag_lang="en",
        detected_lang="en",
        status="agrees",
        languages_heard=["en"],
        n_agreeing=3,
        n_total=3,
        mtime=10.0,
    )
    subgen = _FakeSubgen({"seen.mkv": _unanimous("en"), "new.mkv": _unanimous("nl")})
    worklist = [("seen.mkv", "en", 10.0), ("new.mkv", "da", 10.0)]
    w = AudioAuditWalker(subgen, s, worklist=lambda: worklist, to_subgen=_identity)
    await (await w.start(), w._task)[-1]
    # seen.mkv skipped (mtime matched) → only new.mkv was detected
    assert subgen.calls == ["new.mkv"]
    assert s.get("new.mkv").status == "mislabel"


@pytest.mark.asyncio
async def test_walker_reaudits_when_mtime_changed(tmp_path):
    from subarr.audio_audit import AudioAuditWalker

    s = _store(tmp_path)
    s.upsert(
        canonical_path="f.mkv",
        tag_lang="en",
        detected_lang="en",
        status="agrees",
        languages_heard=["en"],
        n_agreeing=3,
        n_total=3,
        mtime=10.0,
    )
    subgen = _FakeSubgen({"f.mkv": _unanimous("nl")})
    # mtime changed (50.0 != 10.0) → re-audit
    w = AudioAuditWalker(subgen, s, worklist=lambda: [("f.mkv", "da", 50.0)], to_subgen=_identity)
    await (await w.start(), w._task)[-1]
    assert subgen.calls == ["f.mkv"]
    assert s.get("f.mkv").status == "mislabel"
    assert s.get("f.mkv").mtime == 50.0


@pytest.mark.asyncio
async def test_walker_pauses_while_busy_no_detect(tmp_path, monkeypatch):
    """While busy_check() is True the walker must NOT fire a detect — it sleeps
    and yields the GPU. We flip busy off after a couple of ticks and assert the
    detect only happens once it's free."""
    import subarr.audio_audit as aa

    monkeypatch.setattr(aa, "_BUSY_SLEEP_S", 0.01)
    monkeypatch.setattr(aa, "_PER_FILE_SLEEP_S", 0.0)
    s = _store(tmp_path)
    subgen = _FakeSubgen({"x.mkv": _unanimous("nl")})
    busy = {"v": True}
    w = aa.AudioAuditWalker(
        subgen, s, worklist=lambda: [("x.mkv", "da", 10.0)], busy_check=lambda: busy["v"], to_subgen=_identity
    )
    state = await w.start()
    # Let it spin a few busy-loops; assert no detect fired while busy.
    await asyncio.sleep(0.05)
    assert subgen.calls == []
    assert state.processed == 0
    # Free the GPU → the detect should now run and the walk complete.
    busy["v"] = False
    await w._task
    assert subgen.calls == ["x.mkv"]
    assert s.get("x.mkv").status == "mislabel"


@pytest.mark.asyncio
async def test_walker_double_start_guarded(tmp_path, monkeypatch):
    import subarr.audio_audit as aa

    monkeypatch.setattr(aa, "_PER_FILE_SLEEP_S", 0.05)
    s = _store(tmp_path)
    subgen = _FakeSubgen({"a.mkv": _unanimous("en"), "b.mkv": _unanimous("en")})
    w = aa.AudioAuditWalker(
        subgen, s, worklist=lambda: [("a.mkv", "en", 1.0), ("b.mkv", "en", 1.0)], to_subgen=_identity
    )
    await w.start()
    with pytest.raises(RuntimeError):
        await w.start()  # already running
    await w._task


@pytest.mark.asyncio
async def test_walker_cancel_sets_cancelled(tmp_path, monkeypatch):
    import subarr.audio_audit as aa

    monkeypatch.setattr(aa, "_PER_FILE_SLEEP_S", 1.0)
    s = _store(tmp_path)
    subgen = _FakeSubgen({"a.mkv": _unanimous("en"), "b.mkv": _unanimous("en")})
    w = aa.AudioAuditWalker(
        subgen, s, worklist=lambda: [("a.mkv", "en", 1.0), ("b.mkv", "en", 1.0)], to_subgen=_identity
    )
    state = await w.start()
    await asyncio.sleep(0)  # let it start the first file
    await w.stop()
    assert state.status == "cancelled"


@pytest.mark.asyncio
async def test_walker_per_file_exception_recorded_and_continues(tmp_path, monkeypatch):
    import subarr.audio_audit as aa

    monkeypatch.setattr(aa, "_PER_FILE_SLEEP_S", 0.0)
    s = _store(tmp_path)

    class _Boom(_FakeSubgen):
        async def detect_language_robust(self, path):
            self.calls.append(path)
            if path == "bad.mkv":
                raise RuntimeError("subgen exploded")
            return self._by_path.get(path)

    subgen = _Boom({"good.mkv": _unanimous("nl")})
    w = aa.AudioAuditWalker(
        subgen, s, worklist=lambda: [("bad.mkv", "en", 1.0), ("good.mkv", "da", 1.0)], to_subgen=_identity
    )
    state = await w.start()
    await w._task
    assert state.status == "done"
    assert state.processed == 2
    assert len(state.errors) == 1 and state.errors[0]["path"] == "bad.mkv"
    # the good file after the bad one still got audited
    assert s.get("good.mkv").status == "mislabel"
