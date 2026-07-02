"""#357 — override suppressed for multilingual (lang_class='multi') and zxx."""

from __future__ import annotations

from pathlib import Path

from subarr.migrate import run_migrations


def _store(tmp_path: Path):
    db = tmp_path / "subarr.db"
    run_migrations(db)
    from subarr.audio_lang_store import AudioLangStore

    return AudioLangStore(db)


def test_multi_file_forwards_no_override(tmp_path):
    from subarr.audio_lang_store import resolve_audio_language_override

    store = _store(tmp_path)
    store.upsert(
        canonical_path="Movies/TheBeasts.mkv",
        lang_code="gl",
        source="auto-high-conf-multi",
        confidence=0.9,
        lang_class="multi",
        lang_codes=["gl", "es", "fr"],
    )
    assert resolve_audio_language_override(store, "Movies/TheBeasts.mkv") is None


def test_zxx_file_forwards_no_override(tmp_path):
    from subarr.audio_lang_store import resolve_audio_language_override

    store = _store(tmp_path)
    store.upsert(canonical_path="Movies/JunkHead.mkv", lang_code="zxx", source="user", confidence=1.0)
    assert resolve_audio_language_override(store, "Movies/JunkHead.mkv") is None


def test_regular_foreign_single_still_forwards(tmp_path):
    from subarr.audio_lang_store import resolve_audio_language_override

    store = _store(tmp_path)
    store.upsert(canonical_path="TV/S/ep.mkv", lang_code="ja", source="user", confidence=1.0)
    assert resolve_audio_language_override(store, "TV/S/ep.mkv") == "ja"
