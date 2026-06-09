"""Tests for the update notification backend.

Covers:
  - parse_atom_feed: tag/date/notes/body extraction, no-entry feed, and
    XXE/billion-laughs hardening (defusedxml rejects DTD/entities) (#158)
  - First poll populates update_checks row (from the Atom feed)
  - has_update logic (current vs latest)
  - 404 (private repo / no releases) records error without blowing up
  - Empty feed (repo with no releases) records "no public release"
  - Network error preserves prior good state
  - Breaking flag detected from release body
  - refresh_now() forces a poll
  - states() reads back what's been written
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from subarr.migrate import run_migrations
from subarr.update_checker import UpdateChecker, UpdateState, parse_atom_feed


# ─── Atom feed helpers ──────────────────────────────────────────────


def _entry(
    tag: str,
    *,
    name: str | None = None,
    body: str = "",
    updated: str = "2026-05-28T10:00:00Z",
    repo: str = "coaxk/subarr",
) -> str:
    """One <entry> mirroring GitHub's real releases.atom shape: the <title>
    is the release NAME (not the tag), and the tag lives in the link href
    + the <id> suffix."""
    name = name or f"{repo.split('/')[-1]} {tag.lstrip('v')}"
    return f"""  <entry>
    <id>tag:github.com,2008:Repository/123/{tag}</id>
    <updated>{updated}</updated>
    <link rel="alternate" type="text/html" href="https://github.com/{repo}/releases/tag/{tag}"/>
    <title>{name}</title>
    <content type="html">{body}</content>
  </entry>"""


def _feed(*entries: str, repo: str = "coaxk/subarr") -> str:
    body = "\n".join(entries)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xml:lang="en-US">
  <id>tag:github.com,2008:https://github.com/{repo}/releases</id>
  <link type="text/html" rel="alternate" href="https://github.com/{repo}/releases"/>
  <title>Release notes from {repo.split("/")[-1]}</title>
  <updated>2026-06-06T19:21:09Z</updated>
{body}
</feed>"""


# ─── parse_atom_feed unit tests ─────────────────────────────────────


def test_parse_atom_extracts_newest_entry():
    xml = _feed(
        _entry("v1.2.1", name="subarr 1.2.1 — hotfix", body="Fixes things"),
        _entry("v1.2.0"),
    )
    out = parse_atom_feed(xml)
    assert out is not None
    assert out["tag"] == "v1.2.1"  # NOT the title "subarr 1.2.1 — hotfix"
    assert out["notes_url"] == "https://github.com/coaxk/subarr/releases/tag/v1.2.1"
    assert out["released_at"] is not None
    assert "Fixes things" in out["body"]


def test_parse_atom_tag_falls_back_to_id_without_tag_link():
    """If no /releases/tag/ link is present, fall back to the <id> suffix."""
    xml = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>tag:github.com,2008:Repository/123/v9.9.9</id>
    <updated>2026-05-28T10:00:00Z</updated>
    <title>Some release</title>
    <content type="html">notes</content>
  </entry>
