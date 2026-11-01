"""Enrichment requeue (#616): clear stored guesses whose reply does not parse."""

from __future__ import annotations

import logging
import time

import pytest


def _store(tmp_path, name: str = "enrich.db"):
    from subarr.enrichment import EnrichmentStore
    from subarr.migrate import run_migrations

    db = tmp_path / name
    run_migrations(db)
    return EnrichmentStore(db)


def _insert(store, path, iso_code, raw_response, *, error=None, confidence=None):
    store.upsert(
        canonical_path=path,
        title="T",
        raw_response=raw_response,
        iso_code=iso_code,
        model="m",
        error=error,
        confidence=confidence,
    )


def test_prose_row_cleared(tmp_path):
    store = _store(tmp_path)
    path = "TV/A/a.mkv"
    _insert(store, path, "it", "It is Japanese.")
    before = store.get(path)

    assert store.requeue_unparseable() == 1

    row = store.get(path)
    assert row["iso_code"] is None
    assert row["error"] == "reply was not valid JSON"
    assert row["raw_response"] == "It is Japanese."
    assert row["confidence"] is None
    assert row["reasoning"] is None
    assert row["inferred_at"] == before["inferred_at"]
    store.close()


def test_truncated_json_row_cleared(tmp_path):
    store = _store(tmp_path)
    path = "TV/B/b.mkv"
    _insert(store, path, "iso", '{"iso_code": "ko"')

    assert store.requeue_unparseable() == 1

    row = store.get(path)
    assert row["iso_code"] is None
    assert row["error"] == "reply was not valid JSON"
    assert row["raw_response"] == '{"iso_code": "ko"'
    store.close()


@pytest.mark.parametrize("raw_response", ["", None])
def test_empty_raw_response_cleared(tmp_path, raw_response):
    store = _store(tmp_path)
    path = "TV/N/n.mkv"
    if raw_response is None:
        store._conn.execute(
            "INSERT INTO lang_enrichment "
            "(canonical_path, title, raw_response, iso_code, model, inferred_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (path, "T", None, "en", "m", time.time()),
        )
    else:
        _insert(store, path, "en", raw_response)

    assert store.requeue_unparseable() == 1

    row = store.get(path)
    assert row["iso_code"] is None
    assert row["error"] == "reply was not valid JSON"
    store.close()


@pytest.mark.parametrize(
    "raw_response,iso_code",
    [
        ('{"iso_code": "ko", "confidence": 0.9, "reasoning": "r"}', "ko"),
        ("ko\n", "ko"),
        ("es</th>", "es"),
        ('{"iso_code": "ja", "confidence": 0.8}</th>', "ja"),
    ],
)
def test_valid_rows_untouched(tmp_path, raw_response, iso_code):
    store = _store(tmp_path)
    path = "TV/A/a.mkv"
    _insert(store, path, iso_code, raw_response)
    before = store.get(path)

    assert store.requeue_unparseable() == 0
    assert store.get(path) == before
    store.close()


def test_mismatched_code_untouched(tmp_path):
    store = _store(tmp_path)
    path = "TV/A/a.mkv"
    _insert(store, path, "fr", '{"iso_code": "de", "confidence": 0.5}')
    before = store.get(path)

    assert store.requeue_unparseable() == 0
    assert store.get(path) == before
    assert store.get(path)["iso_code"] == "fr"
    store.close()


def test_null_iso_rows_untouched(tmp_path):
    store = _store(tmp_path)
    _insert(store, "TV/A/a.mkv", None, "und")
    _insert(store, "TV/B/b.mkv", None, "", error="boom")
    before_a = store.get("TV/A/a.mkv")
    before_b = store.get("TV/B/b.mkv")

    assert store.requeue_unparseable() == 0
    assert store.get("TV/A/a.mkv") == before_a
    assert store.get("TV/B/b.mkv") == before_b
    assert store.get("TV/A/a.mkv")["error"] is None
    assert store.get("TV/B/b.mkv")["error"] == "boom"
    store.close()


def test_mixed_table_counts(tmp_path):
    store = _store(tmp_path)
    _insert(store, "TV/A/a.mkv", "it", "It is Japanese.")
    _insert(store, "TV/B/b.mkv", "iso", '{"iso_code": "ko"')
    _insert(store, "TV/C/c.mkv", "ko", '{"iso_code": "ko", "confidence": 0.9}')
    _insert(store, "TV/D/d.mkv", "es", "es</th>")

    assert store.requeue_unparseable() == 2

    snapshot = {p: store.get(p) for p in ("TV/A/a.mkv", "TV/B/b.mkv", "TV/C/c.mkv", "TV/D/d.mkv")}
    assert store.requeue_unparseable() == 0
    assert {p: store.get(p) for p in snapshot} == snapshot
    store.close()


