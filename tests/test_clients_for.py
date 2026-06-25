"""clients_for maps a row's @slug library to its bound instance clients (#161)."""

from __future__ import annotations


def test_clients_for_empty_bindings_resolve_instance0(subarr_env):
    from subarr.coverage_engine import IntegrationBundle, clients_for

    bundle = IntegrationBundle()
    rc = clients_for(bundle, "Show/S01E01.mkv")  # library 0, all bindings ""
    assert rc.sonarr is bundle.sonarr
    assert rc.radarr is bundle.radarr
    assert rc.bazarr is bundle.bazarr


def test_clients_for_bound_library_routes_to_instance(anime_stack):
    # anime_stack: library 'anime' bound sonarr_id='anime'; bazarr_id unset
    from subarr.coverage_engine import IntegrationBundle, clients_for

    bundle = IntegrationBundle()
    rc = clients_for(bundle, "@anime/Naruto/S01E01.mkv")
    assert rc.sonarr is bundle.client_for("sonarr", "anime")
    assert rc.bazarr is bundle.bazarr  # bazarr_id unset -> instance 0
