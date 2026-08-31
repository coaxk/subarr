"""#479: is the install still on the default SUBGEN_URL?

108 genuine installs report subgen `unreachable`, and 104 of them logged no
errors at all in 30 days. The leading explanation is that they never configured
subgen: SUBGEN_URL defaults to `http://subgen:9000`, a hostname that resolves
only if you happen to run a container called `subgen` on the same docker
network, so an install that never touched the setting probes a host that does
not exist and reports identically to one whose real subgen died.

⚠️ Worse, `subgen_url` goes through `_env_or`, so `SUBGEN_URL=` falls back to
the default too. There is currently no way to express "I do not use subgen".

One boolean settles which it is. It transmits no URL.
"""

from __future__ import annotations

import pytest

from subarr.config import DEFAULT_SUBGEN_URL, subgen_url_is_default


class TestTheDefaultIsOneDefinition:
    def test_the_constant_is_what_config_actually_falls_back_to(self):
        # If these ever drift, the telemetry silently reports the wrong thing
        # and nothing else breaks, which is the worst failure shape.
        import inspect

        from subarr import config

        src = inspect.getsource(config)
        assert 'subgen_url=_env_or("SUBGEN_URL", DEFAULT_SUBGEN_URL)' in src, (
            "config must use the shared constant, not a duplicated literal"
        )

    def test_the_value_is_the_documented_one(self):
        assert DEFAULT_SUBGEN_URL == "http://subgen:9000"


class TestRecognisingTheDefault:
    def test_the_bare_default(self):
        assert subgen_url_is_default("http://subgen:9000") is True

    @pytest.mark.parametrize(
        "url",
        [
            "http://subgen:9000/",
            "  http://subgen:9000  ",
            "HTTP://SUBGEN:9000",
            "http://Subgen:9000/",
        ],
    )
    def test_trivial_variations_still_count_as_default(self, url):
        # A user who pasted the default back with a trailing slash has still not
        # configured anything. Treating that as "configured" would understate
        # the never-configured population, which is the number this exists to
        # measure.
        assert subgen_url_is_default(url) is True

    def test_empty_counts_as_default_because_that_is_what_config_does(self):
        # ⚠️ Not an arbitrary choice. _env_or maps empty to the default, so an
        # install with SUBGEN_URL= genuinely IS running on the default. Saying
        # otherwise here would make the field disagree with the app's behaviour.
        assert subgen_url_is_default("") is True
        assert subgen_url_is_default("   ") is True
        assert subgen_url_is_default(None) is True


class TestRecognisingRealConfiguration:
    @pytest.mark.parametrize(
        "url",
        [
            "http://subgen-next:9000",
            "http://192.168.1.105:9000",
            "http://subgen:9008",
            "https://subgen:9000",
            "http://subgen.lan:9000",
            "http://subgen:9000/api",
        ],
    )
    def test_anything_the_user_actually_chose_is_not_default(self, url):
        assert subgen_url_is_default(url) is False

    def test_a_different_port_on_the_same_host_is_configured(self):
        # The common real case: subgen-next on 9008. Missing this would fold
        # deliberate users into the never-configured bucket.
        assert subgen_url_is_default("http://subgen:9008") is False


class TestNeverRaises:
    @pytest.mark.parametrize("url", ["://nonsense", "http://[oops", 12345, object()])
    def test_garbage_is_reported_as_not_default_rather_than_crashing(self, url):
        # This runs inside the telemetry payload build. An exception here would
        # take out the whole ping, and a malformed URL is certainly not the
        # default anyway.
        assert subgen_url_is_default(url) is False
