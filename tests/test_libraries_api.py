"""#134 slice 5: /api/settings/libraries CRUD + validation."""

from __future__ import annotations


def test_get_libraries_lists_default(app_with_stub):
    r = app_with_stub.get("/api/settings/libraries")
    assert r.status_code == 200
    libs = r.json()["libraries"]
    assert libs[0]["slug"] == ""
    assert libs[0]["is_default"] is True


def test_put_libraries_persists_and_applies(app_with_stub, tmp_path, monkeypatch):
    import subarr.config_store as cs

    monkeypatch.setenv("SUBARR_CONFIG_STORE", str(tmp_path / "ov.json"))
    body = {
        "libraries": [
            {
                "name": "Disk 2",
                "fs_root": str(tmp_path),
                "subgen_prefix": "/media2",
                "arr_prefix": "/data/d2/",
            }
        ]
    }
    r = app_with_stub.put("/api/settings/libraries", json=body)
    assert r.status_code == 200
    out = r.json()["libraries"]
    assert [lib["slug"] for lib in out] == ["", "disk-2"]
    # persisted
    assert cs.load_overrides()["libraries"][0]["slug"] == "disk-2"
    # applied live
    from subarr.config import settings

    assert [lib.slug for lib in settings.libraries] == ["", "disk-2"]


def test_put_libraries_rejects_duplicate_slug(app_with_stub, tmp_path, monkeypatch):
    monkeypatch.setenv("SUBARR_CONFIG_STORE", str(tmp_path / "ov.json"))
    body = {
        "libraries": [
            {"slug": "x", "name": "A", "fs_root": "/a", "arr_prefix": "/data/a/"},
            {"slug": "x", "name": "B", "fs_root": "/b", "arr_prefix": "/data/b/"},
        ]
    }
    r = app_with_stub.put("/api/settings/libraries", json=body)
    assert r.status_code == 422


def test_validate_library_path(app_with_stub, tmp_path):
    (tmp_path / "TV").mkdir()
    r = app_with_stub.post("/api/settings/libraries/validate", json={"fs_root": str(tmp_path)})
    assert r.status_code == 200
    assert r.json()["ok"] is True
    r2 = app_with_stub.post("/api/settings/libraries/validate", json={"fs_root": str(tmp_path / "nope")})
    assert r2.json()["ok"] is False
