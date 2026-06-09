"""#159: default-audio-track / original-language mismatch detection + swap."""

from __future__ import annotations


from subarr.media_probe import AudioStream, detect_default_track_mismatch


def _a(index, lang, default=False, title=None, codec="aac"):
    return AudioStream(index=index, language=lang, codec=codec, title=title, default=default)


# ── detection: the canonical issue case ───────────────────────────────────


def test_detects_german_default_on_russian_show():
    # Russian show; track 1 = German dub (default), track 2 = original Russian.
    audio = [_a(1, "ger", default=True), _a(2, "rus")]
    m = detect_default_track_mismatch(audio, "Russian")
    assert m is not None
    assert m.default_lang == "de"
    assert m.native_lang == "ru"
    assert m.native_audio_ordinal == 2  # 1-based audio ordinal for mkvpropedit
    assert m.native_stream_index == 2


def test_native_ordinal_is_audio_relative_not_stream_index():
    # Native track is the 1st audio stream but has global stream index 3.
    audio = [_a(3, "rus", default=False), _a(4, "ger", default=True)]
    m = detect_default_track_mismatch(audio, "Russian")
    assert m is not None
    assert m.native_audio_ordinal == 1
    assert m.native_stream_index == 3
    assert m.default_lang == "de"


def test_falls_back_to_first_stream_when_no_default_flag():
    # No disposition.default anywhere → first audio stream is treated as default.
    audio = [_a(1, "ger"), _a(2, "rus")]
    m = detect_default_track_mismatch(audio, "Russian")
    assert m is not None
    assert m.default_lang == "de"
    assert m.native_audio_ordinal == 2


# ── no-fire cases ──────────────────────────────────────────────────────────


def test_no_fire_when_default_already_original():
    audio = [_a(1, "rus", default=True), _a(2, "ger")]
    assert detect_default_track_mismatch(audio, "Russian") is None


def test_no_fire_for_english_original():
    audio = [_a(1, "ger", default=True), _a(2, "eng")]
    assert detect_default_track_mismatch(audio, "English") is None


def test_no_fire_single_track():
    audio = [_a(1, "ger", default=True)]
    assert detect_default_track_mismatch(audio, "Russian") is None


def test_no_fire_when_no_native_track_present():
    # German default, French other — no Russian track to swap to.
    audio = [_a(1, "ger", default=True), _a(2, "fre")]
    assert detect_default_track_mismatch(audio, "Russian") is None


def test_no_fire_when_default_untagged():
    # Default track has no language tag → not a confident mismatch (unknown case).
    audio = [_a(1, None, default=True), _a(2, "rus")]
    assert detect_default_track_mismatch(audio, "Russian") is None


def test_no_fire_when_only_one_distinct_tagged_language():
    # Two Russian tracks (e.g. stereo + 5.1), one default — not a mismatch.
    audio = [_a(1, "rus", default=True), _a(2, "rus")]
    assert detect_default_track_mismatch(audio, "Russian") is None


def test_no_fire_unknown_original_language():
    audio = [_a(1, "ger", default=True), _a(2, "rus")]
    assert detect_default_track_mismatch(audio, None) is None
    assert detect_default_track_mismatch(audio, "und") is None


def test_normalizes_mixed_tag_dialects():
    # original as ISO name, tags as ISO-639-2/B and -1 mixed.
    audio = [_a(1, "deu", default=True), _a(2, "ru")]
    m = detect_default_track_mismatch(audio, "Russian")
    assert m is not None
    assert m.default_lang == "de" and m.native_lang == "ru"


# ── store: per-file dismiss persistence (migration 016) ────────────────────


def _store(tmp_path):
    from subarr.audio_lang_store import AudioLangStore
    from subarr.migrate import run_migrations

    db = tmp_path / "a.db"
    run_migrations(db)
    return AudioLangStore(db)


def test_dismiss_roundtrip(tmp_path):
    s = _store(tmp_path)
    assert s.get_track_mismatch_dismissed_set() == set()
    s.dismiss_track_mismatch("TV/Show/ep.mkv", note="intentional dub default")
    assert s.get_track_mismatch_dismissed_set() == {"TV/Show/ep.mkv"}
    # idempotent upsert
    s.dismiss_track_mismatch("TV/Show/ep.mkv")
    assert s.get_track_mismatch_dismissed_set() == {"TV/Show/ep.mkv"}


def test_undismiss(tmp_path):
    s = _store(tmp_path)
    s.dismiss_track_mismatch("TV/Show/ep.mkv")
    assert s.undismiss_track_mismatch("TV/Show/ep.mkv") is True
    assert s.get_track_mismatch_dismissed_set() == set()
    # second remove → False (nothing there)
    assert s.undismiss_track_mismatch("TV/Show/ep.mkv") is False


# ── coverage wiring: _apply_track_mismatch + to_dict contract ──────────────


def _probe(audio):
    from subarr.media_probe import ProbeResult

    return ProbeResult(canonical_path="x", audio=audio)


def test_apply_sets_fields_on_mkv():
    from subarr.coverage_engine import CoverageItem, _apply_track_mismatch

    item = CoverageItem(
        media_type="episode", title="Show", original_language="Russian", file_canonical_path="TV/Show/ep.mkv"
    )
    _apply_track_mismatch(item, _probe([_a(1, "ger", default=True), _a(2, "rus")]))
    assert item.default_track_mismatch is True
    assert item.mismatch_default_track_lang == "de"
    assert item.mismatch_native_track_lang == "ru"
    assert item.mismatch_native_audio_ordinal == 2
    assert any("double-translated" in n for n in item.audio_label_notes)
    d = item.to_dict()
    assert d["default_track_mismatch"] is True
    assert d["mismatch_native_audio_ordinal"] == 2


def test_apply_skips_non_mkv():
    from subarr.coverage_engine import CoverageItem, _apply_track_mismatch

    item = CoverageItem(
        media_type="movie", title="Mov", original_language="Russian", file_canonical_path="Movies/Mov/mov.mp4"
    )
    _apply_track_mismatch(item, _probe([_a(1, "ger", default=True), _a(2, "rus")]))
    assert item.default_track_mismatch is False  # mkvpropedit is mkv-only


def test_apply_noop_when_no_mismatch():
    from subarr.coverage_engine import CoverageItem, _apply_track_mismatch

    item = CoverageItem(
        media_type="episode", title="Show", original_language="Russian", file_canonical_path="TV/Show/ep.mkv"
    )
    _apply_track_mismatch(item, _probe([_a(1, "rus", default=True), _a(2, "ger")]))
    assert item.default_track_mismatch is False
