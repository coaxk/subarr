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
import sys
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

TASK_NAME = "data-persistence"
NETWORK_FS_TASK = "data-network-fs"

# Filesystem types where WAL mode is a known corruption vector.
_NETWORK_FS_TYPES = {"nfs", "nfs4", "cifs", "smb3", "smbfs", "fuse.glusterfs", "fuse.rclone"}


def mount_fstype_for(target_path: str, mountinfo_text: str) -> str | None:
    """Pure helper: parse /proc/self/mountinfo and return the fstype for the
    mount entry whose mount-point is the longest prefix of target_path.

    mountinfo line format (space-separated fields):
        <mount-id> <parent-id> <major:minor> <root> <mount-point> <mount-options>
        [optional-fields] ' - ' <fstype> <source> <super-options>

    Returns the fstype string, or None if no matching entry is found.
    """
    best_prefix = ""
    best_fstype: str | None = None
    for line in mountinfo_text.splitlines():
        # Split on the ' - ' separator that divides per-mount fields from fs info.
        parts = line.split(" - ", 1)
        if len(parts) != 2:
            continue
        left, right = parts
        left_fields = left.split()
        if len(left_fields) < 5:
            continue
        mount_point = left_fields[4]
        # Normalise: ensure mount_point ends with / so startswith works cleanly.
        mp = mount_point if mount_point.endswith("/") else mount_point + "/"
        tp = target_path if target_path.endswith("/") else target_path + "/"
        if tp.startswith(mp) and len(mp) > len(best_prefix):
            fstype = right.split()[0] if right.split() else None
            if fstype:
                best_prefix = mp
                best_fstype = fstype
    return best_fstype


def data_dir_network_fs(db_path: Path) -> str | None:
    """Return the fstype if db_path's parent is on a network filesystem known
    to be unsafe for WAL mode, else None.

    Container-gated (like data_dir_is_ephemeral): returns None when not inside
    a Docker container, not on Linux, or on any error — never warns on dev hosts.
    """
    try:
        if not os.path.exists("/.dockerenv"):
            return None
        if sys.platform != "linux":
            return None
        data_dir = str(Path(db_path).parent)
        mountinfo_text = Path("/proc/self/mountinfo").read_text(encoding="utf-8")
        fstype = mount_fstype_for(data_dir, mountinfo_text)
        if fstype in _NETWORK_FS_TYPES:
            return fstype
        return None
    except Exception:
        return None


def apply_journal_mode(conn, db_path) -> str:
    """Set the SQLite journal mode appropriate to where `/data` lives: WAL on
    local disk (fast, allows concurrent readers), but a rollback journal
    (TRUNCATE) on a network filesystem, where WAL's shared-memory index is a
    known corruption vector (#291). Container-gated via `data_dir_network_fs`,
    so local and dev installs (and anything not in a container) keep WAL exactly
    as before — only a network-FS `/data` flips to TRUNCATE. Returns the mode
    applied. Two literal PRAGMAs, no interpolation."""
    if data_dir_network_fs(Path(db_path)):
        conn.execute("PRAGMA journal_mode=TRUNCATE")
        return "TRUNCATE"
    conn.execute("PRAGMA journal_mode=WAL")
    return "WAL"


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


@dataclass(frozen=True)
class LegacyConfigMount:
    """[#473] The install followed our own v1.0.0/v1.1.0 docs and lost its data."""

    legacy_db_path: Path
    message: str


def diagnose_legacy_config_mount(
    *,
    db_path: Path,
    data_is_ephemeral: bool | None,
    config_dir: Path = Path("/config"),
) -> LegacyConfigMount | None:
    """Detect the specific broken shape our v1.0.0 and v1.1.0 README produced.

    That example mounted `./subarr/config:/config` and set no SUBARR_DB_PATH,
    while the default has always been `/data/subarr.db`. So the volume the user
    was told to mount is not the one subarr keeps its database in, `/data` sits
    on the container's ephemeral layer, and every recreate wipes everything.

    These people did not misconfigure anything -- they followed the docs. The
    generic "mount a volume at /data" warning reads as wrong to them, because
    they DID mount a volume. This names the actual mismatch instead.

    Returns None unless all three hold: /data is definitely ephemeral, /config
    exists, and a database is actually sitting in it. Anything less and the
    generic warning is the honest one.

    ⚠️ Deliberately does NOT advise copying the database into /data. If /data
    is the ephemeral layer, a copy there is erased on the next recreate: it
    would look like a migration and fix nothing. The data is already in the
    persistent place; what is wrong is where subarr is looking.
    """
    if data_is_ephemeral is not True:
        return None  # None (cannot tell) must never be read as broken
    try:
        legacy = Path(config_dir) / "subarr.db"
        if not legacy.is_file():
            return None
    except OSError:
        return None
    return LegacyConfigMount(
        legacy_db_path=legacy,
        message=(
            f"Your database is at {legacy}, but subarr is reading {db_path}, "
            f"and /data is not a persistent volume so it is wiped on every "
            f"recreate. This is the setup our v1.0.0 and v1.1.0 README "
            f"documented -- it mounted /config while the database default has "
            f"always been /data/subarr.db. Your data is safe where it is. "
            f"Fix it with one line: set SUBARR_DB_PATH={legacy} in your "
            f"environment, or add a persistent volume at /data and point "
            f"SUBARR_DB_PATH at it."
        ),
    )


def check_network_fs(db_path: Path, health) -> str | None:
    """Surface a network-FS `/data` on the Health page (not just a log line):
    SQLite over NFS/SMB is a corruption vector that sporadically loses history +
    verifications, which is easy to miss buried in logs. Returns the fstype (or
    None). Never raises — best-effort, boot continues."""
    fstype = data_dir_network_fs(db_path)
    try:
        health.register(NETWORK_FS_TASK, expected_interval_s=None)
        if fstype:
            from .db_integrity import DatabaseCorruptionError  # reuse a loud, visible error type

            err = DatabaseCorruptionError(
                f"/data is on a {fstype} network filesystem. SQLite over NFS/SMB/CIFS risks "
                f"sporadic database corruption — history, verifications and config can be lost. "
                f"Mount a LOCAL-disk volume at /data (keep media on the NAS, keep /data local)."
            )
            health.record_failure(NETWORK_FS_TASK, err, expected_interval_s=None)
            log.error(
                "DATA NETWORK-FS WARNING: /data is on a %s filesystem — SQLite over a network FS "
                "risks corruption. Mount a local-disk volume at /data.",
                fstype,
            )
        else:
            health.record_success(NETWORK_FS_TASK, expected_interval_s=None)
    except Exception:
        log.debug("network-fs health record failed", exc_info=True)
    return fstype


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

            # [#473] If this is the shape our own v1.0.0/v1.1.0 docs produced,
            # say THAT instead. Those users mounted a volume exactly as told, so
            # "mount a volume at /data" reads as wrong and gets dismissed -- and
            # their data is recoverable, which the generic message never says.
            legacy = diagnose_legacy_config_mount(db_path=db_path, data_is_ephemeral=True)
            err = DatabaseCorruptionError(
                legacy.message
                if legacy
                else (
                    "/data is NOT a persistent volume — your subtitle verifications, "
                    "language intents, and history will be LOST on the next container "
                    "recreate. Mount a named volume or host path at /data. See the "
                    "README 'Backing up your data' section."
                )
            )
            health.record_failure(TASK_NAME, err, expected_interval_s=None)
            if legacy:
                log.error("DATA PERSISTENCE WARNING: %s", legacy.message)
            else:
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
