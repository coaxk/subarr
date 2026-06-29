"""#358: the override forwarded to subgen is the 2-letter canonical (subgen's
LanguageCode.from_string normalizes it; sending 2-letter is correct)."""

from __future__ import annotations


def _store(tmp_path):
    from subarr.audio_lang_store import AudioLangStore
    from subarr.migrate import run_migrations

    db = tmp_path / "subarr.db"
    run_migrations(db)
    return AudioLangStore(db)


def test_override_is_2letter_for_picker_and_whisper(tmp_path):
    from subarr.audio_lang_store import resolve_audio_language_override

    s = _store(tmp_path)
    # picker stored 'glg' (normalized to 'gl' by the store) and Whisper 'ko'
    s.upsert(canonical_path="m.mkv", lang_code="glg", source="user", confidence=1.0)
    s.upsert(canonical_path="k.mkv", lang_code="ko", source="whisper", confidence=1.0)
    assert resolve_audio_language_override(s, "m.mkv") == "gl"
    assert resolve_audio_language_override(s, "k.mkv") == "ko"


def test_override_still_skips_english(tmp_path):
    from subarr.audio_lang_store import resolve_audio_language_override

    s = _store(tmp_path)
    s.upsert(canonical_path="e.mkv", lang_code="en", source="user", confidence=1.0)
    assert resolve_audio_language_override(s, "e.mkv") is None
