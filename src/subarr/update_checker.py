"""Update notification backend.

Polls GitHub releases for tracked products once per 24h, caches result in
SQLite. The UI consumes this via GET /api/updates and renders three
surfaces (header pill, Home tile, Settings panel).

Design choices:

- **Feed, not API (#158)**: we read the per-repo releases **Atom feed**
  (`https://github.com/{repo}/releases.atom`), NOT the REST endpoint
  `api.github.com/repos/{repo}/releases/latest`. The REST API caps
  *unauthenticated* requests at 60/hour **per IP**, and that budget is
  shared across ALL of an IP's GitHub traffic — so users behind NAT/CGNAT
  or running other GitHub-polling tools hit `403 rate limit exceeded` and
  their update checks silently stop. The Atom feed is served by github.com
  (the web host, not api.github.com) and is not subject to that limit, so
  the default unauthenticated path Just Works on busy/shared IPs.

- **Polling cadence**: once per 24h. Forced refresh available via
  POST /api/updates/refresh for admin.

- **State storage**: a single row per product in `update_checks`. Reads
  hit the DB, not GitHub — the UI is fast and offline-tolerant.

- **Privacy**: we send the user's IP to github.com when polling
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
import json
import logging
import re
import sqlite3
import time
import defusedxml.ElementTree as ET  # XXE / billion-laughs hardened (B314)
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from .data_persistence import apply_journal_mode

log = logging.getLogger(__name__)

_ATOM_NS = "{http://www.w3.org/2005/Atom}"
# Defensive cap on feed size. A real releases.atom (last ~10 releases with
# full HTML notes) is a few hundred KB at most; anything past this is
# pathological, so we refuse to parse it rather than feed ElementTree an
# unbounded payload. The source is github.com over TLS, but cheap insurance.
_MAX_FEED_BYTES = 4_000_000


def _parse_entry(entry) -> dict[str, Any]:
    """One atom <entry> → {tag, title, released_at, notes_url, body}.

    Tag resolution: prefer the entry's alternate ``<link>`` href
    (``.../releases/tag/<tag>``), fall back to the ``<id>`` suffix. The
    entry ``<title>`` is the release *name* (e.g. "v1.5.2 - movie coverage"),
    NOT the tag, so we never use it for the version — but it IS the human
    digest the #203 nudge renders.
    """
    notes_url: str | None = None
    tag: str | None = None
    # GitHub gives each entry a single alternate link to the release page.
    for link in entry.findall(f"{_ATOM_NS}link"):
        href = link.get("href")
        if href and "/releases/tag/" in href:
            notes_url = href
            tag = href.rsplit("/releases/tag/", 1)[1]
            break
        if href and notes_url is None:
            notes_url = href
    if tag is None:
        id_el = entry.find(f"{_ATOM_NS}id")
        if id_el is not None and id_el.text and "/" in id_el.text:
            tag = id_el.text.rsplit("/", 1)[1]

    released_at: float | None = None
    updated = entry.find(f"{_ATOM_NS}updated")
    if updated is not None and updated.text:
        try:
            released_at = datetime.fromisoformat(updated.text.strip().replace("Z", "+00:00")).timestamp()
        except ValueError:
            pass

    title_el = entry.find(f"{_ATOM_NS}title")
    title = (title_el.text or "").strip() if title_el is not None else ""

    content = entry.find(f"{_ATOM_NS}content")
    body = content.text if content is not None else None

    return {"tag": tag, "title": title, "released_at": released_at, "notes_url": notes_url, "body": body}


def parse_atom_entries(xml_text: str) -> list[dict[str, Any]]:
    """All releases in a `releases.atom` feed, newest first (GitHub's order),
    each ``{tag, title, released_at, notes_url, body}``. Empty list when the
    feed has no entries or can't be parsed — never raises."""
    if not xml_text or len(xml_text) > _MAX_FEED_BYTES:
        return []
    try:
        root = ET.fromstring(xml_text)
    except Exception:
        # Malformed XML, or defusedxml rejecting a DTD/entity/external-ref
        # (EntitiesForbidden, DTDForbidden, ...). A feed we can't safely
        # parse is treated as "no info" — never crash the poll loop.
        return []
    return [_parse_entry(e) for e in root.findall(f"{_ATOM_NS}entry")]


def parse_atom_feed(xml_text: str) -> dict[str, Any] | None:
    """The newest release as ``{tag, released_at, notes_url, body}`` (the
    original single-entry contract), or ``None`` when the feed is empty."""
    entries = parse_atom_entries(xml_text)
    return entries[0] if entries else None


def parse_vanilla_version(py_source: str) -> str | None:
    """#223: vanilla McCloudS/subgen ships NO releases/tags, so the Atom feed
    can't see it. Its version is the ``subgen_version = '<v>'`` constant at the
    top of subgen.py — pull it from the raw file. Returns None if absent (the
    constant moved/renamed) so the caller fails soft to 'no release info'."""

    m = re.search(r"^subgen_version\s*=\s*['\"]([^'\"]+)['\"]", py_source, re.MULTILINE)
    return m.group(1) if m else None


def _version_key(tag: str | None) -> tuple[int, ...] | None:
    """The numeric components of a version tag as an int tuple for ordering,
    or None when the tag has no digits (an opaque tag we can't order).
    'v2.0.0' → (2, 0, 0); 'v2026.05.3-r9' → (2026, 5, 3, 9)."""
    if not tag:
        return None
    nums = re.findall(r"\d+", tag)
    return tuple(int(n) for n in nums) if nums else None


def compare_versions(a: str | None, b: str | None) -> int | None:
    """Order two version tags: -1 if a<b, 0 if equal, 1 if a>b, or None when
    either has no numeric content (incomparable opaque tags). Shorter tuples
    are zero-padded so '2.0' == '2.0.0'. The 'v' prefix is irrelevant to the
    numeric key, so 'v2.0.0' == '2.0.0'."""
    ka, kb = _version_key(a), _version_key(b)
    if ka is None or kb is None:
        return None
    n = max(len(ka), len(kb))
    ka = ka + (0,) * (n - len(ka))
    kb = kb + (0,) * (n - len(kb))
    return (ka > kb) - (ka < kb)


def missed_releases(entries: list[dict[str, Any]], current_version: str | None) -> list[dict[str, Any]]:
    """The releases an install is missing: every feed entry strictly NEWER
    than its current version (entries are newest-first; we stop at the first
    entry that is the current version or older). Current older than the feed
    window → the whole feed is missed. Current NEWER than the whole feed
    (locally-built, or the window just after we tag a release before the
    cache refreshes) → [] — never "N releases behind". Unknown current → []
    (never guess). Bodies stripped — only {tag, title, notes_url,
    released_at} travel to the UI."""
    if not current_version:
        return []
    cur = current_version.lstrip("v")
    out: list[dict[str, Any]] = []
    for e in entries:
        tag = e.get("tag") or ""
        cmp = compare_versions(tag, current_version)
        if cmp is not None:
            # Orderable tags: stop once an entry is the current version or
            # older. Yields [] when current is ahead of the whole feed.
            if cmp <= 0:
                break
        elif tag.lstrip("v") == cur:
            # Opaque/un-orderable tag: fall back to exact-match stop.
            break
        out.append(
            {
                "tag": e.get("tag"),
                "title": e.get("title") or e.get("tag") or "",
                "notes_url": e.get("notes_url"),
                "released_at": e.get("released_at"),
            }
        )
    return out


def _load_missed_json(raw: str | None) -> list[dict[str, Any]]:
    """missed_json column → list, fail-soft (bad JSON reads as empty)."""
    if not raw:
        return []
    try:
        out = json.loads(raw)
        return out if isinstance(out, list) else []
    except Exception:
        return []


# Default products subarr tracks. Each row maps to one update_checks row.
DEFAULT_PRODUCTS = {
    "subarr": "coaxk/subarr",
    "subarr-subgen": "coaxk/subarr-subgen",
}

# #223: vanilla McCloudS/subgen has no releases/tags. When the connected subgen
# is vanilla (not subarr-subgen), the app swaps the "subarr-subgen" product for
# this one and routes it through the version-constant path (vanilla_products).
VANILLA_SUBGEN_PRODUCT = "subgen"
VANILLA_SUBGEN_REPO = "McCloudS/subgen"
VANILLA_SUBGEN_RAW_URL = "https://raw.githubusercontent.com/McCloudS/subgen/main/subgen.py"

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
    # #203: releases between current and latest — [{tag, title, notes_url,
    # released_at}], newest first. Titles only (the atom <title> is our own
    # release name), so there's no HTML body to sanitize anywhere.
    missed_releases: list[dict[str, Any]] = field(default_factory=list)

    @property
    def has_update(self) -> bool:
        """True only when the running version is strictly OLDER than latest.

        Direction matters: a locally-built install (or the window just after
        we tag a release, before the 24h cache refreshes) can be NEWER than
        the latest release the feed knows about — that's 'up to date', not an
        update. We order by numeric version key; for opaque un-orderable tags
        (no digits) we fall back to 'any difference is news'."""
        if not self.current_version or not self.latest_version:
            return False
        cmp = compare_versions(self.current_version, self.latest_version)
        if cmp is None:
            return self.current_version.lstrip("v") != self.latest_version.lstrip("v")
        return cmp < 0

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
            "missed_releases": self.missed_releases,
        }


