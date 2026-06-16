"""#216 Phase 3: the single-flight background audit service.

Tests drive the service against a tmp library with real SRT files (no video
siblings, so duration probing no-ops) and fake store/provenance, asserting the
state machine, single-flight guard, and provenance-skip behaviour.
"""

from __future__ import annotations

import types

import pytest

from subarr.existing_audit import EXISTING_AUDIT_SOURCE
from subarr.existing_audit_service import ExistingAuditService


class _FakeStore:
    def __init__(self):
        self.records = []

    def record(self, **kw):
        self.records.append(kw)


class _FakeProvenance:
    def __init__(self, generated=None):
        self._gen = set(generated or [])

    def completed_paths_since(self, _since):
        return self._gen


def _lib(path):
    return types.SimpleNamespace(fs_root=path)


def _write_srt(path, body="Real subtitle line"):
    path.write_text(f"1\n00:00:01,000 --> 00:00:03,000\n{body}\n", encoding="utf-8")


@pytest.mark.asyncio
async def test_start_walks_scores_and_records(tmp_path):
    _write_srt(tmp_path / "A.en.srt")
    _write_srt(tmp_path / "B.en.srt")
    store = _FakeStore()
    svc = ExistingAuditService(
        aftercare_store=store, provenance=_FakeProvenance(), libraries=[_lib(tmp_path)], clock=lambda: 1.0
    )

    assert svc.start() is True
    await svc._task  # let the background run finish

    st = svc.status()
    assert st["running"] is False
    assert st["error"] is None
    assert st["summary"]["scanned"] == 2
    assert {r["canonical_path"] for r in store.records} == {
        str(tmp_path / "A.en.srt"),
        str(tmp_path / "B.en.srt"),
    }
    assert all(r["source"] == EXISTING_AUDIT_SOURCE for r in store.records)


@pytest.mark.asyncio
async def test_single_flight_guard():
    svc = ExistingAuditService(
        aftercare_store=_FakeStore(), provenance=_FakeProvenance(), libraries=[], clock=lambda: 1.0
    )
    svc._state["running"] = True  # simulate an in-flight run
    assert svc.start() is False  # second start is rejected


@pytest.mark.asyncio
async def test_skips_subarr_generated_via_provenance(tmp_path):
    _write_srt(tmp_path / "Ours.en.srt")
    _write_srt(tmp_path / "External.en.srt")
    store = _FakeStore()
    svc = ExistingAuditService(
        aftercare_store=store,
        provenance=_FakeProvenance(generated={str(tmp_path / "Ours.en.srt")}),
        libraries=[_lib(tmp_path)],
        clock=lambda: 1.0,
    )
    svc.start()
    await svc._task

    assert svc.status()["summary"]["skipped"] == 1
    assert [r["canonical_path"] for r in store.records] == [str(tmp_path / "External.en.srt")]
