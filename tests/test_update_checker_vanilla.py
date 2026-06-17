"""#223: vanilla subgen (McCloudS/subgen) has zero GitHub releases/tags, so the
Atom-feed checker can't see it. Its version is a module constant
(`subgen_version = '2026.06.4'`) in subgen.py on main. These pin the parser
that reads that constant from the raw file.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from subarr.migrate import run_migrations
from subarr.update_checker import UpdateChecker, parse_vanilla_version

_RAW_URL = "https://raw.githubusercontent.com/McCloudS/subgen/main/subgen.py"


def _vanilla_checker(db_path: Path, handler, current: str | None = "2026.05.3") -> UpdateChecker:
    """A checker with the 'subgen' product routed to the vanilla raw-file path."""
    c = UpdateChecker(
        db_path=db_path,
        products={"subgen": "McCloudS/subgen"},
        current_version_resolver={"subgen": current},
        vanilla_products={"subgen": _RAW_URL},
    )
    c._client = httpx.AsyncClient(base_url="https://github.com", transport=httpx.MockTransport(handler))
    return c


class TestParseVanillaVersion:
    def test_single_quotes(self):
        assert parse_vanilla_version("subgen_version = '2026.06.4'\nimport os\n") == "2026.06.4"

    def test_double_quotes(self):
        assert parse_vanilla_version('subgen_version = "2026.06.4"\n') == "2026.06.4"

    def test_tolerates_whitespace_variants(self):
        assert parse_vanilla_version("subgen_version='2026.05.3'") == "2026.05.3"
        assert parse_vanilla_version("subgen_version   =   '2026.05.3'") == "2026.05.3"

    def test_picks_the_assignment_not_an_interpolation(self):
        # the real file also has f"Subgen {subgen_version}" usages — must match
        # the assignment line, not those.
        src = (
            "subgen_version = '2026.06.4'\ndef fn():\n    return f'Subgen {subgen_version}, stable-ts ...'\n"
        )
        assert parse_vanilla_version(src) == "2026.06.4"

    def test_absent_returns_none(self):
        assert parse_vanilla_version("import os\nx = 1\n") is None

    def test_empty_returns_none(self):
        assert parse_vanilla_version("") is None


@pytest.mark.asyncio
async def test_vanilla_poll_reads_version_constant(tmp_path):
    db = tmp_path / "s.db"
    run_migrations(db)

    def handler(req: httpx.Request) -> httpx.Response:
        assert "raw.githubusercontent.com" in str(req.url)  # raw file, not the atom feed
        return httpx.Response(200, text="subgen_version = '2026.06.4'\nimport os\n")

    c = _vanilla_checker(db, handler)
    await c._poll_all()
    await c._client.aclose()

    [s] = c.states()
    assert s.product == "subgen"
    assert s.current_version == "2026.05.3"
    assert s.latest_version == "2026.06.4"
    assert s.has_update is True
    assert s.release_notes_url == "https://github.com/McCloudS/subgen/commits/main"
    assert s.last_error is None


@pytest.mark.asyncio
async def test_vanilla_no_update_when_current(tmp_path):
    db = tmp_path / "s.db"
    run_migrations(db)

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="subgen_version = '2026.06.4'\n")

    c = _vanilla_checker(db, handler, current="2026.06.4")
    await c._poll_all()
    await c._client.aclose()

    [s] = c.states()
    assert s.has_update is False


@pytest.mark.asyncio
async def test_vanilla_missing_constant_is_fail_soft(tmp_path):
    db = tmp_path / "s.db"
    run_migrations(db)

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="# upstream refactored — constant gone\n")

    c = _vanilla_checker(db, handler)
    await c._poll_all()
    await c._client.aclose()

    [s] = c.states()
    assert s.last_error == "no release info"  # never a crash
