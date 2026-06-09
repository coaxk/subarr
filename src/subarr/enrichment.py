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


# #156: switched from free-text "respond with ONLY an ISO 639-1 code" to a
# structured JSON schema. The model still says what it thinks, but it has
# to fit the schema, which Ollama enforces server-side. Net effect: parse
# errors drop to zero and we get a confidence score for free (lets the
# scorer downstream weight low-confidence guesses appropriately).
_SYSTEM_PROMPT = (
    "You are a media-cataloguing assistant. Given a TV series or movie title "
    "and folder path, identify the ORIGINAL spoken language of the production. "
    "Output a JSON object with: iso_code (ISO 639-1 two-letter code, or 'und' "
    "if you can't tell), confidence (0.0 to 1.0), and a one-sentence reasoning. "
    "Examples of ISO codes: en, fr, es, de, it, ja, ko, zh, ru, pl, pt, hi."
)

_USER_TEMPLATE = "Title: {title}\nFolder path: {path}"

# Schema passed to Ollama's `format` parameter. Ollama (and llama.cpp under
# the hood) honour this as a grammar constraint — the model's output is
# guaranteed to parse as valid JSON matching the schema.
_LANG_SCHEMA = {
    "type": "object",
    "properties": {
        "iso_code": {
            "type": "string",
            "description": "ISO 639-1 two-letter code, or 'und' if unknown",
        },
        "confidence": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
        },
        "reasoning": {
            "type": "string",
        },
    },
    "required": ["iso_code", "confidence"],
}

_ISO_RE = re.compile(r"^[a-z]{2,3}$")


# Schema (lang_enrichment incl. the #156 confidence/reasoning columns) is
# owned by migrations/001_baseline.sql + 008_init_schema_parity.sql.
# run_migrations() runs at boot before this store — no per-store
# init_schema(). confidence is 0.0-1.0; reasoning is the model's
# one-sentence explanation. Both nullable so legacy free-text rows stay
# valid until they cycle through eviction.


@dataclass
class EnrichmentResult:
    canonical_path: str
    title: str
    iso_code: str | None
    raw_response: str
    model: str
    cached: bool
    # #156: surfaced from the structured JSON output. confidence is None on
    # cached rows pre-dating the migration; new inferences always have one.
    confidence: float | None = None
    reasoning: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "canonical_path": self.canonical_path,
            "title": self.title,
            "iso_code": self.iso_code,
            "raw_response": self.raw_response,
            "model": self.model,
            "cached": self.cached,
            "confidence": self.confidence,
            "reasoning": self.reasoning,
        }


class EnrichmentStore:
    """SQLite-backed cache so repeated coverage walks don't pound the LLM."""

    def __init__(self, db_path: Path):
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False, isolation_level=None)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._lock = threading.Lock()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def get(self, canonical_path: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT canonical_path, title, raw_response, iso_code, model, inferred_at, "
                "       error, confidence, reasoning "
                "FROM lang_enrichment WHERE canonical_path = ?",
                (canonical_path,),
            ).fetchone()
        if row is None:
            return None
        return {
            "canonical_path": row[0],
            "title": row[1],
            "raw_response": row[2],
            "iso_code": row[3],
            "model": row[4],
            "inferred_at": row[5],
            "error": row[6],
            "confidence": row[7],
            "reasoning": row[8],
        }

    def upsert(
        self,
        *,
        canonical_path: str,
        title: str,
        raw_response: str,
        iso_code: str | None,
        model: str,
        error: str | None = None,
        confidence: float | None = None,
        reasoning: str | None = None,
    ) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO lang_enrichment "
                "(canonical_path, title, raw_response, iso_code, model, inferred_at, "
                " error, confidence, reasoning) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(canonical_path) DO UPDATE SET "
                "  title=excluded.title, raw_response=excluded.raw_response, "
                "  iso_code=excluded.iso_code, model=excluded.model, "
                "  inferred_at=excluded.inferred_at, error=excluded.error, "
                "  confidence=excluded.confidence, reasoning=excluded.reasoning",
                (
                    canonical_path,
                    title,
                    raw_response,
                    iso_code,
                    model,
                    time.time(),
                    error,
                    confidence,
                    reasoning,
                ),
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


def _parse_structured(raw: str) -> tuple[str | None, float | None, str | None]:
    """Parse Ollama's JSON-mode response into (iso_code, confidence, reasoning).

    Ollama with `format=<schema>` guarantees valid JSON matching the schema —
    but defensively handle the rare case where the model returned something
    malformed (e.g. transient backend hiccup), falling back to the v1.1
    free-text iso parser so legacy prompts/models still work.
    """
    try:
        obj = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        # Fallback: maybe the model returned bare ISO text (vanilla mode).
        return parse_iso(raw or ""), None, None
    iso = (obj.get("iso_code") or "").strip().lower()
    if iso == "und" or not _ISO_RE.match(iso):
        iso_norm: str | None = None
    else:
        iso_norm = iso
    conf = obj.get("confidence")
    try:
        conf_norm = float(conf) if conf is not None else None
    except (TypeError, ValueError):
        conf_norm = None
    reasoning = obj.get("reasoning")
    reasoning_norm = str(reasoning) if reasoning else None
    return iso_norm, conf_norm, reasoning_norm


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
                confidence=cached.get("confidence"),
                reasoning=cached.get("reasoning"),
            )

    prompt = _USER_TEMPLATE.format(title=title, path=canonical_path)
    try:
        # #156: structured JSON output. num_predict bumped from 8 (enough
        # for a bare ISO code) to 120 (enough for ISO + confidence + a
        # one-sentence reason). Grammar-constrained JSON means the model
        # can't over-shoot the schema regardless.
        raw = await ollama.generate(
            prompt,
            system=_SYSTEM_PROMPT,
            temperature=0.0,
            num_predict=120,
            keep_alive=keep_alive,
            format_schema=_LANG_SCHEMA,
        )
    except OllamaError as e:
        store.upsert(
            canonical_path=canonical_path,
            title=title,
            raw_response="",
            iso_code=None,
            model=ollama.model,
            error=str(e),
        )
        raise

    iso, confidence, reasoning = _parse_structured(raw)
    store.upsert(
        canonical_path=canonical_path,
        title=title,
        raw_response=raw,
        iso_code=iso,
        model=ollama.model,
        error=None,
        confidence=confidence,
        reasoning=reasoning,
    )
    return EnrichmentResult(
        canonical_path=canonical_path,
        title=title,
        iso_code=iso,
        raw_response=raw,
        model=ollama.model,
        cached=False,
        confidence=confidence,
        reasoning=reasoning,
    )
