"""#238-A: /api/auth-status backs the dashboard no-auth banner.

Reports whether ANY auth is configured (API key OR a complete HTTP Basic
pair). Logic lives in a pure helper so it's testable without mutating the
frozen settings singleton.
"""

from __future__ import annotations

import pytest


@pytest.mark.parametrize(
    "api_key,user,pwd,expected",
    [
        ("", "", "", False),  # nothing -> open
        ("secret", "", "", True),  # api key
        ("", "u", "p", True),  # basic auth pair
        ("", "u", "", False),  # half-set basic auth is NOT valid
        ("", "", "p", False),  # half-set basic auth is NOT valid
        ("secret", "u", "p", True),  # both
    ],
)
def test_auth_is_configured(subarr_env, api_key, user, pwd, expected):
    from subarr.routers.admin import _auth_is_configured

    assert _auth_is_configured(api_key, user, pwd) is expected


def test_auth_status_endpoint_shape(subarr_env):
    import asyncio

    from subarr.routers import admin

    # asyncio.run() spins a fresh loop — get_event_loop() RuntimeErrors in the
    # full-suite context once an earlier async test has closed the current loop.
    out = asyncio.run(admin.auth_status())
    assert set(out) == {"configured"}
    assert isinstance(out["configured"], bool)


def test_auth_status_bypasses_api_key_middleware(subarr_env):
    # must be readable in the no-auth case the banner targets
    from subarr.api_security import _KEY_BYPASS_EXACT

    assert "/api/auth-status" in _KEY_BYPASS_EXACT
