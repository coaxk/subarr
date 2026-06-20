"""#196 — boot-time SQLite integrity check.

Everything irreplaceable (audio-language verifications, series intents, the
provenance ledger, tuning-lab history) lives in one SQLite file. Corruption
must be LOUD: a damaged page deep in a B-tree can otherwise serve reads for
weeks while quietly poisoning writes. We run PRAGMA quick_check on boot
(milliseconds on healthy files; skips the slow index↔table cross-checks of
full integrity_check) and feed the result into the existing task_health
surface — Health page row + red header pill, the same place every other
silent-failure class lands.

Best-effort by design: the check itself must never take boot down.

#291 Slice B adds two on-demand helpers:
  - deep_integrity_check: full PRAGMA integrity_check (index↔table cross-check)
  - vacuum_backup: VACUUM INTO a timestamped clean copy in backups_dir
"""

from __future__ import annotations

import logging
import sqlite3
import time
from pathlib import Path

log = logging.getLogger(__name__)

TASK_NAME = "db-integrity"

# Re-checked at most daily by the caller's discretion; the boot check is the
# one that matters (covers unclean shutdowns, the main corruption window).
# #291: daily, not weekly — an always-on container's pill shouldn't read
# "stale" for a week between boots.
EXPECTED_INTERVAL_S = 1 * 86400.0


class DatabaseCorruptionError(Exception):
    """quick_check reported damage. The message carries SQLite's findings."""


def check_db_integrity(db_path: Path, health) -> bool:
    """PRAGMA quick_check against db_path; record the outcome in task_health.

    Returns True when healthy. Never raises — boot continues either way, the
    Health page carries the bad news.
    """
    try:
        health.register(TASK_NAME, expected_interval_s=EXPECTED_INTERVAL_S)
    except Exception:
        pass
    try:
        conn = sqlite3.connect(str(db_path), isolation_level=None)
        try:
            rows = conn.execute("PRAGMA quick_check").fetchall()
        finally:
            conn.close()
        findings = [r[0] for r in rows]
        if findings == ["ok"]:
            health.record_success(TASK_NAME, expected_interval_s=EXPECTED_INTERVAL_S)
            log.info("db integrity: quick_check ok (%s)", db_path)
            return True
        err = DatabaseCorruptionError(
            f"quick_check reported {len(findings)} finding(s): " + "; ".join(findings[:10])
        )
        health.record_failure(TASK_NAME, err, expected_interval_s=EXPECTED_INTERVAL_S)
        log.error(
            "db integrity: CORRUPTION detected in %s — back up /data and see the Health page. Findings: %s",
            db_path,
            findings[:10],
        )
        return False
    except Exception as e:
        # Unopenable/unreadable counts as a failure too (e.g. "file is not a
        # database"), recorded the same way.
        try:
            health.record_failure(TASK_NAME, e, expected_interval_s=EXPECTED_INTERVAL_S)
        except Exception:
            pass
        log.error("db integrity: check failed for %s: %s", db_path, e)
        return False


def deep_integrity_check(db_path: Path) -> tuple[bool, list[str]]:
    """PRAGMA integrity_check against db_path (the full check).

    Unlike quick_check (used at boot), integrity_check also verifies
    index↔table consistency — slower on large databases but catches a
    broader class of corruption. Safe to run against a live database.

    Returns (True, ['ok']) when healthy; (False, [findings...]) on any
    corruption or failure. Never raises — all exceptions are converted to
    a (False, [str(e)]) pair so callers can always unpack the tuple.
    """
    try:
        conn = sqlite3.connect(str(db_path), isolation_level=None)
        try:
            rows = conn.execute("PRAGMA integrity_check").fetchall()
        finally:
            conn.close()
        findings = [r[0] for r in rows]
        ok = findings == ["ok"]
        if ok:
            log.info("db deep integrity: integrity_check ok (%s)", db_path)
        else:
            log.error(
                "db deep integrity: CORRUPTION detected in %s — findings: %s",
                db_path,
                findings[:10],
            )
        return ok, findings
    except Exception as e:
        log.error("db deep integrity: check failed for %s: %s", db_path, e)
        return False, [str(e)]


def vacuum_backup(
    db_path: Path,
    backups_dir: Path,
    *,
    when: float,
    keep: int = 5,
) -> dict:
    """Write a clean consistent copy of db_path via VACUUM INTO.

    VACUUM INTO copies all live pages into a brand-new file atomically —
    safe to run while the database is open for reads/writes. The copy is
    a valid, fully defragmented SQLite file with no WAL or journal artefacts.

    Args:
        db_path:     Source database path.
        backups_dir: Directory to write backups into (created if absent).
        when:        Caller-supplied timestamp (time.time()) — used for the
                     filename so tests can pass a fixed value and assert the
                     exact name without clock races.
        keep:        Maximum number of backups to retain. Oldest by name
                     (lexicographic, which sorts chronologically given the
                     YYYYmmdd-HHMMSS prefix) are pruned when the count
                     exceeds this limit. Default 5.

    Returns a dict:
        {
            "path":         str absolute path of the new backup,
            "size_bytes":   int size of the backup file,
            "created_at":   float (the `when` param),
            "pruned":       list[str] filenames that were removed,
        }

    Raises on failure (IOError, sqlite3.Error, etc.) — callers should map
    to HTTPException + safe_error.
    """
    backups_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(when))
    dest = backups_dir / f"subarr-{stamp}.db"

    conn = sqlite3.connect(str(db_path), isolation_level=None)
    try:
        conn.execute(f"VACUUM INTO '{dest}'")
    finally:
        conn.close()

    size = dest.stat().st_size
    log.info("db backup: wrote %s (%d bytes)", dest, size)

    # Prune oldest backups beyond keep limit (sort lexicographically = chronological).
    existing = sorted(backups_dir.glob("subarr-*.db"))
    pruned: list[str] = []
    while len(existing) > keep:
        oldest = existing.pop(0)
        try:
            oldest.unlink()
            pruned.append(oldest.name)
            log.info("db backup: pruned %s", oldest.name)
        except Exception as e:
            log.warning("db backup: could not prune %s: %s", oldest.name, e)

    return {
        "path": str(dest),
        "size_bytes": size,
        "created_at": when,
        "pruned": pruned,
    }
