"""#633: choose transcribe vs translate per file (pure logic, capabilities, config).

The contract with subarr-subgen is the shared fixture
tests/fixtures/subgen_track_choice_cases.json. subarr mirrors subgen's own
choice of audio track so it can predict the language Whisper will transcribe.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

FIXTURE = Path(__file__).parent / "fixtures" / "subgen_track_choice_cases.json"

# Update BOTH repos together: subarr-subgen pins the same constant in
# tests/smoke_track_choice.py. A change on one side only fails one suite loudly.
FIXTURE_SHA256 = "fb9ac252754a8c744f23aa82ef9cb262590d806ffb9bb22485087184c797561e"

CASES = json.loads(FIXTURE.read_text(encoding="utf-8"))["cases"]


def test_these_tests_import_this_checkouts_source():
    """A subarr worktree once ran its tests against MAIN's source (the editable
    install names an absolute path). pyproject pythonpath must win."""
    import subarr

    src = (Path(__file__).resolve().parents[1] / "src").resolve()
    assert Path(subarr.__file__).resolve().is_relative_to(src), subarr.__file__


def test_contract_fixture_is_byte_identical_to_the_subgen_copy():
    assert hashlib.sha256(FIXTURE.read_bytes()).hexdigest() == FIXTURE_SHA256


# ---- subgen_track_language ---------------------------------------------------


def test_track_language_pins_b_and_t_pairs():
    from subarr.per_file_task import subgen_track_language as f

    assert f("ger") == "de" and f("deu") == "de"
    assert f("fre") == "fr" and f("fra") == "fr"
    assert f("chi") == "zh" and f("zho") == "zh"
    assert f("jpn") == "ja" and f("eng") == "en"


def test_track_language_two_letter_empty_und_and_unknown_are_none():
    from subarr.per_file_task import subgen_track_language as f

    # `en` is NOT a 639-2 code: subgen reads a two-letter tag as unknown.
    for raw in ("en", "und", "", None, "xxx", "Unknown", " eng", "eng "):
        assert f(raw) is None, raw


def test_track_language_is_case_sensitive_like_upstream():
    """KNOWN DIVERGENCE: subgen sees `ENG` as unknown; the probe store already
    lowercases tags, so on real data this reads as `en`. Pinned, not fixed."""
    from subarr.per_file_task import subgen_track_language as f

    assert f("ENG") is None


def test_iso_table_matches_upstream_shape():
    from subarr.langs import UPSTREAM_ISO639_2_TO_1

    assert "und" not in UPSTREAM_ISO639_2_TO_1
    assert all(len(k) == 3 and len(v) in (2, 3) for k, v in UPSTREAM_ISO639_2_TO_1.items())


# ---- resolve_source_language: every contract case ----------------------------


def _p1(p):
    """Fixture `preferred` is written the way subgen's env is (639-2 or 639-1);
    the capability subarr receives is subgen's to_iso_639_1(), i.e. 639-1."""
    from subarr.per_file_task import subgen_track_language

    if p is None:
        return None
    return p if len(p) == 2 else subgen_track_language(p)


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_resolve_source_language_matches_the_contract(case):
    from subarr.per_file_task import resolve_source_language

    got = resolve_source_language(
        [(t, d) for t, d in case["tracks"]],
        forced=case["forced"],
        force_detected=_p1(case["force_detected"]),
        preferred=tuple(_p1(p) for p in case["preferred"]),
    )
    assert got == case["expected_source"]


def test_null_preferred_with_unknown_track_stops_the_scan():
    """Mutation (a) lives here: the falsy-NONE fall-through must skip later
    entries when an unknown-language track exists."""
    from subarr.per_file_task import resolve_source_language

    tracks = [("und", False), ("eng", False), ("jpn", True)]
    assert resolve_source_language(tracks, forced=None, force_detected=None, preferred=(None, "en")) == "ja"


def test_null_preferred_without_unknown_track_continues():
    from subarr.per_file_task import resolve_source_language

    tracks = [("jpn", True), ("eng", False)]
    assert resolve_source_language(tracks, forced=None, force_detected=None, preferred=(None, "en")) == "en"


def test_forced_beats_everything_and_force_detected_beats_preferred():
    from subarr.per_file_task import resolve_source_language as r

    t = [("jpn", True), ("eng", False)]
    assert r(t, forced="fr", force_detected="ko", preferred=("en",)) == "fr"
    assert r(t, forced=None, force_detected="ko", preferred=("en",)) == "ko"
    assert r(t, forced="", force_detected="", preferred=("en",)) == "en"


def test_no_tracks_and_no_forced_is_unknown():
    from subarr.per_file_task import resolve_source_language as r

    assert r([], forced=None, force_detected=None, preferred=("en",)) is None
    assert r([], forced="fr", force_detected=None, preferred=("en",)) == "fr"


# ---- choose_task / producible_for_task ---------------------------------------


@pytest.mark.parametrize(
    "source,wanted,expected",
    [
        ("en", {"en", "es"}, "transcribe"),
        ("es", {"en", "es"}, "transcribe"),
        ("ja", {"en", "es"}, "translate"),
        (None, {"en", "es"}, None),
        ("ja", {"es"}, None),  # not wanted, and no English to translate into
        ("ja", {"en"}, "translate"),
        ("en", {"es"}, None),
        ("es", {"es"}, "transcribe"),
    ],
)
def test_choose_task(source, wanted, expected):
    from subarr.per_file_task import choose_task

    assert choose_task(source, wanted) == expected


