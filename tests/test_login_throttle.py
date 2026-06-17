"""#260: login brute-force throttle — IP resolution + sliding-window limiter."""

from __future__ import annotations

from subarr.login_throttle import LoginThrottle, parse_cidrs, resolve_client_ip


# ─── parse_cidrs ────────────────────────────────────────────────────


def test_parse_cidrs_mixed_and_bad():
    nets = parse_cidrs("192.168.1.0/24, 10.0.0.5 , garbage, ")
    # bad/empty entries dropped; a bare host becomes a /32
    assert len(nets) == 2


def test_parse_cidrs_empty():
    assert parse_cidrs("") == []
    assert parse_cidrs(None) == []


# ─── resolve_client_ip ──────────────────────────────────────────────


def test_direct_no_proxy_uses_peer():
    # peer not trusted → XFF ignored entirely
    assert resolve_client_ip("203.0.113.9", "1.2.3.4", parse_cidrs("")) == "203.0.113.9"


def test_spoofed_xff_from_untrusted_peer_ignored():
    assert resolve_client_ip("203.0.113.9", "9.9.9.9", parse_cidrs("10.0.0.0/24")) == "203.0.113.9"


def test_trusted_proxy_single_client():
    assert resolve_client_ip("10.0.0.2", "203.0.113.9", parse_cidrs("10.0.0.0/24")) == "203.0.113.9"


def test_trusted_proxy_chain_returns_first_untrusted():
    # client, then an internal hop; peer is the outer proxy. Walk right→left,
    # skip trusted hops, first untrusted = real client.
    ip = resolve_client_ip("10.0.0.2", "203.0.113.9, 10.0.0.5", parse_cidrs("10.0.0.0/24"))
    assert ip == "203.0.113.9"


def test_trusted_proxy_all_trusted_falls_back_to_peer():
    ip = resolve_client_ip("10.0.0.2", "10.0.0.7, 10.0.0.5", parse_cidrs("10.0.0.0/24"))
    assert ip == "10.0.0.2"


def test_malformed_xff_entries_skipped():
    ip = resolve_client_ip("10.0.0.2", "not-an-ip, 203.0.113.9", parse_cidrs("10.0.0.0/24"))
    assert ip == "203.0.113.9"


# ─── LoginThrottle ──────────────────────────────────────────────────


def _throttle(**over):
    kw = dict(max_attempts=5, window_s=300, allowlist=parse_cidrs(""), max_ips=4096)
    kw.update(over)
    return LoginThrottle(**kw)


def test_fresh_ip_not_blocked():
    t = _throttle()
    blocked, retry = t.check("1.2.3.4", now=1000.0)
    assert blocked is False and retry == 0


def test_blocks_at_threshold():
    t = _throttle()
    for i in range(5):
        t.record_failure("1.2.3.4", now=1000.0 + i)
    blocked, retry = t.check("1.2.3.4", now=1005.0)
    assert blocked is True
    assert retry > 0


def test_under_threshold_allowed():
    t = _throttle()
    for i in range(4):
        t.record_failure("1.2.3.4", now=1000.0 + i)
    assert t.check("1.2.3.4", now=1004.0)[0] is False


def test_window_slides_and_resets():
    t = _throttle()
    for i in range(5):
        t.record_failure("1.2.3.4", now=1000.0 + i)
    # well past the window → all failures aged out
    assert t.check("1.2.3.4", now=1000.0 + 301)[0] is False


def test_success_clears():
    t = _throttle()
    for i in range(5):
        t.record_failure("1.2.3.4", now=1000.0 + i)
    t.clear("1.2.3.4")
    assert t.check("1.2.3.4", now=1006.0)[0] is False


def test_allowlisted_ip_never_blocked():
    t = _throttle(allowlist=parse_cidrs("1.2.3.0/24"))
    for i in range(50):
        t.record_failure("1.2.3.4", now=1000.0 + i)
    assert t.check("1.2.3.4", now=1050.0)[0] is False


def test_memory_cap_evicts():
    t = _throttle(max_ips=10)
    for i in range(50):
        t.record_failure(f"10.0.0.{i}", now=1000.0)
    assert t.size() <= 10
