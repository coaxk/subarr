"""The README fell eight releases behind (2.5.0 -> 2.7.2) before anyone
noticed, because nothing checked it. `scripts/check_readme_freshness.py` runs
in CI's lint job and fails the build when the README's version markers do not
match the version in pyproject.toml. A release commit bumps pyproject; if the
README was not touched, main goes red before the tag is pushed.

Three markers are checked, all of which carried "2.5" for two months:
  - the status badge                 status-v<major.minor>
  - the "New in" heading             ## New in <major.minor>
  - the known-limitations heading    ## Known limitations (v<major.minor>)
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_readme_freshness.py"


def _load():
    spec = importlib.util.spec_from_file_location("check_readme_freshness", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


README_OK = """
[![status](https://img.shields.io/badge/status-v2.7-violet)](x)
## New in 2.7
## Known limitations (v2.7)
"""


def test_all_markers_current_passes():
    mod = _load()
    assert mod.stale_markers(README_OK, "2.7.2") == []


def test_each_stale_marker_is_named():
    mod = _load()
    stale = mod.stale_markers(README_OK, "2.8.0")
    assert len(stale) == 3
    joined = " ".join(stale)
    assert "status badge" in joined
    assert "New in" in joined
    assert "Known limitations" in joined


def test_a_single_stale_marker_is_enough_to_fail():
    mod = _load()
    readme = README_OK.replace("## New in 2.7", "## New in 2.5")
    stale = mod.stale_markers(readme, "2.7.2")
    assert len(stale) == 1
    assert "New in" in stale[0]
    assert "2.5" in stale[0]


def test_patch_releases_do_not_require_a_readme_change():
    # 2.7.0 -> 2.7.2 is the same major.minor; the README says "2.7" for all of them.
    mod = _load()
    assert mod.stale_markers(README_OK, "2.7.0") == []
    assert mod.stale_markers(README_OK, "2.7.9") == []


def test_the_real_readme_is_current():
    # The check that actually guards the repo: run it on the files as committed.
    mod = _load()
    root = SCRIPT.parents[1]
    version = mod.read_pyproject_version(root / "pyproject.toml")
    stale = mod.stale_markers((root / "README.md").read_text(encoding="utf-8"), version)
    assert stale == [], "\n".join(stale)
