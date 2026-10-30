"""#633: the #505 gate, with the per-file task in play.

The gate keeps intersecting the producible set with the row's Bazarr
`missing_subtitles`. The wanted-language setting W only chooses the TASK.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest


def _caps(**kw):
    base = dict(
        per_request_task=True,
        preferred_audio_languages=("en",),
        preferred_audio_languages_unreadable=(),
        force_detected_language_to=None,
        skip_audio_languages=None,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _cfg(wanted=("en", "es"), **caps_kw):
    from subarr.per_file_task import active_config

    return active_config(True, list(wanted), _caps(**caps_kw))


def _item(*, missing, tracks, audio=None, path="TV/Show/S01E01.mkv"):
    from subarr.coverage_engine import CoverageItem

    it = CoverageItem(title="Show", media_type="episode", canonical_path="TV/Show", file_canonical_path=path)
    it.missing_subtitles = list(missing)
    it.audio_tracks = list(tracks)
    it.audio_langs = list(audio if audio is not None else [t for t, _ in tracks if t])
    it.score = 9999
    it.monitored = True
    it.verification_state = "verified"
    return it


def _run(it, mode, cfg, forced=None):
    from subarr.coverage_engine import _score

    lookup = (lambda p: forced) if forced is not None else (lambda p: None)
    _score(it, {}, subgen_output_task=mode, per_file_task=cfg, per_file_forced=lookup)
    return it


def _reason_task(it):
    for r in it.score_reasons:
        if r.startswith("per-file task: "):
            return r.split(": ", 1)[1].split(" ", 1)[0]
    return None


def test_ja_audio_wanted_en_es_is_not_gated_in_transcribe_mode():
    tracks = [("jpn", True)]
    today = _run(_item(missing=["en", "es"], tracks=tracks), "transcribe", None)
    assert today.wanted_lang_subgen_cannot_produce is True, "premise: gated today"
    it = _run(_item(missing=["en", "es"], tracks=tracks), "transcribe", _cfg())
    assert it.wanted_lang_subgen_cannot_produce is False
    assert "per-file task: translate (ja audio)" in it.score_reasons


def test_es_audio_with_existing_es_missing_en_is_gated_the_lingarr_race():
    """W = {en, es} picks transcribe for es audio. Missing = {en}: transcribe
    writes es only, so en is unproducible and the row is held back. Choosing
    the task by MISSING (translate) would queue it into a refusal loop."""
    it = _run(_item(missing=["en"], tracks=[("spa", True)]), "translate", _cfg())
    assert it.wanted_lang_subgen_cannot_produce is True
    assert "per-file task: transcribe (es audio)" in it.score_reasons


def test_dual_audio_with_preferred_eng_transcribes_en():
    it = _run(
        _item(missing=["en"], tracks=[("jpn", True), ("eng", False)], audio=["jpn", "eng"]),
        "translate",
        _cfg(wanted=("en", "es")),
    )
    assert it.wanted_lang_subgen_cannot_produce is False
    assert "per-file task: transcribe (en audio)" in it.score_reasons


@pytest.mark.parametrize(
    "why",
    ["disabled", "no_wanted", "no_per_request_task", "no_list", "unreadable"],
)
def test_inactive_feature_is_identical_to_today(why):
    from subarr.per_file_task import active_config

    caps = _caps()
    enabled, wanted = True, ["en", "es"]
    if why == "disabled":
        enabled = False
    elif why == "no_wanted":
        wanted = []
    elif why == "no_per_request_task":
        caps = _caps(per_request_task=False)
    elif why == "no_list":
        caps = _caps(preferred_audio_languages=None)
    elif why == "unreadable":
        caps = _caps(preferred_audio_languages_unreadable=("zzz",))
    cfg = active_config(enabled, wanted, caps)
    assert cfg is None
    for mode in ("transcribe", "translate", None):
        a = _run(_item(missing=["en", "es"], tracks=[("jpn", True)]), mode, cfg)
        b = _run(_item(missing=["en", "es"], tracks=[("jpn", True)]), mode, None)
        assert a.wanted_lang_subgen_cannot_produce == b.wanted_lang_subgen_cannot_produce
        assert a.score_reasons == b.score_reasons
        assert a.score == b.score


def test_no_probe_tracks_is_unchanged():
    a = _run(_item(missing=["en", "es"], tracks=[], audio=["jpn"]), "transcribe", _cfg())
    b = _run(_item(missing=["en", "es"], tracks=[], audio=["jpn"]), "transcribe", None)
    assert a.wanted_lang_subgen_cannot_produce is b.wanted_lang_subgen_cannot_produce is True
    assert a.score_reasons == b.score_reasons


def test_unknown_source_falls_through_to_the_global_mode():
    # untagged default track: L is None, so the old path (transcribe + untagged
    # audio = unknown = not gated) applies
    it = _run(_item(missing=["es"], tracks=[(None, True)], audio=[]), "transcribe", _cfg())
    assert it.wanted_lang_subgen_cannot_produce is False
    assert _reason_task(it) is None


def test_gate_reads_raw_tracks_not_the_rewritten_audio_langs():
    """audio_langs is rewritten by user/Plex/Whisper hints; the tracks are what
    subgen reads. A user verdict reaches L only through `forced`."""
    it = _run(_item(missing=["en"], tracks=[("jpn", True)], audio=["es"]), "translate", _cfg())
    assert "per-file task: translate (ja audio)" in it.score_reasons


def test_forced_override_is_the_source_language():
    it = _run(
        _item(missing=["en"], tracks=[("eng", True)]),
        "transcribe",
        _cfg(wanted=("en",)),
        forced="ja",
    )
    assert "per-file task: translate (ja audio)" in it.score_reasons


def test_gate_does_not_leak_audio_tracks_into_the_api_payload():
    it = _item(missing=["en"], tracks=[("jpn", True)])
    assert "audio_tracks" not in it.to_dict()
    assert "audio_tracks" not in json.dumps(it.to_dict())


def test_probe_attach_stores_tracks_in_probe_order():
    from subarr.coverage_engine import CoverageItem, _apply_episode_probe
    from subarr.media_probe import AudioStream, ProbeResult

    probe = ProbeResult(
        canonical_path="TV/Show/S01E01.mkv",
        audio=[
            AudioStream(index=1, language="jpn", codec="aac", title=None, default=True),
            AudioStream(index=2, language=None, codec="aac", title=None, default=False),
            AudioStream(index=3, language="eng", codec="aac", title=None, default=False),
        ],
    )
    it = CoverageItem(title="Show", media_type="episode")
    _apply_episode_probe(
        it,
        "TV/Show/S01E01.mkv",
        probe,
        tautulli_hints=None,
        user_verifications=None,
        plex_hints=None,
        whisper_verifications=None,
    )
    assert it.audio_tracks == [("jpn", True), (None, False), ("eng", False)]


def test_movie_probe_attach_stores_tracks():
    from subarr.coverage_engine import CoverageItem, _attach_probe_movie
    from subarr.media_probe import AudioStream, ProbeResult

    probe = ProbeResult(
        canonical_path="Movies/M/M.mkv",
        audio=[AudioStream(index=1, language="spa", codec="aac", title=None, default=True)],
    )
    it = CoverageItem(title="M", media_type="movie", canonical_path="Movies/M")
    _attach_probe_movie(it, {"Movies/M": [("Movies/M/M.mkv", probe)]})
    assert it.audio_tracks == [("spa", True)]
