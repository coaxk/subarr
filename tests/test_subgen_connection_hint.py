"""#479: the wizard's subgen connection test said "subgen not reachable at
this URL" for every failure. The re-measure showed configured-but-broken
installs holding at 15%, with `refused` (host resolves, nothing on the port)
as the leading cause. A test that says WHY, and what to check, at the moment
the URL is typed, is the cheapest intervention the thread named.

The classifier already exists (subgen_client.classify_probe_failure); this
routes its verdict into the test-connection response as `cause` plus a
human `hint`, and the wizard renders the hint."""

from __future__ import annotations

import asyncio

import pytest

from subarr.routers.onboarding import TestRequest, _test_subgen, probe_failure_hint


def _live_client_class():
    # ⚠️ Resolve at CALL time, not import time. Other suites reload
    # subarr.subgen_client, after which a class imported at the top of this
    # file is a stale object; _test_subgen re-imports the module when it runs,
    # so the patch has to land on whatever sys.modules holds now. Patching the
    # stale class passed locally (small run, no reload) and probed the real
    # network on CI.
    import importlib

    mod = importlib.import_module("subarr.subgen_client")
    return mod.SubgenClient, mod.SubgenCapabilities


@pytest.mark.parametrize(
    "cause, must_mention",
    [
        ("dns", "resolve"),
        ("refused", "port"),
        ("connect_timeout", "firewall"),
        ("read_timeout", "model"),
        ("tls", "http"),
        ("http_502", "502"),
        ("http_401", "401"),
        ("transport", "reach"),
    ],
)
def test_every_cause_has_a_hint_that_names_the_thing_to_check(cause, must_mention):
    hint = probe_failure_hint(cause)
    assert hint, f"no hint for {cause}"
    assert must_mention.lower() in hint.lower(), f"{cause}: hint does not mention {must_mention!r}: {hint}"


def test_unknown_cause_still_gets_a_generic_hint():
    assert probe_failure_hint("something_new") != ""
    assert probe_failure_hint(None) != ""


def _unreachable(monkeypatch, cause: str):
    client_cls, caps_cls = _live_client_class()

    async def fake_probe(self):
        return caps_cls.unreachable(cause)

    monkeypatch.setattr(client_cls, "probe_capabilities", fake_probe)


def test_response_carries_cause_and_hint_on_failure(monkeypatch):
    _unreachable(monkeypatch, "refused")
    r = asyncio.run(_test_subgen(TestRequest(url="http://subgen:9001")))
    assert r["ok"] is False
    assert r["cause"] == "refused"
    assert "port" in r["hint"].lower()
    # The headline error names the cause so a plain-text renderer still helps.
    assert "refused" in r["error"]


def test_a_live_refused_port_is_classified_without_a_mock():
    # Bind a socket to grab a free port, close it, then probe it: nothing is
    # listening, so the OS refuses. This exercises the real classifier chain.
    import socket

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    r = asyncio.run(_test_subgen(TestRequest(url=f"http://127.0.0.1:{port}")))
    assert r["ok"] is False
    assert r["cause"] == "refused", r
    assert r["hint"]


def test_success_response_carries_no_cause(monkeypatch):
    client_cls, caps_cls = _live_client_class()

    async def fake_probe(self):
        return caps_cls(
            reachable=True, version="2026.08.1", is_subarr_subgen=True, has_queue=True, has_batch=True
        )

    monkeypatch.setattr(client_cls, "probe_capabilities", fake_probe)
    r = asyncio.run(_test_subgen(TestRequest(url="http://subgen:9000")))
    assert r["ok"] is True
    assert r.get("cause") is None
