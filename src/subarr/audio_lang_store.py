"""v1.1-O Layer 4: Manual audio-language verifications store.

When subarr's auto-detection (ffprobe / title-parse / Tautulli /
originalLanguage cross-check / Whisper) isn't confident, the user
confirms the actual language. That confirmation is stored here as
authoritative ground truth — beats all other signals on subsequent
coverage builds and survives re-walks.

Schema:

    audio_lang_verifications
        canonical_path  TEXT PRIMARY KEY   -- file path (relative to media_root)
        lang_code       TEXT NOT NULL      -- canonical 2-letter ISO-639-1 (#358; normalized on write/read)
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
import logging
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .data_persistence import apply_journal_mode
from .langs import normalize_lang
from .log_safe import scrub


# Schema (audio_lang_verifications + idx, series_lang_intent) is owned by
# migrations/008_init_schema_parity.sql. run_migrations() runs at boot
# before this store — no per-store init_schema().
#
# #226 note: series_lang_intent records series-level intent ONCE ("every
# Cheers episode is English") instead of spamming the per-file table.
# series_prefix MUST end with '/' to disambiguate "TV/Cheers/" from
# "TV/Cheers Reboot/".


@dataclass
class AudioLangVerification:
    canonical_path: str
    lang_code: str
    source: str
    confidence: float
    verified_at: float
    verified_by: str | None
    evidence: dict | None
    lang_class: str = "single"  # #357: 'single' | 'multi'
    lang_codes: list[str] | None = None  # #357: ordered set, only when multi

    def to_dict(self) -> dict[str, Any]:
        return {
            "canonical_path": self.canonical_path,
            "lang_code": self.lang_code,
            "source": self.source,
            "confidence": self.confidence,
            "verified_at": self.verified_at,
            "verified_by": self.verified_by,
            "evidence": self.evidence,
            "lang_class": self.lang_class,
            "lang_codes": self.lang_codes,
        }


def _decode_lang_codes(raw: str | None) -> list[str] | None:
    """#357: deserialise the lang_codes JSON array. Malformed -> None (treat as
    single), logged, never crash (design error-handling rule)."""
    if not raw:
        return None
    try:
        val = json.loads(raw)
    except (ValueError, TypeError):
        logging.getLogger(__name__).warning("malformed lang_codes JSON; treating as single")
        return None
    return val if isinstance(val, list) else None


class AudioLangStore:
    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            str(db_path),
            check_same_thread=False,
            isolation_level=None,
        )
        apply_journal_mode(self._conn, db_path)
        self._lock = threading.Lock()

    def upsert(
        self,
        *,
        canonical_path: str,
        lang_code: str,
        source: str = "user",
        confidence: float = 1.0,
        verified_by: str | None = None,
        evidence: dict | None = None,
        lang_class: str = "single",  # #357
        lang_codes: list[str] | None = None,  # #357
    ) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO audio_lang_verifications "
                "(canonical_path, lang_code, source, confidence, verified_at, verified_by, evidence, "
                " lang_class, lang_codes) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(canonical_path) DO UPDATE SET "
                "  lang_code=excluded.lang_code, source=excluded.source, "
                "  confidence=excluded.confidence, verified_at=excluded.verified_at, "
                "  verified_by=excluded.verified_by, evidence=excluded.evidence, "
                "  lang_class=excluded.lang_class, lang_codes=excluded.lang_codes",
                (
                    canonical_path,
                    # #358: canonical 2-letter ISO-639-1 (was raw .lower()).
                    normalize_lang(lang_code) or lang_code.lower(),
                    source,
                    confidence,
                    time.time(),
                    verified_by,
                    json.dumps(evidence) if evidence else None,
                    lang_class,
                    # #357: normalize set members symmetrically with the singular
                    # lang_code (2-letter canonical), so the set can never drift
                    # into a different format than lang_code regardless of caller.
                    json.dumps([normalize_lang(c) or c.lower() for c in lang_codes]) if lang_codes else None,
                ),
            )

    def get(self, canonical_path: str) -> AudioLangVerification | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT canonical_path, lang_code, source, confidence, "
                "       verified_at, verified_by, evidence, lang_class, lang_codes "
                "FROM audio_lang_verifications WHERE canonical_path = ?",
                (canonical_path,),
            ).fetchone()
        if row:
            return AudioLangVerification(
                canonical_path=row[0],
                lang_code=normalize_lang(row[1]) or row[1],  # #358
                source=row[2],
                confidence=row[3],
                verified_at=row[4],
                verified_by=row[5],
                evidence=json.loads(row[6]) if row[6] else None,
                lang_class=row[7] or "single",  # #357
                lang_codes=_decode_lang_codes(row[8]),  # #357
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
                lang_code=normalize_lang(lang) or lang,  # #358
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
        return {r[0]: (normalize_lang(r[1]) or r[1]) for r in rows}  # #358

    def get_all_multi_as_lookup(self) -> dict[str, list[str]]:
        """#357: {canonical_path: lang_codes} for every lang_class='multi' row.
        Read by build_coverage so multilingual files surface the set + skip the
        suspect flag. Mirrors get_all_as_lookup()'s fast-path (no series-intent)."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT canonical_path, lang_codes FROM audio_lang_verifications WHERE lang_class = 'multi'"
            ).fetchall()
        out: dict[str, list[str]] = {}
        for path, raw in rows:
            codes = _decode_lang_codes(raw)
            if codes:
                out[path] = codes
        return out

    def get_all_sources_as_lookup(self) -> dict[str, str]:
        """Return {canonical_path: source} for all per-file verifications, so
        Coverage can show HOW each audio language was determined (user /
        whisper-robust / auto-high-conf). Mirrors get_all_as_lookup()'s
        fast-path semantics (no series-intent expansion)."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT canonical_path, source FROM audio_lang_verifications"
            ).fetchall()
        return {r[0]: r[1] for r in rows}

    def delete(self, canonical_path: str) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM audio_lang_verifications WHERE canonical_path = ?",
                (canonical_path,),
            )
            return cur.rowcount > 0

    def all_paths(self) -> list[str]:
        """Every canonical_path we hold a verification for.

        [#453] Required by orphan_prune. The prune core's docstring claimed
        this store already had this shape; it did not, and the documented
        usage would have raised AttributeError on first use.
        """
        with self._lock:
            rows = self._conn.execute("SELECT canonical_path FROM audio_lang_verifications").fetchall()
        return [r[0] for r in rows]

    def list_all(self) -> list[AudioLangVerification]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT canonical_path, lang_code, source, confidence, "
                "       verified_at, verified_by, evidence, lang_class, lang_codes "
                "FROM audio_lang_verifications "
                "ORDER BY verified_at DESC"
            ).fetchall()
        out = []
        for r in rows:
            out.append(
                AudioLangVerification(
                    canonical_path=r[0],
                    lang_code=normalize_lang(r[1]) or r[1],  # #358
                    source=r[2],
                    confidence=r[3],
                    verified_at=r[4],
                    verified_by=r[5],
                    evidence=json.loads(r[6]) if r[6] else None,
                    lang_class=r[7] or "single",  # #357
                    lang_codes=_decode_lang_codes(r[8]),  # #357
                )
            )
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

    def set_series_intent(
        self,
        *,
        series_prefix: str,
        lang_code: str,
        source: str = "user",
        confidence: float = 1.0,
        declared_by: str | None = None,
        note: str | None = None,
    ) -> None:
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
                (
                    series_prefix,
                    normalize_lang(lang_code) or lang_code.lower(),  # #358: 2-letter canonical
                    source,
                    confidence,
                    time.time(),
                    declared_by,
                    note,
                ),
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
                "series_prefix": r[0],
                "lang_code": normalize_lang(r[1]) or r[1],  # #358
                "source": r[2],
                "confidence": r[3],
                "declared_at": r[4],
                "declared_by": r[5],
                "note": r[6],
            }
            for r in rows
        ]

    # ── #140: mis-grouped-series dismiss ────────────────────────────────

    def dismiss_mixed(self, series_path: str, note: str | None = None) -> None:
        """Mark a series (by its directory path) as a known-legit multilingual
        show so the #140 mixed-language flag stays quiet on future walks."""
        with self._lock:
            self._conn.execute(
                "INSERT INTO mixed_language_dismissed (series_path, dismissed_at, note) "
                "VALUES (?, ?, ?) "
                "ON CONFLICT(series_path) DO UPDATE SET "
                "  dismissed_at=excluded.dismissed_at, note=excluded.note",
                (series_path, time.time(), note),
            )

    def undismiss_mixed(self, series_path: str) -> bool:
        """Re-enable the mixed-language flag for a series. Returns True if a
        dismissal existed."""
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM mixed_language_dismissed WHERE series_path = ?",
                (series_path,),
            )
            return cur.rowcount > 0

    def get_mixed_dismissed_set(self) -> set[str]:
        """All currently-dismissed series paths — read by build_coverage to
        suppress the #140 flag."""
        with self._lock:
            rows = self._conn.execute("SELECT series_path FROM mixed_language_dismissed").fetchall()
        return {r[0] for r in rows}

    # ── #159: default-track mismatch dismiss (keyed by FILE path) ────────

    def dismiss_track_mismatch(self, file_path: str, note: str | None = None) -> None:
        """Mark a file's default audio track as an intentional choice so the
        #159 track-mismatch prompt stays quiet on future coverage walks."""
        with self._lock:
            self._conn.execute(
                "INSERT INTO track_mismatch_dismissed (file_path, dismissed_at, note) "
                "VALUES (?, ?, ?) "
                "ON CONFLICT(file_path) DO UPDATE SET "
                "  dismissed_at=excluded.dismissed_at, note=excluded.note",
                (file_path, time.time(), note),
            )

    def undismiss_track_mismatch(self, file_path: str) -> bool:
        """Re-enable the #159 prompt for a file. True if a dismissal existed."""
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM track_mismatch_dismissed WHERE file_path = ?",
                (file_path,),
            )
            return cur.rowcount > 0

    def get_track_mismatch_dismissed_set(self) -> set[str]:
        """All currently-dismissed file paths — read by build_coverage to
        suppress the #159 flag."""
        with self._lock:
            rows = self._conn.execute("SELECT file_path FROM track_mismatch_dismissed").fetchall()
        return {r[0] for r in rows}

    # ── #316: per-title sub ignore (movie file path, or series prefix) ────

    def ignore_title(self, path: str, note: str | None = None) -> None:
        """Mark a title as ignored so build_coverage drops its rows from gaps /
        Review / the auto-queue. `path` is a movie's file_canonical_path or a
        series prefix ending in '/'. Idempotent."""
        with self._lock:
            self._conn.execute(
                "INSERT INTO subs_ignored (path, ignored_at, note) "
                "VALUES (?, ?, ?) "
                "ON CONFLICT(path) DO UPDATE SET "
                "  ignored_at=excluded.ignored_at, note=excluded.note",
                (path, time.time(), note),
            )

    def unignore_title(self, path: str) -> bool:
        """Stop ignoring a title. True if an ignore existed."""
        with self._lock:
            cur = self._conn.execute("DELETE FROM subs_ignored WHERE path = ?", (path,))
            return cur.rowcount > 0

    def get_ignored_titles_set(self) -> set[str]:
        """All currently-ignored paths — read by build_coverage to drop rows."""
        with self._lock:
            rows = self._conn.execute("SELECT path FROM subs_ignored").fetchall()
        return {r[0] for r in rows}

    def list_ignored_titles(self) -> list[dict[str, Any]]:
        """Ignored titles with metadata, newest first — for the management UI."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT path, ignored_at, note FROM subs_ignored ORDER BY ignored_at DESC"
            ).fetchall()
        return [{"path": r[0], "ignored_at": r[1], "note": r[2]} for r in rows]

    def bulk_for_series(
        self,
        series_canonical_prefix: str,
        lang_code: str,
        file_paths: list[str],
        source: str = "user",
        confidence: float = 1.0,
        verified_by: str | None = None,
    ) -> int:
        """Apply a verification to every file under a series. Returns
        number of rows upserted. UI calls this after the user confirms
        "apply to all Flics episodes?"."""
        n = 0
        for p in file_paths:
            if not p.startswith(series_canonical_prefix):
                continue
            self.upsert(
                canonical_path=p,
                lang_code=lang_code,
                source=source,
                confidence=confidence,
                verified_by=verified_by,
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

    Returns the canonical 2-letter ISO-639-1 code (e.g. 'ja', 'fr') if the
    verification passes the evidence gate, else None. (#358: was 3-letter;
    subgen parses the override via LanguageCode.from_string which accepts any
    form, so the 2-letter canonical the store now holds is forwarded as-is.)
    None means "let subgen detect from audio" — never returns an English code
    because subgen's default SKIP_IF_AUDIO_LANGUAGES=eng makes it redundant.

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
    _log = log or logging.getLogger(__name__)

    if store is None:
        return None
    verification = store.get(canonical)
    if verification is None:
        return None

    # #357: multilingual + zxx files have no single source language to declare —
    # let subgen self-detect per chunk rather than forward a wrong override.
    if getattr(verification, "lang_class", "single") == "multi":
        _log.info(
            "%s: no override for %s — multilingual (lang_codes=%s); subgen self-detects",
            caller,
            scrub(canonical),
            getattr(verification, "lang_codes", None),
        )
        return None
    if (verification.lang_code or "").strip().lower() == "zxx":
        _log.info(
            "%s: no override for %s — zxx (no linguistic content); subgen self-detects",
            caller,
            scrub(canonical),
        )
        return None

    lang = (verification.lang_code or "").strip().lower()
    src = (verification.source or "").strip().lower()
    conf = float(getattr(verification, "confidence", 0.0) or 0.0)

    if not lang or lang in ("en", "eng"):
        return None

    if not src:
        _log.warning(
            "%s: REFUSING override=%s for %s — verification has no source field (corrupt store entry?)",
            caller,
            lang,
            scrub(canonical),
        )
        return None
    if conf < _MIN_CONFIDENCE:
        _log.warning(
            "%s: REFUSING override=%s for %s — confidence %.2f < %.2f "
            "(source=%s). Let subgen detect from audio instead.",
            caller,
            lang,
            scrub(canonical),
            conf,
            _MIN_CONFIDENCE,
            src,
        )
        return None

    evidence_keys = list((verification.evidence or {}).keys())
    if lang in _RISKY_LANGS:
        _log.info(
            "%s: forwarding RISKY override=%s for %s (source=%s, conf=%.2f, evidence=%s)",
            caller,
            lang,
            scrub(canonical),
            src,
            conf,
            evidence_keys,
        )
    else:
        _log.info(
            "%s: forwarding audio_language_override=%s for %s (source=%s, conf=%.2f, evidence=%s)",
            caller,
            lang,
            scrub(canonical),
            src,
            conf,
            evidence_keys,
        )
    return lang
