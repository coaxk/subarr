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
import os
import re
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
    # #571: the arr id this verdict was recorded against, so carry-over can
    # match on identity rather than on an SxxExx token the name may not carry.
    sonarr_episode_id: int | None = None
    radarr_movie_id: int | None = None

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
            "sonarr_episode_id": self.sonarr_episode_id,
            "radarr_movie_id": self.radarr_movie_id,
        }


# Every full read of the table goes through these two, so a new column can
# never be added to one read site and forgotten in the other.
_VERDICT_COLUMNS = (
    "canonical_path, lang_code, source, confidence, verified_at, verified_by, "
    "evidence, lang_class, lang_codes, sonarr_episode_id, radarr_movie_id"
)
# Hoisted for the same reason as pending_queue._SELECT: the call sites then pass
# a constant with no literal SQL keyword, so bandit's B608 (string-built SQL) is
# quiet by construction. Only this line interpolates, and only a fixed column
# list of our own — every user value is bound as a `?` parameter.
_SELECT_VERDICT = f"SELECT {_VERDICT_COLUMNS} FROM audio_lang_verifications"  # nosec B608


def _row_to_verification(row: tuple) -> AudioLangVerification:
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
        sonarr_episode_id=row[9],  # #571
        radarr_movie_id=row[10],  # #571
    )


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


# ─── #563: a verdict follows its episode across a file replacement ─────────
_EPISODE_TOKEN = re.compile(r"(?i)(?<![a-z0-9])s(\d{1,3})[ ._-]?e(\d{1,4})")
_SEASON_DIR = re.compile(
    r"(?i)^(?:(?:season|series|staffel|saison|temporada|stagione|seizoen)[ ._-]*\d{1,4}|s\d{1,3}|specials?)$"
)


def episode_key(canonical_path: str) -> tuple[int, int] | None:
    """(season, episode) from an SxxExx token in the file NAME, or None."""
    m = _EPISODE_TOKEN.search(canonical_path.rsplit("/", 1)[-1])
    return (int(m.group(1)), int(m.group(2))) if m else None


def series_folder(canonical_path: str) -> str | None:
    """The show folder a file belongs to: its parent, or its grandparent when
    the parent is a season folder. Never the library root (`TV`), so a lookup
    can never span shows."""
    parts = canonical_path.split("/")[:-1]
    if parts and _SEASON_DIR.match(parts[-1]):
        parts = parts[:-1]
    return "/".join(parts) if len(parts) >= 2 else None


def _with_slash(prefix: str) -> str:
    """A series prefix in its canonical form. The trailing slash is what keeps
    'TV/Alert/' from matching 'TV/Alerts/', so every comparison uses it."""
    return prefix if prefix.endswith("/") else prefix + "/"


def _like_prefix(prefix: str) -> str:
    """A LIKE pattern for everything under `prefix/`, escaping with a backslash."""
    return prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "/%"


