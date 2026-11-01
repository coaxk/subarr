"""Enrichment prompt (#616): library name + relative path, strict reply parsing."""

from __future__ import annotations

import json

import httpx
import pytest


class _RecordingLLM:
    model = "m1"

    def __init__(self, reply: str = '{"iso_code": "ja", "confidence": 0.9, "reasoning": "r"}', exc=None):
        self.reply = reply
        self.exc = exc
        self.calls: list[tuple[str, dict]] = []

    async def generate(self, prompt, **kwargs):
        self.calls.append((prompt, kwargs))
        if self.exc is not None:
            raise self.exc
        return self.reply


def _store(tmp_path):
    from subarr.enrichment import EnrichmentStore
    from subarr.migrate import run_migrations

    db = tmp_path / "enrich.db"
    run_migrations(db)
    return EnrichmentStore(db)


def _anime_libraries(tmp_path):
    from subarr import config
    from subarr.libraries import Library

    libs = (
        config.settings.libraries[0],
        Library(
            slug="anime",
            name="Anime",
            fs_root=tmp_path,
            subgen_prefix="/media",
            arr_prefix="/data/Anime/",
        ),
    )
    old = config.settings.libraries
    object.__setattr__(config.settings, "libraries", libs)
    return old


def test_prompt_library_and_relative_path(subarr_env, tmp_path):
    from subarr import config
    from subarr.enrichment import build_user_prompt

    old = _anime_libraries(tmp_path)
    try:
        prompt = build_user_prompt("T", "@anime/Show (2013)/S01E01.mkv")
        assert prompt == "Title: T\nLibrary: Anime\nPath: Show (2013)/S01E01.mkv"
        assert "@anime" not in prompt
    finally:
        object.__setattr__(config.settings, "libraries", old)


def test_prompt_default_library(subarr_env):
    from subarr import config
    from subarr.enrichment import build_user_prompt

    prompt = build_user_prompt("T", "TV/Show/S01E01.mkv")
    assert prompt == (f"Title: T\nLibrary: {config.settings.libraries[0].name}\nPath: TV/Show/S01E01.mkv")


def test_prompt_unknown_slug_falls_back(subarr_env):
    from subarr import config
    from subarr.enrichment import build_user_prompt

    prompt = build_user_prompt("T", "@nope/x/y.mkv")
    assert prompt == f"Title: T\nLibrary: {config.settings.libraries[0].name}\nPath: x/y.mkv"


def test_relative_part(subarr_env):
    from subarr.paths import relative_part

    assert relative_part("@disk2/Movies/x") == "Movies/x"
    assert relative_part("TV/Show") == "TV/Show"
    assert relative_part("@disk2") == ""
    assert relative_part("/TV/Show/") == "TV/Show"


def test_system_prompt_wording(subarr_env):
    from subarr.enrichment import _SYSTEM_PROMPT

    assert "the library it is filed in and its path inside that library" in _SYSTEM_PROMPT
    assert "folder path" not in _SYSTEM_PROMPT
    assert _SYSTEM_PROMPT.startswith("You are a media-cataloguing assistant.")
    assert _SYSTEM_PROMPT.endswith("pt, hi.")


@pytest.mark.asyncio
async def test_enrich_sends_new_prompt_and_schema_name(subarr_env, tmp_path):
    from subarr import config
    from subarr.enrichment import _LANG_SCHEMA, _SYSTEM_PROMPT, build_user_prompt, enrich_one

    old = _anime_libraries(tmp_path)
    try:
        llm = _RecordingLLM()
        store = _store(tmp_path)
        await enrich_one(canonical_path="@anime/Show/S01E01.mkv", title="Show", ollama=llm, store=store)
        prompt, kwargs = llm.calls[0]
        assert prompt == build_user_prompt("Show", "@anime/Show/S01E01.mkv")
        assert kwargs["schema_name"] == "subarr_lang"
        assert kwargs["format_schema"] is _LANG_SCHEMA
        assert kwargs["system"] is _SYSTEM_PROMPT
        assert kwargs["num_predict"] == 120
        assert kwargs["temperature"] == 0.0
        store.close()
    finally:
        object.__setattr__(config.settings, "libraries", old)


@pytest.mark.asyncio
async def test_prose_reply_with_schema_raises(subarr_env, tmp_path):
    from subarr.enrichment import enrich_one
    from subarr.integrations.llm import LLMError

    llm = _RecordingLLM("It is Japanese.")
    store = _store(tmp_path)
    path = "TV/Show/S01E01.mkv"
    with pytest.raises(LLMError) as excinfo:
        await enrich_one(canonical_path=path, title="Show", ollama=llm, store=store)
    assert type(excinfo.value).__name__ == "NotJSONReply"
    row = store.get(path)
    assert row["iso_code"] is None
    assert row["error"] == "reply was not valid JSON"
    assert row["raw_response"] == "It is Japanese."
    store.close()


@pytest.mark.asyncio
async def test_prose_reply_is_not_cached(subarr_env, tmp_path):
    from subarr.enrichment import enrich_one
    from subarr.integrations.llm import LLMError

    path = "TV/Show/S01E01.mkv"
    store = _store(tmp_path)
    with pytest.raises(LLMError):
        await enrich_one(
            canonical_path=path, title="Show", ollama=_RecordingLLM("It is Japanese."), store=store
        )
    assert store.get(path)["error"]

    good = _RecordingLLM()
    result = await enrich_one(canonical_path=path, title="Show", ollama=good, store=store)
    assert len(good.calls) == 1
    assert result.cached is False
    row = store.get(path)
    assert row["iso_code"] == "ja"
    assert row["error"] is None
    assert row["raw_response"] == '{"iso_code": "ja", "confidence": 0.9, "reasoning": "r"}'
    store.close()


