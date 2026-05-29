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
    Returns (has_any, list_of_filenames). Best-effort; errors swallowed.

    Kept for the movie path (Radarr's path IS the movie folder, with the
    video + sibling .srt at the same level). For episodes use
    `_scan_for_srt_recursive` + `_match_episode_srt_pattern` because the
    series dir contains Season N/ subfolders the .srt actually lives in.
    """
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


def _scan_for_srt_recursive(canonical_dir: str) -> list[str]:
    """Walk every .srt under <media_root>/<canonical_dir> and return
    relative paths. Cached per series during one coverage build via the
    caller — series with 200 episodes only rglob once.

    Returns relative paths so the caller can match per-episode by looking
    for an S01E03-style substring in the path.
    """
    if not canonical_dir:
        return []
    try:
        full = settings.media_root / Path(canonical_dir)
        if not full.is_dir():
            return []
        return sorted(
            str(p.relative_to(full))
            for p in full.rglob("*.srt")
            if p.is_file()
        )
    except (OSError, ValueError):
        return []


def _match_episode_srt_pattern(srt_paths: list[str], episode_number: str | None) -> tuple[bool, list[str]]:
    """Given a list of relative .srt paths under a series dir and an
    episode_number ('1x3'), return (any_match, matching_paths).
    Match is case-insensitive S<NN>E<NN> substring in the srt filename.

    FALLBACK PATTERN ONLY — used when Sonarr's authoritative episodeFile.path
    isn't available. Misses release-team naming conventions like 'Part.N',
    'Ep01', 'Episode_3', etc. Prefer `_sidecars_for_file` when the file path
    is known."""
    if not episode_number or "x" not in episode_number or not srt_paths:
        return False, []
    try:
        season, ep = episode_number.split("x")
        pat = f"s{int(season):02d}e{int(ep):02d}"
    except (ValueError, TypeError):
        return False, []
    matches = [p for p in srt_paths if pat in p.lower()]
    return bool(matches), matches


def _sidecars_for_file(srt_paths: list[str], file_canonical: str) -> list[str]:
    """Authoritative sidecar finder: given the actual video file's canonical
    path (e.g. 'TV/Stanley H/Season 1/Stanley.H.Part.1.SUBBED.720p.WEB.h264-WEBTUBE.mkv')
    return every .srt that shares the basename stem.

    Catches Part.N / Episode_NN / 1x1 / arbitrary release naming because we
    match against the actual filename Sonarr says is on disk — no guessing."""
    if not file_canonical or not srt_paths:
        return []
    name = file_canonical.rsplit("/", 1)[-1]
    stem = name.rsplit(".", 1)[0].lower()  # strip extension
    return [p for p in srt_paths if p.rsplit("/", 1)[-1].lower().startswith(stem + ".")
            or p.rsplit("/", 1)[-1].lower() == stem + ".srt"]


def _langs_in_sidecars(sidecars: list[str]) -> set[str]:
    """Parse 2-letter language codes from sidecar filenames.

    Convention: '<basename>.<lang>.srt' (e.g. '...WEBTUBE.en.srt' → 'en').
    Plain '<basename>.srt' → 'und' (undefined; could be anything).
    Tool-suffix forms ('....en.alass.srt', '....en.ffsubsync.srt') are still
    parsed correctly because we grab the lang slot, not the last token."""
    import re
    # [2026-05-30] Split on BOTH dots AND hyphens. Release teams pack
    # the lang code into a hyphen-suffixed cluster ("…x264-iND-en.srt")
    # because they reuse a release-name template. The dot-only splitter
    # in v1.0 missed those because it saw the whole "x264-iND-en" as
    # one token (not alpha → skipped, fell through to "und"). Original
    # subarr handled both separators.
    KNOWN_TOOLS = {"alass", "autosubsync", "ffsubsync", "subsync"}
    # Also skip release-team tokens that look like 2-3 char alpha but
    # AREN'T language codes. Heuristic list — extend as we hit new ones.
    KNOWN_NON_LANG = {"web", "hdtv", "dvd", "uhd", "hdr", "sdr", "ddp",
                       "aac", "dts", "ac3", "flac", "x264", "x265",
                       "ind", "rep", "tla", "ntb", "syn"}

    langs: set[str] = set()
    for p in sidecars:
        name = p.rsplit("/", 1)[-1].lower()
        if not name.endswith(".srt"):
            continue
        # Strip .srt, then split on dots AND hyphens to extract every
        # potential lang-shaped token regardless of separator style.
        tokens = re.split(r"[.\-]", name[:-4])
        found_lang = None
        for tok in reversed(tokens):
            if 2 <= len(tok) <= 3 and tok.isalpha():
                if tok in KNOWN_TOOLS or tok in KNOWN_NON_LANG:
                    continue
                found_lang = tok
                break
        langs.add(found_lang or "und")
    return langs


def _stale_for_episode(
    *, sonarr_episode_id: int | None,
    ep_file_paths: dict[int, str],
    sonarr_eps_by_id: dict[int, dict],
    series_srt_paths: list[str],
    episode_number: str | None,
    missing_subs: list[str],
) -> tuple[bool, list[str]]:
    """Authoritative stale-disk check.

    1. Resolve sonarrEpisodeId → episodeFileId → episodeFile.path via the
       prefetched maps (no per-call network).
    2. Find every sidecar .srt that shares the file's basename stem.
    3. Stale ONLY if a sidecar matches a WANTED language — an English
       sidecar doesn't satisfy a Dutch wanted row.

    Falls back to S<NN>E<NN> substring match if Sonarr didn't give us a
    file path (episode not yet downloaded, or pre-Sonarr-v3 cluster)."""
    if sonarr_episode_id is None or not series_srt_paths:
        return _match_episode_srt_pattern(series_srt_paths, episode_number) if not missing_subs else (False, [])
    ep = sonarr_eps_by_id.get(sonarr_episode_id) or {}
    ep_file_id = ep.get("episodeFileId")
    abs_path = ep_file_paths.get(ep_file_id) if ep_file_id else None
    if not abs_path:
        # No file on disk yet → can't be stale; fall back to pattern only
        # if Bazarr's view might be wrong (rare).
        return _match_episode_srt_pattern(series_srt_paths, episode_number)
    file_canonical = _strip_arr_prefix(abs_path) or abs_path
    sidecars = _sidecars_for_file(series_srt_paths, file_canonical)
    if not sidecars:
        # [2026-05-30] Basename-stem match failed (common when the
        # video and its .srt come from different release groups —
        # e.g. THESYNDiCATE .mkv with iND-en .srt). Fall back to the
        # S<NN>E<NN> pattern match before declaring no sidecar — the
        # pattern is fuzzier but catches release mismatch. Original
        # subarr behaved this way; the v1.0 rewrite over-tightened.
        ep_pattern_hit, pattern_matches = _match_episode_srt_pattern(
            series_srt_paths, episode_number,
        )
        if not ep_pattern_hit:
            return False, []
        sidecars = pattern_matches
    langs_present = _langs_in_sidecars(sidecars)
    wanted_codes = {(c or "").lower()[:2] for c in (missing_subs or []) if c}
    # If no missing-subs language list (shouldn't happen for Bazarr wanted
    # rows but be defensive) → any sidecar counts as stale.
    if not wanted_codes:
        return True, sidecars
    # Stale only if at least one sidecar lang matches what's wanted.
    # 'und' (no lang tag) is ambiguous → don't count as stale unless it's
    # the only sidecar; safer to keep showing the gap.
    satisfying = wanted_codes.intersection(langs_present)
    if satisfying:
        return True, sidecars
    return False, sidecars


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
        # Strong negative — disk has a sub, Bazarr's view is stale. Flip
        # suggest_bazarr_rescan too so the UI can offer the →Bazarr action
        # (the existing /api/bazarr/sync-disk endpoint).
        s -= 5000
        reasons.append("stale: disk already has .srt (Bazarr needs scan-disk)")
        item.suggest_bazarr_rescan = True
    # v1.1 hotfix + 2026-05-27 SDH collapse: SDH counts the same as a
    # clean English track for scoring purposes (an SDH track IS English).
    # Forced + commentary tracks remain partial (those genuinely aren't
    # full subs for the show).
    if item.embedded_en in {"EN", "EN(SDH)"}:
        s -= 3000
        label = "full English" if item.embedded_en == "EN" else "English SDH"
        reasons.append(f"embedded: {label} sub in file (Bazarr missed it)")
        item.suggest_bazarr_rescan = True
    elif item.embedded_en in {"EN(forced)", "EN(commentary)"}:
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

    # Per-series srt index cache so 12 episodes of one show only walk the
    # filesystem once.
    series_srt_index: dict[str, list[str]] = {}

    # Authoritative file-path resolution via Sonarr (one episode + episodefile
    # call per series with wanted eps). Replaces fragile S<NN>E<NN> filename
    # pattern matching — catches releases that use Part.N / Episode_NN / other
    # non-canonical naming (Stanley H is the canary).
    wanted_series_ids = {w.get("sonarrSeriesId") for w in bz_eps if w.get("sonarrSeriesId")}
    sonarr_eps_by_id: dict[int, dict] = {}
    ep_file_paths: dict[int, str] = {}
    if wanted_series_ids and bundle.sonarr.is_configured():
        async def _fetch_series_files(sid: int):
            try:
                eps, files = await asyncio.gather(
                    bundle.sonarr.episodes(sid),
                    bundle.sonarr.episode_files_for_series(sid),
                    return_exceptions=False,
                )
                return sid, eps, files
            except IntegrationError as e:
                log.debug("sonarr episode lookup failed for series %s: %s", sid, e)
                return sid, [], []
        results = await asyncio.gather(*[_fetch_series_files(sid) for sid in wanted_series_ids])
        for sid, eps, files in results:
            for ep in eps:
                if isinstance(ep, dict) and "id" in ep:
                    sonarr_eps_by_id[ep["id"]] = ep
            for f in files:
                if isinstance(f, dict) and "id" in f and f.get("path"):
                    ep_file_paths[f["id"]] = f["path"]
        sources["sonarr_files"] = {"ok": True, "series": len(wanted_series_ids),
                                   "files": len(ep_file_paths)}

    # Episodes (Bazarr → Sonarr enrichment via sonarrSeriesId)
    for w in bz_eps:
        sonarr_id = w.get("sonarrSeriesId")
        s = sonarr_by_id.get(sonarr_id, {})
        canonical = _strip_arr_prefix(s.get("path"))
        if canonical and canonical not in series_srt_index:
            series_srt_index[canonical] = _scan_for_srt_recursive(canonical)
        srt_paths = series_srt_index.get(canonical or "", [])
        missing_codes = [ms.get("code2") or ms.get("name") or "?"
                         for ms in (w.get("missing_subtitles") or [])]
        has_srt, srts = _stale_for_episode(
            sonarr_episode_id=w.get("sonarrEpisodeId"),
            ep_file_paths=ep_file_paths,
            sonarr_eps_by_id=sonarr_eps_by_id,
            series_srt_paths=srt_paths,
            episode_number=w.get("episode_number"),
            missing_subs=missing_codes,
        )
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
