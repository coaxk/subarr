"""Tests for the update notification backend.

Covers:
  - First poll populates update_checks row
  - has_update logic (current vs latest)
  - 404 (private repo / no releases) records error without blowing up
  - Network error preserves prior good state
  - Breaking flag detected from release body
  - refresh_now() forces a poll
  - states() reads back what's been written
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest

from subarr.migrate import run_migrations
from subarr.update_checker import UpdateChecker, UpdateState


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
    """UpdateChecker with a mocked httpx transport."""
    c = UpdateChecker(
        db_path=db_path,
        products=products or {"subarr": "coaxk/subarr"},
        current_version_resolver=current_versions or {"subarr": "v0.1.0"},
    )
    c._client = httpx.AsyncClient(
        base_url="https://api.github.com",
        transport=httpx.MockTransport(handler),
    )
    return c


# ─── Tests ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_first_poll_populates_row(db_path: Path):
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/repos/coaxk/subarr/releases/latest"
        return httpx.Response(200, json={
            "tag_name": "v1.0.0",
            "published_at": "2026-05-28T10:00:00Z",
            "html_url": "https://github.com/coaxk/subarr/releases/tag/v1.0.0",
            "body": "Initial public release.",
        })

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
        return httpx.Response(200, json={
            "tag_name": "v1.0.0",
            "published_at": "2026-05-28T10:00:00Z",
            "html_url": "https://github.com/coaxk/subarr/releases/tag/v1.0.0",
            "body": "Patch release",
        })

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
        return httpx.Response(200, json={
            "tag_name": "v1.0.0",
            "published_at": "2026-05-28T10:00:00Z",
            "html_url": "https://github.com/x/y/releases/tag/v1.0.0",
            "body": "",
        })

    c = _checker_with_mock(db_path, handler, current_versions={"subarr": "1.0.0"})
    await c._poll_all()
    await c._client.aclose()

    [s] = c.states()
    assert s.has_update is False


@pytest.mark.asyncio
async def test_404_records_error_without_crashing(db_path: Path):
    """Private repos or repos without any releases return 404. Should
    write a row with last_error set, no latest_version, has_update False."""
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"message": "Not Found"})

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
            return httpx.Response(200, json={
                "tag_name": "v1.0.0",
                "published_at": "2026-05-28T10:00:00Z",
                "html_url": "https://github.com/x/y/releases/tag/v1.0.0",
                "body": "ok",
            })
        raise httpx.ConnectError("network is unreachable", request=req)

    c = _checker_with_mock(db_path, handler)
    await c._poll_all()  # succeeds
    await c._poll_all()  # fails
    await c._client.aclose()

    [s] = c.states()
    assert s.latest_version == "v1.0.0"  # preserved
    assert s.last_error is not None       # error recorded
    assert "unreachable" in s.last_error


@pytest.mark.asyncio
async def test_breaking_flag_detected_from_body(db_path: Path):
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "tag_name": "v2.0.0",
            "published_at": "2026-05-28T10:00:00Z",
            "html_url": "https://github.com/x/y/releases/tag/v2.0.0",
            "body": "## BREAKING CHANGE\n\nConfig file format changed.",
        })

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
        return httpx.Response(200, json={
            "tag_name": f"v0.0.{call_count}",
            "published_at": "2026-05-28T10:00:00Z",
            "html_url": "https://x/y",
            "body": "",
        })

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
        return httpx.Response(200, json={
            "tag_name": "v1.0.0",
            "published_at": "2026-05-28T10:00:00Z",
            "html_url": f"https://github.com{req.url.path}",
            "body": "",
        })

    c = _checker_with_mock(
        db_path, handler,
        products={"subarr": "coaxk/subarr", "subarr-subgen": "coaxk/subarr-subgen"},
        current_versions={"subarr": "v0.1.0", "subarr-subgen": "2026.05.3"},
    )
    await c._poll_all()
    await c._client.aclose()

    assert set(paths_hit) == {
        "/repos/coaxk/subarr/releases/latest",
        "/repos/coaxk/subarr-subgen/releases/latest",
    }
    states = c.states()
    assert {s.product for s in states} == {"subarr", "subarr-subgen"}


def test_update_state_to_dict_includes_has_update():
    s = UpdateState(
        product="subarr", repo="coaxk/subarr",
        current_version="v0.1.0", latest_version="v1.0.0",
        latest_released_at=1779000000.0,
        release_notes_url="https://example",
        is_breaking=False, checked_at=1779000001.0, last_error=None,
    )
    d = s.to_dict()
    assert d["has_update"] is True
    assert d["product"] == "subarr"
    assert d["latest_version"] == "v1.0.0"
