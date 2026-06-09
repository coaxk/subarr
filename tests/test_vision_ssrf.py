"""SSRF guard for POST /api/vision/check.

vision_describe fetches image_url server-side, so an unrestricted
image_url lets a caller make subarr GET arbitrary internal URLs. Thumbnails
only ever come from the configured Plex / Tautulli hosts, so we allowlist
those.
"""

from __future__ import annotations


def _imp():
    from subarr.routers.vision import _image_url_allowed

    return _image_url_allowed


def test_allows_configured_host():
    allowed = {"plex.local:32400", "tautulli.local:8181"}
    assert _imp()("http://plex.local:32400/photo/:/transcode?url=x", allowed) is True


def test_blocks_cloud_metadata_endpoint():
    assert _imp()("http://169.254.169.254/latest/meta-data/", {"plex.local:32400"}) is False


def test_blocks_arbitrary_host():
    assert _imp()("http://evil.example.com/x", {"plex.local:32400"}) is False


def test_blocks_non_http_scheme():
    assert _imp()("file:///etc/passwd", {"plex.local:32400"}) is False


def test_empty_allowlist_denies():
    # Nothing configured to validate against -> deny (caller must use image_b64).
    assert _imp()("http://plex.local:32400/x", set()) is False
