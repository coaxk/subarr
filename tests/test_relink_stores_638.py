"""#638: store-level moves used by the relink apply."""

from __future__ import annotations

import pytest

OLD = "Old/Show/S1/e.mkv"
NEW = "Show/S1/e.mkv"


@pytest.fixture
def als(subarr_env, tmp_path):
    from subarr.audio_lang_store import AudioLangStore
    from subarr.migrate import run_migrations

    db = tmp_path / "al.db"
    run_migrations(db)
    return AudioLangStore(db)


@pytest.fixture
def probes(subarr_env, tmp_path):
    from subarr.migrate import run_migrations
    from subarr.probe_store import ProbeStore

    db = tmp_path / "pr.db"
    run_migrations(db)
    return ProbeStore(db)


def _probe(store, path, size=10, source="ffprobe"):
    from subarr.media_probe import ProbeResult

    store.upsert(
        canonical_path=path, mtime=1.0, size=size, result=ProbeResult(canonical_path=path), source=source
    )


def test_relink_records_relinked_from_not_carried_from(als):
    als.upsert(canonical_path=OLD, lang_code="fr", source="user", confidence=0.9, sonarr_episode_id=7)
    before = als.get(OLD)
    moved = als.relink(OLD, NEW)
    assert moved is not None
    assert moved.canonical_path == NEW
    assert moved.evidence["relinked_from"] == OLD
    assert isinstance(moved.evidence["relinked_at"], float)
    assert "carried_from" not in moved.evidence and "carried_at" not in moved.evidence
    assert (moved.lang_code, moved.source, moved.confidence) == (
        before.lang_code,
        before.source,
        before.confidence,
    )
    assert moved.verified_at == before.verified_at
    assert moved.sonarr_episode_id == 7
    assert als.get(OLD) is None


def test_relink_keeps_existing_evidence(als):
    als.upsert(canonical_path=OLD, lang_code="en", evidence={"note": "hand"})
    moved = als.relink(OLD, NEW)
    assert moved.evidence["note"] == "hand"
    assert moved.evidence["relinked_from"] == OLD


def test_relink_refuses_when_target_has_verdict(als):
    als.upsert(canonical_path=OLD, lang_code="en")
    als.upsert(canonical_path=NEW, lang_code="fr")
    assert als.relink(OLD, NEW) is None
    assert als.get(OLD).lang_code == "en"
    assert als.get(NEW).lang_code == "fr"
    assert "relinked_from" not in (als.get(NEW).evidence or {})
    assert "relinked_from" not in (als.get(OLD).evidence or {})


def test_relink_missing_old_returns_none(als):
    assert als.relink(OLD, NEW) is None
    assert als.get(NEW) is None


def test_carry_over_evidence_unchanged(als):
    als.upsert(canonical_path=OLD, lang_code="en")
    moved = als._move_verdict(OLD, NEW)  # default kind
    assert moved.evidence["carried_from"] == OLD
    assert "carried_at" in moved.evidence
    assert "relinked_from" not in moved.evidence


def test_log_wording_differs_by_kind(als, caplog):
    import logging

    caplog.set_level(logging.INFO, logger="subarr.audio_lang_store")
    als.upsert(canonical_path=OLD, lang_code="en")
    als.upsert(canonical_path="A/B/C/d.mkv", lang_code="en")
    als.relink(OLD, NEW)
    als._move_verdict("A/B/C/d.mkv", "A/B/C/e.mkv")
    text = caplog.text
    assert "relinked (#638)" in text
    assert "carried over (#563)" in text


def test_probe_move_and_collision(probes):
    _probe(probes, OLD, size=11)
    assert probes.move(OLD, NEW) is True
    assert probes.sizes_for([OLD, NEW]) == {NEW: 11}
    # Collision: both exist, the second move refuses and leaves both intact.
    _probe(probes, OLD, size=22)
    assert probes.move(OLD, NEW) is False
    assert probes.sizes_for([OLD, NEW]) == {OLD: 22, NEW: 11}


def test_probe_move_absent_old_is_false(probes):
    assert probes.move(OLD, NEW) is False
    assert probes.all_paths() == []


def test_probe_move_carries_failure_row(probes):
    _probe(probes, OLD)
    probes.record_failure(OLD, "boom")
    assert probes.move(OLD, NEW) is True
    assert probes.get_failure(OLD) is None
    assert probes.get_failure(NEW)["error"] == "boom"


def test_probe_move_collision_leaves_failure_rows_alone(probes):
    _probe(probes, OLD)
    _probe(probes, NEW)
    probes.record_failure(OLD, "old-err")
    assert probes.move(OLD, NEW) is False
    assert probes.get_failure(OLD)["error"] == "old-err"
    assert probes.get_failure(NEW) is None


def test_probe_move_does_not_clobber_existing_failure_at_new(probes):
    _probe(probes, OLD)
    probes.record_failure(OLD, "old-err")
    probes.record_failure(NEW, "new-err")
    assert probes.move(OLD, NEW) is True
    assert probes.get_failure(NEW)["error"] == "new-err"


def test_sizes_for_returns_only_existing_rows_in_chunks(probes):
    paths = [f"L/S{i}/e{i}.mkv" for i in range(1200)]
    for i, p in enumerate(paths):
        _probe(probes, p, size=i)
    got = probes.sizes_for([paths[0], paths[600], paths[1199], "unknown/x/y.mkv"])
    assert got == {paths[0]: 0, paths[600]: 600, paths[1199]: 1199}
    assert len(probes.sizes_for(paths)) == 1200  # spans three chunks


def test_sizes_for_empty_and_duplicates(probes):
    assert probes.sizes_for([]) == {}
    _probe(probes, OLD, size=5)
    assert probes.sizes_for([OLD, OLD]) == {OLD: 5}


def test_arr_mediainfo_size_counts(probes):
    _probe(probes, OLD, size=9, source="arr_mediainfo")
    assert probes.sizes_for([OLD]) == {OLD: 9}