def _index_videos(series_fs: Path) -> dict[tuple[int, int], list[str]]:
    """{(season, episode): [absolute paths]} for every video file under a show."""
    from .paths import VIDEO_EXTS

    found: dict[tuple[int, int], list[str]] = {}
    for root, _dirs, files in os.walk(series_fs):
        for n in files:
            if os.path.splitext(n)[1].lower() in VIDEO_EXTS:
                k = episode_key(n)
                if k is not None:
                    found.setdefault(k, []).append(os.path.join(root, n))
    return found


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
        sonarr_episode_id: int | None = None,  # #571
        radarr_movie_id: int | None = None,  # #571
    ) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO audio_lang_verifications "
                "(canonical_path, lang_code, source, confidence, verified_at, verified_by, evidence, "
                " lang_class, lang_codes, sonarr_episode_id, radarr_movie_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(canonical_path) DO UPDATE SET "
                "  lang_code=excluded.lang_code, source=excluded.source, "
                "  confidence=excluded.confidence, verified_at=excluded.verified_at, "
                "  verified_by=excluded.verified_by, evidence=excluded.evidence, "
                "  lang_class=excluded.lang_class, lang_codes=excluded.lang_codes, "
                # #571: a re-verify that carries no id must not erase the one
                # already recorded — COALESCE keeps it.
                "  sonarr_episode_id=COALESCE(excluded.sonarr_episode_id, sonarr_episode_id), "
                "  radarr_movie_id=COALESCE(excluded.radarr_movie_id, radarr_movie_id)",
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
                    sonarr_episode_id,  # #571
                    radarr_movie_id,  # #571
                ),
            )

    def get(self, canonical_path: str, *, follow_replaced: bool = False) -> AudioLangVerification | None:
        """The verdict for this exact path, else (#226) the longest matching
        series intent.

        #563: `follow_replaced=True` first looks for a verdict orphaned when
        Sonarr replaced this file (see `carry_over`) and moves it here. Only the
        queue-time override opts in: it can touch the filesystem, and every
        bulk reader stays an exact lookup."""
        exact = self._get_exact(canonical_path)
        if exact is not None:
            return exact
        if follow_replaced:
            try:
                carried = self.carry_over(canonical_path)
            except Exception:  # noqa: BLE001 - a failed carry-over must not block the lookup
                logging.getLogger(__name__).warning(
                    "verdict carry-over failed for %s", scrub(canonical_path), exc_info=True
                )
                carried = None
            if carried is not None:
                return carried
        return self._get_intent(canonical_path)

    def _get_exact(self, canonical_path: str) -> AudioLangVerification | None:
        with self._lock:
            row = self._conn.execute(
                f"{_SELECT_VERDICT} WHERE canonical_path = ?",
                (canonical_path,),
            ).fetchone()
        return _row_to_verification(row) if row else None

    def _get_intent(self, canonical_path: str) -> AudioLangVerification | None:
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
            rows = self._conn.execute(f"{_SELECT_VERDICT} ORDER BY verified_at DESC").fetchall()
        return [_row_to_verification(r) for r in rows]

    # ─── #563: carry a verdict across a file replacement ─────────────

    def find_carry_over(self, new_path: str, *, _videos: dict | None = None) -> str | None:
        """The orphaned verdict path to move onto `new_path`, or None.

        Moves only when every one of these holds:
          - `new_path` has no verdict of its own;
          - it names an episode (SxxExx) inside a show folder;
          - a verdict exists for the same episode in the same show whose file
            is GONE (two live files never share a verdict), and the show folder
            itself reads, so an unmounted share never looks like a replacement;
          - `new_path` is the ONLY video file for that episode in the show;
          - every orphaned verdict for the episode agrees on the language (the
            most recent one moves).
        The database is checked first, so a path with no candidate costs no
        filesystem access. `_videos` caches the show walk across a sweep."""
        from .paths import PathOutsideRootError, canonical_to_fs

        key = episode_key(new_path)
        series = series_folder(new_path)
        if key is None or series is None:
            return None
        with self._lock:
            if self._conn.execute(
                "SELECT 1 FROM audio_lang_verifications WHERE canonical_path = ?", (new_path,)
            ).fetchone():
                return None
            rows = self._conn.execute(
                "SELECT canonical_path, lang_code, lang_class, lang_codes, verified_at "
                "FROM audio_lang_verifications WHERE canonical_path LIKE ? ESCAPE '\\'",
                (_like_prefix(series),),
            ).fetchall()
        rows = [r for r in rows if r[0] != new_path and episode_key(r[0]) == key]
        if not rows:
            return None
        try:
            series_fs = canonical_to_fs(series)
            new_fs = canonical_to_fs(new_path)
        except PathOutsideRootError:
            return None
        # No separate "is the share mounted" check: an unmounted share makes
        # every old file look gone, but then the walk below cannot find
        # `new_path` either, so the uniqueness rule refuses.
        orphans = []
        for r in rows:
            try:
                if canonical_to_fs(r[0]).exists():
                    continue
            except PathOutsideRootError:
                continue
            orphans.append(r)
        if not orphans:
            return None
        if _videos is not None and series in _videos:
            videos = _videos[series]
        else:
            videos = _index_videos(series_fs)
            if _videos is not None:
                _videos[series] = videos
        same_episode = videos.get(key, [])
        if len(same_episode) != 1 or Path(same_episode[0]) != new_fs:
            return None
        languages = {(normalize_lang(r[1]) or r[1], r[2] or "single", r[3] or "") for r in orphans}
        if len(languages) != 1:
            return None
        return max(orphans, key=lambda r: r[4] or 0.0)[0]

    def carry_over(self, new_path: str, *, _videos: dict | None = None) -> AudioLangVerification | None:
        """#563: move the verdict orphaned by a file replacement onto `new_path`
        (see `find_carry_over`) and return it, or None. The moved verdict keeps
        its source, confidence and date, and records `carried_from` and
        `carried_at` in its evidence so the UI can say where it came from."""
        old = self.find_carry_over(new_path, _videos=_videos)
        if old is None:
            return None
        return self._move_verdict(old, new_path)

    def _move_verdict(self, old: str, new_path: str) -> AudioLangVerification | None:
        """Re-key one verdict onto `new_path`, recording where it came from.
        The row is UPDATEd rather than re-inserted, so the language, source,
        confidence, date and (#571) the arr id all travel with it."""
        with self._lock:
            row = self._conn.execute(
                "SELECT evidence FROM audio_lang_verifications WHERE canonical_path = ?", (old,)
            ).fetchone()
            if row is None:
                return None
            try:
                evidence = json.loads(row[0]) if row[0] else {}
            except (ValueError, TypeError):
                evidence = {}
            if not isinstance(evidence, dict):
                evidence = {"previous_evidence": evidence}
            evidence["carried_from"] = old
            evidence["carried_at"] = time.time()
            cur = self._conn.execute(
                "UPDATE audio_lang_verifications SET canonical_path = ?, evidence = ? "
                "WHERE canonical_path = ? AND NOT EXISTS "
                "(SELECT 1 FROM audio_lang_verifications WHERE canonical_path = ?)",
                (new_path, json.dumps(evidence), old, new_path),
            )
            if cur.rowcount != 1:
                return None
        logging.getLogger(__name__).info(
            "audio-lang verdict carried over (#563): %s -> %s", scrub(old), scrub(new_path)
        )
        return self._get_exact(new_path)

    def sweep_carry_overs(
        self, path_ids: dict[str, tuple[int | None, int | None]] | None = None
    ) -> list[tuple[str, str]]:
        """Move every verdict whose file was replaced by a single successor.
        Returns (old, new) pairs. Stats each verdict path once and walks each
        affected show once; safe to repeat (a moved verdict is no longer an
        orphan).

        #571: `path_ids` maps a file that EXISTS to the (sonarr_episode_id,
        radarr_movie_id) the coverage snapshot holds for it. Given it, a verdict
        carrying the same id is carried onto that file even when the name has no
        SxxExx token to match — which is the only route for a date-named episode
        (#561) or a movie. The mapping is passed in rather than fetched, so the
        sweep makes no API call and stays runnable on a timer."""
        from .paths import PathOutsideRootError, canonical_to_fs

        with self._lock:
            paths = [r[0] for r in self._conn.execute("SELECT canonical_path FROM audio_lang_verifications")]
        videos: dict = {}
        successors: set[str] = set()
        for old in paths:
            key = episode_key(old)
            series = series_folder(old)
            if key is None or series is None:
                continue
            try:
                if canonical_to_fs(old).exists():
                    continue
                series_fs = canonical_to_fs(series)
            except PathOutsideRootError:
                continue
            if series not in videos:  # an unreadable show folder walks to nothing
                videos[series] = _index_videos(series_fs)
            same = videos[series].get(key, [])
            if len(same) == 1:
                rel = os.path.relpath(same[0], series_fs).replace(os.sep, "/")
                successors.add(f"{series}/{rel}")
        moved: list[tuple[str, str]] = []
        for new in sorted(successors):
            carried = self.carry_over(new, _videos=videos)
            if carried is not None:
                moved.append((carried.evidence["carried_from"], new))
        moved.extend(self._carry_by_id(path_ids or {}))
        return moved

    def _carry_by_id(self, path_ids: dict[str, tuple[int | None, int | None]]) -> list[tuple[str, str]]:
        """#571: carry each orphaned verdict onto the file its arr id now names.

        Refuses unless the successor is unambiguous, on the same terms the token
        match uses: the old file is really gone, exactly ONE existing file
        carries that id, it has no verdict of its own, and it is in the SAME
        LIBRARY — the same Sonarr id in another instance is a different file
        (#161)."""
        from .paths import PathOutsideRootError, _split_canonical, canonical_to_fs

        if not path_ids:
            return []
        # (kind, id, library slug) -> the existing files that claim it
        claims: dict[tuple[str, int, str], list[str]] = {}
        for path, (sonarr_id, radarr_id) in path_ids.items():
            try:
                slug, _rel = _split_canonical(path)
            except PathOutsideRootError:
                continue
            for kind, ident in (("sonarr", sonarr_id), ("radarr", radarr_id)):
                if ident is not None:
                    claims.setdefault((kind, int(ident), slug), []).append(path)

        with self._lock:
            rows = self._conn.execute(
                "SELECT canonical_path, sonarr_episode_id, radarr_movie_id "
                "FROM audio_lang_verifications "
                "WHERE sonarr_episode_id IS NOT NULL OR radarr_movie_id IS NOT NULL"
            ).fetchall()

        moved: list[tuple[str, str]] = []
        for old, sonarr_id, radarr_id in rows:
            try:
                if canonical_to_fs(old).exists():
                    continue  # two live files never share one verdict
                slug, _rel = _split_canonical(old)
            except PathOutsideRootError:
                continue
            kind, ident = ("sonarr", sonarr_id) if sonarr_id is not None else ("radarr", radarr_id)
            candidates = claims.get((kind, int(ident), slug), [])
            if len(candidates) != 1:
                continue  # none, or ambiguous
            new = candidates[0]
            # A verdict already on `new` is refused by _move_verdict's own
            # NOT EXISTS clause, which is the authoritative guard; checking it
            # here as well only duplicated it.
            if new == old:
                continue
            try:
                if not canonical_to_fs(new).exists():
                    continue  # the snapshot is stale; the file is not there now
            except PathOutsideRootError:
                continue
            if self._move_verdict(old, new) is not None:
                moved.append((old, new))
        return moved

    def backfill_ids(self, path_ids: dict[str, tuple[int | None, int | None]]) -> dict[str, int]:
        """#571: give verdicts recorded before this existed the arr id of the
        file they name, so a future replacement can carry them.

        Only for a verdict whose file is STILL ON DISK: without the file there is
        nothing to confirm the mapping is about this verdict rather than about
        whatever now occupies the path. An id already recorded is never
        overwritten. Idempotent, and returns counts so the caller can report
        what it did."""
        from .paths import PathOutsideRootError, canonical_to_fs

        with self._lock:
            rows = self._conn.execute(
                "SELECT canonical_path FROM audio_lang_verifications "
                "WHERE sonarr_episode_id IS NULL AND radarr_movie_id IS NULL"
            ).fetchall()
        considered = updated = skipped_file_missing = 0
        for (path,) in rows:
            ids = path_ids.get(path)
            if ids is None:
                continue
            considered += 1
            sonarr_id, radarr_id = ids
            if sonarr_id is None and radarr_id is None:
                continue
            try:
                if not canonical_to_fs(path).exists():
                    skipped_file_missing += 1
                    continue
            except PathOutsideRootError:
                continue
            with self._lock:
                cur = self._conn.execute(
                    "UPDATE audio_lang_verifications "
                    "SET sonarr_episode_id = ?, radarr_movie_id = ? "
                    "WHERE canonical_path = ? "
                    "  AND sonarr_episode_id IS NULL AND radarr_movie_id IS NULL",
                    (sonarr_id, radarr_id, path),
                )
            updated += cur.rowcount
        return {
            "considered": considered,
            "updated": updated,
            "skipped_file_missing": skipped_file_missing,
        }

    def get_carried_lookup(self) -> dict[str, str]:
        """{canonical_path: carried_from} for every verdict moved by #563."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT canonical_path, evidence FROM audio_lang_verifications WHERE evidence LIKE '%carried_from%'"
            ).fetchall()
        out: dict[str, str] = {}
        for path, raw in rows:
            try:
                ev = json.loads(raw) if raw else None
            except (ValueError, TypeError):
                continue
            if isinstance(ev, dict) and isinstance(ev.get("carried_from"), str):
                out[path] = ev["carried_from"]
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

    # ── #568: suggest a series rule from agreeing episode verdicts ──────

    def dismiss_series_suggestion(self, series_prefix: str, note: str | None = None) -> None:
        """Never offer a series rule for this show again."""
        with self._lock:
            self._conn.execute(
                "INSERT INTO series_rule_suggestion_dismissed "
                "(series_prefix, dismissed_at, note) VALUES (?, ?, ?) "
                "ON CONFLICT(series_prefix) DO UPDATE SET "
                "  dismissed_at=excluded.dismissed_at, note=excluded.note",
                (_with_slash(series_prefix), time.time(), note),
            )

    def undismiss_series_suggestion(self, series_prefix: str) -> bool:
        """Offer this show again. True if a dismissal existed."""
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM series_rule_suggestion_dismissed WHERE series_prefix = ?",
                (_with_slash(series_prefix),),
            )
            return cur.rowcount > 0

    def get_suggestion_dismissed_set(self) -> set[str]:
        with self._lock:
            rows = self._conn.execute("SELECT series_prefix FROM series_rule_suggestion_dismissed").fetchall()
        return {r[0] for r in rows}

    def suggest_series_rules(self, min_agreeing: int = 3) -> list[dict[str, Any]]:
        """Shows whose per-episode verdicts all agree and that have no rule yet.

        A show is offered only when EVERY verdict under it names the same single
        language: a show whose verdicts disagree is a genuinely multilingual one
        (#140) where the per-episode verdicts are the right answer, and one
        dissenting verdict is enough to silence the show. Ordered by how many
        verdicts the rule would cover, because that is how much per-file work it
        saves.
        """
        by_show: dict[str, list[AudioLangVerification]] = {}
        for v in self.list_all():
            folder = series_folder(v.canonical_path)
            if folder is None:
                continue  # a movie, or a file sitting at a library root
            by_show.setdefault(folder, []).append(v)

        intents = [i["series_prefix"] for i in self.list_series_intents()]
        dismissed = self.get_suggestion_dismissed_set()
        mixed = self.get_mixed_dismissed_set()

        out: list[dict[str, Any]] = []
        for folder, verdicts in by_show.items():
            prefix = _with_slash(folder)
            if len(verdicts) < min_agreeing:
                continue
            # Compare prefix-to-prefix: both carry the trailing slash, so
            # 'TV/Alert/' can never swallow 'TV/Alerts/'.
            if any(prefix.startswith(i) for i in intents):
                continue
            if prefix in dismissed:
                continue
            if folder in mixed or prefix in mixed:
                continue
            if any(v.lang_class == "multi" for v in verdicts):
                continue
            langs = {v.lang_code for v in verdicts}
            if len(langs) != 1:
                continue
            newest = sorted(verdicts, key=lambda v: v.verified_at, reverse=True)
            out.append(
                {
                    "series_prefix": prefix,
                    "title": folder.split("/")[-1],
                    "lang_code": langs.pop(),
                    "agreeing": len(verdicts),
                    "sample_paths": [v.canonical_path for v in newest[:3]],
                }
            )
        out.sort(key=lambda s: (-s["agreeing"], s["series_prefix"]))
        return out

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
    skip_audio_languages: "tuple[str, ...] | list[str] | None" = None,
) -> str | None:
    """Look up the user-verified audio language for `canonical` and
    decide whether to forward it to subgen as audio_language_override.

    Returns the canonical 2-letter ISO-639-1 code (e.g. 'ja', 'fr') if the
    verification passes the evidence gate, else None. (#358: was 3-letter;
    subgen parses the override via LanguageCode.from_string which accepts any
    form, so the 2-letter canonical the store now holds is forwarded as-is.)
    None means "let subgen detect from audio".

    `skip_audio_languages` is the connected subgen's effective
    SKIP_IF_AUDIO_LANGUAGES list (subarr-subgen v4.28+ advertises it on /queue).
    None means the build does not advertise it, i.e. we cannot tell.

    Evidence gate (#105):
      - lang missing → no override
      - lang is English → forwarded ONLY when we can see subgen does not skip
        English audio; withheld when it does, and withheld when unknown (#498)
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
    # #563: follow a verdict across a Sonarr/Radarr file replacement.
    verification = store.get(canonical, follow_replaced=True)
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

    if not lang:
        return None

    # #498: English is special, and not for the reason the old guard implied.
    # subgen's audio_language_override SUBSTITUTES the declared language into the
    # skip check rather than bypassing it:
    #
    #     if audio_language_override is not None:
    #         audio_langs = [audio_language_override]
    #     ...
    #     if any(lang in skip_audio_languages for lang in audio_langs):
    #         return True   # skip
    #
    # So forwarding 'en' to an install running SKIP_IF_AUDIO_LANGUAGES=eng puts
    # the file ON the skip list, which is the opposite of what the user who
    # verified it wanted. It was therefore excluded outright, at the cost of
    # silently discarding that verification on installs that skip nothing.
    #
    # Forward it only when we can SEE that the connected subgen will not skip it
    # (subarr-subgen v4.28+ advertises the effective list via /queue). Unknown
    # means withhold: guessing wrong in that direction stops transcription
    # silently, which is far worse than merely failing to start it.
    #
    # Deliberately scoped to English. A verified non-English language is
    # forwarded exactly as before even when subgen would skip it, because
    # changing that alters behaviour well beyond the reported bug.
    if lang in ("en", "eng"):
        if skip_audio_languages is None:
            return None
        skips = {str(code).strip().lower() for code in skip_audio_languages}
        if skips & {"en", "eng"}:
            _log.info(
                "%s: no override for %s — verified English, but this subgen skips "
                "English audio (%s), so declaring it would skip the file",
                caller,
                scrub(canonical),
                sorted(skips & {"en", "eng"}),
            )
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
