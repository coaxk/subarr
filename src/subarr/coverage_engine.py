"""Coverage reconciliation engine.

Given Bazarr's "wanted" list, enrich each row with:
- Sonarr/Radarr metadata (originalLanguage, monitored, tags, path)
- Filesystem reality (does any .*.srt already exist next to the file? if so,
  Bazarr's view is stale; flag for scan-disk)
- Tautulli watch-history score (recency + grandparent rewatch signal)

Output is a flat list of CoverageItem dicts. The frontend renders + filters.

Sequential per-source then merge — never blocks on one slow integration
unless every upstream is slow. Each upstream is wrapped in try/except so
a single failure degrades gracefully (the engine returns whatever it
managed to assemble; the response includes a `sources` field showing
which integrations contributed).
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import settings
from .integrations import IntegrationError
from .integrations.bazarr import BazarrClient
from .integrations.radarr import RadarrClient
from .integrations.sonarr import SonarrClient
from .integrations.tautulli import TautulliClient

log = logging.getLogger(__name__)


@dataclass
class CoverageItem:
    media_type: str  # "episode" | "movie"
    title: str  # series title (episodes) / movie title
    episode_title: str | None = None
    episode_number: str | None = None  # "1x03"
    original_language: str | None = None
    monitored: bool | None = None
    tags: list[str] = field(default_factory=list)
    # Filesystem reality
    canonical_path: str | None = None  # relative to media_root, parent dir of the video
    has_sub_on_disk: bool = False  # any *.srt next to the video
    sub_files_seen: list[str] = field(default_factory=list)
    # Bazarr cross-reference
    bazarr_sonarr_id: int | None = None
    bazarr_radarr_id: int | None = None
    bazarr_episode_id: int | None = None
    missing_subtitles: list[str] = field(default_factory=list)
    # ffprobe-driven embedded reconciliation (v1.1 batch 1 hotfix)
    embedded_en: str | None = None   # 'EN' / 'EN(forced)' / 'EN(SDH)' / 'EN(commentary)' / None
    audio_langs: list[str] = field(default_factory=list)
    suggest_bazarr_rescan: bool = False
    # Resolved file path (file-level, not series-dir; populated when
    # Sonarr's episode_file is fetched during enrichment)
    file_canonical_path: str | None = None
    # Scoring
    score: int = 0
    score_reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "media_type": self.media_type,
            "title": self.title,
            "episode_title": self.episode_title,
            "episode_number": self.episode_number,
            "original_language": self.original_language,
            "monitored": self.monitored,
            "tags": self.tags,
            "canonical_path": self.canonical_path,
            "has_sub_on_disk": self.has_sub_on_disk,
            "sub_files_seen": self.sub_files_seen,
            "bazarr": {
                "sonarr_id": self.bazarr_sonarr_id,
                "radarr_id": self.bazarr_radarr_id,
                "episode_id": self.bazarr_episode_id,
                "missing_subtitles": self.missing_subtitles,
            },
            "embedded_en": self.embedded_en,
            "audio_langs": self.audio_langs,
            "suggest_bazarr_rescan": self.suggest_bazarr_rescan,
            "file_canonical_path": self.file_canonical_path,
            "score": self.score,
            "score_reasons": self.score_reasons,
        }


@dataclass
class CoverageReport:
    generated_at: float
    sources: dict[str, dict[str, Any]]  # name -> {ok: bool, count?: int, error?: str}
    items: list[CoverageItem]

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "sources": self.sources,
            "items": [i.to_dict() for i in self.items],
            "totals": {
                "items": len(self.items),
                "episodes": sum(1 for i in self.items if i.media_type == "episode"),
                "movies": sum(1 for i in self.items if i.media_type == "movie"),
                "with_disk_sub": sum(1 for i in self.items if i.has_sub_on_disk),
                "embedded_full_en": sum(1 for i in self.items if i.embedded_en == "EN"),
                "suggest_bazarr_rescan": sum(1 for i in self.items if i.suggest_bazarr_rescan),
            },
        }


class IntegrationBundle:
    """Holds the four integration clients + close-all helper. App lifespan
    owns one instance."""

    def __init__(self):
        self.bazarr = BazarrClient()
        self.sonarr = SonarrClient()
        self.radarr = RadarrClient()
        self.tautulli = TautulliClient()

    async def aclose(self) -> None:
        await asyncio.gather(
            self.bazarr.aclose(),
            self.sonarr.aclose(),
            self.radarr.aclose(),
            self.tautulli.aclose(),
            return_exceptions=True,
        )


# ───────────────────────────── helpers ──────────────────────────────────────


def _strip_arr_prefix(arr_path: str | None) -> str | None:
    """Sonarr returns 'path': '/data/Media/TV/Foo' (its container view).
    Strip the configured prefix to canonical form 'TV/Foo'."""
    if not arr_path:
        return None
    prefix = settings.arr_path_prefix
    s = arr_path
    if prefix and s.startswith(prefix):
        s = s[len(prefix):]
    return s.strip("/")


def _scan_for_srt(canonical_dir: str) -> tuple[bool, list[str]]:
    """Shallow check: does any *.srt exist directly under <media_root>/<canonical>?
    Returns (has_any, list_of_filenames). Best-effort; errors swallowed."""
    if not canonical_dir:
        return False, []
    try:
        full = settings.media_root / Path(canonical_dir)
        if not full.is_dir():
            return False, []
        srts = sorted(p.name for p in full.iterdir() if p.is_file() and p.suffix.lower() == ".srt")
        return bool(srts), srts
    except (OSError, ValueError):
        return False, []


def _tags_for(arr_record: dict[str, Any], tag_map: dict[int, str]) -> list[str]:
    return sorted(tag_map.get(t, str(t)) for t in (arr_record.get("tags") or []))


# ───────────────────────────── upstream fetches ─────────────────────────────


async def _fetch_bazarr(bz: BazarrClient, sources: dict) -> tuple[list[dict], list[dict]]:
    if not bz.is_configured():
        sources["bazarr"] = {"ok": False, "configured": False}
        return [], []
    try:
        eps_task = asyncio.create_task(bz.episodes_wanted())
        movs_task = asyncio.create_task(bz.movies_wanted())
        eps = await eps_task
        movs = await movs_task
        sources["bazarr"] = {"ok": True, "configured": True,
                             "episodes_wanted": len(eps), "movies_wanted": len(movs)}
        return eps, movs
    except IntegrationError as e:
        sources["bazarr"] = {"ok": False, "configured": True, "error": str(e)}
        return [], []


async def _fetch_arr(name: str, client, sources: dict, fetch_fn: str) -> list[dict]:
    if not client.is_configured():
        sources[name] = {"ok": False, "configured": False}
        return []
    try:
        rows = await getattr(client, fetch_fn)()
        sources[name] = {"ok": True, "configured": True, "count": len(rows)}
        return rows
    except IntegrationError as e:
        sources[name] = {"ok": False, "configured": True, "error": str(e)}
        return []


async def _fetch_arr_tags(name: str, client, sources: dict) -> dict[int, str]:
    if not client.is_configured():
        return {}
    try:
        rows = await client.tags()
        return {row["id"]: row.get("label", str(row["id"])) for row in rows}
    except IntegrationError as e:
        # Tag fetch is non-fatal; we just lose tag labels.
        sources.setdefault(name, {}).setdefault("warnings", []).append(f"tags: {e}")
        return {}


async def _fetch_tautulli(t: TautulliClient, sources: dict) -> list[dict]:
    if not t.is_configured():
        sources["tautulli"] = {"ok": False, "configured": False}
        return []
    try:
        rows = await t.history(length=500, days=30)
        sources["tautulli"] = {"ok": True, "configured": True, "history_rows": len(rows)}
        return rows
    except IntegrationError as e:
        sources["tautulli"] = {"ok": False, "configured": True, "error": str(e)}
        return []


# ───────────────────────────── scoring ──────────────────────────────────────


def _tautulli_signals(history: list[dict]) -> dict[str, dict]:
    """Aggregate history by grandparent (series). Returns
    {grandparent_title_lower: {last_played: epoch, plays_30d: int}}.

    Title-based join because Sonarr ids and Tautulli rating_keys don't
    naturally line up (would need a TVDB↔TMDB↔ratingKey lookup table
    which is v1.2 territory). Title match is lossy but cheap and
    surfaces the obvious wins.
    """
    out: dict[str, dict] = {}
    now = time.time()
    for row in history:
        if row.get("media_type") != "episode":
            continue
        gp = (row.get("grandparent_title") or "").strip().lower()
        if not gp:
            continue
        date = row.get("date") or 0
        agg = out.setdefault(gp, {"last_played": 0, "plays_30d": 0})
        if date > agg["last_played"]:
            agg["last_played"] = date
        if now - date <= 30 * 86400:
            agg["plays_30d"] += 1
    return out


def _episode_filename_pattern(episode_number: str | None) -> str | None:
    """Convert Bazarr's "1x3" → "S01E03" regex pattern for filename matching.
    Returns None if unparseable."""
    if not episode_number or "x" not in episode_number:
        return None
    try:
        season, episode = episode_number.split("x")
        return f"S{int(season):02d}E{int(episode):02d}".lower()
    except (ValueError, TypeError):
        return None


def _attach_probe_episode(item: CoverageItem, idx: dict[str, list]) -> None:
    """Look up a probed file under the series prefix whose basename
    contains S01E03 (or equivalent). On match, copy embedded_en +
    audio_langs + file_canonical_path onto the item."""
    from .media_probe import audio_lang_summary, english_track_summary
    if not item.canonical_path:
        return
    candidates = idx.get(item.canonical_path) or []
    if not candidates:
        return
    pattern = _episode_filename_pattern(item.episode_number)
    if not pattern:
        return
    for file_canonical, probe in candidates:
        basename = file_canonical.rsplit("/", 1)[-1].lower()
        if pattern in basename:
            item.file_canonical_path = file_canonical
            item.embedded_en = english_track_summary(probe)
            item.audio_langs = audio_lang_summary(probe)
            return


def _attach_probe_movie(item: CoverageItem, idx: dict[str, list]) -> None:
    """Movies: a single video file lives directly under the movie dir.
    First probe under the movie's canonical wins."""
    from .media_probe import audio_lang_summary, english_track_summary
    if not item.canonical_path:
        return
    candidates = idx.get(item.canonical_path) or []
    if not candidates:
        return
    file_canonical, probe = candidates[0]
    item.file_canonical_path = file_canonical
    item.embedded_en = english_track_summary(probe)
    item.audio_langs = audio_lang_summary(probe)


