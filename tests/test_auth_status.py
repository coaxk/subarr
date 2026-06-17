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
    import types

    from subarr.routers import admin

    # #238: auth_status now reads request.app.state.auth_store. A fake request
    # with no store exercises the "no stored cred" path; we assert SHAPE (the
    # boolean value depends on env/store and is covered elsewhere).
    req = types.SimpleNamespace(app=types.SimpleNamespace(state=types.SimpleNamespace(auth_store=None)))
    out = asyncio.run(admin.auth_status(req))
    assert set(out) == {"configured"}
    assert isinstance(out["configured"], bool)
