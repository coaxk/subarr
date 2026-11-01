"""scripts/eval_title_language.py: prompt A/B harness (#616)."""

from __future__ import annotations

import importlib.util
from dataclasses import replace
from pathlib import Path

import httpx
import pytest


def _load():
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location(
        "eval_title_language", root / "scripts" / "eval_title_language.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _FakeLLM:
    model = "fake-model"

    def __init__(self, reply: str = '{"iso_code": "ja", "confidence": 0.9, "reasoning": "r"}', exc=None):
        self.reply = reply
        self.exc = exc
        self.prompts: list[str] = []
        self.calls: list[dict] = []
        self.closed = 0

    def is_configured(self) -> bool:
        return True

    async def generate(self, prompt, **kwargs):
        self.prompts.append(prompt)
        self.calls.append(kwargs)
        if self.exc is not None:
            raise self.exc
        return self.reply

    async def aclose(self):
        self.closed += 1


class _FakeArr:
    def __init__(self, items=None, exc=None):
        self.items = items or []
        self.exc = exc
        self.closed = 0

    async def series(self):
        if self.exc is not None:
            raise self.exc
        return self.items

    async def movies(self):
        if self.exc is not None:
            raise self.exc
        return self.items

    async def aclose(self):
        self.closed += 1


def _patch_arrs(monkeypatch, sonarr, radarr):
    import subarr.integrations.radarr as radarr_mod
    import subarr.integrations.sonarr as sonarr_mod

    monkeypatch.setattr(sonarr_mod, "SonarrClient", lambda: sonarr)
    monkeypatch.setattr(radarr_mod, "RadarrClient", lambda: radarr)


def _patch_llm(monkeypatch, llm):
    import subarr.integrations.llm as llm_mod

    monkeypatch.setattr(llm_mod, "build_llm_client", lambda: llm)


def test_canonical_for(subarr_env):
    from subarr.libraries import Library

    mod = _load()
    libs = (
        Library(
            slug="",
            name="Media",
            fs_root=Path("/media"),
            subgen_prefix="/media",
            arr_prefix="/data/Media/",
        ),
        Library(
            slug="anime",
            name="Anime",
            fs_root=Path("/anime"),
            subgen_prefix="/media",
            arr_prefix="/data/Media/Anime/",
        ),
    )
    assert mod.canonical_for("/data/Media/TV/Show", libs) == "TV/Show"
    assert mod.canonical_for("/data/Media/Anime/Show", libs) == "@anime/Show"
    assert mod.canonical_for("/data/MediaX/Show", libs) is None
    assert mod.canonical_for("/other/Show", libs) is None
    assert mod.canonical_for("/data/Media", libs) == ""


def test_canonical_for_skips_empty_prefix(subarr_env):
    from subarr.libraries import Library

    mod = _load()
    libs = (
        Library(
            slug="empty",
            name="Empty",
            fs_root=Path("/empty"),
            subgen_prefix="/empty",
            arr_prefix="",
        ),
    )
    assert mod.canonical_for("/data/Media/TV/Show", libs) is None


def test_truth_code(subarr_env):
    mod = _load()
    assert mod.truth_code({"originalLanguage": {"name": "Japanese"}}) == "ja"
    assert mod.truth_code({"originalLanguage": {"name": "English"}}) == "en"
    assert mod.truth_code({"originalLanguage": {"name": "Any"}}) is None
    assert mod.truth_code({"originalLanguage": {"name": "Unknown"}}) is None
    assert mod.truth_code({}) is None
    assert mod.truth_code({"originalLanguage": None}) is None


def _sample_rows():
    rows = [{"title": f"E{i:02d}", "canonical": f"en/{i}", "truth": "en"} for i in range(10)]
    rows += [{"title": f"O{i:02d}", "canonical": f"other/{i}", "truth": "ko"} for i in range(10)]
    return rows


def test_pick_sample_split_and_deterministic(subarr_env):
    mod = _load()
    rows = _sample_rows()
    sample = mod.pick_sample(rows, 6, 0)
    assert len(sample) == 6
    assert all(r["truth"] == "en" for r in sample[:3])
    assert all(r["truth"] != "en" for r in sample[3:])
    assert mod.pick_sample(list(reversed(rows)), 6, 0) == sample
    assert any(mod.pick_sample(rows, 6, seed) != sample for seed in range(1, 6))


def test_pick_sample_short_side(subarr_env):
    mod = _load()
    rows = [{"title": "E", "canonical": "en", "truth": "en"}]
    rows += [{"title": f"O{i:02d}", "canonical": f"other/{i}", "truth": "ko"} for i in range(10)]
    sample = mod.pick_sample(rows, 6, 0)
    assert len(sample) == 4
    assert [r["truth"] for r in sample] == ["en", "ko", "ko", "ko"]


def test_score(subarr_env):
    mod = _load()
    results = [
        {"title": "A", "canonical": "a", "truth": "en", "guess": "en", "error": None},
        {"title": "B", "canonical": "b", "truth": "en", "guess": "ja", "error": None},
        {"title": "C", "canonical": "c", "truth": "ko", "guess": "ko", "error": None},
        {"title": "D", "canonical": "d", "truth": "ja", "guess": None, "error": "boom"},
    ]
    stats = mod.score(results)
    assert stats["correct"] == 2
    assert stats["n"] == 4
    assert (stats["en_correct"], stats["en_n"]) == (1, 2)
    assert (stats["other_correct"], stats["other_n"]) == (1, 2)
    assert [r["canonical"] for r in stats["disagreements"]] == ["b", "d"]


@pytest.mark.asyncio
async def test_ask_maps_errors(subarr_env):
    from subarr.integrations.llm import LLMError
    from subarr.integrations.openai_compat import LLMCoolingDown

    mod = _load()

    assert await mod.ask(_FakeLLM("It is Japanese."), "sys", "p") == (None, "reply was not valid JSON")
    assert await mod.ask(_FakeLLM(exc=LLMError("boom")), "sys", "p") == (None, "boom")
    with pytest.raises(LLMCoolingDown):
        await mod.ask(_FakeLLM(exc=LLMCoolingDown("x")), "sys", "p")

    ok = _FakeLLM('{"iso_code": "ja", "confidence": 0.9, "reasoning": "r"}')
    assert await mod.ask(ok, "sys", "p") == ("ja", None)
    assert ok.calls[0]["schema_name"] == "subarr_lang"
    assert ok.calls[0]["temperature"] == 0.0
    assert ok.calls[0]["num_predict"] == 120


def test_old_prompt_is_the_pre_change_text(subarr_env):
    from subarr import enrichment

    mod = _load()
    assert "and folder path, identify the ORIGINAL" in mod.OLD_SYSTEM_PROMPT
    assert (
        mod.OLD_SYSTEM_PROMPT.replace(
            "title and folder path,",
            "title, the library it is filed in and its path inside that library,",
        )
        == enrichment._SYSTEM_PROMPT
    )


def test_main_rejects_small_n(subarr_env):
    mod = _load()
    with pytest.raises(SystemExit) as excinfo:
        mod.main(["--n", "1"])
    assert excinfo.value.code == 2


def test_main_not_configured(subarr_env, monkeypatch, capsys):
    mod = _load()
    import subarr.integrations.llm as llm_mod
    import subarr.integrations.radarr as radarr_mod
    import subarr.integrations.sonarr as sonarr_mod

    class _Unconfigured:
        def is_configured(self):
            return False

    built: list[object] = []

    def _arr():
        built.append(object())
        return _FakeArr()

    monkeypatch.setattr(llm_mod, "build_llm_client", lambda: _Unconfigured())
    monkeypatch.setattr(sonarr_mod, "SonarrClient", _arr)
    monkeypatch.setattr(radarr_mod, "RadarrClient", _arr)

    assert mod.main(["--n", "4"]) == 1
    assert "LLM not configured" in capsys.readouterr().out
    assert built == []


def test_main_end_to_end_no_writes(subarr_env, monkeypatch, capsys):
    import subarr.enrichment as enrichment_mod
    from subarr import config

    mod = _load()
    old_libs = config.settings.libraries
    object.__setattr__(
        config.settings,
        "libraries",
        (replace(old_libs[0], arr_prefix="/data/Media/"),),
    )
    try:
        llm = _FakeLLM()
        sonarr = _FakeArr(
            items=[
                {
                    "title": "Anime Show",
                    "path": "/data/Media/TV/Anime Show",
                    "originalLanguage": {"name": "Japanese"},
                },
                {
                    "title": "British Show",
                    "path": "/data/Media/TV/British Show",
                    "originalLanguage": {"name": "English"},
                },
                {
                    "title": "Outside",
                    "path": "/other/Show",
                    "originalLanguage": {"name": "Japanese"},
                },
                {
                    "title": "Any Lang",
                    "path": "/data/Media/TV/Any Lang",
                    "originalLanguage": {"name": "Any"},
                },
                {"title": "No Path", "originalLanguage": {"name": "Japanese"}},
            ]
        )
        radarr = _FakeArr(
            items=[
                {
                    "title": "Film",
                    "path": "/data/Media/Movies/Film",
                    "originalLanguage": {"name": "Any"},
                }
            ]
        )
        _patch_llm(monkeypatch, llm)
        _patch_arrs(monkeypatch, sonarr, radarr)

        def _upsert(*args, **kwargs):
            raise AssertionError("EnrichmentStore.upsert was called")

        monkeypatch.setattr(enrichment_mod.EnrichmentStore, "upsert", _upsert)

        assert mod.main(["--n", "2", "--arm", "both"]) == 0
        out = capsys.readouterr().out
        assert "sample: 2 of 2 requested" in out
        assert "old: 1/2 correct" in out
        assert "new: 1/2 correct" in out
        assert out.index("old: 1/2 correct") < out.index("new: 1/2 correct")
        assert (llm.closed, sonarr.closed, radarr.closed) == (1, 1, 1)
        assert any("\nLibrary: " in p for p in llm.prompts)
        assert any("\nFolder path: " in p for p in llm.prompts)
    finally:
        object.__setattr__(config.settings, "libraries", old_libs)


def test_main_arr_failure_reported(subarr_env, monkeypatch, capsys):
    from subarr import config

    mod = _load()
    old_key = config.settings.sonarr_api_key
    object.__setattr__(config.settings, "sonarr_api_key", "abc")
    try:
        llm = _FakeLLM()
        sonarr = _FakeArr(exc=httpx.ConnectError("refused"))
        radarr = _FakeArr(
            items=[
                {
                    "title": "Film",
                    "path": "/data/Media/Movies/Film",
                    "originalLanguage": {"name": "Japanese"},
                }
            ]
        )
        _patch_llm(monkeypatch, llm)
        _patch_arrs(monkeypatch, sonarr, radarr)

        assert mod.main(["--n", "4"]) == 0
        out = capsys.readouterr().out
        assert "sonarr: skipped (ConnectError)" in out
        assert "radarr: skipped" not in out
        assert "1/1 correct" in out
        assert "abc" not in out
    finally:
        object.__setattr__(config.settings, "sonarr_api_key", old_key)


def test_main_empty_sample(subarr_env, monkeypatch, capsys):
    mod = _load()
    llm = _FakeLLM()
    sonarr = _FakeArr(exc=httpx.ConnectError("refused"))
    radarr = _FakeArr(exc=httpx.ConnectError("refused"))
    _patch_llm(monkeypatch, llm)
    _patch_arrs(monkeypatch, sonarr, radarr)

    assert mod.main(["--n", "4"]) == 1
    out = capsys.readouterr().out
    assert "no rows to evaluate" in out
    assert llm.prompts == []
    assert (llm.closed, sonarr.closed, radarr.closed) == (1, 1, 1)
