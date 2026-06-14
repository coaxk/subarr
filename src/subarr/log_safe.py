"""Neutralise control characters before user-supplied values hit a log line.

User strings (subgen webhook payloads, canonical paths from verification
request bodies) can contain CR/LF. Logged verbatim, a caller could forge or
split log records (CWE-117 log injection). subarr ships with no auth by
default, so any LAN client reaching these endpoints is untrusted. scrub()
replaces every C0 control char (incl. CR/LF/TAB) and DEL with U+FFFD so a
forged newline can't break out of its record — the suspicious content stays
visible, inline, on one line.
"""

from __future__ import annotations

import re

_CTRL = re.compile(r"[\x00-\x1f\x7f]")


def scrub(value) -> str:
    """Stringify `value` and replace control characters with U+FFFD. Use on
    any user-influenced value passed to a logger."""
    return _CTRL.sub("�", str(value))
