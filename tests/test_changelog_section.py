"""#229: release.yml auto-creates the GitHub Release from CHANGELOG.md.

The #203 update nudge reads releases.atom, so the derived title must never be
empty and must not bleed one version's notes into another's. These tests pin
the extraction + title-derivation logic the release job depends on.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

# Load scripts/changelog_section.py by path (it isn't an installed module).
_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "changelog_section.py"
_spec = importlib.util.spec_from_file_location("changelog_section", _SCRIPT)
cs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cs)


SAMPLE = """\
# Changelog

## [1.6.0] - 2026-06-14

Headline: subarr now configures Whisper for your hardware.

### Added
- **Guided subgen setup (#231).** A detect-guide-apply flow.
- **No-auth warning banner (#238).** Dismissible.

## [1.5.4] - 2026-06-13

### Fixed
- **Log injection (CWE-117, #239).** Scrubbed.

## [1.5.0] - 2026-06-11
Multi-arch images.
"""


class TestExtractSection:
    def test_extracts_only_the_target_version(self):
        sec = cs.extract_section(SAMPLE, "1.6.0")
        assert "Guided subgen setup" in sec
        assert "No-auth warning banner" in sec
        # must NOT bleed into the next version's notes
        assert "Log injection" not in sec
        assert "[1.5.4]" not in sec

    def test_middle_version_is_bounded_both_sides(self):
        sec = cs.extract_section(SAMPLE, "1.5.4")
        assert "Log injection" in sec
        assert "Guided subgen setup" not in sec  # not the one above
        assert "Multi-arch" not in sec  # not the one below

    def test_accepts_v_prefix(self):
        assert cs.extract_section(SAMPLE, "v1.6.0") == cs.extract_section(SAMPLE, "1.6.0")

    def test_unknown_version_returns_empty(self):
        assert cs.extract_section(SAMPLE, "9.9.9") == ""


class TestDeriveTitle:
    def test_uses_first_bold_phrase_and_strips_issue_ref(self):
        sec = cs.extract_section(SAMPLE, "1.6.0")
        assert cs.derive_title(sec, "1.6.0") == "v1.6.0 — Guided subgen setup"

    def test_strips_trailing_punctuation(self):
        assert cs.derive_title("- **Hotfix:** stuff", "1.5.5") == "v1.5.5 — Hotfix"

    def test_falls_back_to_bare_version_without_bold(self):
        assert cs.derive_title("just prose, no bold", "1.5.0") == "v1.5.0"

    def test_empty_section_falls_back_to_bare_version(self):
        assert cs.derive_title("", "1.5.0") == "v1.5.0"

    def test_title_is_never_empty(self):
        # the #203 contract — whatever we feed it, a usable title comes out
        for ver in ("1.0.0", "2.3.4", "v9.9.9"):
            assert cs.derive_title("", ver).strip()