</feed>"""
    out = parse_atom_feed(xml)
    assert out is not None
    assert out["tag"] == "v9.9.9"


def test_parse_atom_empty_feed_returns_none():
    """A repo with no releases yet → feed with zero <entry> → None."""
    assert parse_atom_feed(_feed()) is None


def test_parse_atom_malformed_returns_none():
    assert parse_atom_feed("not xml at all <<<") is None
    assert parse_atom_feed("") is None


def test_parse_atom_oversize_returns_none():
    huge = "<feed>" + ("x" * 5_000_000) + "</feed>"
    assert parse_atom_feed(huge) is None


def test_parse_atom_rejects_entity_expansion():
    """Security (#158): defusedxml must refuse DTD/entity payloads
    (billion-laughs / XXE) — we return None instead of expanding them."""
    malicious = """<?xml version="1.0"?>
<!DOCTYPE feed [
  <!ENTITY lol "lol">
  <!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">
]>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry><title>&lol2;</title></entry>
</feed>"""
    assert parse_atom_feed(malicious) is None


# ─── Fixtures ───────────────────────────────────────────────────────


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    p = tmp_path / "subarr.db"
    run_migrations(p)  # creates schema_versions + 001_baseline + 002_update_checks
    return p


def _checker_with_mock(
    db_path: Path,
    handler,
    products: dict[str, str] | None = None,
    current_versions: dict[str, str | None] | None = None,
) -> UpdateChecker:
    """UpdateChecker with a mocked httpx transport (serves Atom XML)."""
    c = UpdateChecker(
        db_path=db_path,
        products=products or {"subarr": "coaxk/subarr"},
        current_version_resolver=current_versions or {"subarr": "v0.1.0"},
    )
    c._client = httpx.AsyncClient(
        base_url="https://github.com",
        transport=httpx.MockTransport(handler),
    )
    return c


def _atom_response(
    req: httpx.Request, tag: str = "v1.0.0", *, body: str = "", repo: str = "coaxk/subarr"
) -> httpx.Response:
    return httpx.Response(
        200,
        text=_feed(_entry(tag, body=body, repo=repo), repo=repo),
        headers={"content-type": "application/atom+xml"},
    )


# ─── Tests ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_first_poll_populates_row(db_path: Path):
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/coaxk/subarr/releases.atom"
        return _atom_response(req, "v1.0.0", body="Initial public release.")

    c = _checker_with_mock(db_path, handler)
    await c._poll_all()
    await c._client.aclose()

    states = c.states()
    assert len(states) == 1
    s = states[0]
    assert s.product == "subarr"
    assert s.current_version == "v0.1.0"
    assert s.latest_version == "v1.0.0"
    assert s.has_update is True
    assert s.is_breaking is False
    assert s.last_error is None
    assert s.release_notes_url.endswith("/v1.0.0")
    assert s.latest_released_at is not None


@pytest.mark.asyncio
async def test_has_update_false_when_versions_match(db_path: Path):
    def handler(req: httpx.Request) -> httpx.Response:
        return _atom_response(req, "v1.0.0")

    c = _checker_with_mock(db_path, handler, current_versions={"subarr": "v1.0.0"})
    await c._poll_all()
    await c._client.aclose()

    [s] = c.states()
    assert s.has_update is False


@pytest.mark.asyncio
async def test_strips_v_prefix_for_comparison(db_path: Path):
    """Current version is '1.0.0', latest tag is 'v1.0.0' — should still
    treat as no update (the 'v' is just a tag convention)."""

    def handler(req: httpx.Request) -> httpx.Response:
        return _atom_response(req, "v1.0.0")

    c = _checker_with_mock(db_path, handler, current_versions={"subarr": "1.0.0"})
    await c._poll_all()
    await c._client.aclose()

    [s] = c.states()
    assert s.has_update is False


@pytest.mark.asyncio
async def test_404_records_error_without_crashing(db_path: Path):
    """Private/non-existent repos return 404. Should write a row with
    last_error set, no latest_version, has_update False."""

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="Not Found")

    c = _checker_with_mock(db_path, handler)
    await c._poll_all()
    await c._client.aclose()

    [s] = c.states()
    assert s.latest_version is None
    assert s.last_error == "no public release"
    assert s.has_update is False


@pytest.mark.asyncio
async def test_empty_feed_records_no_public_release(db_path: Path):
    """An existing repo with no releases returns an entry-less feed."""

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=_feed(), headers={"content-type": "application/atom+xml"})

    c = _checker_with_mock(db_path, handler)
    await c._poll_all()
    await c._client.aclose()

    [s] = c.states()
    assert s.latest_version is None
    assert s.last_error == "no public release"
    assert s.has_update is False


@pytest.mark.asyncio
async def test_network_error_preserves_prior_good_state(db_path: Path):
    """First poll succeeds, second poll fails — last_error gets set but
    latest_version/etc stay populated so the UI keeps showing the last
    known good state."""
    call_count = 0

    def handler(req: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return _atom_response(req, "v1.0.0", body="ok")
        raise httpx.ConnectError("network is unreachable", request=req)

    c = _checker_with_mock(db_path, handler)
    await c._poll_all()  # succeeds
    await c._poll_all()  # fails
    await c._client.aclose()

    [s] = c.states()
    assert s.latest_version == "v1.0.0"  # preserved
    assert s.last_error is not None  # error recorded
    assert "unreachable" in s.last_error


@pytest.mark.asyncio
async def test_breaking_flag_detected_from_body(db_path: Path):
    def handler(req: httpx.Request) -> httpx.Response:
        return _atom_response(
            req, "v2.0.0", body="&lt;h2&gt;BREAKING CHANGE&lt;/h2&gt; Config format changed."
        )

    c = _checker_with_mock(db_path, handler)
    await c._poll_all()
    await c._client.aclose()

    [s] = c.states()
    assert s.is_breaking is True


@pytest.mark.asyncio
async def test_refresh_now_forces_poll(db_path: Path):
    call_count = 0

    def handler(req: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return _atom_response(req, f"v0.0.{call_count}")

    c = _checker_with_mock(db_path, handler)
    await c.refresh_now()
    await c.refresh_now()
    await c._client.aclose()

    [s] = c.states()
    assert s.latest_version == "v0.0.2"
    assert call_count == 2


@pytest.mark.asyncio
async def test_multiple_products_polled(db_path: Path):
    paths_hit: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        paths_hit.append(req.url.path)
        # repo from the path: /<owner>/<repo>/releases.atom
        repo = "/".join(req.url.path.strip("/").split("/")[:2])
        return _atom_response(req, "v1.0.0", repo=repo)

    c = _checker_with_mock(
        db_path,
        handler,
        products={"subarr": "coaxk/subarr", "subarr-subgen": "coaxk/subarr-subgen"},
        current_versions={"subarr": "v0.1.0", "subarr-subgen": "2026.05.3"},
    )
    await c._poll_all()
    await c._client.aclose()

    assert set(paths_hit) == {
        "/coaxk/subarr/releases.atom",
        "/coaxk/subarr-subgen/releases.atom",
    }
    states = c.states()
    assert {s.product for s in states} == {"subarr", "subarr-subgen"}


def test_update_state_to_dict_includes_has_update():
    s = UpdateState(
        product="subarr",
        repo="coaxk/subarr",
        current_version="v0.1.0",
        latest_version="v1.0.0",
        latest_released_at=1779000000.0,
        release_notes_url="https://example",
        is_breaking=False,
        checked_at=1779000001.0,
        last_error=None,
    )
    d = s.to_dict()
    assert d["has_update"] is True
    assert d["product"] == "subarr"
    assert d["latest_version"] == "v1.0.0"