def _score(item: CoverageItem, signals: dict[str, dict]) -> None:
    s = 0
    reasons: list[str] = []
    sig = signals.get(item.title.strip().lower())
    if sig:
        age_days = (time.time() - sig["last_played"]) / 86400.0 if sig["last_played"] else 9999
        if age_days <= 7:
            s += 1000
            reasons.append(f"watched in last 7d ({age_days:.0f}d)")
        elif age_days <= 30:
            s += 500
            reasons.append(f"watched in last 30d ({age_days:.0f}d)")
        if sig["plays_30d"] > 3:
            s += 200
            reasons.append(f"{sig['plays_30d']} plays in 30d")
    if item.original_language and item.original_language.lower() != "english":
        s += 100
        reasons.append(f"non-english ({item.original_language})")
    if item.monitored:
        s += 50
        reasons.append("monitored")
    if item.has_sub_on_disk:
        # Strong negative — disk has a sub, Bazarr's view is stale.
        s -= 5000
        reasons.append("stale: disk already has .srt (Bazarr needs scan-disk)")
    # v1.1 hotfix: embedded English in the container itself.
    if item.embedded_en == "EN":
        s -= 3000
        reasons.append("embedded: full English sub in file (Bazarr missed it)")
        item.suggest_bazarr_rescan = True
    elif item.embedded_en in {"EN(forced)", "EN(SDH)", "EN(commentary)"}:
        s -= 500
        reasons.append(f"embedded: {item.embedded_en} — partial coverage")
    item.score = s
    item.score_reasons = reasons