def test_logs_count(tmp_path, caplog):
    store = _store(tmp_path)
    _insert(store, "TV/A/a.mkv", "it", "It is Japanese.")

    with caplog.at_level(logging.INFO, logger="subarr.enrichment"):
        assert store.requeue_unparseable() == 1
    assert any(
        r.getMessage() == "enrichment: cleared 1 stored language guess(es) that were not valid replies"
        for r in caplog.records
    )

    caplog.clear()
    empty = _store(tmp_path, "empty.db")
    with caplog.at_level(logging.INFO, logger="subarr.enrichment"):
        assert empty.requeue_unparseable() == 0
    assert any(
        r.getMessage() == "enrichment: cleared 0 stored language guess(es) that were not valid replies"
        for r in caplog.records
    )
    store.close()
    empty.close()


class FakeLLM:
    model = "m"

    def __init__(self, reply):
        self.reply = reply
        self.calls = 0

    async def generate(self, prompt, **kwargs):
        self.calls += 1
        return self.reply


@pytest.mark.asyncio
async def test_cleared_row_is_reasked(tmp_path):
    from subarr.enrichment import enrich_one

    store = _store(tmp_path)
    path = "@x/Show/S01E01.mkv"
    _insert(store, path, "it", "It is Japanese.")
    assert store.requeue_unparseable() == 1

    llm = FakeLLM('{"iso_code": "ja", "confidence": 0.9}')
    result = await enrich_one(canonical_path=path, title="Show", ollama=llm, store=store)

    assert llm.calls == 1
    assert result.cached is False
    assert result.iso_code == "ja"
    assert store.get(path)["error"] is None
    store.close()


@pytest.mark.asyncio
async def test_uncleared_row_is_cache_hit(tmp_path):
    from subarr.enrichment import enrich_one

    store = _store(tmp_path)
    path = "@x/Show/S01E01.mkv"
    _insert(store, path, "ko", '{"iso_code": "ko", "confidence": 0.9}')
    assert store.requeue_unparseable() == 0

    llm = FakeLLM('{"iso_code": "ja", "confidence": 0.9}')
    result = await enrich_one(canonical_path=path, title="Show", ollama=llm, store=store)

    assert llm.calls == 0
    assert result.cached is True
    assert result.iso_code == "ko"
    store.close()


@pytest.fixture
def seeded_db(subarr_env):
    from subarr.config import settings
    from subarr.enrichment import EnrichmentStore
    from subarr.migrate import run_migrations

    run_migrations(settings.db_path)
    store = EnrichmentStore(settings.db_path)
    _insert(store, "TV/A/a.mkv", "it", "It is Japanese.")
    store.close()
    return settings.db_path


def test_lifespan_clears_seeded_row(seeded_db, app_with_stub):
    from subarr.enrichment import EnrichmentStore

    store = EnrichmentStore(seeded_db)
    row = store.get("TV/A/a.mkv")
    assert row["iso_code"] is None
    assert row["error"] == "reply was not valid JSON"
    store.close()


@pytest.fixture
def broken_requeue(subarr_env, monkeypatch):
    from subarr.app import EnrichmentStore

    calls = []

    def _boom(self):
        calls.append(1)
        raise RuntimeError("x")

    monkeypatch.setattr(EnrichmentStore, "requeue_unparseable", _boom)
    return calls


def test_lifespan_survives_requeue_failure(broken_requeue, app_with_stub):
    assert len(broken_requeue) == 1
    assert app_with_stub.get("/api/health/tasks").status_code == 200


def test_unexpected_error_rolls_back(tmp_path, monkeypatch, caplog):
    from subarr import enrichment

    store = _store(tmp_path)
    _insert(store, "TV/A/a.mkv", "it", "It is Japanese.")
    _insert(store, "TV/B/b.mkv", "en", "BOOM")

    original = enrichment._parse_structured

    def _wrapped(raw):
        if raw == "BOOM":
            raise ValueError("x")
        return original(raw)

    monkeypatch.setattr(enrichment, "_parse_structured", _wrapped)

    with caplog.at_level(logging.INFO, logger="subarr.enrichment"), pytest.raises(ValueError):
        store.requeue_unparseable()

    row = store.get("TV/A/a.mkv")
    assert row["iso_code"] == "it"
    assert row["error"] is None
    assert not any("cleared" in r.getMessage() for r in caplog.records)
    store.close()
