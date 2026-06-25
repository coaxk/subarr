"""Unit tests for the multi-instance config model (#161 Phase 1)."""

from __future__ import annotations

import pytest


def _defaults():
    from subarr.instances import Instance

    return [
        Instance(id="", service="sonarr", name="default", url="http://sonarr:8989", api_key="k1"),
        Instance(id="", service="radarr", name="default", url="http://radarr:7878", api_key="k2"),
        Instance(id="", service="bazarr", name="default", url="http://bazarr:6767", api_key="k3"),
    ]


def test_build_instances_single_is_just_defaults():
    from subarr.instances import build_instances

    insts = build_instances(_defaults(), {})
    assert len(insts) == 3
    assert {i.service for i in insts} == {"sonarr", "radarr", "bazarr"}
    assert all(i.id == "" for i in insts)


def test_build_instances_forces_default_id_empty():
    from subarr.instances import Instance, build_instances

    bad_default = [Instance(id="nonempty", service="sonarr", name="d", url="u", api_key="k")]
    insts = build_instances(bad_default, {})
    assert insts[0].id == ""


def test_build_instances_adds_extra_with_slug_from_name():
    from subarr.instances import build_instances

    extras = {"sonarr": [{"name": "Anime", "url": "http://s2:8989", "api_key": "kk"}]}
    insts = build_instances(_defaults(), extras)
    sonarrs = [i for i in insts if i.service == "sonarr"]
    assert len(sonarrs) == 2
    extra = next(i for i in sonarrs if i.id != "")
    assert extra.id == "anime"
    assert extra.name == "Anime"
    assert extra.url == "http://s2:8989"


def test_build_instances_explicit_slug_preferred():
    from subarr.instances import build_instances

    extras = {"radarr": [{"slug": "fixed", "name": "Anime Films", "url": "u", "api_key": "k"}]}
    insts = build_instances(_defaults(), extras)
    assert any(i.id == "fixed" for i in insts if i.service == "radarr")


@pytest.mark.parametrize(
    "extras",
    [
        {"sonarr": [{"name": "", "url": "u", "api_key": "k"}]},
        {"sonarr": [{"name": "x", "url": "", "api_key": "k"}]},
        {"sonarr": [{"name": "x", "url": "u", "api_key": ""}]},
        {"sonarr": [{"name": "///", "url": "u", "api_key": "k"}]},
        {
            "sonarr": [
                {"slug": "", "name": "x", "url": "u", "api_key": "k"},
                {"slug": "", "name": "x", "url": "u2", "api_key": "k2"},
            ]
        },
    ],
)
def test_build_instances_rejects_bad(extras):
    from subarr.instances import InstanceConfigError, build_instances

    with pytest.raises(InstanceConfigError):
        build_instances(_defaults(), extras)


def test_build_instances_ignores_unknown_service_key():
    from subarr.instances import build_instances

    insts = build_instances(_defaults(), {"plex": [{"name": "x", "url": "u", "api_key": "k"}]})
    assert len(insts) == 3
