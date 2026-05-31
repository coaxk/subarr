"""v1.1-O Layer 4: Manual audio-language verifications store.

When subarr's auto-detection (ffprobe / title-parse / Tautulli /
originalLanguage cross-check / Whisper) isn't confident, the user
confirms the actual language. That confirmation is stored here as
authoritative ground truth — beats all other signals on subsequent
coverage builds and survives re-walks.

Schema:

    audio_lang_verifications
        canonical_path  TEXT PRIMARY KEY   -- file path (relative to media_root)
        lang_code       TEXT NOT NULL      -- 3-letter ISO 639-2/B
        source          TEXT NOT NULL      -- 'user' | 'auto-high-conf' | 'whisper-robust'
        confidence      REAL               -- 0.0-1.0 (1.0 for user confirmations)
        verified_at     REAL NOT NULL      -- epoch
        verified_by     TEXT               -- future: per-user when auth lands
        evidence        TEXT               -- JSON dump of cross-check trail at time of verify

Verifications cascade: confirming Flics S01E02 doesn't auto-propagate to
S01E03 — but the API offers a bulk_for_series helper that the UI calls
after a single confirm ("apply to all Flics episodes?"). Stored per file
so corrections of individual mixed-track episodes still work.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


_SCHEMA = """
CREATE TABLE IF NOT EXISTS audio_lang_verifications (
    canonical_path  TEXT PRIMARY KEY,
    lang_code       TEXT NOT NULL,
    source          TEXT NOT NULL DEFAULT 'user',
    confidence      REAL NOT NULL DEFAULT 1.0,
    verified_at     REAL NOT NULL,
    verified_by     TEXT,
    evidence        TEXT
);
CREATE INDEX IF NOT EXISTS idx_audio_lang_verifications_lang
    ON audio_lang_verifications(lang_code);
"""


@dataclass
class AudioLangVerification:
    canonical_path: str
    lang_code: str
    source: str
    confidence: float
    verified_at: float
    verified_by: str | None
    evidence: dict | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "canonical_path": self.canonical_path,
            "lang_code": self.lang_code,
            "source": self.source,
            "confidence": self.confidence,
            "verified_at": self.verified_at,
            "verified_by": self.verified_by,
            "evidence": self.evidence,
        }


class AudioLangStore:
    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            str(db_path), check_same_thread=False, isolation_level=None,
        )
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._lock = threading.Lock()

    def init_schema(self) -> None:
        with self._lock:
            self._conn.executescript(_SCHEMA)

    def upsert(self, *, canonical_path: str, lang_code: str,
               source: str = "user", confidence: float = 1.0,
               verified_by: str | None = None,
               evidence: dict | None = None) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO audio_lang_verifications "
                "(canonical_path, lang_code, source, confidence, verified_at, verified_by, evidence) "
                "VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(canonical_path) DO UPDATE SET "
                "  lang_code=excluded.lang_code, source=excluded.source, "
                "  confidence=excluded.confidence, verified_at=excluded.verified_at, "
                "  verified_by=excluded.verified_by, evidence=excluded.evidence",
                (
                    canonical_path, lang_code.lower(), source, confidence,
                    time.time(), verified_by,
                    json.dumps(evidence) if evidence else None,
                ),
            )

    def get(self, canonical_path: str) -> AudioLangVerification | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT canonical_path, lang_code, source, confidence, "
                "       verified_at, verified_by, evidence "
                "FROM audio_lang_verifications WHERE canonical_path = ?",
                (canonical_path,),
            ).fetchone()
        if not row:
            return None
        return AudioLangVerification(
            canonical_path=row[0], lang_code=row[1], source=row[2],
            confidence=row[3], verified_at=row[4], verified_by=row[5],
            evidence=json.loads(row[6]) if row[6] else None,
        )

    def get_all_as_lookup(self) -> dict[str, str]:
        """Return {canonical_path: lang_code} for all verifications.
        Used as the highest-priority lookup in build_coverage."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT canonical_path, lang_code FROM audio_lang_verifications"
            ).fetchall()
        return {r[0]: r[1] for r in rows}

    def delete(self, canonical_path: str) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM audio_lang_verifications WHERE canonical_path = ?",
                (canonical_path,),
            )
            return cur.rowcount > 0

    def list_all(self) -> list[AudioLangVerification]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT canonical_path, lang_code, source, confidence, "
                "       verified_at, verified_by, evidence "
                "FROM audio_lang_verifications "
                "ORDER BY verified_at DESC"
            ).fetchall()
        out = []
        for r in rows:
            out.append(AudioLangVerification(
                canonical_path=r[0], lang_code=r[1], source=r[2],
                confidence=r[3], verified_at=r[4], verified_by=r[5],
                evidence=json.loads(r[6]) if r[6] else None,
            ))
        return out

    def bulk_for_series(self, series_canonical_prefix: str, lang_code: str,
                         file_paths: list[str], source: str = "user",
                         confidence: float = 1.0,
                         verified_by: str | None = None) -> int:
        """Apply a verification to every file under a series. Returns
        number of rows upserted. UI calls this after the user confirms
        "apply to all Flics episodes?"."""
        n = 0
        for p in file_paths:
            if not p.startswith(series_canonical_prefix):
                continue
            self.upsert(
                canonical_path=p, lang_code=lang_code, source=source,
                confidence=confidence, verified_by=verified_by,
            )
            n += 1
        return n
