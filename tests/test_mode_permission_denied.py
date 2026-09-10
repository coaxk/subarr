"""#524: a user on OMV mounted the subgen compose file into subarr, could read
it from a root `docker exec` shell, and got `Permission denied` from the app -
because subarr drops to PUID:PGID before starting and OMV writes the file
root-only. The panel said "HTTP 503" and nothing else. The 503 must say WHY,
name the uid that was refused, and say what to do."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest
from fastapi import HTTPException

from subarr.routers import mode as mod


def _point_at(monkeypatch, compose: Path) -> None:
    # settings is a frozen dataclass: swap the module's reference, do not mutate.
    monkeypatch.setattr(mod, "settings", dataclasses.replace(mod.settings, subgen_compose_path=compose))


def _deny_reads_of(monkeypatch, target: Path):
    real = Path.read_text

    def read_text(self, *a, **kw):
        if Path(self) == target:
            raise PermissionError(13, "Permission denied", str(target))
        return real(self, *a, **kw)

    monkeypatch.setattr(Path, "read_text", read_text)


def test_permission_denied_is_explained_not_just_503(tmp_path, monkeypatch):
    compose = tmp_path / "Subgen.yml"
    compose.write_text("services: {}\n", encoding="utf-8")
    _point_at(monkeypatch, compose)
    monkeypatch.setattr(mod, "_running_uid", lambda: 1000)
    _deny_reads_of(monkeypatch, compose)

    with pytest.raises(HTTPException) as ei:
        mod.get_mode()
    assert ei.value.status_code == 503
    detail = ei.value.detail
    assert "permission" in detail.lower()
    assert "1000" in detail, "the uid that was refused must be named"
    assert "PUID" in detail
    assert str(compose) in detail


def test_other_read_errors_keep_the_generic_message(tmp_path, monkeypatch):
    compose = tmp_path / "Subgen.yml"
    compose.write_text("services: {}\n", encoding="utf-8")
    _point_at(monkeypatch, compose)
    real = Path.read_text

    def read_text(self, *a, **kw):
        if Path(self) == compose:
            raise OSError(5, "Input/output error", str(compose))
        return real(self, *a, **kw)

    monkeypatch.setattr(Path, "read_text", read_text)
    with pytest.raises(HTTPException) as ei:
        mod.get_mode()
    assert ei.value.status_code == 503
    assert "could not read subgen compose" in ei.value.detail
    assert "PUID" not in ei.value.detail
