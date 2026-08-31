"""#479: why a subgen probe failed, not merely that it did.

`SubgenCapabilities.unreachable()` was returned from two structurally different
conditions and the difference was discarded at the point it was known: a
transport error (DNS, refused, timeout, TLS) and a non-200 response, which means
something IS listening and answering but is not a healthy subgen. Telemetry then
showed one `unreachable` bucket covering 108 installs with no way to tell which.

The vocabulary is deliberately small and closed. It carries no hostname, URL,
port or exception message, because this value is transmitted.
"""

from __future__ import annotations

import socket

import httpx
import pytest

from subarr.subgen_client import SubgenCapabilities, classify_probe_failure


def _chain(top: Exception, *causes: Exception) -> Exception:
    """Build an exception with a __cause__ chain, the shape httpx produces."""
    cur = top
    for c in causes:
        cur.__cause__ = c
        cur = c
    return top


class TestTransportClassification:
    """The chains here are the ones MEASURED on Linux, which is what runs in
    production. On Windows httpx does not surface socket.gaierror at all, so a
    classifier written against a Windows observation would have silently
    mislabelled every real install."""

    def test_dns_failure(self):
        # httpx.ConnectError <- httpcore.ConnectError <- socket.gaierror
        exc = _chain(
            httpx.ConnectError("All connection attempts failed"),
            socket.gaierror(-2, "Name or service not known"),
        )
        assert classify_probe_failure(exc) == "dns"

    def test_connection_refused(self):
        exc = _chain(
            httpx.ConnectError("All connection attempts failed"),
            ConnectionRefusedError(111, "Connection refused"),
        )
        assert classify_probe_failure(exc) == "refused"

    def test_dns_wins_over_the_generic_connect_error_wrapping_it(self):
        # The outer type is identical for DNS and refused, so classifying on the
        # top-level class alone collapses exactly the two cases this exists to
        # separate.
        dns = _chain(httpx.ConnectError("x"), socket.gaierror(-2, "y"))
        refused = _chain(httpx.ConnectError("x"), ConnectionRefusedError(111, "y"))
        assert classify_probe_failure(dns) != classify_probe_failure(refused)

    def test_connect_timeout(self):
        assert classify_probe_failure(httpx.ConnectTimeout("timed out")) == "connect_timeout"

    def test_read_timeout(self):
        assert classify_probe_failure(httpx.ReadTimeout("timed out")) == "read_timeout"

    def test_tls_failure(self):
        assert classify_probe_failure(httpx.ConnectError("[SSL] bad certificate")) == "tls"

    def test_unknown_transport_error_is_labelled_not_dropped(self):
        # An unrecognised error must still produce a bucket. Returning None here
        # would put it back in the same undifferentiated hole this replaces.
        assert classify_probe_failure(httpx.HTTPError("something new")) == "transport"

    def test_a_deep_chain_is_still_walked(self):
        exc = _chain(
            httpx.ConnectError("outer"),
            OSError("middle"),
            ConnectionRefusedError(111, "inner"),
        )
        assert classify_probe_failure(exc) == "refused"

    def test_a_cyclic_or_absurdly_deep_chain_terminates(self):
        # Defensive: a self-referential __cause__ must not hang the probe, which
        # runs in the app lifespan.
        a = httpx.ConnectError("a")
        b = httpx.ConnectError("b")
        a.__cause__ = b
        b.__cause__ = a
        assert classify_probe_failure(a) == "transport"


class TestStatusClassification:
    def test_http_status_is_carried_with_its_code(self):
        # "Something answered but was not subgen" is a completely different
        # diagnosis from "nothing is there", and the code says which kind.
        assert classify_probe_failure(status=401) == "http_401"
        assert classify_probe_failure(status=502) == "http_502"

    def test_status_takes_a_bounded_shape(self):
        # HTTP status space is closed, so this cannot become high-cardinality
        # or carry anything user-specific.
        for code in (400, 401, 403, 404, 500, 502, 503):
            assert classify_probe_failure(status=code) == f"http_{code}"

    def test_a_nonsense_status_does_not_produce_a_junk_bucket(self):
        assert classify_probe_failure(status=99) == "http_other"
        assert classify_probe_failure(status=700) == "http_other"


class TestNeverLeaksTheTarget:
    """This value is transmitted. It must never carry a hostname, URL, port or
    raw exception text."""

    @pytest.mark.parametrize(
        "exc",
        [
            httpx.ConnectError("connection to media-nas.home.arpa:9000 failed"),
            httpx.ReadTimeout("timeout reading from http://10.0.0.7:9000/status"),
            httpx.HTTPError("failed for user@secret-host"),
        ],
    )
    def test_no_part_of_the_message_survives(self, exc):
        out = classify_probe_failure(exc)
        for leak in ("media-nas", "10.0.0.7", "secret-host", "9000", "arpa", "@"):
            assert leak not in out

    def test_every_output_is_from_the_closed_vocabulary(self):
        allowed = {
            "dns",
            "refused",
            "connect_timeout",
            "read_timeout",
            "timeout",
            "tls",
            "transport",
            "http_other",
        }
        samples = [
            httpx.ConnectError("x"),
            httpx.ConnectTimeout("x"),
            httpx.ReadTimeout("x"),
            httpx.PoolTimeout("x"),
            httpx.HTTPError("x"),
            _chain(httpx.ConnectError("x"), socket.gaierror(-2, "y")),
        ]
        for exc in samples:
            out = classify_probe_failure(exc)
            assert out in allowed, out
        assert classify_probe_failure(status=418).startswith("http_")


class TestCapabilitiesCarryTheReason:
    def test_unreachable_records_why(self):
        caps = SubgenCapabilities.unreachable("dns")
        assert caps.reachable is False
        assert caps.probe_failure == "dns"

    def test_unreachable_without_a_reason_is_still_valid(self):
        # Back-compat: existing callers pass nothing.
        assert SubgenCapabilities.unreachable().probe_failure is None

    def test_a_reachable_probe_has_no_failure(self):
        caps = SubgenCapabilities(
            reachable=True,
            version="2026.08.1",
            has_queue=True,
            has_batch=True,
            is_subarr_subgen=True,
        )
        assert caps.probe_failure is None

    def test_probe_failure_is_a_real_field_not_a_getattr_default(self):
        # SubgenCapabilities is a FROZEN dataclass. #458 shipped a gate that
        # could never open because it read a field that did not exist via
        # getattr with a default. Assert the field is declared.
        assert "probe_failure" in SubgenCapabilities.__dataclass_fields__

    def test_it_is_serialised(self):
        # If to_dict drops it, the UI and every consumer see nothing while the
        # object looks correct in a debugger.
        assert SubgenCapabilities.unreachable("refused").to_dict()["probe_failure"] == "refused"
