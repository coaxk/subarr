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
"""

from __future__ import annotations

import logging
import sqlite3
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
