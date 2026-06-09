"""#112 — persisted UI/wizard config overrides.

A small JSON file on the persisted volume (/data) holding settings the user
changed from the UI. `config.load()` overlays these on top of built-in
defaults but BELOW env vars (the operator's authoritative config), so the
precedence is: env > this file > default.

Deliberately dependency-free (stdlib only) and fail-soft: a missing or
corrupt file reads as "no overrides", never raising — config must always
load.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)

_STORE_ENV = "SUBARR_CONFIG_STORE"


def store_path() -> Path:
    """Override location: explicit env, else beside the DB on /data."""
    override = os.environ.get(_STORE_ENV)
    if override:
        return Path(override)
    db = os.environ.get("SUBARR_DB_PATH", "/data/subarr.db")
    return Path(db).parent / "subarr-overrides.json"


def load_overrides() -> dict:
    p = store_path()
    if not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text("utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        log.warning("config override store unreadable (%s); ignoring", p, exc_info=True)
        return {}


def _write(data: dict) -> None:
    p = store_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=1, sort_keys=True), "utf-8")
    tmp.replace(p)  # atomic on POSIX


def save_override(key: str, value) -> None:
    data = load_overrides()
    data[key] = value
    _write(data)


def clear_override(key: str) -> None:
    data = load_overrides()
    if key in data:
        del data[key]
        _write(data)
