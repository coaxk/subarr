"""Update notification backend.

Polls GitHub releases for tracked products once per 24h, caches result in
SQLite. The UI consumes this via GET /api/updates and renders three
surfaces (header pill, Home tile, Settings panel).

Design choices:

- **Polling cadence**: once per 24h. Cheap, well under GitHub's
  unauthenticated 60-req/h limit. Forced refresh available via
  POST /api/updates/refresh for admin.

- **State storage**: a single row per product in `update_checks`. Reads
  hit the DB, not the GitHub API — the UI is fast and offline-tolerant.

- **Privacy**: we send the user's IP to api.github.com when polling
  (unavoidable). No telemetry, no identifiers. Documented in the
  Settings → Updates panel "what we collect" disclosure.

- **Failures**: a failed poll updates `last_error` but doesn't clear
  the previous-good cached state. So a transient GitHub outage doesn't
  make the UI go "no update info" — it keeps showing the last-known.

- **Compat-mode case**: when subarr is pointed at vanilla subgen
  instead of our subarr-subgen image, the subarr-subgen product entry
  is still tracked but the UI surfaces "tracking manually" with a
  link to McCloudS/subgen releases instead of our own.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

log = logging.getLogger(__name__)

# Default products subarr tracks. Each row maps to one update_checks row.
DEFAULT_PRODUCTS = {
    "subarr":         "coaxk/subarr",
    "subarr-subgen":  "coaxk/subarr-subgen",
}

# Poll cadence. The 24h floor keeps us well below GitHub's anonymous
# rate limit (60/h shared across all IPs from a NAT — homelab users
# may hit it if many tools poll, hence the conservative interval).
DEFAULT_POLL_INTERVAL_S = 86400  # 24 hours


@dataclass
class UpdateState:
    """One row from update_checks, hydrated for the API."""
    product: str
    repo: str
    current_version: str | None
    latest_version: str | None
    latest_released_at: float | None
    release_notes_url: str | None
    is_breaking: bool
    checked_at: float
    last_error: str | None

    @property
    def has_update(self) -> bool:
        """True iff we know both current + latest and they differ.

        Uses string equality — versions are opaque tags like
        'v1.0.0' or 'v2026.05.3-r1'. We don't try to semver-compare;
        any mismatch surfaces a notification and the user decides."""
        if not self.current_version or not self.latest_version:
            return False
        return self.current_version.lstrip("v") != self.latest_version.lstrip("v")

    def to_dict(self) -> dict[str, Any]:
        return {
            "product": self.product,
            "repo": self.repo,
            "current_version": self.current_version,
            "latest_version": self.latest_version,
            "latest_released_at": self.latest_released_at,
            "release_notes_url": self.release_notes_url,
            "is_breaking": self.is_breaking,
            "checked_at": self.checked_at,
            "last_error": self.last_error,
            "has_update": self.has_update,
        }


class UpdateChecker:
    """Polls GitHub + caches the result for the UI to consume."""

    def __init__(
        self,
        db_path: Path,
        products: dict[str, str] | None = None,
        poll_interval_s: int = DEFAULT_POLL_INTERVAL_S,
        current_version_resolver: dict[str, str | None] | None = None,
    ):
        self._db_path = db_path
        self._products = products or DEFAULT_PRODUCTS
        self._interval_s = poll_interval_s
        # Static map of product → current version. Caller supplies; we
        # don't introspect ourselves to keep this module decoupled from
        # SubgenClient + subarr.__version__.
        self._current = current_version_resolver or {}
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._client: httpx.AsyncClient | None = None

    # ─── Lifecycle ─────────────────────────────────────────────────

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._loop(), name="subarr-update-checker")

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # ─── Public read API ───────────────────────────────────────────

    def states(self) -> list[UpdateState]:
        """All cached product states, sorted by product name."""
        conn = sqlite3.connect(str(self._db_path), isolation_level=None)
        try:
            rows = conn.execute(
                "SELECT product, repo, current_version, latest_version, "
                "       latest_released_at, release_notes_url, is_breaking, "
                "       checked_at, last_error "
                "FROM update_checks ORDER BY product"
            ).fetchall()
        finally:
            conn.close()
        return [
            UpdateState(
                product=r[0], repo=r[1],
                current_version=r[2], latest_version=r[3],
                latest_released_at=r[4], release_notes_url=r[5],
                is_breaking=bool(r[6]),
                checked_at=r[7], last_error=r[8],
            )
            for r in rows
        ]

    async def refresh_now(self) -> list[UpdateState]:
        """Force a poll across all products (admin endpoint)."""
        await self._poll_all()
        return self.states()

    # ─── Internal ───────────────────────────────────────────────────

    async def _loop(self) -> None:
        # Poll immediately on start so the UI has data on first paint,
        # then on the configured cadence.
        try:
            await self._poll_all()
            _h = getattr(self, "_health", None)  # #157: record the boot poll too
            if _h:
                _h.record_success("update-checker", expected_interval_s=self._interval_s)
        except Exception as e:
            _h = getattr(self, "_health", None)
            if _h:
                _h.record_failure("update-checker", e, expected_interval_s=self._interval_s)
            log.exception("initial update poll failed: %s", e)
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._interval_s)
                return  # stop was set
            except asyncio.TimeoutError:
                pass
            try:
                await self._poll_all()
                _h = getattr(self, "_health", None)  # #157 supervision hook
                if _h:
                    _h.record_success("update-checker", expected_interval_s=self._interval_s)
            except Exception as e:
                _h = getattr(self, "_health", None)
                if _h:
                    _h.record_failure("update-checker", e, expected_interval_s=self._interval_s)
                log.exception("update poll tick failed: %s", e)

    async def _poll_all(self) -> None:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url="https://api.github.com",
                timeout=httpx.Timeout(connect=3.0, read=10.0, write=5.0, pool=3.0),
                headers={"Accept": "application/vnd.github+json",
                         "User-Agent": "subarr-update-checker"},
            )
        for product, repo in self._products.items():
            try:
                await self._poll_one(product, repo)
            except Exception as e:
                log.warning("update poll failed for %s: %s", product, e)
                self._write_error(product, repo, str(e))

    async def _poll_one(self, product: str, repo: str) -> None:
        # GitHub: latest release endpoint. Returns 404 if no releases yet
        # (or for private repos without auth) — handled as "no update info".
        r = await self._client.get(f"/repos/{repo}/releases/latest")
        if r.status_code == 404:
            log.debug("no public release for %s (404 — private repo or no releases)", repo)
            self._write_error(product, repo, "no public release")
            return
        r.raise_for_status()
        body = r.json()
        latest_tag: str | None = body.get("tag_name")
        released_at: float | None = None
        if iso := body.get("published_at"):
            try:
                from datetime import datetime
                released_at = datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
            except Exception:
                pass
        notes_url: str | None = body.get("html_url")
        release_body: str = (body.get("body") or "").lower()
        is_breaking = (
            "breaking" in release_body
            or "BREAKING" in (body.get("body") or "")
            or "[breaking]" in release_body
        )

        self._write_state(
            product=product, repo=repo,
            current_version=self._current.get(product),
            latest_version=latest_tag,
            latest_released_at=released_at,
            release_notes_url=notes_url,
            is_breaking=is_breaking,
            last_error=None,
        )
        log.info("update poll: %s → latest=%s (current=%s)",
                 product, latest_tag, self._current.get(product))

    # ─── DB writes ─────────────────────────────────────────────────

    def _write_state(
        self, *, product: str, repo: str,
        current_version: str | None,
        latest_version: str | None,
        latest_released_at: float | None,
        release_notes_url: str | None,
        is_breaking: bool,
        last_error: str | None,
    ) -> None:
        conn = sqlite3.connect(str(self._db_path), isolation_level=None)
        try:
            conn.execute(
                "INSERT INTO update_checks "
                "(product, repo, current_version, latest_version, "
                " latest_released_at, release_notes_url, is_breaking, "
                " checked_at, last_error) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(product) DO UPDATE SET "
                "  repo=excluded.repo, "
                "  current_version=excluded.current_version, "
                "  latest_version=excluded.latest_version, "
                "  latest_released_at=excluded.latest_released_at, "
                "  release_notes_url=excluded.release_notes_url, "
                "  is_breaking=excluded.is_breaking, "
                "  checked_at=excluded.checked_at, "
                "  last_error=excluded.last_error",
                (
                    product, repo, current_version, latest_version,
                    latest_released_at, release_notes_url,
                    1 if is_breaking else 0,
                    time.time(), last_error,
                ),
            )
        finally:
            conn.close()

    def _write_error(self, product: str, repo: str, error: str) -> None:
        """Update last_error WITHOUT clearing prior successful poll data."""
        conn = sqlite3.connect(str(self._db_path), isolation_level=None)
        try:
            existing = conn.execute(
                "SELECT 1 FROM update_checks WHERE product = ?",
                (product,),
            ).fetchone()
            if existing:
                conn.execute(
                    "UPDATE update_checks "
                    "SET checked_at = ?, last_error = ? "
                    "WHERE product = ?",
                    (time.time(), error, product),
                )
            else:
                # First-poll failure — insert a stub row so /api/updates
                # has something to render.
                conn.execute(
                    "INSERT INTO update_checks "
                    "(product, repo, checked_at, last_error) "
                    "VALUES (?, ?, ?, ?)",
                    (product, repo, time.time(), error),
                )
        finally:
            conn.close()
