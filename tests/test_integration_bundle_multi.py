"""IntegrationBundle multi-instance dicts + instance-0 alias (#161 Phase 1)."""

from __future__ import annotations


def test_bundle_alias_resolves_instance0(subarr_env):
    # single-stack: settings.instances has only the 3 instance-0 defaults
    from subarr.coverage_engine import IntegrationBundle

    bundle = IntegrationBundle()
    assert bundle.sonarr is bundle.client_for("sonarr", "")
    assert bundle.radarr is bundle.client_for("radarr", "")
    assert bundle.bazarr is bundle.client_for("bazarr", "")


def test_bundle_client_for_unknown_id_falls_back_to_instance0(subarr_env):
    from subarr.coverage_engine import IntegrationBundle

    bundle = IntegrationBundle()
    # an unbound/empty id and an unknown id both resolve to instance 0
    assert bundle.client_for("sonarr", "") is bundle.sonarr
    assert bundle.client_for("sonarr", "doesnotexist") is bundle.sonarr


def test_bundle_builds_extra_instance(anime_stack):
    # anime_stack reloaded coverage_engine after writing the override store
    from subarr.coverage_engine import IntegrationBundle

    bundle = IntegrationBundle()
    anime = bundle.client_for("sonarr", "anime")
    assert anime is not bundle.sonarr
    assert anime._base_url == "http://s2.test:8989"