def test_producible_for_task():
    from subarr.per_file_task import producible_for_task

    assert producible_for_task("transcribe", "es") == {"es"}
    assert producible_for_task("translate", "ja") == {"en"}


# ---- active_config -----------------------------------------------------------


def _caps(**kw):
    base = dict(
        per_request_task=True,
        preferred_audio_languages=("en",),
        preferred_audio_languages_unreadable=(),
        force_detected_language_to=None,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def test_active_config_all_inputs_present():
    from subarr.per_file_task import PerFileTaskConfig, active_config

    cfg = active_config(True, ["EN", "es", "es", "Spanish"], _caps(force_detected_language_to="ja"))
    assert cfg == PerFileTaskConfig(wanted=frozenset({"en", "es"}), preferred=("en",), force_detected="ja")


@pytest.mark.parametrize(
    "enabled,wanted,caps",
    [
        (False, ["en"], _caps()),
        (True, [], _caps()),
        (True, ["", None, "und"], _caps()),
        (True, ["en"], _caps(per_request_task=False)),
        (True, ["en"], _caps(preferred_audio_languages=None)),
        (True, ["en"], _caps(preferred_audio_languages_unreadable=("klingon",))),
        (True, ["en"], None),
    ],
)
def test_active_config_inert_in_every_missing_input_state(enabled, wanted, caps):
    from subarr.per_file_task import active_config

    assert active_config(enabled, wanted, caps) is None


def test_active_config_has_no_eng_fallback_for_an_unadvertised_list():
    from subarr.per_file_task import active_config

    assert active_config(True, ["en"], _caps(preferred_audio_languages=None)) is None


def test_active_config_keeps_null_and_duplicate_preferred_entries_in_place():
    from subarr.per_file_task import active_config

    cfg = active_config(True, ["en"], _caps(preferred_audio_languages=(None, "en", "en")))
    assert cfg.preferred == (None, "en", "en")


# ---- capabilities parsing (B1) -----------------------------------------------


async def _probe(caps_block):
    from subarr.subgen_client import SubgenClient

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/status":
            return httpx.Response(200, json={"version": "Subgen 2026.05.3"})
        if request.url.path == "/queue":
            return httpx.Response(200, json={"queued": [], "processing": [], "capabilities": caps_block})
        return httpx.Response(404)

    c = SubgenClient(base_url="http://fake-subgen:9000")
    c._client = httpx.AsyncClient(base_url="http://fake-subgen:9000", transport=httpx.MockTransport(handler))
    caps = await c.probe_capabilities()
    await c.aclose()
    return caps


@pytest.mark.asyncio
async def test_caps_parse_preferred_list_in_order_with_null_in_place_and_duplicates():
    caps = await _probe(
        {
            "per_request_task": True,
            "preferred_audio_languages": [None, "en", "ja", "en"],
            "force_detected_language_to": "JA",
        }
    )
    assert caps.preferred_audio_languages == (None, "en", "ja", "en")
    assert caps.preferred_audio_languages_unreadable == ()
    assert caps.force_detected_language_to == "ja"
    d = caps.to_dict()
    assert d["preferred_audio_languages"] == (None, "en", "ja", "en")
    assert d["force_detected_language_to"] == "ja"


@pytest.mark.asyncio
async def test_caps_absent_means_not_advertised():
    caps = await _probe({"per_request_task": True})
    assert caps.preferred_audio_languages is None
    assert caps.force_detected_language_to is None
    assert caps.preferred_audio_languages_unreadable == ()


@pytest.mark.asyncio
async def test_caps_empty_list_is_advertised_and_empty():
    caps = await _probe({"preferred_audio_languages": [], "force_detected_language_to": None})
    assert caps.preferred_audio_languages == ()


@pytest.mark.asyncio
async def test_caps_unreadable_entry_is_reported_not_turned_into_null():
    """A None here would fake a fall-through subgen never makes."""
    caps = await _probe({"preferred_audio_languages": ["en", 42, "***"]})
    assert caps.preferred_audio_languages_unreadable == ("42", "***")
    assert None not in (caps.preferred_audio_languages or ())


@pytest.mark.asyncio
async def test_caps_unmappable_force_detected_is_none():
    caps = await _probe({"force_detected_language_to": 7})
    assert caps.force_detected_language_to is None


@pytest.mark.asyncio
async def test_caps_not_a_list_is_not_advertised():
    caps = await _probe({"preferred_audio_languages": "en"})
    assert caps.preferred_audio_languages is None


# ---- config (B3) -------------------------------------------------------------


def test_config_defaults_and_coercion():
    from subarr import config

    assert config.settings.per_file_task_enabled is False
    assert config.settings.per_file_task_wanted == []
    co = config._FIELD_COERCE["per_file_task_wanted"]
    assert co([" EN ", "es", "en", "Spanish", ""]) == ["en", "es"]
    assert co("en, es  ja") == ["en", "es", "ja"]
    assert co(None) == []
    assert config._FIELD_COERCE["per_file_task_enabled"]("false") is False


def test_config_env_vars_are_read_and_guard_the_saved_choice(monkeypatch):
    from subarr import config

    monkeypatch.setenv("SUBARR_PER_FILE_TASK_ENABLED", "1")
    monkeypatch.setenv("SUBARR_PER_FILE_TASK_WANTED", "EN, es")
    s = config.load()
    assert s.per_file_task_enabled is True
    assert s.per_file_task_wanted == ["en", "es"]
    assert config.env_is_set("per_file_task_enabled") and config.env_is_set("per_file_task_wanted")
