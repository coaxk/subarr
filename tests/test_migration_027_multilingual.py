"""#357 — migration 027 adds lang_class/lang_codes to audio_lang_verifications,
applies cleanly to an existing DB, and defaults existing rows to 'single'."""

from __future__ import annotations

import sqlite3

from subarr.migrate import run_migrations


def test_migration_027_adds_columns_and_defaults_single(tmp_path):
    db = tmp_path / "subarr.db"
    run_migrations(db)  # applies 001..027

    conn = sqlite3.connect(str(db))
    # seed a pre-existing row via the base columns only
    conn.execute(
        "INSERT INTO audio_lang_verifications "
        "(canonical_path, lang_code, source, confidence, verified_at) "
        "VALUES ('TV/Old/ep.mkv', 'ja', 'user', 1.0, 1.0)"
    )
    conn.commit()

    cols = {r[1] for r in conn.execute("PRAGMA table_info(audio_lang_verifications)")}
    assert "lang_class" in cols
    assert "lang_codes" in cols

    row = conn.execute(
        "SELECT lang_class, lang_codes FROM audio_lang_verifications WHERE canonical_path='TV/Old/ep.mkv'"
    ).fetchone()
    assert row[0] == "single"  # NOT NULL DEFAULT
    assert row[1] is None
    conn.close()


def test_migration_027_is_idempotent_rerun(tmp_path):
    db = tmp_path / "subarr.db"
    run_migrations(db)
    # second run is a no-op (version already recorded)
    applied = run_migrations(db)
    assert applied == []