@pytest.mark.parametrize("reply", ["", "   ", '{"iso_code": "ko"'])
@pytest.mark.asyncio
async def test_empty_and_truncated_replies_raise(subarr_env, tmp_path, reply):
    from subarr.enrichment import enrich_one
    from subarr.integrations.llm import LLMError

    store = _store(tmp_path)
    path = "TV/Show/S01E01.mkv"
    with pytest.raises(LLMError) as excinfo:
        await enrich_one(canonical_path=path, title="Show", ollama=_RecordingLLM(reply), store=store)
    assert type(excinfo.value).__name__ == "NotJSONReply"
    assert store.get(path)["iso_code"] is None
    store.close()


@pytest.mark.parametrize(
    "reply,expected",
    [("ko\n", "ko"), ("'ja'", "ja"), ("es</th>\n", "es"), ("FR.", "fr")],
)
@pytest.mark.asyncio
async def test_bare_code_reply_still_parses(subarr_env, tmp_path, reply, expected):
    from subarr.enrichment import enrich_one

    store = _store(tmp_path)
    path = "TV/Show/S01E01.mkv"
    result = await enrich_one(canonical_path=path, title="Show", ollama=_RecordingLLM(reply), store=store)
    assert result.iso_code == expected
    assert store.get(path)["error"] is None
    store.close()


@pytest.mark.asyncio
async def test_bare_und_is_none_not_error(subarr_env, tmp_path):
    from subarr.enrichment import enrich_one

    store = _store(tmp_path)
    path = "TV/Show/S01E01.mkv"
    result = await enrich_one(canonical_path=path, title="Show", ollama=_RecordingLLM("und"), store=store)
    assert result.iso_code is None
    assert store.get(path)["error"] is None
    store.close()


@pytest.mark.asyncio
async def test_sentence_with_code_raises(subarr_env, tmp_path):
    from subarr.enrichment import enrich_one
    from subarr.integrations.llm import LLMError

    store = _store(tmp_path)
    with pytest.raises(LLMError) as excinfo:
        await enrich_one(
            canonical_path="TV/Show/S01E01.mkv",
            title="Show",
            ollama=_RecordingLLM("ko (Korean)"),
            store=store,
        )
    assert type(excinfo.value).__name__ == "NotJSONReply"
    store.close()


@pytest.mark.asyncio
async def test_json_reply_unchanged(subarr_env, tmp_path):
    from subarr.enrichment import enrich_one

    store = _store(tmp_path)
    result = await enrich_one(
        canonical_path="TV/Show/S01E01.mkv",
        title="Show",
        ollama=_RecordingLLM('{"iso_code": "ko", "confidence": 0.9, "reasoning": "r"}</th>'),
        store=store,
    )
    assert result.iso_code == "ko"
    assert result.confidence == 0.9
    assert result.reasoning == "r"
    store.close()


@pytest.mark.asyncio
async def test_enrich_catches_llm_error(subarr_env, tmp_path):
    from subarr.enrichment import enrich_one
    from subarr.integrations.llm import LLMError

    store = _store(tmp_path)
    path = "TV/Show/S01E01.mkv"
    with pytest.raises(LLMError) as excinfo:
        await enrich_one(
            canonical_path=path, title="Show", ollama=_RecordingLLM(exc=LLMError("boom")), store=store
        )
    assert str(excinfo.value) == "boom"
    row = store.get(path)
    assert row["error"] == "boom"
    assert row["raw_response"] == ""
    assert row["iso_code"] is None
    store.close()


def test_bare_token_cases(subarr_env):
    from subarr.enrichment import _bare_token

    assert _bare_token("ko\n") == "ko"
    assert _bare_token("'ja'") == "ja"
    assert _bare_token("es</th>\n") == "es"
    assert _bare_token("und") == "und"
    assert _bare_token("FR.") == "FR"
    assert _bare_token("It is Japanese.") is None
    assert _bare_token("ko (Korean)") is None
    assert _bare_token("") is None
    assert _bare_token("japanese") is None


@pytest.mark.asyncio
async def test_ollama_generate_ignores_schema_name(subarr_env):
    from subarr.enrichment import _LANG_SCHEMA
    from subarr.integrations.ollama import OllamaClient

    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"response": "ok"})

    c = OllamaClient(base_url="http://ollama:11434", model="test-model")
    c._client = httpx.AsyncClient(base_url="http://ollama:11434", transport=httpx.MockTransport(handler))
    reply = await c.generate("p", format_schema=_LANG_SCHEMA, schema_name="subarr_lang")
    await c.generate("p", format_schema=_LANG_SCHEMA)
    assert reply == "ok"
    assert len(bodies) == 2
    assert bodies[0] == bodies[1]
    assert "subarr_lang" not in json.dumps(bodies[0])
    await c.aclose()


def test_onboarding_names_still_exported(subarr_env):
    from subarr.enrichment import _LANG_SCHEMA, _SYSTEM_PROMPT

    assert isinstance(_SYSTEM_PROMPT, str)
    assert _LANG_SCHEMA["required"] == ["iso_code", "confidence"]
