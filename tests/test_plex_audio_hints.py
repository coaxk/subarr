"""#12 Plex-direct: per-show selected audio language read from Plex
metadata, applied as funnel layer L2.6 — below Tautulli-live (freshest,
in-session pick) and above the ffprobe heuristics.

Mirrors the manual pick a user makes in Plex (and that plex-auto-languages
persists per-episode), which Tautulli only surfaces while streaming.
"""
from __future__ import annotations


def _item(title="Klovn", original_language="Danish", audio_langs=("eng",)):
    from subarr.coverage_engine import CoverageItem
    it = CoverageItem(
        media_type="episode", title=title, episode_number="1x1",
        original_language=original_language, monitored=True,
        canonical_path="TV/Klovn", file_canonical_path="TV/Klovn/S01E01.mkv",
    )
    it.audio_langs = list(audio_langs)
    return it


def test_plex_hint_overrides_ffprobe_and_clears_suspect():
    from subarr.coverage_engine import _classify_audio_label
    # Foreign show, ffprobe mislabelled audio as English → would be suspect.
    it = _item(original_language="Danish", audio_langs=("eng",))
    _classify_audio_label(it, plex_hints={"klovn": "dan"})
    assert it.audio_langs == ["dan"]
    assert it.audio_label_suspect is False
    assert it.audio_label_unknown is False
    assert any("Plex" in n for n in it.audio_label_notes)


def test_tautulli_takes_precedence_over_plex():
    from subarr.coverage_engine import _classify_audio_label
    it = _item(audio_langs=("eng",))
    _classify_audio_label(
        it,
        tautulli_hints={"klovn": "swe"},   # live session pick — freshest
        plex_hints={"klovn": "dan"},       # persisted per-show pick
    )
    assert it.audio_langs == ["swe"]       # Tautulli (L2.5) wins over Plex (L2.6)


def test_user_verification_beats_plex():
    from subarr.coverage_engine import _classify_audio_label
    it = _item(audio_langs=("eng",))
    _classify_audio_label(
        it,
        user_verifications={"TV/Klovn/S01E01.mkv": "fra"},
        plex_hints={"klovn": "dan"},
    )
    assert it.audio_langs == ["fra"]       # L0 user-verify wins
    assert it.audio_verified is True


def test_no_plex_hint_falls_through_to_heuristics():
    from subarr.coverage_engine import _classify_audio_label
    # No hint for this title → suspect logic still runs (foreign + eng audio).
    it = _item(title="Klovn", original_language="Danish", audio_langs=("eng",))
    _classify_audio_label(it, plex_hints={"some other show": "dan"})
    assert it.audio_label_suspect is True  # unchanged behaviour


# ---- PlexClient.audio_lang_hints orchestration + caching ----------------

def _plex():
    from subarr.integrations.plex import PlexClient
    return PlexClient(base_url="http://plex.test:32400", token="t")


def _canned_xml(path: str, params):
    """Stub Plex responses: one show 'Klovn' (key 12353) whose first
    episode (key 12561) has a selected Danish audio stream."""
    import defusedxml.ElementTree as ET
    if path == "/library/sections":
        return ET.fromstring('<MediaContainer><Directory type="show" key="1"/>'
                             '<Directory type="movie" key="2"/></MediaContainer>')
    if path == "/library/sections/1/all":
        return ET.fromstring('<MediaContainer>'
                             '<Directory ratingKey="12353" title="Klovn"/>'
                             '<Directory ratingKey="999" title="Some English Show"/>'
                             '</MediaContainer>')
    if path == "/library/metadata/12353/allLeaves":
        return ET.fromstring('<MediaContainer><Video ratingKey="12561"/></MediaContainer>')
    if path == "/library/metadata/999/allLeaves":
        return ET.fromstring('<MediaContainer><Video ratingKey="888"/></MediaContainer>')
    if path == "/library/metadata/12561":
        return ET.fromstring('<MediaContainer><Video><Media><Part>'
                             '<Stream streamType="2" selected="1" languageCode="dan"/>'
                             '<Stream streamType="2" languageCode="eng"/>'
                             '</Part></Media></Video></MediaContainer>')
    if path == "/library/metadata/888":
        return ET.fromstring('<MediaContainer><Video><Media><Part>'
                             '<Stream streamType="2" selected="1" languageCode="eng"/>'
                             '</Part></Media></Video></MediaContainer>')
    raise AssertionError(f"unexpected path {path}")


def test_audio_lang_hints_reads_selected_stream(monkeypatch):
    import asyncio
    p = _plex()
    calls = []
    async def fake(path, extra_params=None):
        calls.append(path)
        return _canned_xml(path, extra_params)
    monkeypatch.setattr(p, "_get_xml", fake)
    out = asyncio.run(p.audio_lang_hints({"Klovn", "Some English Show"}))
    assert out == {"klovn": "dan", "some english show": "eng"}


def test_audio_lang_hints_caches_across_calls(monkeypatch):
    import asyncio
    p = _plex()
    calls = []
    async def fake(path, extra_params=None):
        calls.append(path)
        return _canned_xml(path, extra_params)
    monkeypatch.setattr(p, "_get_xml", fake)
    asyncio.run(p.audio_lang_hints({"Klovn"}))
    n_after_first = len(calls)
    asyncio.run(p.audio_lang_hints({"Klovn"}))  # fully cached → no new calls
    assert len(calls) == n_after_first


def test_audio_lang_hints_unknown_title_no_hint(monkeypatch):
    import asyncio
    p = _plex()
    async def fake(path, extra_params=None):
        return _canned_xml(path, extra_params)
    monkeypatch.setattr(p, "_get_xml", fake)
    out = asyncio.run(p.audio_lang_hints({"Nonexistent Show"}))
    assert out == {}
