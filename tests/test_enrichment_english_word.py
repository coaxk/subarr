"""Enrichment (#616): a bare reply that is also an English word is not a language code."""

from __future__ import annotations

import pytest

ENGLISH_WORD_CODES = ["am", "as", "be", "ha", "haw", "he", "hi", "id", "is", "it", "my", "no", "so", "yo"]


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


@pytest.mark.parametrize("word", ENGLISH_WORD_CODES)
def test_bare_english_word_rejected(word):
    from subarr.enrichment import NotJSONReply, _parse_structured

    with pytest.raises(NotJSONReply) as excinfo:
        _parse_structured(word)
    assert str(excinfo.value) == "bare reply is an English word, not a language code"


@pytest.mark.parametrize("raw", ["No", "NO", "no.", "'no'", "No\n", "no</th>", "It", "HAW"])
def test_bare_english_word_variants_rejected(raw):
    from subarr.enrichment import NotJSONReply, _parse_structured

    with pytest.raises(NotJSONReply):
        _parse_structured(raw)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("ja", ("ja", None, None)),
        ("fr", ("fr", None, None)),
        ("ko\n", ("ko", None, None)),
        ("'de'", ("de", None, None)),
        ("es</th>", ("es", None, None)),
        ("en", ("en", None, None)),
        ("FR.", ("fr", None, None)),
        ("yue", ("yue", None, None)),
    ],
)
def test_bare_unambiguous_code_accepted(raw, expected):
    from subarr.enrichment import _parse_structured

    assert _parse_structured(raw) == expected


def test_bare_und_still_null():
    from subarr.enrichment import _parse_structured

    assert _parse_structured("und") == (None, None, None)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ('{"iso_code": "no", "confidence": 0.9}', ("no", 0.9, None)),
        ('{"iso_code": "it", "confidence": 0.8, "reasoning": "r"}', ("it", 0.8, "r")),
        ('{"iso_code": "he", "confidence": 0.7}</th>', ("he", 0.7, None)),
    ],
)
def test_json_reply_with_word_code_accepted(raw, expected):
    from subarr.enrichment import _parse_structured

    assert _parse_structured(raw) == expected


def test_prose_still_rejected():
    from subarr.enrichment import NotJSONReply, _parse_structured

    with pytest.raises(NotJSONReply):
        _parse_structured("No idea.")


def test_bare_token_helper_unchanged():
    from subarr.enrichment import _bare_token

    assert _bare_token("No") == "No"
    assert _bare_token("no.") == "no"


def test_constant_is_exact():
    from subarr.enrichment import _ENGLISH_WORD_CODES

    assert _ENGLISH_WORD_CODES == frozenset(
        {"am", "as", "be", "ha", "haw", "he", "hi", "id", "is", "it", "my", "no", "so", "yo"}
    )


class FakeLLM:
    model = "m"

    def __init__(self, reply):
        self.reply = reply
        self.calls = 0

    async def generate(self, prompt, **kwargs):
        self.calls += 1
        return self.reply


@pytest.mark.asyncio
async def test_enrich_one_word_reply_stores_failure(tmp_path):
    from subarr.enrichment import NotJSONReply, enrich_one

    store = _store(tmp_path)
    path = "@x/Show/S01E01.mkv"
    llm = FakeLLM("No")

    with pytest.raises(NotJSONReply):
        await enrich_one(canonical_path=path, title="Show", ollama=llm, store=store)

    row = store.get(path)
    assert row["iso_code"] is None
    assert row["raw_response"] == "No"
    assert row["error"] == "bare reply is an English word, not a language code"
    assert llm.calls == 1
    store.close()


@pytest.mark.asyncio
async def test_enrich_one_code_reply_still_cached(tmp_path):
    from subarr.enrichment import enrich_one

    store = _store(tmp_path)
    path = "@x/Show/S01E01.mkv"
    llm = FakeLLM("ja")

    result = await enrich_one(canonical_path=path, title="Show", ollama=llm, store=store)
    assert result.iso_code == "ja"
    row = store.get(path)
    assert row["iso_code"] == "ja"
    assert row["error"] is None

    again = await enrich_one(canonical_path=path, title="Show", ollama=llm, store=store)
    assert again.cached is True
    assert llm.calls == 1
    store.close()


def test_requeue_clears_stored_word_row(tmp_path):
    store = _store(tmp_path)
    path_a = "TV/A/a.mkv"
    path_b = "TV/B/b.mkv"
    _insert(store, path_a, "no", "No")
    _insert(store, path_b, "ja", "ja")
    before_b = store.get(path_b)

    assert store.requeue_unparseable() == 1

    row_a = store.get(path_a)
    assert row_a["iso_code"] is None
    assert row_a["error"] == "reply was not valid JSON"
    assert store.get(path_b) == before_b
    assert store.requeue_unparseable() == 0
    store.close()


def test_requeue_keeps_json_word_row(tmp_path):
    store = _store(tmp_path)
    path = "TV/A/a.mkv"
    _insert(store, path, "no", '{"iso_code": "no", "confidence": 0.9}')
    before = store.get(path)

    assert store.requeue_unparseable() == 0
    assert store.get(path) == before
    store.close()


@pytest.mark.asyncio
async def test_cleared_word_row_is_reasked(tmp_path):
    from subarr.enrichment import enrich_one

    store = _store(tmp_path)
    path = "@x/Show/S01E01.mkv"
    _insert(store, path, "no", "No")
    assert store.requeue_unparseable() == 1

    llm = FakeLLM('{"iso_code": "ja", "confidence": 0.9}')
    result = await enrich_one(canonical_path=path, title="Show", ollama=llm, store=store)

    assert llm.calls == 1
    assert result.cached is False
    assert result.iso_code == "ja"
    store.close()
