"""#157 gap-fill: RUN-level supervision of the opt-in audio-audit walker. A
clean run records success; a run-level crash records failure. Per-file errors
still land in AuditState.errors. A missing _health never breaks a run."""

from __future__ import annotations

import pytest

from subarr.audio_audit import AudioAuditWalker


class _FakeHealth:
    def __init__(self):
        self.successes: list[str] = []
        self.failures: list[tuple[str, BaseException]] = []

    def record_success(self, name, *, expected_interval_s=None):
        self.successes.append(name)

    def record_failure(self, name, exc, *, expected_interval_s=None):
        self.failures.append((name, exc))


class _FakeStore:
    """Minimal audit store: get() returns None (never resumes/skips); upsert()
    records nothing (the audit body is exercised elsewhere)."""

    def get(self, canonical_path):
        return None

    def upsert(self, **kwargs):
        pass


def _walker(worklist, *, health=None, audit_one=None):
    w = AudioAuditWalker(
        subgen=object(),
        audit_store=_FakeStore(),
        worklist=lambda scope="coverage": worklist,
        probe_store=None,
        busy_check=None,
        audio_lang=None,
    )
    if health is not None:
        w._health = health
    if audit_one is not None:
        # Replace the per-file body so the test controls success/failure.
        w._audit_one = audit_one  # type: ignore[assignment]
    return w


@pytest.mark.asyncio
async def test_clean_run_records_success():
    health = _FakeHealth()

    async def ok(state, canonical_path, tag_lang, mtime):
        return None  # clean per-file

    w = _walker([("a.mkv", "en", 1.0)], health=health, audit_one=ok)
    state = await w.start(scope="coverage")
    await w._task  # await the run to completion
    assert state.status == "done"
    assert health.successes == ["audio-audit"]
    assert health.failures == []


@pytest.mark.asyncio
async def test_run_level_crash_records_failure():
    health = _FakeHealth()

    # Force a run-level crash INSIDE _run: a malformed 2-tuple worklist entry
    # makes the tuple-unpack `canonical_path, tag_lang, mtime = entry` raise a
    # ValueError. That unpack is OUTSIDE the per-file try, so it is a RUN-level
    # (not per-file) failure caught by _run's outer except. A 2-tuple stays
    # iterable, so it survives start()'s eager worklist resolution and reaches
    # _run intact (unlike a raising-__iter__ list, which start() swallows).
    w = _walker([("a.mkv", "en")], health=health)
    state = await w.start(scope="coverage")
    await w._task
    assert state.status == "error"
    assert health.successes == []
    assert len(health.failures) == 1
    assert health.failures[0][0] == "audio-audit"
    assert isinstance(health.failures[0][1], ValueError)


@pytest.mark.asyncio
async def test_per_file_error_does_not_flip_run_to_failure():
    health = _FakeHealth()

    async def boom_one(state, canonical_path, tag_lang, mtime):
        raise ValueError("per-file boom")

    w = _walker([("a.mkv", "en", 1.0)], health=health, audit_one=boom_one)
    state = await w.start(scope="coverage")
    await w._task
    # Per-file error accumulated, run still completed cleanly -> success.
    assert state.status == "done"
    assert len(state.errors) == 1
    assert state.errors[0]["path"] == "a.mkv"
    assert health.successes == ["audio-audit"]
    assert health.failures == []


@pytest.mark.asyncio
async def test_missing_health_never_raises():
    async def ok(state, canonical_path, tag_lang, mtime):
        return None

    w = _walker([("a.mkv", "en", 1.0)], health=None, audit_one=ok)
    state = await w.start(scope="coverage")
    await w._task  # must complete without AttributeError
    assert state.status == "done"
