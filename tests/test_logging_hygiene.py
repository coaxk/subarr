"""Log hygiene: the HTTP client must not flood the info log.

r/sonarr feedback (furian11): the info log was a wall of "200 OK" lines because
httpx logs every request at INFO, and Subarr polls subgen/sonarr/radarr/bazarr/
tautulli/plex on a loop. Those routine successes belong below INFO so the info
log stays signal (errors + real events), not health-poll noise.
"""

from __future__ import annotations

import logging


def test_httpx_request_logging_pinned_below_info():
    import subarr.app  # noqa: F401 — importing runs the module-level logging setup

    assert logging.getLogger("httpx").level == logging.WARNING
    assert logging.getLogger("httpcore").level == logging.WARNING
