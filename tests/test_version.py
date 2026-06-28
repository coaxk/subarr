"""subarr.__version__ must stay in lockstep with pyproject (the release source of
truth). Guards the drift that shipped 2.3.0 reporting itself as "2.2.1" in the UI
+ telemetry — the release procedure bumps pyproject, and this test fails CI if the
__version__ constant wasn't bumped to match."""

from __future__ import annotations

import tomllib
from pathlib import Path


def test_version_matches_pyproject():
    import subarr

    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    assert subarr.__version__ == data["project"]["version"], (
        "src/subarr/__init__.py __version__ is out of sync with pyproject.toml — "
        "bump both when cutting a release"
    )
