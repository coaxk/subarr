"""LLM-based language enrichment for ambiguous Coverage rows.

For rows where Sonarr's `originalLanguage` is null / 'und', we ask the
local LLM what the most likely language is, based on title + folder
context. Cached in SQLite so we don't re-ask.

Gating: caller (router) checks subgen `/queue` idle before enrichment
to avoid GPU contention with active transcribes. The LLM also runs on
the same GPU.
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .integrations.ollama import OllamaClient, OllamaError

log = logging.getLogger(__name__)


_SYSTEM_PROMPT = (
    "You are a media-cataloguing assistant. Given a TV series or movie title "
    "and folder path, identify the ORIGINAL spoken language of the production. "
    "Respond with ONLY an ISO 639-1 two-letter code (e.g. 'en', 'fr', 'ja', 'ko', "
    "'pl') — nothing else. If you cannot determine the language with reasonable "
    "confidence, respond with 'und'."
)

_USER_TEMPLATE = (
    "Title: {title}\n"
    "Folder path: {path}\n"
    "Original language (ISO 639-1 only):"
)

_ISO_RE = re.compile(r"^[a-z]{2,3}$")


_SCHEMA = """
CREATE TABLE IF NOT EXISTS lang_enrichment (
    canonical_path  TEXT PRIMARY KEY,
    title           TEXT NOT NULL,
    raw_response    TEXT,
    iso_code        TEXT,
    model           TEXT,
    inferred_at     REAL NOT NULL,
    error           TEXT
);
"""


@dataclass
class EnrichmentResult:
    canonical_path: str
    title: str
    iso_code: str | None
    raw_response: str
    model: str
    cached: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "canonical_path": self.canonical_path,
            "title": self.title,
            "iso_code": self.iso_code,
            "raw_response": self.raw_response,
            "model": self.model,
            "cached": self.cached,
        }


class EnrichmentStore:
    """SQLite-backed cache so repeated coverage walks don't pound the LLM."""

    def __init__(self, db_path: Path):
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False, isolation_level=None)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._lock = threading.Lock()

    def init_schema(self) -> None:
        with self._lock:
            self._conn.executescript(_SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def get(self, canonical_path: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT canonical_path, title, raw_response, iso_code, model, inferred_at, error "
                "FROM lang_enrichment WHERE canonical_path = ?",
                (canonical_path,),
            ).fetchone()
        if row is None:
            return None
        return {
            "canonical_path": row[0], "title": row[1], "raw_response": row[2],
            "iso_code": row[3], "model": row[4], "inferred_at": row[5], "error": row[6],
        }

    def upsert(self, *, canonical_path: str, title: str, raw_response: str,
                iso_code: str | None, model: str, error: str | None = None) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO lang_enrichment "
                "(canonical_path, title, raw_response, iso_code, model, inferred_at, error) "
                "VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(canonical_path) DO UPDATE SET "
                "  title=excluded.title, raw_response=excluded.raw_response, "
                "  iso_code=excluded.iso_code, model=excluded.model, "
                "  inferred_at=excluded.inferred_at, error=excluded.error",
                (canonical_path, title, raw_response, iso_code, model, time.time(), error),
            )


def parse_iso(raw: str) -> str | None:
    """Strip the LLM response down to a probable ISO 639-1 code, or None."""
    s = (raw or "").strip().lower()
    # First word, drop quotes/punctuation
    s = re.sub(r"[^a-z]", " ", s).strip()
    first = s.split()[0] if s else ""
    if _ISO_RE.match(first) and first != "und":
        return first
    return None


async def enrich_one(
    *,
    canonical_path: str,
    title: str,
    ollama: OllamaClient,
    store: EnrichmentStore,
    use_cache: bool = True,
    keep_alive: str | int | None = None,
) -> EnrichmentResult:
    if use_cache:
        cached = store.get(canonical_path)
        if cached and cached.get("iso_code"):
            return EnrichmentResult(
                canonical_path=cached["canonical_path"],
                title=cached["title"],
                iso_code=cached["iso_code"],
                raw_response=cached.get("raw_response") or "",
                model=cached.get("model") or ollama.model,
                cached=True,
            )

    prompt = _USER_TEMPLATE.format(title=title, path=canonical_path)
    try:
        raw = await ollama.generate(
            prompt, system=_SYSTEM_PROMPT, temperature=0.0, num_predict=8,
            keep_alive=keep_alive,
        )
    except OllamaError as e:
        store.upsert(
            canonical_path=canonical_path, title=title,
            raw_response="", iso_code=None,
            model=ollama.model, error=str(e),
        )
        raise

    iso = parse_iso(raw)
    store.upsert(
        canonical_path=canonical_path, title=title,
        raw_response=raw, iso_code=iso,
        model=ollama.model, error=None,
    )
    return EnrichmentResult(
        canonical_path=canonical_path, title=title, iso_code=iso,
        raw_response=raw, model=ollama.model, cached=False,
    )
