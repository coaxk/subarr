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

-- #226: series-level intent. When the user says "every Cheers episode
-- is English", we record that fact ONCE here rather than spamming the
-- per-file table with a row for every episode. Future episodes added
-- to the library inherit the intent automatically on first lookup.
-- canonical_path series_prefix MUST end with '/' to disambiguate
-- "TV/Cheers/" from "TV/Cheers Reboot/".
CREATE TABLE IF NOT EXISTS series_lang_intent (
    series_prefix   TEXT PRIMARY KEY,
    lang_code       TEXT NOT NULL,
    source          TEXT NOT NULL DEFAULT 'user',
    confidence      REAL NOT NULL DEFAULT 1.0,
    declared_at     REAL NOT NULL,
    declared_by     TEXT,
    note            TEXT
);
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
        if row:
            return AudioLangVerification(
                canonical_path=row[0], lang_code=row[1], source=row[2],
                confidence=row[3], verified_at=row[4], verified_by=row[5],
                evidence=json.loads(row[6]) if row[6] else None,
            )
        # #226: fall through to series intent — every episode of a
        # declared-language series inherits the declaration automatically.
        # New episodes added after the declaration get covered without
        # needing per-file re-verification.
        intent = self._lookup_series_intent(canonical_path)
        if intent is not None:
            prefix, lang, src, conf, declared_at, declared_by = intent
            return AudioLangVerification(
                canonical_path=canonical_path,
                lang_code=lang,
                source=f"series_intent:{src}",
                confidence=conf,
                verified_at=declared_at,
                verified_by=declared_by,
                evidence={"inherited_from_series_prefix": prefix},
            )
        return None

    def get_all_as_lookup(self) -> dict[str, str]:
        """Return {canonical_path: lang_code} for all per-file verifications.
        Series intent is not flattened here — callers needing intent lookup
        for an arbitrary path use get() instead. This stays explicit so
        bulk consumers (Coverage build) keep their fast-path semantics
        and don't accidentally expand to thousands of synthetic rows."""
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

    # ─── #226: series-level intent ──────────────────────────────────

    def _lookup_series_intent(self, canonical_path: str) -> tuple | None:
        """Find the longest series_prefix that the given path starts with.
        Returns (prefix, lang, source, confidence, declared_at, declared_by)
        or None. Lookup is O(N) over the series table — fine: most installs
        will have <100 declared series, all comfortably in memory."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT series_prefix, lang_code, source, confidence, "
                "       declared_at, declared_by "
                "FROM series_lang_intent"
            ).fetchall()
        # Longest matching prefix wins so "TV/Cheers/Season 2/" beats
        # "TV/Cheers/" if both somehow got declared.
        best: tuple | None = None
        best_len = -1
        for r in rows:
            prefix = r[0]
            if canonical_path.startswith(prefix) and len(prefix) > best_len:
                best = r
                best_len = len(prefix)
        return best

    def set_series_intent(self, *, series_prefix: str, lang_code: str,
                          source: str = "user", confidence: float = 1.0,
                          declared_by: str | None = None,
                          note: str | None = None) -> None:
        """Record an intent declaration. Must end with '/' to disambiguate
        adjacent series — caller responsibility."""
        if not series_prefix.endswith("/"):
            series_prefix = series_prefix + "/"
        with self._lock:
            self._conn.execute(
                "INSERT INTO series_lang_intent "
                "(series_prefix, lang_code, source, confidence, declared_at, "
                " declared_by, note) "
                "VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(series_prefix) DO UPDATE SET "
                "  lang_code=excluded.lang_code, source=excluded.source, "
                "  confidence=excluded.confidence, declared_at=excluded.declared_at, "
                "  declared_by=excluded.declared_by, note=excluded.note",
                (series_prefix, lang_code.lower(), source, confidence,
                 time.time(), declared_by, note),
            )

    def delete_series_intent(self, series_prefix: str) -> bool:
        if not series_prefix.endswith("/"):
            series_prefix = series_prefix + "/"
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM series_lang_intent WHERE series_prefix = ?",
                (series_prefix,),
            )
            return cur.rowcount > 0

    def list_series_intents(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT series_prefix, lang_code, source, confidence, "
                "       declared_at, declared_by, note "
                "FROM series_lang_intent "
                "ORDER BY declared_at DESC"
            ).fetchall()
        return [
            {
                "series_prefix": r[0], "lang_code": r[1], "source": r[2],
                "confidence": r[3], "declared_at": r[4],
                "declared_by": r[5], "note": r[6],
            }
            for r in rows
        ]

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


# ─── Override-resolution helper (#229) ──────────────────────────────
#
# Used by both coverage_actions.queue (first-time submission from the
# Coverage page) AND queue.requeue (replay from Queue history). Before
# this lived inline in coverage_actions only, so requeue lost the
# audio_language_override — subgen silently skipped any file whose
# audio tag matched SKIP_IF_AUDIO_LANGUAGES, requeue clicks looked
# successful (200 OK queued=0), and the user had no working manual
# recovery path. One helper, two call sites, single source of truth.

_RISKY_LANGS = {"ja", "ko", "zh"}
_MIN_CONFIDENCE = 0.5


def resolve_audio_language_override(
    store: "AudioLangStore | None",
    canonical: str,
    *,
    caller: str = "queue",
    log: "logging.Logger | None" = None,
) -> str | None:
    """Look up the user-verified audio language for `canonical` and
    decide whether to forward it to subgen as audio_language_override.

    Returns the ISO 639-2/B code (e.g. 'jpn', 'fre') if the verification
    passes the evidence gate, else None. None means "let subgen detect
    from audio" — never returns an English code because subgen's default
    SKIP_IF_AUDIO_LANGUAGES=eng makes the override redundant there.

    Evidence gate (#105):
      - lang missing or 'en'/'eng' → no override (no point)
      - source field empty → REFUSE (corrupt store entry)
      - confidence < 0.5 → REFUSE (likely Tautulli-signal-only guess)
      - else → forward, with structured log at INFO

    Risky non-Latin scripts (ja, ko, zh) log with 'RISKY override' phrasing
    so post-hoc audits can grep specifically for the dangerous category
    where a wrong override produces unusable Whisper output instead of
    merely-degraded text.

    `caller` is just a log-tag so the line answers "which submission path
    forwarded this?" (`coverage_queue` vs `requeue`).
    """
    import logging as _logging
    _log = log or _logging.getLogger(__name__)

    if store is None:
        return None
    verification = store.get(canonical)
    if verification is None:
        return None

    lang = (verification.lang_code or "").strip().lower()
    src = (verification.source or "").strip().lower()
    conf = float(getattr(verification, "confidence", 0.0) or 0.0)

    if not lang or lang in ("en", "eng"):
        return None

    if not src:
        _log.warning(
            "%s: REFUSING override=%s for %s — verification has no "
            "source field (corrupt store entry?)",
            caller, lang, canonical,
        )
        return None
    if conf < _MIN_CONFIDENCE:
        _log.warning(
            "%s: REFUSING override=%s for %s — confidence %.2f < %.2f "
            "(source=%s). Let subgen detect from audio instead.",
            caller, lang, canonical, conf, _MIN_CONFIDENCE, src,
        )
        return None

    evidence_keys = list((verification.evidence or {}).keys())
    if lang in _RISKY_LANGS:
        _log.info(
            "%s: forwarding RISKY override=%s for %s "
            "(source=%s, conf=%.2f, evidence=%s)",
            caller, lang, canonical, src, conf, evidence_keys,
        )
    else:
        _log.info(
            "%s: forwarding audio_language_override=%s for %s "
            "(source=%s, conf=%.2f, evidence=%s)",
            caller, lang, canonical, src, conf, evidence_keys,
        )
    return lang
