"""#358: the store is canonical 2-letter — normalize on write AND read so the
3-letter picker, Whisper's 2-letter, and legacy mixed rows all agree."""

from __future__ import annotations


def _store(tmp_path):
    from subarr.audio_lang_store import AudioLangStore
    from subarr.migrate import run_migrations

    db = tmp_path / "subarr.db"
    run_migrations(db)
    return AudioLangStore(db)


def test_upsert_normalizes_on_write(tmp_path):
    s = _store(tmp_path)
    s.upsert(canonical_path="Movies/Beasts.mkv", lang_code="glg", source="user")
    s.upsert(canonical_path="TV/a.mkv", lang_code="Galician", source="user")
    s.upsert(canonical_path="TV/b.mkv", lang_code="gl", source="whisper")
    assert s.get("Movies/Beasts.mkv").lang_code == "gl"
    assert s.get("TV/a.mkv").lang_code == "gl"
    assert s.get("TV/b.mkv").lang_code == "gl"  # idempotent


def test_read_paths_normalize_legacy_rows(tmp_path):
    # Simulate a pre-#358 row written raw 3-letter directly to the table.
    import time

    s = _store(tmp_path)
    with s._lock:
        s._conn.execute(
            "INSERT INTO audio_lang_verifications "
            "(canonical_path, lang_code, source, confidence, verified_at) "
            "VALUES (?, ?, ?, ?, ?)",
            ("x.mkv", "glg", "user", 1.0, time.time()),
        )
    assert s.get("x.mkv").lang_code == "gl"
    assert s.get_all_as_lookup()["x.mkv"] == "gl"
    assert s.list_all()[0].lang_code == "gl"


def test_verify_endpoint_normalizes_picker_code(app_with_stub):
    # POST the picker's 3-letter 'glg' → response + store both canonical 'gl'.
    canonical = "Movies/The Beasts/The Beasts (2022).mkv"
    r = app_with_stub.post(
        "/api/audio-lang/verifications",
        json={"canonical_path": canonical, "lang_code": "glg", "source": "user"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["lang_code"] == "gl"
    assert app_with_stub.app.state.audio_lang.get(canonical).lang_code == "gl"
