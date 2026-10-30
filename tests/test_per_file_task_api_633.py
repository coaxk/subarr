"""#633: /api/per-file-task/config."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

PATH = "/api/per-file-task/config"


@pytest.fixture
def api(app_with_stub, tmp_path, monkeypatch):
    from subarr import config

    monkeypatch.setenv("SUBARR_CONFIG_STORE", str(tmp_path / "ov.json"))
    prior = (config.settings.per_file_task_enabled, config.settings.per_file_task_wanted)
    yield app_with_stub
    object.__setattr__(config.settings, "per_file_task_enabled", prior[0])
    object.__setattr__(config.settings, "per_file_task_wanted", prior[1])


def _caps(**kw):
    base = dict(
        per_request_task=True,
        preferred_audio_languages=(None, "en"),
        preferred_audio_languages_unreadable=(),
        force_detected_language_unreadable=None,
        force_detected_language_to="ja",
    )
    base.update(kw)
    return SimpleNamespace(**base)


def test_get_shape_available(api):
    api.app.state.subgen_caps = _caps()
    body = api.get(PATH).json()
    assert set(body) == {
        "enabled",
        "wanted",
        "available",
        "unavailable_reason",
        "preferred_audio_languages",
        "force_detected_language_to",
    }
    assert body["enabled"] is False and body["wanted"] == []
    assert body["available"] is True and body["unavailable_reason"] is None
    assert body["preferred_audio_languages"] == [None, "en"]
    assert body["force_detected_language_to"] == "ja"


def test_get_reports_why_unavailable_per_request_task(api):
    api.app.state.subgen_caps = _caps(per_request_task=False)
    body = api.get(PATH).json()
    assert body["available"] is False
    assert body["unavailable_reason"] == "subgen does not accept a per-request task"


def test_get_reports_why_unavailable_no_list(api):
    api.app.state.subgen_caps = _caps(preferred_audio_languages=None)
    body = api.get(PATH).json()
    assert body["available"] is False
    assert "does not advertise its preferred audio languages" in body["unavailable_reason"]
    assert "v4.38" in body["unavailable_reason"]
    assert body["preferred_audio_languages"] is None


def test_get_reports_unreadable_entry(api):
    api.app.state.subgen_caps = _caps(preferred_audio_languages_unreadable=("klingon",))
    body = api.get(PATH).json()
    assert body["available"] is False
    assert (
        "subgen advertised a preferred audio language subarr cannot read: klingon"
        in body["unavailable_reason"]
    )


def test_get_reports_unreadable_force_detected(api):
    api.app.state.subgen_caps = _caps(force_detected_language_unreadable="klingon")
    body = api.get(PATH).json()
    assert body["available"] is False
    assert (
        "subgen advertised a forced detected language subarr cannot read: klingon"
        in body["unavailable_reason"]
    )


def test_get_with_caps_not_probed_is_unavailable(api):
    api.app.state.subgen_caps = None
    body = api.get(PATH).json()
    assert body["available"] is False and body["unavailable_reason"]


def test_post_persists_to_disk_and_live_applies(api):
    from subarr import config, config_store

    r = api.post(PATH, json={"enabled": True, "wanted": [" EN ", "es", "en", "Spanish"]})
    assert r.status_code == 200
    assert r.json()["enabled"] is True and r.json()["wanted"] == ["en", "es"]
    assert config.settings.per_file_task_enabled is True
    assert config.settings.per_file_task_wanted == ["en", "es"]
    on_disk = config_store.load_overrides()
    assert on_disk["per_file_task_enabled"] is True
    assert on_disk["per_file_task_wanted"] == ["en", "es"]


def test_post_disabled_with_empty_list_is_allowed(api):
    r = api.post(PATH, json={"enabled": False, "wanted": []})
    assert r.status_code == 200 and r.json()["enabled"] is False


def test_post_400_for_an_unknown_code(api):
    r = api.post(PATH, json={"enabled": True, "wanted": ["en", "xx-not-a-language"]})
    assert r.status_code == 400
    assert "xx-not-a-language" in r.text


def test_post_400_for_enabled_with_empty_list(api):
    assert api.post(PATH, json={"enabled": True, "wanted": []}).status_code == 400
    assert api.post(PATH, json={"enabled": True, "wanted": ["", "  "]}).status_code == 400


def test_post_400_does_not_persist(api):
    from subarr import config_store

    api.post(PATH, json={"enabled": True, "wanted": ["bogus!"]})
    assert "per_file_task_wanted" not in config_store.load_overrides()


def test_post_while_unavailable_is_kept_and_reported(api):
    api.app.state.subgen_caps = _caps(preferred_audio_languages=None)
    r = api.post(PATH, json={"enabled": True, "wanted": ["en"]})
    assert r.status_code == 200
    body = api.get(PATH).json()
    assert body["enabled"] is True and body["available"] is False


def test_post_wrong_body_type_is_422(api):
    assert api.post(PATH, json={"enabled": "yes please", "wanted": "en"}).status_code == 422


def test_route_is_registered_and_not_auth_bypassed(api):
    from subarr.auth import is_path_bypassed

    # Through a real request, not `app.routes`: FastAPI >= 0.16x lists an
    # included router as one lazy `_IncludedRouter` entry with no `path`.
    # JSON is required so the SPA catch-all (HTML 200) cannot pass this.
    api.app.state.subgen_caps = _caps()
    r = api.get(PATH)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/json")
    assert "enabled" in r.json()
    assert is_path_bypassed(PATH) is False


@pytest.mark.asyncio
async def test_unauthenticated_request_is_refused_by_the_gate():
    from subarr import auth

    class _Store:
        def has_credential(self):
            return True

    seen = {}

    async def receive():
        return {"type": "http.request"}

    async def send(msg):
        if msg["type"] == "http.response.start":
            seen["status"] = msg["status"]

    async def inner(s, r, sd):
        seen["inner"] = True

    settings = SimpleNamespace(
        auth_user="", auth_pass="", api_key="", auth_disabled=False, cookie_samesite="lax"
    )
    scope = {
        "type": "http",
        "method": "POST",
        "path": PATH,
        "headers": [],
        "query_string": b"",
        "session": {},
    }
    await auth.AuthGateMiddleware(inner, settings=settings, get_store=lambda: _Store())(scope, receive, send)
    assert seen.get("status") == 401 and "inner" not in seen


def test_post_env_pinned_is_saved_but_env_stays_in_effect(api, monkeypatch):
    from subarr import config, config_store

    monkeypatch.setenv("SUBARR_PER_FILE_TASK_ENABLED", "0")
    monkeypatch.setenv("SUBARR_PER_FILE_TASK_WANTED", "fr")
    r = api.post(PATH, json={"enabled": True, "wanted": ["en"]})
    assert r.status_code == 200
    assert config.settings.per_file_task_enabled is False
    assert config.settings.per_file_task_wanted == []
    assert config_store.load_overrides()["per_file_task_enabled"] is True
