#!/usr/bin/env python3
"""Phase 2 corpus builder (#171): pick a density-stratified clip set from the
real media library and extract one fixed-length clip per pick with ffmpeg.

  python scripts/phase2_corpus.py --dry-run --per-band 2   # inspect the pick, write nothing
  python scripts/phase2_corpus.py --per-band 5             # extract the real corpus

Thin IO over ``subarr.study.corpus`` (band_for_cues_per_minute, stratify) and
``subarr.subtitle_readability.parse_srt`` -- this file owns the filesystem
walk, the ffprobe/ffmpeg calls, and the manifest; the density banding and
quota picking stay pure and unit-tested in src/subarr/study/corpus.py.

Clips are extracted ONCE here and reused across all three study arms (see
manifest.json, and re-runs skip clips already present unless --force).
Re-extracting per arm would let ffmpeg re-encode nondeterminism leak into a
comparison whose entire purpose is isolating the segmenter.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from subarr.study.corpus import (
    DensityBand,
    band_for_cues_per_minute,
    stratify_eligible,
)
from subarr.subtitle_readability import parse_srt

VIDEO_EXTENSIONS = {".mkv", ".mp4", ".avi", ".m4v", ".ts", ".webm", ".mov"}

DEFAULT_ROOT = "/mnt/nas/Media"
DEFAULT_OUT = "study/clips"
DEFAULT_CLIP_SECONDS = 120.0
DEFAULT_OFFSET_SECONDS = 300.0  # 5 min in -- opening credits are not dialogue
MIN_SPAN_MINUTES = 1.0  # below this a cues-per-minute estimate is noise, not signal


@dataclass(frozen=True)
class Candidate:
    video: Path
    srt: Path
    cues_per_minute: float


def verify_nas(root: Path) -> None:
    """Fail loudly, before any walk, if the share looks mounted but is not
    actually serving files.

    ``ls`` alone is not proof: the mountpoint directory lives on WSL's own
    disk and can look fully populated while a failed CIFS automount serves
    nothing underneath it. Check the mount table AND do a real file read.
    """
    root_str = str(root).replace("\\", "/")
    if root_str.startswith("/mnt/nas"):
        try:
            mounts = subprocess.run(["mount"], capture_output=True, text=True, check=False).stdout
        except FileNotFoundError:
            mounts = ""
        if "/mnt/nas" not in mounts:
            proc_mounts = Path("/proc/mounts")
            if proc_mounts.exists() and "/mnt/nas" in proc_mounts.read_text(
                encoding="utf-8", errors="ignore"
            ):
                pass
            else:
                print(
                    "FATAL: /mnt/nas does not appear in the mount table -- the CIFS "
                    "automount looks failed (this is the nofail trap: it can sit "
                    "broken indefinitely after a NAS outage). "
                    "Try: sudo systemctl restart mnt-nas.automount",
                    file=sys.stderr,
                )
                raise SystemExit(1)

    if not root.is_dir():
        print(f"FATAL: library root {root} does not exist or is not a directory.", file=sys.stderr)
        raise SystemExit(1)

    probe = _find_any_file(root)
    if probe is None:
        print(
            f"FATAL: {root} has no files within the first few directories -- "
            "the share looks mounted but is not serving anything real.",
            file=sys.stderr,
        )
        raise SystemExit(1)
    try:
        with open(probe, "rb") as fh:
            fh.read(4096)
    except OSError as exc:
        print(f"FATAL: real file read failed for {probe}: {exc}", file=sys.stderr)
        raise SystemExit(1)


def _find_any_file(root: Path, *, max_dirs: int = 200) -> Path | None:
    seen_dirs = 0
    for dirpath, _dirnames, filenames in os.walk(root):
        seen_dirs += 1
        if seen_dirs > max_dirs:
            break
        if filenames:
            return Path(dirpath) / filenames[0]
    return None


def _cues_per_minute(srt_path: Path) -> float | None:
    try:
        text = srt_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None
    cues = parse_srt(text)
    if len(cues) < 2:
        return None
    span_minutes = (cues[-1].end_ms - cues[0].start_ms) / 1000.0 / 60.0
    if span_minutes < MIN_SPAN_MINUTES:
        return None
    return len(cues) / span_minutes


def find_candidates(root: Path) -> list[Candidate]:
    """Video files that have a sibling .srt in the same directory.

    "Sibling" means the subtitle's name starts with the video's stem, so both
    ``Movie.srt`` and language-tagged ``Movie.en.srt`` match ``Movie.mkv``
    without accidentally pairing across two different videos in one folder.
    """
    candidates: list[Candidate] = []
    for dirpath, _dirnames, filenames in os.walk(root):
        srt_files = [f for f in filenames if f.lower().endswith(".srt")]
        if not srt_files:
            continue
        video_files = [f for f in filenames if Path(f).suffix.lower() in VIDEO_EXTENSIONS]
        for video_name in video_files:
            stem = Path(video_name).stem
            sibling = next((s for s in srt_files if s.startswith(stem)), None)
            if sibling is None:
                continue
            srt_path = Path(dirpath) / sibling
            cpm = _cues_per_minute(srt_path)
            if cpm is None:
                continue
            candidates.append(Candidate(video=Path(dirpath) / video_name, srt=srt_path, cues_per_minute=cpm))
    return candidates


def probe_duration_seconds(video: Path) -> float | None:
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(video),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return None
    try:
        return float(result.stdout.strip())
    except ValueError:
        return None


def extract_clip(video: Path, dest: Path, *, offset: float, duration: float) -> tuple[float, float] | None:
    """Extract ``duration`` seconds starting at ``offset`` with ffmpeg.

    Returns the (offset, duration) actually used, clamped so the clip fits
    inside the file, or None if the file is too short to hold a clip of this
    length at all. Stream copy (no re-encode) for speed and to avoid
    introducing a second source of nondeterminism beyond the seek itself.
    """
    total = probe_duration_seconds(video)
    used_offset = offset
    if total is not None:
        if total < duration:
            return None
        used_offset = min(offset, max(0.0, total - duration))
    dest.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg",
        "-y",
        "-ss",
        f"{used_offset:.3f}",
        "-i",
        str(video),
        "-t",
        f"{duration:.3f}",
        "-c",
        "copy",
        str(dest),
    ]
    subprocess.run(cmd, check=True, capture_output=True, text=True)
    return (used_offset, duration)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=DEFAULT_ROOT, help=f"library root to walk (default {DEFAULT_ROOT})")
    ap.add_argument(
        "--out", default=DEFAULT_OUT, help=f"output dir for clips + manifest.json (default {DEFAULT_OUT})"
    )
    ap.add_argument("--per-band", type=int, default=5, help="max picks per density band (default 5)")
    ap.add_argument(
        "--clip-seconds", type=float, default=DEFAULT_CLIP_SECONDS, help="clip length (default 120)"
    )
    ap.add_argument(
        "--offset-seconds", type=float, default=DEFAULT_OFFSET_SECONDS, help="clip start offset (default 300)"
    )
    ap.add_argument(
        "--dry-run", action="store_true", help="walk, measure and pick only -- no ffmpeg, no writes"
    )
    ap.add_argument("--force", action="store_true", help="re-extract clips already present in the manifest")
    args = ap.parse_args()

    root = Path(args.root)
    out_dir = Path(args.out)

    verify_nas(root)

    print(f"walking {root} ...")
    candidates = find_candidates(root)
    print(f"found {len(candidates)} candidate(s) with a sibling .srt and a usable span")

    counts: dict[DensityBand, int] = {}
    for c in candidates:
        b = band_for_cues_per_minute(c.cues_per_minute)
        counts[b] = counts.get(b, 0) + 1
    print("band distribution (all candidates):")
    for band in DensityBand:
        print(f"  {band.value:11s} {counts.get(band, 0)}")

    items = [(str(c.video), c.cues_per_minute) for c in candidates]
    by_video = {str(c.video): c for c in candidates}

    # Length-check at PICK time, not at extraction time. Picking blind and
    # letting ffmpeg refuse leaves the band silently under quota -- 5 of the 14
    # very_dense candidates turned out to be 33-second sample files posing as
    # episodes, which took that band to 9 of 14 without anything failing loudly.
    needed = args.offset_seconds + args.clip_seconds
    rejected: list[tuple[str, float]] = []

    def long_enough(video: str) -> bool:
        dur = probe_duration_seconds(Path(video))
        if dur is None or dur < needed:
            rejected.append((video, dur or 0.0))
            return False
        return True

    picked_keys = stratify_eligible(items, per_band=args.per_band, eligible=long_enough)
    picks = [by_video[video] for video, _ in picked_keys]

    if rejected:
        print(
            f"\nrejected {len(rejected)} candidate(s) too short for a "
            f"{args.clip_seconds:.0f}s clip at offset {args.offset_seconds:.0f}s:"
        )
        for video, dur in rejected:
            print(f"  {dur:8.1f}s  {Path(video).name}")

    print(f"\npicked {len(picks)} clip(s):")
    for c in picks:
        band = band_for_cues_per_minute(c.cues_per_minute)
        print(f"  [{band.value:11s}] {c.cues_per_minute:6.2f} cpm  {c.video}")

    if args.dry_run:
        print("\n--dry-run: no ffmpeg invoked, nothing written.")
        return 0

    if not picks:
        print("\nnothing picked -- nothing to extract.", file=sys.stderr)
        return 1

    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = out_dir / "manifest.json"
    manifest: list[dict] = []
    if manifest_path.exists() and not args.force:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    already = {entry["source_video"] for entry in manifest}

    for i, c in enumerate(picks):
        if str(c.video) in already and not args.force:
            print(f"skip (already extracted): {c.video}")
            continue
        band = band_for_cues_per_minute(c.cues_per_minute)
        dest = out_dir / f"{band.value}_{i:02d}_{c.video.stem}{c.video.suffix}"
        try:
            result = extract_clip(c.video, dest, offset=args.offset_seconds, duration=args.clip_seconds)
        except subprocess.CalledProcessError as exc:
            print(f"FAILED (ffmpeg) {c.video}: {exc.stderr}", file=sys.stderr)
            continue
        if result is None:
            print(f"skip (file shorter than clip length): {c.video}", file=sys.stderr)
            continue
        used_offset, used_duration = result
        manifest = [e for e in manifest if e["source_video"] != str(c.video)]
        manifest.append(
            {
                "source_video": str(c.video),
                "source_srt": str(c.srt),
                "band": band.value,
                "cues_per_minute": round(c.cues_per_minute, 3),
                "offset_seconds": used_offset,
                "duration_seconds": used_duration,
                "clip_path": str(dest),
            }
        )
        print(f"extracted: {dest}")

    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"\nmanifest written: {manifest_path} ({len(manifest)} clip(s))")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
