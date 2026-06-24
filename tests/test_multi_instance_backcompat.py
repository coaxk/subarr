"""#161 single-stack byte-identical invariant: with no `instances`/`libraries`
overrides, the multi-instance plumbing must behave exactly like instance 0.
This is the CI guard that single-stack installs (the majority) are unaffected.
"""

from __future__ import annotations


def test_single_stack_bundle_matches_direct_construction(subarr_env):
    from subarr.coverage_engine import IntegrationBundle
    from subarr.integrations.sonarr import SonarrClient

    bundle = IntegrationBundle()
    direct = SonarrClient()  # the pre-#161 construction path

    assert bundle.sonarr._base_url == direct._base_url
    assert dict(bundle.sonarr._client.headers).get("X-Api-Key") == dict(direct._client.headers).get(
        "X-Api-Key"
    )


def test_single_stack_clients_for_is_always_instance0(subarr_env):
    from subarr.coverage_engine import IntegrationBundle, clients_for

    bundle = IntegrationBundle()
    for canonical in ["", "Show/S01E01.mkv", "@unknownlib/x", "Movie (2020)/m.mkv"]:
        rc = clients_for(bundle, canonical)
        assert rc.sonarr is bundle.sonarr
        assert rc.radarr is bundle.radarr
        assert rc.bazarr is bundle.bazarr


def test_single_stack_has_exactly_one_instance_per_service(subarr_env):
    from subarr import config

    for svc in ("sonarr", "radarr", "bazarr"):
        assert len([i for i in config.settings.instances if i.service == svc]) == 1
