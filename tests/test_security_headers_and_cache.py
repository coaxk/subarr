"""Tests for issue #138: security headers, gzip, favicon, static caching.

Auth is OFF in the default `subarr_env`, so no Authorization header needed.
"""

from __future__ import annotations


# --- Security headers ---------------------------------------------


def test_csp_header_present_on_html_screen(app_with_stub):
    r = app_with_stub.get("/static/v1/home.html")
    assert r.status_code == 200
    csp = r.headers.get("content-security-policy")
    assert csp is not None
    assert "default-src 'self'" in csp
    assert "object-src 'none'" in csp
    assert "frame-ancestors 'none'" in csp
    assert "script-src 'self'" in csp
    # style-src must allow inline (the <style> blocks) + google fonts
    assert "'unsafe-inline'" in csp
    assert "https://fonts.googleapis.com" in csp


def test_hardening_headers_present(app_with_stub):
    r = app_with_stub.get("/api/health")
    assert r.headers.get("x-content-type-options") == "nosniff"
    assert r.headers.get("x-frame-options") == "DENY"
    assert r.headers.get("referrer-policy") == "strict-origin-when-cross-origin"
    assert "camera=()" in r.headers.get("permissions-policy", "")


# --- Static Cache-Control -----------------------------------------


def test_vendor_asset_long_cache(app_with_stub):
    r = app_with_stub.get("/static/v1/vendor/react.production.min.js")
    assert r.status_code == 200
    assert r.headers["cache-control"] == "max-age=604800, must-revalidate"


def test_flag_asset_long_cache(app_with_stub):
    r = app_with_stub.get("/static/v1/flags/fr.svg")
    assert r.status_code == 200
    assert "max-age=604800" in r.headers["cache-control"]


def test_favicon_png_long_cache(app_with_stub):
    r = app_with_stub.get("/static/v1/favicon-32.png")
    assert r.status_code == 200
    assert "max-age=604800" in r.headers["cache-control"]


def test_og_card_long_cache(app_with_stub):
    r = app_with_stub.get("/static/v1/og-card.png")
    assert r.status_code == 200
    assert "max-age=604800" in r.headers["cache-control"]


def test_favicon_svg_long_cache(app_with_stub):
    r = app_with_stub.get("/static/v1/favicon.svg")
    assert r.status_code == 200
    assert "max-age=604800" in r.headers["cache-control"]


def test_bundle_stays_no_cache(app_with_stub):
    # JS bundles are NOT content-hashed -> must revalidate every load.
    r = app_with_stub.get("/static/v1/home-hifi/home.bundle.js")
    assert r.status_code == 200
    assert r.headers["cache-control"] == "no-cache"


def test_html_stays_no_cache(app_with_stub):
    r = app_with_stub.get("/static/v1/home.html")
    assert r.status_code == 200
    assert r.headers["cache-control"] == "no-cache"


# --- Favicon route ------------------------------------------------


def test_favicon_ico_route(app_with_stub):
    r = app_with_stub.get("/favicon.ico")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("image/")
    assert len(r.content) > 0
