"""#357 — zxx (no linguistic content) is selectable via /api/languages."""

from __future__ import annotations

from subarr.langs import WHISPER_LANGUAGES, normalize_lang


def test_zxx_in_whisper_languages():
    assert WHISPER_LANGUAGES.get("zxx") == "No linguistic content"


def test_zxx_normalizes_to_itself():
    assert normalize_lang("zxx") == "zxx"


def test_languages_endpoint_offers_zxx(app_with_stub):
    resp = app_with_stub.get("/api/languages")
    assert resp.status_code == 200
    codes = {row["code"] for row in resp.json()["languages"]}
    assert "zxx" in codes
