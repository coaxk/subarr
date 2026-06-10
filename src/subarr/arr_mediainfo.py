"""v1.1-A: Hydrate the probe_store from Sonarr/Radarr's pre-computed mediaInfo.

The "obvious in retrospect" win: Sonarr/Radarr already run mediainfo on
every file at import time and surface the result on /api/v3/episodefile
+ /api/v3/moviefile. Every row's `mediaInfo` block contains:

    audioLanguages: 'eng'                              (slash-delimited)
    subtitles:      'eng/eng/eng/ara/cze/dan/...'      (slash-delimited)
    videoCodec:     'h265'
    runTime:        '1:01:39'                          (HH:MM:SS)

For a 1500-series library, that's tens of thousands of files arr has
already mediainfo'd — and v1.0 was running ffprobe on every one of them.

This module:
1. Pulls every file with non-null mediaInfo from Sonarr + Radarr (bulk
   calls; no per-file fan-out).
2. Parses arr's slash-delimited subtitle/audio language strings into our
   AudioStream/SubtitleStream shape.
3. Stats the file (once) for the (mtime, size) cache key. Skips upsert
   if the path doesn't resolve on subarr's mount (rare; usually means a
   pending download or a stale arr record).
4. Upserts with source='arr_mediainfo' — the upsert layer refuses to
   overwrite richer ffprobe data, so this is always safe to re-run.

ffprobe stays the gold standard: where it runs, its per-track default/
forced/hi flags + codec details beat arr's flattened language list. But
on a cold install or a freshly added library, arr_mediainfo gets us 80%
of the way there with one HTTP round-trip per integration instead of
thousands of subprocess.exec() calls.

Telemetry: ProbeStore.count_by_source() reports the split so the UI can
celebrate "12,847 from Sonarr · 142 from ffprobe · 23,189 ffprobe-walks
skipped this scan."
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from typing import Any

from pathlib import Path

from .config import settings
from .coverage_engine import IntegrationBundle
from .paths import strip_arr_prefix
from .media_probe import AudioStream, ProbeResult, SubtitleStream
from .probe_store import ProbeStore

log = logging.getLogger(__name__)


# Arr stores languages as slash-delimited 3-letter ISO 639-2/B (eng/ara/cze).
# Our schema accepts that as-is — coverage_engine.py's ENGLISH_TAGS already
# matches both 'en' and 'eng' so no remapping needed.
def _split_arr_langs(s: str | None) -> list[str]:
    if not s:
        return []
    return [tok.strip().lower() for tok in s.split("/") if tok.strip()]


# "1:01:39" / "57:12" / "92" → seconds. Sonarr/Radarr aren't perfectly
# consistent — some rows give HH:MM:SS, some just minutes. Best effort,
# never raises (returns None on parse failure).
_RUNTIME_RE = re.compile(r"^(\d+):(\d{2}):(\d{2})$|^(\d+):(\d{2})$|^(\d+)(?:\.\d+)?$")


def _parse_runtime_to_seconds(rt: Any) -> float | None:
    if rt is None:
        return None
    if isinstance(rt, (int, float)):
        # Sonarr's `runtime` (episode-level) is in minutes; mediaInfo's
        # runTime field is HH:MM:SS string. We only handle the string case
        # here. Numeric got = caller mistake.
        return float(rt) * 60.0
    if not isinstance(rt, str):
        return None
    m = _RUNTIME_RE.match(rt.strip())
    if not m:
        return None
    if m.group(1):
        h, mi, s = int(m.group(1)), int(m.group(2)), int(m.group(3))
        return h * 3600 + mi * 60 + s
    if m.group(4):
        mi, s = int(m.group(4)), int(m.group(5))
        return mi * 60 + s
    return float(m.group(6))


@dataclass
class ArrMediainfoSync:
    """Pull arr's mediaInfo and hydrate probe_store. One-shot or
    scheduler-driven. Returns a summary dict for the API + telemetry."""

    bundle: IntegrationBundle
    probe_store: ProbeStore

    async def run(self) -> dict[str, Any]:
        started = time.time()
        summary = {
            "sonarr": {
                "queried": 0,
                "upserted": 0,
                "skipped_no_path": 0,
                "skipped_no_mediainfo": 0,
                "skipped_ffprobe_won": 0,
            },
            "radarr": {
                "queried": 0,
                "upserted": 0,
                "skipped_no_path": 0,
                "skipped_no_mediainfo": 0,
                "skipped_ffprobe_won": 0,
            },
            "duration_s": 0.0,
        }

        if self.bundle.sonarr.is_configured():
            await self._sync_sonarr(summary["sonarr"])
        if self.bundle.radarr.is_configured():
            await self._sync_radarr(summary["radarr"])

        summary["duration_s"] = round(time.time() - started, 2)
        summary["counts_by_source"] = self.probe_store.count_by_source()
        log.info(
            "arr_mediainfo sync done in %.2fs: sonarr=%d/%d radarr=%d/%d",
            summary["duration_s"],
            summary["sonarr"]["upserted"],
            summary["sonarr"]["queried"],
            summary["radarr"]["upserted"],
            summary["radarr"]["queried"],
        )
        return summary

    async def _sync_sonarr(self, stats: dict[str, int]) -> None:
        rows = await self.bundle.sonarr.all_episode_files_with_mediainfo()
        stats["queried"] = len(rows)
        for row in rows:
            self._handle_row(row, stats)

    async def _sync_radarr(self, stats: dict[str, int]) -> None:
        rows = await self.bundle.radarr.all_movie_files_with_mediainfo()
        stats["queried"] = len(rows)
        for row in rows:
            self._handle_row(row, stats)

    def _handle_row(self, row: dict[str, Any], stats: dict[str, int]) -> None:
        path = row.get("path")
        mi = row.get("mediaInfo") or {}
        if not path:
            stats["skipped_no_path"] += 1
            return
        if not mi.get("audioLanguages") and not mi.get("subtitles"):
            # No usable mediaInfo block — skip; ffprobe path will pick it up.
            stats["skipped_no_mediainfo"] += 1
            return

        # Translate arr's container-view path → subarr's canonical form.
        canonical = strip_arr_prefix(path)
        if not canonical:
            stats["skipped_no_path"] += 1
            return

        # Cache-key inputs: stat the file IF visible on our mount; if not,
        # use arr's `size` + 0.0 mtime fallback so we still surface the data
        # (won't auto-invalidate when the file changes — next ffprobe walk
        # will, which is the source-of-truth path anyway).
        subarr_path = settings.media_root / Path(canonical)
        try:
            st = subarr_path.stat()
            mtime = st.st_mtime
            size = st.st_size
        except OSError:
            mtime = 0.0
            size = int(row.get("size") or 0)

        result = _arr_row_to_probe_result(row, canonical)

        # upsert with source='arr_mediainfo' — refuses to clobber ffprobe.
        before = self.probe_store.get(canonical)
        self.probe_store.upsert(
            canonical_path=canonical,
            mtime=mtime,
            size=size,
            result=result,
            source="arr_mediainfo",
        )
        if before is not None:
            # might have been a no-op if previous source was ffprobe
            after = self.probe_store.get(canonical)
            if after and after.audio == before.audio and after.subtitles == before.subtitles:
                stats["skipped_ffprobe_won"] += 1
                return
        stats["upserted"] += 1


def _arr_row_to_probe_result(row: dict[str, Any], canonical: str) -> ProbeResult:
    """Translate one arr episodefile/moviefile row → ProbeResult shape.

    Arr only gives us flattened language lists, not per-track default/forced/
    sdh flags. So we synthesize one stream per language with sensible
    defaults: index = position, default = (i == 0). All other flags = False
    (ffprobe will replace these later if it runs)."""
    mi = row.get("mediaInfo") or {}
    audio_langs = _split_arr_langs(mi.get("audioLanguages"))
    sub_langs = _split_arr_langs(mi.get("subtitles"))
    duration_s = _parse_runtime_to_seconds(mi.get("runTime"))

    audio = [
        AudioStream(
            index=i,
            language=lang,
            codec=None,
            title=None,
            default=(i == 0),
        )
        for i, lang in enumerate(audio_langs)
    ]
    subs = [
        SubtitleStream(
            index=i,
            language=lang,
            codec=None,
            title=None,
            default=False,
            forced=False,
            sdh=False,
            commentary=False,
        )
        for i, lang in enumerate(sub_langs)
    ]
    r = ProbeResult(canonical_path=canonical)
    r.audio = audio
    r.subtitles = subs
    r.duration_s = duration_s
    return r
