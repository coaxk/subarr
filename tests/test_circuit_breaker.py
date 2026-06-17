"""#235: per-integration circuit breaker so a flapping/hung upstream can't be
hammered every cycle. CLOSED -> (N consecutive failures) -> OPEN -> (cooldown)
-> HALF_OPEN probe -> CLOSED on success / OPEN on failure.
"""

from __future__ import annotations

from subarr.circuit_breaker import CircuitBreaker


def _clock():
    """A controllable monotonic clock for deterministic cooldown tests."""
    t = {"now": 1000.0}
    return t, (lambda: t["now"])


def test_starts_closed_and_allows():
    cb = CircuitBreaker(fail_threshold=3, cooldown_s=30)
    assert cb.state == "closed"
    assert cb.allow() is True


def test_opens_after_threshold_consecutive_failures():
    t, clock = _clock()
    cb = CircuitBreaker(fail_threshold=3, cooldown_s=30, clock=clock)
    cb.record_failure()
    cb.record_failure()
    assert cb.state == "closed"  # not yet
    cb.record_failure()
    assert cb.state == "open"
    assert cb.allow() is False  # blocked while open


def test_success_resets_failure_count():
    cb = CircuitBreaker(fail_threshold=3, cooldown_s=30)
    cb.record_failure()
    cb.record_failure()
    cb.record_success()  # reset
    cb.record_failure()
    cb.record_failure()
    assert cb.state == "closed"  # 2 < 3 after the reset


def test_open_transitions_to_half_open_after_cooldown():
    t, clock = _clock()
    cb = CircuitBreaker(fail_threshold=1, cooldown_s=30, clock=clock)
    cb.record_failure()
    assert cb.state == "open"
    assert cb.allow() is False
    t["now"] += 31  # cooldown elapses
    assert cb.allow() is True  # one probe permitted
    assert cb.state == "half_open"


def test_half_open_probe_success_closes():
    t, clock = _clock()
    cb = CircuitBreaker(fail_threshold=1, cooldown_s=30, clock=clock)
    cb.record_failure()
    t["now"] += 31
    cb.allow()  # -> half_open
    cb.record_success()
    assert cb.state == "closed"
    assert cb.allow() is True


def test_half_open_probe_failure_reopens_and_resets_cooldown():
    t, clock = _clock()
    cb = CircuitBreaker(fail_threshold=1, cooldown_s=30, clock=clock)
    cb.record_failure()
    t["now"] += 31
    cb.allow()  # -> half_open
    cb.record_failure()  # probe failed
    assert cb.state == "open"
    assert cb.allow() is False  # cooldown restarts, blocked again immediately
    t["now"] += 31
    assert cb.allow() is True  # probes again after a fresh cooldown


def test_open_blocks_only_until_cooldown_not_forever():
    t, clock = _clock()
    cb = CircuitBreaker(fail_threshold=2, cooldown_s=10, clock=clock)
    cb.record_failure()
    cb.record_failure()
    assert cb.allow() is False
    t["now"] += 5
    assert cb.allow() is False  # still cooling
    t["now"] += 6
    assert cb.allow() is True  # past cooldown
