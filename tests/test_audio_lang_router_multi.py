"""#357 — POST /api/audio-lang/verifications accepts lang_class + lang_codes."""

from __future__ import annotations


def _row(client, path):
    rows = client.get("/api/audio-lang/verifications").json()["verifications"]
    return next(r for r in rows if r["canonical_path"] == path)


def test_post_multi_verification_persists_class_and_codes(app_with_stub):
    body = {
        "canonical_path": "Movies/TheBeasts.mkv",
        "lang_code": "gl",
        "source": "user",
        "lang_class": "multi",
        "lang_codes": ["gl", "es", "fr"],
    }
    resp = app_with_stub.post("/api/audio-lang/verifications", json=body)
    assert resp.status_code == 200

    row = _row(app_with_stub, "Movies/TheBeasts.mkv")
    assert row["lang_class"] == "multi"
    assert row["lang_codes"] == ["gl", "es", "fr"]
    assert row["lang_code"] == "gl"


def test_post_single_verification_defaults_single(app_with_stub):
    resp = app_with_stub.post(
        "/api/audio-lang/verifications",
        json={"canonical_path": "TV/S/ep.mkv", "lang_code": "ja", "source": "user"},
    )
    assert resp.status_code == 200
    row = _row(app_with_stub, "TV/S/ep.mkv")
    assert row["lang_class"] == "single"
    assert row["lang_codes"] is None