# ───────────────────────────── main entrypoint ──────────────────────────────


async def build_coverage(
    bundle: IntegrationBundle,
    *,
    use_tautulli: bool = True,
    probe_store: Any = None,  # ProbeStore | None — avoid circular import
) -> CoverageReport:
    sources: dict[str, dict] = {}

    bz_eps, bz_movs = await _fetch_bazarr(bundle.bazarr, sources)
    sonarr_series_task = asyncio.create_task(
        _fetch_arr("sonarr", bundle.sonarr, sources, "series")
    )
    radarr_movies_task = asyncio.create_task(
        _fetch_arr("radarr", bundle.radarr, sources, "movies")
    )
    sonarr_tags_task = asyncio.create_task(_fetch_arr_tags("sonarr", bundle.sonarr, sources))
    radarr_tags_task = asyncio.create_task(_fetch_arr_tags("radarr", bundle.radarr, sources))
    tautulli_task = (
        asyncio.create_task(_fetch_tautulli(bundle.tautulli, sources))
        if use_tautulli else None
    )

    sonarr_series = await sonarr_series_task
    radarr_movies = await radarr_movies_task
    sonarr_tags = await sonarr_tags_task
    radarr_tags = await radarr_tags_task
    history = await tautulli_task if tautulli_task else []

    sonarr_by_id = {s["id"]: s for s in sonarr_series if isinstance(s, dict) and "id" in s}
    radarr_by_title = {m.get("title", "").strip().lower(): m
                       for m in radarr_movies if isinstance(m, dict)}
    tt_signals = _tautulli_signals(history) if history else {}

    # Probe-cache index: { series_canonical_prefix → [(file_canonical, ProbeResult)] }
    # Pre-build once so per-row lookup is O(files-under-this-series) not O(total-cache).
    probe_by_series_prefix: dict[str, list[tuple[str, Any]]] = {}
    if probe_store is not None:
        sources["probe_cache"] = {"ok": True, "entries": 0}
        for path in probe_store.all_paths():
            entry = probe_store.get(path)  # non-strict — accept whatever's cached
            if entry is None:
                continue
            sources["probe_cache"]["entries"] += 1
            # Index under all ancestor prefixes so a single all-series cache scan
            # finds matches for any series-level Bazarr row.
            parts = path.split("/")
            for i in range(2, len(parts)):  # skip top-level (TV/Movies)
                prefix = "/".join(parts[:i])
                probe_by_series_prefix.setdefault(prefix, []).append((path, entry))

    items: list[CoverageItem] = []

    # Episodes (Bazarr → Sonarr enrichment via sonarrSeriesId)
    for w in bz_eps:
        sonarr_id = w.get("sonarrSeriesId")
        s = sonarr_by_id.get(sonarr_id, {})
        canonical = _strip_arr_prefix(s.get("path"))
        has_srt, srts = _scan_for_srt(canonical) if canonical else (False, [])
        item = CoverageItem(
            media_type="episode",
            title=w.get("seriesTitle") or s.get("title") or "(unknown)",
            episode_title=w.get("episodeTitle"),
            episode_number=w.get("episode_number"),
            original_language=(s.get("originalLanguage") or {}).get("name"),
            monitored=s.get("monitored"),
            tags=_tags_for(s, sonarr_tags),
            canonical_path=canonical,
            has_sub_on_disk=has_srt,
            sub_files_seen=srts,
            bazarr_sonarr_id=sonarr_id,
            bazarr_episode_id=w.get("sonarrEpisodeId"),
            missing_subtitles=[ms.get("code2") or ms.get("name") or "?"
                               for ms in (w.get("missing_subtitles") or [])],
        )
        _attach_probe_episode(item, probe_by_series_prefix)
        _score(item, tt_signals)
        items.append(item)

    # Movies (Bazarr → Radarr enrichment via title — Bazarr doesn't expose
    # radarrId in the wanted payload the same way it does sonarrSeriesId).
    for w in bz_movs:
        title = w.get("title") or w.get("movieTitle") or ""
        m = radarr_by_title.get(title.strip().lower(), {})
        canonical = _strip_arr_prefix(m.get("path"))
        has_srt, srts = _scan_for_srt(canonical) if canonical else (False, [])
        item = CoverageItem(
            media_type="movie",
            title=title,
            original_language=(m.get("originalLanguage") or {}).get("name"),
            monitored=m.get("monitored"),
            tags=_tags_for(m, radarr_tags),
            canonical_path=canonical,
            has_sub_on_disk=has_srt,
            sub_files_seen=srts,
            bazarr_radarr_id=m.get("id"),
            missing_subtitles=[ms.get("code2") or ms.get("name") or "?"
                               for ms in (w.get("missing_subtitles") or [])],
        )
        _attach_probe_movie(item, probe_by_series_prefix)
        _score(item, tt_signals)
        items.append(item)

    items.sort(key=lambda i: i.score, reverse=True)
    return CoverageReport(generated_at=time.time(), sources=sources, items=items)
