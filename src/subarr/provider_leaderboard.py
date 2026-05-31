"""v1.1-J: Bazarr provider success leaderboard.

Pulls subtitle-download history from Bazarr (episodes + movies), aggregates
per-provider success/quality metrics, and serves a leaderboard with
per-language breakdowns. Tracks:

  - downloads      : count of successful downloads (action=1)
  - blacklisted    : count of subsequently blacklisted downloads (bad subs)
  - avg_score      : mean match score
  - languages      : counts per ISO 639-1 language code
  - last_seen      : most recent download timestamp
  - throttled      : current /api/providers status != "Good"

Telemetry angle: this data is gold for cross-user aggregation. Anonymous
per-install snapshot (provider name + counts, no titles/paths) can feed a
global leaderboard at subarr.com/stats showing OpenSubs hit rate globally,
which anime-providers actually work, etc. v1.1.1 hook.

Scoring integration: coverage_engine consults the leaderboard to decide
whether a Bazarr-wanted row is worth waiting for human subs vs queueing
Whisper. +200 score when any provider for that language has >=70% success
and is not throttled; -1000 when ALL providers for that language are
throttled or have <30% success (route directly to subgen).
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ProviderStats:
    name: str
    downloads: int = 0
    blacklisted: int = 0
    score_sum: float = 0.0  # for avg
    score_count: int = 0
    languages: dict[str, int] = field(default_factory=dict)
    last_seen: float = 0.0
    throttled: bool = False
    status_label: str = "unknown"

    @property
    def avg_score(self) -> float:
        return self.score_sum / self.score_count if self.score_count else 0.0

    @property
    def success_rate(self) -> float:
        """Fraction of downloads that didn't get subsequently blacklisted.
        100% = every download stuck. <50% = users keep finding bad subs."""
        if not self.downloads:
            return 0.0
        return 1.0 - (self.blacklisted / self.downloads)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "downloads": self.downloads,
            "blacklisted": self.blacklisted,
            "avg_score": round(self.avg_score, 1),
            "success_rate": round(self.success_rate * 100, 1),  # %
            "languages": dict(sorted(
                self.languages.items(), key=lambda kv: kv[1], reverse=True,
            )),
            "last_seen": self.last_seen,
            "throttled": self.throttled,
            "status_label": self.status_label,
        }


def _parse_score(raw: Any) -> float | None:
    """Bazarr stores score as e.g. "96.67%" — strip the % and float it."""
    if raw is None:
        return None
    s = str(raw).rstrip("%").strip()
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def aggregate(
    episode_history: list[dict],
    movie_history: list[dict],
    providers_status: list[dict],
) -> list[ProviderStats]:
    """Roll up per-provider stats across the combined history.
    Returns a list sorted by downloads desc."""
    by_name: dict[str, ProviderStats] = {}

    # Seed from the providers_status list so configured-but-unused providers
    # still appear (zero downloads, but visible).
    for ps in providers_status:
        nm = (ps.get("name") or "").strip()
        if not nm:
            continue
        s = by_name.setdefault(nm, ProviderStats(name=nm))
        status = (ps.get("status") or "").strip()
        s.status_label = status
        # "Good" and "History" both indicate healthy. Anything else = throttle/error.
        s.throttled = status.lower() not in ("good", "history", "")

    # Bazarr action codes (verified against bazarr 1.5.6, 2026-05-31):
    #   1 = downloaded from a provider                 ← count toward provider
    #   2 = manually uploaded                          ← count: includes subarr's
    #         v1.1-G direct multipart uploads of Whisper output from the
    #         completion watcher (which carry provider='whisperai'). Live
    #         Judd install: 19 such rows. Also includes embedded-sub
    #         extractions which legitimately credit the 'embeddedsubtitles'
    #         provider for surfacing them.
    #   3 = upgraded                                   ← skip (already counted on prior 1/5)
    #   4 = synced                                     ← skip (no new sub)
    #   5 = translated                                 ← count: Whisper / subgen output.
    #         Bazarr records NO provider name on these rows, so we synthesise
    #         "whisperai" to surface its throughput. Live Judd install:
    #         261 such rows, the single biggest "provider" by volume.
    # Other actions (e.g. upgraded, blacklisted) tracked via the blacklisted flag.
    for row in episode_history + movie_history:
        provider = (row.get("provider") or "").strip()
        action = row.get("action")
        if not provider:
            if action == 5:
                # Whisper translation: Bazarr attributes none, we attribute
                # it to "whisperai" so users see throughput from subgen.
                provider = "whisperai"
            else:
                continue
        s = by_name.setdefault(provider, ProviderStats(name=provider))
        if action in (1, 2, 5):  # download OR manual-upload OR Whisper-translation
            s.downloads += 1
            score = _parse_score(row.get("score"))
            if score is not None:
                s.score_sum += score
                s.score_count += 1
            # Bazarr returns language as a dict {name, code2, code3, ...}
            # or sometimes as a string. Handle both.
            lang = row.get("language")
            lang_key = None
            if isinstance(lang, dict):
                lang_key = (lang.get("code3") or lang.get("code2") or "").lower()
            elif isinstance(lang, str):
                lang_key = lang.lower()[:3]
            if lang_key:
                s.languages[lang_key] = s.languages.get(lang_key, 0) + 1
            # parsed_timestamp comes as 'MM/DD/YY HH:MM:SS' string in Bazarr.
            ts = row.get("parsed_timestamp")
            if isinstance(ts, str):
                try:
                    import datetime
                    dt = datetime.datetime.strptime(ts, "%m/%d/%y %H:%M:%S")
                    s.last_seen = max(s.last_seen, dt.timestamp())
                except (ValueError, TypeError):
                    pass
            elif isinstance(ts, (int, float)):
                s.last_seen = max(s.last_seen, float(ts))
        if row.get("blacklisted"):
            s.blacklisted += 1

    return sorted(by_name.values(), key=lambda x: x.downloads, reverse=True)


async def build_leaderboard(bazarr_client) -> dict[str, Any]:
    """Top-level entrypoint. Pulls fresh data from Bazarr and aggregates.
    Cheap enough to run on demand (3 HTTP calls, ~5s on a 50k-history install).
    Cache TTL handled at the router level."""
    started = time.time()
    if not bazarr_client.is_configured():
        return {"available": False, "providers": []}
    providers = await bazarr_client.providers_status()
    eps = await bazarr_client.episodes_history(length=2000)
    movs = await bazarr_client.movies_history(length=2000)
    stats = aggregate(eps, movs, providers)
    return {
        "available": True,
        "generated_at": time.time(),
        "duration_s": round(time.time() - started, 2),
        "history_rows": len(eps) + len(movs),
        "providers": [s.to_dict() for s in stats],
    }


def telemetry_snapshot(leaderboard: dict[str, Any]) -> dict[str, Any]:
    """Anonymous snapshot suitable for cross-install aggregation at
    subarr.com/stats. Drops anything user-identifiable; keeps only the
    per-provider rollup metrics that benefit from N>1 users."""
    if not leaderboard.get("available"):
        return {}
    return {
        "snapshot_at": leaderboard["generated_at"],
        "providers": [
            {
                "name": p["name"],
                "downloads": p["downloads"],
                "blacklisted": p["blacklisted"],
                "avg_score": p["avg_score"],
                "success_rate": p["success_rate"],
                "throttled": p["throttled"],
                # Aggregate per-lang counts only (no titles)
                "languages": p["languages"],
            }
            for p in leaderboard["providers"]
        ],
    }
