"""GET /api/browse?path=<canonical> — lazy folder + file tree.

Folders are returned first (sorted by name), then video files (sorted by
name) as leaf entries the user can individually select for scanning.
Subgen's /batch handler accepts both directories and single files (the
v4.1-patched transcribe_existing branch handles `os.path.isfile(path)`),
so a leaf-file checkbox queues just that one file.

Per-entry data builds the Library "source of truth" view: status dot,
audio languages, embedded sub languages, sibling .srt languages — at a
glance you know which episodes need work. Folder rollups make it easy
to see coverage state at the show/season level (red/yellow/green).
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from ..media_probe import audio_lang_summary, english_track_summary
from ..paths import VIDEO_EXTS, PathOutsideRootError, canonical_to_fs, fs_to_canonical

router = APIRouter(prefix="/api", tags=["browse"])


# Heuristic non-language tokens we should NOT mistake for ISO codes when
# parsing srt filenames. Matches coverage_engine._langs_in_sidecars.
_KNOWN_TOOLS = {"alass", "autosubsync", "ffsubsync", "subsync"}
_KNOWN_NON_LANG = {
    "web", "hdtv", "dvd", "uhd", "hdr", "sdr", "ddp", "aac", "dts", "ac3",
    "flac", "x264", "x265", "ind", "rep", "tla", "ntb", "syn",
}


def _lang_from_srt_name(name: str) -> str:
    """Parse 2-3 char language code from a sidecar .srt filename. Returns
    'und' when no recognisable token. Mirrors coverage_engine logic so
    Library + Coverage agree on what each .srt looks like."""
    lower = name.lower()
    if lower.endswith(".srt"):
        lower = lower[:-4]
    tokens = re.split(r"[.\-]", lower)
    for tok in reversed(tokens):
        if 2 <= len(tok) <= 3 and tok.isalpha():
            if tok in _KNOWN_TOOLS or tok in _KNOWN_NON_LANG:
                continue
            return tok
    return "und"


# Languages that count as "English coverage" for the status indicator.
_EN_LIKE = {"en", "eng"}


class BrowseEntry(BaseModel):
    name: str
    path: str  # canonical (forward slashes, no leading /, relative to media_root)
    is_dir: bool

    # Folder rollups (zero for files). Walked recursively when ?rollup=true
    # is set; cheap top-level scan otherwise.
    video_count: int = 0
    srt_count: int = 0
    videos_with_en: int = 0  # videos with any source of English coverage (disk srt OR embedded)
    videos_probed: int = 0   # videos that have a probe cache entry
    coverage_status: str | None = None  # 'full' | 'partial' | 'none' | 'unknown'

    # File-only fields (defaults for dirs):
    has_sibling_srt: bool = False
    embedded_en: str | None = None
    audio_langs: list[str] = []      # ['fre', 'eng']
    sub_langs: list[str] = []        # langs from sibling .srt files
    srt_filenames: list[str] = []    # actual sidecar filenames (not full path)
    duration_s: float | None = None
    size_mb: float | None = None
    file_status: str | None = None   # 'covered' | 'embedded-only' | 'srt-only' | 'missing' | 'unknown'


class BrowseResponse(BaseModel):
    path: str
    parent: str | None
    entries: list[BrowseEntry]


def _classify_file_status(has_en_srt: bool, embedded_en: str | None, probed: bool) -> str:
    """One-word status for the UI status dot.

    covered      → has both a disk .srt AND embedded EN track
    srt-only     → disk .srt exists, no English embedded track
    embedded-only→ embedded EN (full / SDH) but no disk .srt
    missing      → no English from any source AND the file has been probed
    unknown      → file hasn't been probed yet (can't say either way)
    """
    embedded = embedded_en in {"EN", "EN(SDH)"}
    if has_en_srt and embedded:
        return "covered"
    if has_en_srt:
        return "srt-only"
    if embedded:
        return "embedded-only"
    if not probed:
        return "unknown"
    return "missing"


def _classify_folder_status(videos_with_en: int, total_videos: int, probed_count: int) -> str:
    """Folder rollup status for the traffic-light indicator.

    full    → every video has English coverage (from any source)
    partial → some have coverage, some don't
    none    → no videos have English coverage AND we've probed them all
    unknown → empty dir, or we don't have probe data for most files
    """
    if total_videos == 0:
        return "unknown"
    if probed_count < total_videos / 2:
        # Less than half the videos have been probed — can't say for sure.
        # User should run a probe walk over this branch.
        return "unknown"
    if videos_with_en == total_videos:
        return "full"
    if videos_with_en == 0:
        return "none"
    return "partial"


_EP_PATTERN = re.compile(r"s(\d{1,2})e(\d{1,2})", re.IGNORECASE)


def _episode_pattern(name: str) -> str | None:
    """Extract normalised S<NN>E<NN> token from a filename if present."""
    m = _EP_PATTERN.search(name.lower())
    if not m:
        return None
    return f"s{int(m.group(1)):02d}e{int(m.group(2)):02d}"


def _sibling_srts_by_video(all_children: list[Path]) -> dict[str, list[Path]]:
    """Return {video_stem (lowercase) → [.srt sibling Path]}.

    Match strategy (mirrors coverage_engine._stale_for_episode):
    1. Basename-stem prefix: srt stem starts with video stem + '.' or
       equals it. Catches well-named releases.
    2. Fallback — episode pattern: video and srt share the same
       S<NN>E<NN> token. Catches release-name mismatch (video from
       THESYNDiCATE, srt from iND, both for S06E03).

    Without the fallback, mismatched-release pairs (very common in
    real libraries) show as 'missing' even though the srt is sitting
    right next to the video.
    """
    videos = [c for c in all_children if c.is_file() and c.suffix.lower() in VIDEO_EXTS]
    srts = [c for c in all_children if c.is_file() and c.suffix.lower() == ".srt"]
    out: dict[str, list[Path]] = {}
    for v in videos:
        v_stem = v.stem.lower()
        v_ep = _episode_pattern(v.name)
        matching: list[Path] = []
        for s in srts:
            s_stem = s.stem.lower()
            # Primary: basename stem prefix.
            if s_stem == v_stem or s_stem.startswith(v_stem + "."):
                matching.append(s)
                continue
            # Fallback: same S<NN>E<NN> token.
            if v_ep and _episode_pattern(s.name) == v_ep:
                matching.append(s)
        out[v_stem] = matching
    return out


def _walk_for_rollup(folder: Path, probe_store, max_videos: int = 500) -> dict[str, Any]:
    """Recursive walk to compute folder rollup. Returns
    {total_videos, videos_with_en, videos_probed, srt_count, bail}.

    Uses os.walk (single syscall per dir) and buckets video + srt
    siblings together in one pass. The old implementation re-iter'd
    each video's parent directory looking for srts which made N²
    behaviour on big trees — 43s on TV/. New version: ~1s.

    Bounded by max_videos to keep the BROWSE endpoint snappy — when
    cap is hit we bail with bail=True and downstream forces 'unknown'.
    """
    import os
    total_videos = videos_with_en = videos_probed = srt_count = 0
    bail = False
    try:
        for dirpath, _dirnames, filenames in os.walk(folder):
            # Bucket videos + srts in this directory in one pass.
            videos_here: list[str] = []
            srts_here: list[str] = []
            for name in filenames:
                if name.startswith("."):
                    continue
                lower = name.lower()
                if lower.endswith(".srt"):
                    srts_here.append(name)
                else:
                    # Cheap ext check — VIDEO_EXTS is a set.
                    dot = lower.rfind(".")
                    if dot >= 0 and lower[dot:] in VIDEO_EXTS:
                        videos_here.append(name)

            srt_count += len(srts_here)

            # Pre-compute per-srt lang + episode pattern once per dir.
            srt_meta = [
                (n, n.lower().rsplit(".", 1)[0], _episode_pattern(n), _lang_from_srt_name(n))
                for n in srts_here
            ]

            for vname in videos_here:
                total_videos += 1
                if total_videos > max_videos:
                    bail = True
                    break

                v_stem = vname.lower().rsplit(".", 1)[0]
                v_ep = _episode_pattern(vname)
                has_en_srt = False
                for (_sname, s_stem, s_ep, s_lang) in srt_meta:
                    matched = (
                        s_stem == v_stem
                        or s_stem.startswith(v_stem + ".")
                        or (v_ep and s_ep == v_ep)
                    )
                    if matched and s_lang in _EN_LIKE:
                        has_en_srt = True
                        break

                embedded_en = None
                if probe_store is not None:
                    vpath = Path(dirpath) / vname
                    try:
                        st = vpath.stat()
                        cached = probe_store.get(
                            fs_to_canonical(vpath), mtime=st.st_mtime, size=st.st_size,
                        )
                        if cached is not None:
                            videos_probed += 1
                            embedded_en = english_track_summary(cached)
                    except OSError:
                        pass

                if has_en_srt or embedded_en in {"EN", "EN(SDH)"}:
                    videos_with_en += 1

            if bail:
                break
    except (PermissionError, OSError):
        pass

    return {
        "total_videos": total_videos,
        "videos_with_en": videos_with_en,
        "videos_probed": videos_probed,
        "srt_count": srt_count,
        "bail": bail,
    }


@router.get("/browse", response_model=BrowseResponse)
def browse(
    request: Request,
    path: str = Query("", description="Canonical path relative to media root"),
    rollup: bool = Query(
        True,
        description="Recurse into subdirs to compute coverage rollups for the "
                    "traffic-light dot. Set false for fastest browse-only mode.",
    ),
) -> BrowseResponse:
    try:
        target = canonical_to_fs(path)
    except PathOutsideRootError:
        raise HTTPException(400, detail=f"path escapes media root: {path!r}")

    if not target.exists():
        raise HTTPException(404, detail=f"not found: {path!r}")
    if not target.is_dir():
        raise HTTPException(400, detail=f"not a directory: {path!r}")

    try:
        all_children = list(target.iterdir())
    except PermissionError:
        raise HTTPException(403, detail=f"permission denied: {path!r}")

    # Bucket children. Skip dotfiles.
    dirs: list[Path] = []
    files: list[Path] = []
    for child in all_children:
        if child.name.startswith("."):
            continue
        if child.is_dir():
            dirs.append(child)
        elif child.is_file() and child.suffix.lower() in VIDEO_EXTS:
            files.append(child)
    dirs.sort(key=lambda p: p.name.lower())
    files.sort(key=lambda p: p.name.lower())

    probe_store = getattr(request.app.state, "probe_store", None)

    # Map every video file in this dir to its sibling .srt list (cheap one
    # iterdir per dir — we just did the listdir above so this reuses it).
    sib_srts_by_stem = _sibling_srts_by_video(all_children)

    # Auto-disable rollup when there are too many sibling dirs (e.g.
    # the TV root with 1700 shows). 1700 × rollup walks would block
    # the response for minutes. User drills in one more level — each
    # show has 1-10 seasons which rollup fine. Tunable; 30 chosen as
    # the boundary between "season list" and "show list".
    rollup_effective = rollup and len(dirs) <= 30

    entries: list[BrowseEntry] = []

    # Folder entries with optional recursive rollup.
    # Skip shallow _count_media too when there are too many siblings —
    # 1700 iterdir() calls is also slow. The "Refresh tree" button can
    # force a deeper recount per-entry later if needed.
    for d in dirs:
        if rollup_effective:
            video_count, srt_count = _count_media(d)
        else:
            video_count = srt_count = 0
        e = BrowseEntry(
            name=d.name,
            path=fs_to_canonical(d),
            is_dir=True,
            video_count=video_count,
            srt_count=srt_count,
        )
        if rollup_effective:
            r = _walk_for_rollup(d, probe_store)
            e.video_count = r["total_videos"]
            e.srt_count = r["srt_count"]
            e.videos_with_en = r["videos_with_en"]
            e.videos_probed = r["videos_probed"]
            if r["bail"]:
                # Hit the per-request video cap — too big to rollup synchronously.
                # Force 'unknown' so the UI shows the muted dot + user drills in.
                e.coverage_status = "unknown"
            else:
                e.coverage_status = _classify_folder_status(
                    r["videos_with_en"], r["total_videos"], r["videos_probed"],
                )
        entries.append(e)

    # File entries with full probe data.
    for f in files:
        try:
            st = f.stat()
            size_mb = round(st.st_size / (1024 * 1024), 1)
        except OSError:
            st = None
            size_mb = None
        canonical = fs_to_canonical(f)

        sibs = sib_srts_by_stem.get(f.stem.lower(), [])
        srt_filenames = [s.name for s in sibs]
        sub_langs: list[str] = []
        for s in sibs:
            lang = _lang_from_srt_name(s.name)
            if lang not in sub_langs:
                sub_langs.append(lang)
        has_en_srt = any(l in _EN_LIKE for l in sub_langs)

        embedded = None
        audio_langs: list[str] = []
        duration_s = None
        probed = False
        if probe_store is not None and st is not None:
            cached = probe_store.get(canonical, mtime=st.st_mtime, size=st.st_size)
            if cached is not None:
                probed = True
                embedded = english_track_summary(cached)
                audio_langs = audio_lang_summary(cached)
                duration_s = cached.duration_s

        entries.append(BrowseEntry(
            name=f.name,
            path=canonical,
            is_dir=False,
            has_sibling_srt=bool(sibs),
            embedded_en=embedded,
            audio_langs=audio_langs,
            sub_langs=sub_langs,
            srt_filenames=srt_filenames,
            duration_s=duration_s,
            size_mb=size_mb,
            file_status=_classify_file_status(has_en_srt, embedded, probed),
        ))

    canonical = path.strip().strip("/")
    parent = None
    if canonical:
        parent = "/".join(canonical.split("/")[:-1])

    return BrowseResponse(path=canonical, parent=parent, entries=entries)


def _count_media(folder: Path) -> tuple[int, int]:
    """Shallow count of video + srt files directly in `folder`. Cheap; lazy load."""
    video = srt = 0
    try:
        for entry in folder.iterdir():
            if not entry.is_file():
                continue
            ext = entry.suffix.lower()
            if ext in VIDEO_EXTS:
                video += 1
            elif ext == ".srt":
                srt += 1
    except (PermissionError, OSError):
        return 0, 0
    return video, srt
