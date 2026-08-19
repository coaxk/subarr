"""#112 — persisted UI/wizard config overrides.

A small JSON file on the persisted volume (/data) holding settings the user
changed from the UI. `config.load()` overlays these on top of built-in
defaults but BELOW env vars (the operator's authoritative config), so the
precedence is: env > this file > default.

Integrity (the config-loss fix): all reads/writes are serialized by a module
lock so two concurrent saves can never clobber each other's keys; writes are
atomic + fsynced; and a save NEVER overwrites a present-but-unreadable file
(which would destroy recoverable config) — a transient read error aborts the
save, and a genuinely corrupt file is preserved as `<name>.corrupt-<ts>` before
starting fresh. Reads stay fail-soft so config always loads. The bulk
save_overrides/clear_overrides operations inherit these same guarantees: one
lock, one strict read, one fsynced atomic write, so a multi-field update or
reset can never leave partial persistence.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path

log = logging.getLogger(__name__)

_STORE_ENV = "SUBARR_CONFIG_STORE"

# Serialize read-modify-write: two concurrent save_override calls must not each
# read the file and write back a dict missing the other's key (silent loss).
# RLock so a save's internal read never self-deadlocks.
_lock = threading.RLock()


class ConfigStoreError(RuntimeError):
    """The overrides file exists but could not be read, so writing would destroy
    recoverable config. Callers abort the save rather than clobber it."""


def store_path() -> Path:
    """Override location: explicit env, else beside the DB on /data."""
    override = os.environ.get(_STORE_ENV)
    if override:
        return Path(override)
    db = os.environ.get("SUBARR_DB_PATH", "/data/subarr.db")
    return Path(db).parent / "subarr-overrides.json"


def _preserve_corrupt(p: Path) -> None:
    """Move a corrupt overrides file aside so a subsequent write cannot silently
    destroy it — it stays recoverable as `<name>.corrupt-<ts>`."""
    try:
        backup = p.with_name(f"{p.name}.corrupt-{int(time.time())}")
        p.rename(backup)
        log.error(
            "config overrides file was corrupt; preserved as %s and starting fresh. "
            "UI settings may need re-entering; the old file is recoverable.",
            backup.name,
        )
    except OSError:
        log.error("config overrides corrupt and could not be preserved", exc_info=True)


def _read_strict(p: Path) -> dict:
    """Current overrides for the WRITE path. Returns {} if the file is absent.
    RAISES ConfigStoreError if the file EXISTS but cannot be read (transient IO)
    — the caller must not overwrite it. A present-but-corrupt file is preserved
    (renamed aside) and read as {}."""
    if not p.is_file():
        return {}
    try:
        raw = p.read_text("utf-8")
    except OSError as e:
        raise ConfigStoreError(f"overrides present but unreadable: {e}") from e
    try:
        data = json.loads(raw)
    except ValueError:
        _preserve_corrupt(p)
        return {}
    return dict(data) if isinstance(data, dict) else {}


def load_overrides() -> dict:
    """Read path (boot / config.load). Fail-soft: never raises. A transient read
    error yields {} without touching the file; a corrupt file is preserved and
    yields {} — so config always loads and a bad read never becomes a wipe."""
    with _lock:
        try:
            return _read_strict(store_path())
        except ConfigStoreError:
            log.warning("config overrides temporarily unreadable; using none this load", exc_info=True)
            return {}


def _write(data: dict) -> None:
    p = store_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1, sort_keys=True)
        f.flush()
        os.fsync(f.fileno())  # durability: survive power-loss between write and the replace
    os.replace(tmp, p)  # atomic


def save_overrides(mapping: dict) -> None:
    """Atomically persist several keys in ONE locked read-modify-write.

    Under the module lock: one strict read, overlay every key from `mapping`,
    one fsynced atomic write. This guarantees a multi-field update cannot leave
    partial persistence if the process fails mid-loop. Preserves the
    abort-on-unreadable guarantee: a present-but-unreadable file raises
    ConfigStoreError and nothing is written, so recoverable config is never
    clobbered by a bulk save.
    """
    with _lock:
        # _read_strict raises ConfigStoreError on a present-but-unreadable file,
        # so we abort rather than overwrite recoverable config.
        data = _read_strict(store_path())
        data.update(mapping)
        _write(data)


def clear_overrides(keys: list[str]) -> None:
    """Atomically remove several keys in ONE locked read-modify-write.

    Absent keys are a no-op; an empty list is a no-op (single read, no write).
    Only writes when at least one listed key is actually present, matching the
    single-key clear behaviour.
    """
    with _lock:
        data = _read_strict(store_path())
        if any(k in data for k in keys):
            for k in keys:
                data.pop(k, None)
            _write(data)


def save_override(key: str, value) -> None:
    save_overrides({key: value})


def clear_override(key: str) -> None:
    clear_overrides([key])
