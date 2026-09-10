"""#524: a mounted Docker socket enabled the Logs page (docker SDK finds
/var/run/docker.sock by itself) but NOT onboarding auto-detect, which the app
only constructed when SUBARR_DOCKER_PROXY_URL or SUBARR_DOCKER_SOCKET_PATH was
set, even though the discovery client already had a fallback to the
conventional socket path that nothing ever reached. One feature worked and
the other said "discovery not configured" on the same mount.

The decision of where discovery should talk to is now one pure function."""

from __future__ import annotations

from types import SimpleNamespace

from subarr.docker_discovery import DEFAULT_SOCKET, resolve_discovery_endpoint


def _settings(proxy="", sock=""):
    return SimpleNamespace(docker_proxy_url=proxy, docker_socket_path=sock)


def test_proxy_url_wins():
    ep = resolve_discovery_endpoint(_settings(proxy="http://proxy:2375"), socket_exists=lambda p: True)
    assert ep == {"kind": "proxy", "endpoint": "http://proxy:2375", "source": "SUBARR_DOCKER_PROXY_URL"}


def test_explicit_socket_path_is_used_even_if_it_does_not_exist_yet():
    # An explicit setting is the operator's statement; report it, let the
    # probe fail loudly rather than silently choosing something else.
    ep = resolve_discovery_endpoint(_settings(sock="/run/docker.sock"), socket_exists=lambda p: False)
    assert ep == {"kind": "socket", "endpoint": "/run/docker.sock", "source": "SUBARR_DOCKER_SOCKET_PATH"}


def test_mounted_conventional_socket_enables_discovery_with_no_env_at_all():
    seen = []

    def exists(p):
        seen.append(p)
        return p == DEFAULT_SOCKET

    ep = resolve_discovery_endpoint(_settings(), socket_exists=exists)
    assert ep == {"kind": "socket", "endpoint": DEFAULT_SOCKET, "source": "mounted"}
    assert DEFAULT_SOCKET in seen


def test_nothing_configured_and_no_socket_means_no_discovery():
    assert resolve_discovery_endpoint(_settings(), socket_exists=lambda p: False) is None


def test_default_socket_is_the_conventional_path():
    assert DEFAULT_SOCKET == "/var/run/docker.sock"
