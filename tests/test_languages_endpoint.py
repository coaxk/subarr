"""#358: the picker's data source — the full Whisper set, 2-letter native."""

from __future__ import annotations


def test_languages_endpoint_serves_full_set_sorted(app_with_stub):
    r = app_with_stub.get("/api/languages")
    assert r.status_code == 200, r.text
    data = r.json()["languages"]
    codes = {row["code"] for row in data}
    assert "gl" in codes and "ko" in codes and "en" in codes
    assert 95 <= len(data) <= 110
    # sorted by display name
    names = [row["name"] for row in data]
    assert names == sorted(names)
    gl = next(row for row in data if row["code"] == "gl")
    assert gl["name"] == "Galician"
