"""#196/#202 — detect a non-persistent /data volume.

If a user runs subarr without a persistent volume for `/data`, every
`docker compose up` starts from an empty database: all their audio-language
verifications, intents, and provenance are gone, and a fresh telemetry
install_id is minted (which also inflated our fleet stats with phantom
one-day installs). This is a classic *arr footgun and a brutal first
impression. We detect it on boot and surface it loudly (Health page + log),
and report it as a telemetry signal so we can measure how common it is.

Heuristic: a real bind/named volume mounted at `/data` has a different
filesystem device id than the container's root. If `/data`'s parent shares
the root's device, it lives in the container's ephemeral writable layer.
Only meaningful inside a container — returns None (unknown) otherwise, so
dev hosts and bare-metal runs never see a false warning.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)

TASK_NAME = "data-persistence"


def data_dir_is_ephemeral(db_path: Path) -> bool | None:
    """True  → /data is the container's writable layer (NOT persisted).
    False → /data is a real mount (safe).
    None  → can't tell (not in a container / not Linux) — never warn.
    """
    try:
        if not os.path.exists("/.dockerenv"):
            return None  # not a container — persistence is the host filesystem's job
        data_dir = Path(db_path).parent
        if not data_dir.exists():
            return None
        # A mount point has a different st_dev than the filesystem it's mounted
        # under. Same device as `/` ⇒ /data is in the container overlay ⇒ wiped
        # on every recreate.
        return os.stat(data_dir).st_dev == os.stat("/").st_dev
    except Exception:
        return None


def check_data_persistence(db_path: Path, health) -> bool | None:
    """Record the persistence verdict into task_health and return whether
    /data is PERSISTENT: True = persistent (safe), False = ephemeral (will be
    wiped), None = couldn't tell. The return feeds the `data_persistent`
    telemetry signal directly, so it's persistence-oriented, NOT the raw
    ephemeral flag. Never raises — best-effort, boot continues."""
    ephemeral = data_dir_is_ephemeral(db_path)
    if ephemeral is None:
        return None
    try:
        health.register(TASK_NAME, expected_interval_s=None)
        if ephemeral:
            from .db_integrity import DatabaseCorruptionError  # reuse a loud error type

            err = DatabaseCorruptionError(
                "/data is NOT a persistent volume — your subtitle verifications, "
                "language intents, and history will be LOST on the next container "
                "recreate. Mount a named volume or host path at /data. See the "
                "README 'Backing up your data' section."
            )
            health.record_failure(TASK_NAME, err, expected_interval_s=None)
            log.error(
                "DATA PERSISTENCE WARNING: /data appears to be the container's "
                "ephemeral layer, not a mounted volume. Everything in /data "
                "(verifications, intents, provenance) will be lost on recreate. "
                "Add a volume for /data."
            )
        else:
            health.record_success(TASK_NAME, expected_interval_s=None)
    except Exception:
        log.debug("data-persistence health record failed", exc_info=True)
    return not ephemeral
