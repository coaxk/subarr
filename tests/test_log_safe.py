"""Log-injection guard (CWE-117): user-supplied strings reach log lines.

subarr ships with no auth by default, so a LAN caller hitting the subgen
webhook / verification endpoints is untrusted — a CR/LF in a path or
event field would let them forge or split log records. scrub() neutralises
control characters before the value is formatted into a log line.
"""

from __future__ import annotations


def test_scrub_strips_newlines_and_cr(subarr_env):
    from subarr.log_safe import scrub

    out = scrub("episode.mkv\nINFO root: forged admin login")
    assert "\n" not in out
    assert "\r" not in scrub("a\r\nb")
    # the forged content survives as inert text on the SAME line
    assert "forged admin login" in out


def test_scrub_passes_clean_values(subarr_env):
    from subarr.log_safe import scrub

    assert scrub("/media/TV/Show/S01E01.mkv") == "/media/TV/Show/S01E01.mkv"
    assert scrub("import-complete") == "import-complete"


def test_scrub_coerces_non_strings(subarr_env):
    from subarr.log_safe import scrub

    assert scrub(None) == "None"
    assert scrub(42) == "42"


def test_scrub_handles_other_control_chars(subarr_env):
    from subarr.log_safe import scrub

    assert "\x00" not in scrub("a\x00b")
    assert "\x1b" not in scrub("a\x1b[31mred")  # ANSI escape can't reach the log