class UpdateChecker:
    """Polls GitHub + caches the result for the UI to consume."""

    def __init__(
        self,
        db_path: Path,
        products: dict[str, str] | None = None,
        poll_interval_s: int = DEFAULT_POLL_INTERVAL_S,
        current_version_resolver: dict[str, str | None] | None = None,
        vanilla_products: dict[str, str] | None = None,
    ):
        self._db_path = db_path
        self._products = products or DEFAULT_PRODUCTS
        self._interval_s = poll_interval_s
        # Static map of product → current version. Caller supplies; we
        # don't introspect ourselves to keep this module decoupled from
        # SubgenClient + subarr.__version__.
        self._current = current_version_resolver or {}
        # #223: product → raw subgen.py URL for products that have NO GitHub
        # releases (vanilla McCloudS/subgen). These use the version-constant
        # path instead of the Atom feed.
        self._vanilla_products = vanilla_products or {}
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
        apply_journal_mode(conn, self._db_path)  # #291: boot-order-independent WAL
        try:
            rows = conn.execute(
                "SELECT product, repo, current_version, latest_version, "
                "       latest_released_at, release_notes_url, is_breaking, "
                "       checked_at, last_error, missed_json "
                "FROM update_checks ORDER BY product"
            ).fetchall()
        finally:
            conn.close()
        return [
            UpdateState(
                product=r[0],
                repo=r[1],
                current_version=r[2],
                latest_version=r[3],
                latest_released_at=r[4],
                release_notes_url=r[5],
                is_breaking=bool(r[6]),
                checked_at=r[7],
                last_error=r[8],
                missed_releases=_load_missed_json(r[9]),
            )
            for r in rows
        ]

    def _prune_untracked(self) -> int:
        """Drop persisted state for products we no longer track.

        states() returns every row in update_checks, so a product tracked
        under a previous config (e.g. the vanilla 'subgen' row left behind
        after the box switched to the subarr-subgen fork, or vice versa)
        would render on the Updates page forever. Pruning to the live product
        set keeps the page honest. Returns the count of orphan rows removed."""
        keep = set(self._products.keys())
        conn = sqlite3.connect(str(self._db_path), isolation_level=None)
        apply_journal_mode(conn, self._db_path)
        try:
            # Resolve orphans in Python and delete each by exact product = ? —
            # fully parameterized, no dynamic IN-clause SQL. The tracked set is
            # tiny (2-3 products), so the per-row deletes are negligible.
            rows = conn.execute("SELECT DISTINCT product FROM update_checks").fetchall()
            orphans = [r[0] for r in rows if r[0] not in keep]
            for product in orphans:
                conn.execute("DELETE FROM update_checks WHERE product = ?", (product,))
            return len(orphans)
        finally:
            conn.close()

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
        self._prune_untracked()  # drop orphan rows for products no longer tracked
        if self._client is None:
            self._client = httpx.AsyncClient(
                # github.com (the web host), NOT api.github.com — the Atom
                # feed dodges the 60/hr unauthenticated REST limit (#158).
                base_url="https://github.com",
                timeout=httpx.Timeout(connect=3.0, read=10.0, write=5.0, pool=3.0),
                headers={"Accept": "application/atom+xml", "User-Agent": "subarr-update-checker"},
                follow_redirects=True,  # renamed repos 301 to the new slug
            )
        for product, repo in self._products.items():
            try:
                raw_url = self._vanilla_products.get(product)
                if raw_url:
                    await self._poll_vanilla(product, repo, raw_url)
                else:
                    await self._poll_one(product, repo)
            except Exception as e:
                log.warning("update poll failed for %s: %s", product, e)
                self._write_error(product, repo, str(e))

    async def _poll_one(self, product: str, repo: str) -> None:
        # GitHub releases Atom feed. Returns 404 for a non-existent/private
        # repo; an existing repo with no releases returns a feed with zero
        # <entry> elements (parse_atom_feed → None). Both → "no public
        # release", same graceful handling as before.
        r = await self._client.get(f"/{repo}/releases.atom")
        if r.status_code == 404:
            log.debug("no atom feed for %s (404 — private repo or no releases)", repo)
            self._write_error(product, repo, "no public release")
            return
        r.raise_for_status()

        entries = parse_atom_entries(r.text)
        parsed = entries[0] if entries else None
        if parsed is None or not parsed.get("tag"):
            log.debug("atom feed for %s has no releases yet", repo)
            self._write_error(product, repo, "no public release")
            return

        latest_tag: str | None = parsed["tag"]
        released_at: float | None = parsed["released_at"]
        notes_url: str | None = parsed["notes_url"]
        raw_body: str = parsed.get("body") or ""
        release_body = raw_body.lower()
        is_breaking = "breaking" in release_body or "[breaking]" in release_body
        # #203: everything between the install's version and latest — the
        # release titles are the "what you're missing" digest.
        missed = missed_releases(entries, self._current.get(product))

        self._write_state(
            product=product,
            repo=repo,
            current_version=self._current.get(product),
            latest_version=latest_tag,
            latest_released_at=released_at,
            release_notes_url=notes_url,
            is_breaking=is_breaking,
            last_error=None,
            missed=missed,
        )
        log.info("update poll: %s → latest=%s (current=%s)", product, latest_tag, self._current.get(product))

    async def _poll_vanilla(self, product: str, repo: str, raw_url: str) -> None:
        """#223: version check for a product with NO GitHub releases (vanilla
        McCloudS/subgen). Reads the `subgen_version` constant from the raw
        subgen.py on main and compares to the installed version. Notes link to
        the commit history (there are no release notes). Fail-soft: a moved
        constant or fetch error → 'no release info', never a crash."""
        r = await self._client.get(raw_url)
        r.raise_for_status()
        latest = parse_vanilla_version(r.text)
        if not latest:
            log.debug("vanilla version constant not found for %s (%s)", product, raw_url)
            self._write_error(product, repo, "no release info")
            return
        self._write_state(
            product=product,
            repo=repo,
            current_version=self._current.get(product),
            latest_version=latest,
            latest_released_at=None,  # a raw file has no release timestamp
            release_notes_url=f"https://github.com/{repo}/commits/main",
            is_breaking=False,  # no release body to scan
            last_error=None,
            missed=[],  # no per-release digest for an untagged upstream
        )
        log.info(
            "update poll (vanilla): %s → latest=%s (current=%s)",
            product,
            latest,
            self._current.get(product),
        )

    # ─── DB writes ─────────────────────────────────────────────────

    def _write_state(
        self,
        *,
        product: str,
        repo: str,
        current_version: str | None,
        latest_version: str | None,
        latest_released_at: float | None,
        release_notes_url: str | None,
        is_breaking: bool,
        last_error: str | None,
        missed: list[dict[str, Any]] | None = None,
    ) -> None:
        conn = sqlite3.connect(str(self._db_path), isolation_level=None)
        apply_journal_mode(conn, self._db_path)  # #291: boot-order-independent WAL
        try:
            conn.execute(
                "INSERT INTO update_checks "
                "(product, repo, current_version, latest_version, "
                " latest_released_at, release_notes_url, is_breaking, "
                " checked_at, last_error, missed_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(product) DO UPDATE SET "
                "  repo=excluded.repo, "
                "  current_version=excluded.current_version, "
                "  latest_version=excluded.latest_version, "
                "  latest_released_at=excluded.latest_released_at, "
                "  release_notes_url=excluded.release_notes_url, "
                "  is_breaking=excluded.is_breaking, "
                "  checked_at=excluded.checked_at, "
                "  last_error=excluded.last_error, "
                "  missed_json=excluded.missed_json",
                (
                    product,
                    repo,
                    current_version,
                    latest_version,
                    latest_released_at,
                    release_notes_url,
                    1 if is_breaking else 0,
                    time.time(),
                    last_error,
                    json.dumps(missed or []),
                ),
            )
        finally:
            conn.close()

    def _write_error(self, product: str, repo: str, error: str) -> None:
        """Update last_error WITHOUT clearing prior successful poll data."""
        conn = sqlite3.connect(str(self._db_path), isolation_level=None)
        apply_journal_mode(conn, self._db_path)  # #291: boot-order-independent WAL
        try:
            existing = conn.execute(
                "SELECT 1 FROM update_checks WHERE product = ?",
                (product,),
            ).fetchone()
            if existing:
                conn.execute(
                    "UPDATE update_checks SET checked_at = ?, last_error = ? WHERE product = ?",
                    (time.time(), error, product),
                )
            else:
                # First-poll failure — insert a stub row so /api/updates
                # has something to render.
                conn.execute(
                    "INSERT INTO update_checks (product, repo, checked_at, last_error) VALUES (?, ?, ?, ?)",
                    (product, repo, time.time(), error),
                )
        finally:
            conn.close()
