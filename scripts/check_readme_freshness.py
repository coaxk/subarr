#!/usr/bin/env python3
"""Fail when README.md's version markers lag the version in pyproject.toml.

The README stopped at 2.5.0 and stayed there through 2.7.2 - eight releases -
because nothing checked it. This runs in CI's lint job on every push. A
release commit bumps pyproject.toml; if the README's markers still name the
previous minor, main goes red before the tag is pushed.

Markers, all major.minor (a patch release does not require a README change):
  status badge         status-v<major.minor>
  "New in" heading     ## New in <major.minor>
  limitations heading  ## Known limitations (v<major.minor>)

Stdlib only, so the CI runner's python3 is enough. Exit 1 lists every stale
marker; exit 0 prints one line.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path


def read_pyproject_version(path: Path) -> str:
    m = re.search(r'^version\s*=\s*"([^"]+)"', path.read_text(encoding="utf-8"), re.M)
    if not m:
        raise SystemExit(f'no version = "..." line in {path}')
    return m.group(1)


def _major_minor(version: str) -> str:
    parts = version.lstrip("v").split(".")
    return ".".join(parts[:2])


def stale_markers(readme: str, version: str) -> list[str]:
    """Every marker that does not name `version`'s major.minor, with what it
    names instead, so the failure reads as an instruction."""
    want = _major_minor(version)
    checks = [
        ("status badge", r"status-v(\d+\.\d+)-"),
        ('"New in" heading', r"^## New in (\d+\.\d+)\s*$"),
        ('"Known limitations" heading', r"^## Known limitations \(v(\d+\.\d+)\)\s*$"),
    ]
    stale: list[str] = []
    for label, pattern in checks:
        m = re.search(pattern, readme, re.M)
        if not m:
            stale.append(f"{label}: marker not found (expected it to name {want})")
        elif m.group(1) != want:
            stale.append(f"{label}: says {m.group(1)}, pyproject.toml says {want}")
    return stale


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    version = read_pyproject_version(root / "pyproject.toml")
    stale = stale_markers((root / "README.md").read_text(encoding="utf-8"), version)
    if stale:
        print(f"README.md is behind pyproject.toml ({version}):", file=sys.stderr)
        for s in stale:
            print(f"  - {s}", file=sys.stderr)
        print(
            "Update README.md's 'New in', status badge and Known limitations for this minor.", file=sys.stderr
        )
        return 1
    print(f"README.md version markers match pyproject.toml ({_major_minor(version)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
