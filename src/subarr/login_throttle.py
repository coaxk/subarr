"""#260: login brute-force throttle.

In-memory, per-process sliding-window limiter on failed logins, keyed by the
resolved client IP. Never a permanent lockout — the window slides, so the worst
case is a short wait. An allowlist exempts trusted ranges entirely; trusted
proxies let us key on the real client behind a reverse proxy instead of the
proxy's own IP. Fine for subarr's single-container model.
"""

from __future__ import annotations

import ipaddress
import logging
import math
import threading
from collections import OrderedDict, deque

log = logging.getLogger(__name__)


def parse_cidrs(csv: str | None) -> list[ipaddress._BaseNetwork]:
    """Parse a comma-separated list of IPs/CIDRs into networks. A bare host
    becomes a /32 (or /128). Bad entries are logged and skipped, never fatal."""
    out: list[ipaddress._BaseNetwork] = []
    for raw in (csv or "").split(","):
        cidr = raw.strip()
        if not cidr:
            continue
        try:
            out.append(ipaddress.ip_network(cidr, strict=False))
        except ValueError:
            log.warning("ignoring invalid CIDR/IP in config: %r", cidr)
    return out


def ip_in(ip: str, networks: list[ipaddress._BaseNetwork]) -> bool:
    if not networks:
        return False
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(addr in net for net in networks)


def resolve_client_ip(peer_ip: str, xff_header: str, trusted_proxies) -> str:
    """Resolve the real client IP. If the socket peer is not a trusted proxy,
    X-Forwarded-For is ignored (a spoofed header can't evade the limit or frame
    another IP). If it is trusted, walk XFF right→left skipping trusted hops; the
    first non-trusted entry is the client. Falls back to the peer."""
    if not peer_ip or not ip_in(peer_ip, trusted_proxies):
        return peer_ip
    for entry in reversed([e.strip() for e in (xff_header or "").split(",")]):
        if not entry:
            continue
        try:
            ipaddress.ip_address(entry)
        except ValueError:
            continue  # skip malformed hops
        if not ip_in(entry, trusted_proxies):
            return entry
    return peer_ip


class LoginThrottle:
    """Sliding-window failed-login limiter. Thread-safe."""

    def __init__(self, *, max_attempts: int, window_s: int, allowlist, max_ips: int = 4096):
        self._max = max(1, int(max_attempts))
        self._window = max(1, int(window_s))
        self._allowlist = allowlist or []
        self._max_ips = max(1, int(max_ips))
        self._lock = threading.Lock()
        # OrderedDict so we can evict the oldest IP when capped.
        self._fails: OrderedDict[str, deque] = OrderedDict()

    def _prune(self, ip: str, now: float) -> deque:
        dq = self._fails.get(ip)
        if dq is None:
            return deque()
        cutoff = now - self._window
        while dq and dq[0] <= cutoff:
            dq.popleft()
        if not dq:
            self._fails.pop(ip, None)
            return deque()
        return dq

    def check(self, ip: str, *, now: float) -> tuple[bool, int]:
        """(blocked, retry_after_seconds). Allowlisted IPs are never blocked."""
        if ip_in(ip, self._allowlist):
            return (False, 0)
        with self._lock:
            dq = self._prune(ip, now)
            if len(dq) < self._max:
                return (False, 0)
            # Blocked until the oldest failure ages out of the window.
            retry = max(1, math.ceil(dq[0] + self._window - now))
            return (True, retry)

    def record_failure(self, ip: str, *, now: float) -> None:
        if ip_in(ip, self._allowlist):
            return
        with self._lock:
            dq = self._fails.get(ip)
            if dq is None:
                dq = deque()
                self._fails[ip] = dq
            self._fails.move_to_end(ip)
            dq.append(now)
            # Evict oldest IPs if over the cap (memory-DoS guard).
            while len(self._fails) > self._max_ips:
                self._fails.popitem(last=False)

    def clear(self, ip: str) -> None:
        with self._lock:
            self._fails.pop(ip, None)

    def size(self) -> int:
        with self._lock:
            return len(self._fails)
