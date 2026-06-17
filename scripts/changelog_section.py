#!/usr/bin/env python3
"""Extract a CHANGELOG.md section and derive a release title for a version tag.

Used by release.yml (#229) to auto-create the GitHub Release on tag push. The
#203 update nudge + Settings → Updates "notes" link read releases.atom, so a
release MUST have a non-empty, descriptive title — a bare tag renders blank
digest entries fleet-wide. This helper guarantees a usable title even when the
changelog section is missing.

CLI:
  changelog_section.py <version> [--changelog CHANGELOG.md] --body-out FILE
    Writes the changelog body (or a fallback) to FILE; prints the title to
    stdout for the workflow to capture.
"""

from __future__ import annotations

import argparse
import re
import sys


def _normalize(version: str) -> str:
    """Strip a leading 'v' so 'v1.6.0' and '1.6.0' are equivalent."""
    return version[1:] if version.startswith("v") else version


def extract_section(changelog: str, version: str) -> str:
    """Return the body of the `## [<version>] ...` section, or "" if absent.

    Bounded below by the next top-level `## ` heading (the next version) or EOF,
    so one release's notes never bleed into another's.
    """
    ver = _normalize(version)
    lines = changelog.splitlines()
    # Heading looks like: ## [1.6.0] - 2026-06-14
    head_re = re.compile(r"^##\s+\[" + re.escape(ver) + r"\]")
    next_re = re.compile(r"^##\s+")

    start = None
    for i, line in enumerate(lines):
        if head_re.match(line):
            start = i + 1
            break
    if start is None:
        return ""

    end = len(lines)
    for j in range(start, len(lines)):
        if next_re.match(lines[j]):
            end = j
            break
    return "\n".join(lines[start:end]).strip()


def derive_title(section: str, version: str) -> str:
    """Build "v<version> — <first bold phrase>", or "v<version>" if none.

    The bold phrase is the first `**...**` run in the section, with a trailing
    issue ref `(#NNN)` and trailing punctuation stripped.
    """
    ver = _normalize(version)
    base = f"v{ver}"
    m = re.search(r"\*\*(.+?)\*\*", section)
    if not m:
        return base
    phrase = m.group(1)
    phrase = re.sub(r"\s*\(#\d+\)", "", phrase)  # drop "(#231)"
    phrase = phrase.strip(" .:;,—-")
    return f"{base} — {phrase}" if phrase else base


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("version", help="tag or version, e.g. v1.6.0 or 1.6.0")
    ap.add_argument("--changelog", default="CHANGELOG.md")
    ap.add_argument("--body-out", required=True, help="file to write the release body to")
    args = ap.parse_args(argv)

    try:
        with open(args.changelog, encoding="utf-8") as fh:
            changelog = fh.read()
    except OSError as e:
        print(f"could not read {args.changelog}: {e}", file=sys.stderr)
        changelog = ""

    section = extract_section(changelog, args.version)
    ver = _normalize(args.version)
    body = section or (
        f"Release v{ver}.\n\nSee [CHANGELOG.md]"
        "(https://github.com/coaxk/subarr/blob/main/CHANGELOG.md) for details."
    )
    with open(args.body_out, "w", encoding="utf-8") as fh:
        fh.write(body + "\n")

    print(derive_title(section, args.version))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
