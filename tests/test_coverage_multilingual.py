"""#357 — a confident-multilingual file surfaces 'multilingual', not 'suspect'.

The wiring is a post-pass (_apply_multilingual_verifications) that runs after the
audio-source refinement, keyed on the store's lang_class='multi' lookup."""

from __future__ import annotations

from subarr.coverage_engine import CoverageItem, _apply_multilingual_verifications


def _item(**kw):
    base = dict(media_type="movie", title="The Beasts", canonical_path="Movies/TheBeasts")
    base.update(kw)
    return CoverageItem(**base)


def test_multilingual_file_is_not_suspect():
    it = _item(
        audio_langs=["gl"],
        original_language="Spanish",
        file_canonical_path="Movies/TheBeasts.mkv",
        audio_label_suspect=True,  # a false alarm the post-pass must clear
        audio_source="whisper",  # what _refine_audio_sources would have set
    )
    _apply_multilingual_verifications([it], {"Movies/TheBeasts.mkv": ["gl", "es", "fr"]})
    assert it.audio_source == "multilingual"
    assert it.audio_label_suspect is False
    assert it.audio_langs == ["gl", "es", "fr"]
    assert it.audio_lang_codes == ["gl", "es", "fr"]
    assert any("multilingual audio" in n for n in it.audio_label_notes)


def test_single_entry_multi_map_is_ignored():
    it = _item(audio_langs=["eng"], file_canonical_path="Movies/Reg.mkv", audio_source="ffprobe")
    _apply_multilingual_verifications([it], {"Movies/Reg.mkv": ["en"]})  # <2 -> not multilingual
    assert it.audio_source == "ffprobe"
    assert it.audio_lang_codes is None


def test_no_multi_map_unchanged():
    it = _item(audio_langs=["eng"], file_canonical_path="Movies/Reg.mkv", audio_source="ffprobe")
    _apply_multilingual_verifications([it], None)
    assert it.audio_source == "ffprobe"
    assert it.audio_lang_codes is None


def test_note_not_duplicated_on_rerun():
    it = _item(audio_langs=["gl"], file_canonical_path="Movies/TheBeasts.mkv")
    m = {"Movies/TheBeasts.mkv": ["gl", "es"]}
    _apply_multilingual_verifications([it], m)
    _apply_multilingual_verifications([it], m)  # idempotent note
    assert sum("multilingual audio" in n for n in it.audio_label_notes) == 1
