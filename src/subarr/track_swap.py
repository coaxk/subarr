"""#159: flip the default audio-track flag in-place via mkvpropedit.

mkvpropedit edits the Matroska header WITHOUT remuxing — instant, lossless, and
reversible (it only changes the default-track disposition flag). Matroska only;
callers gate on `.mkv` (mp4 default-track handling differs and isn't supported).

mkvpropedit exit codes: 0 = ok, 1 = ok-with-warnings, 2 = error.
"""

from __future__ import annotations

import asyncio
import logging
import shutil

log = logging.getLogger(__name__)


class TrackSwapError(RuntimeError):
    pass


class TrackSwapWriteError(TrackSwapError):
    """mkvpropedit couldn't write the file — typically a read-only media mount
    or missing write permission. Distinct so the router can surface an
    actionable, leak-free message instead of the generic sanitized one."""


def mkvpropedit_available() -> bool:
    return shutil.which("mkvpropedit") is not None


def build_mkvpropedit_args(fs_path: str, target_ordinal: int, audio_ordinals: list[int]) -> list[str]:
    """Build the mkvpropedit argv: set flag-default=1 on the target audio track
    and =0 on every other audio track, so exactly one audio track is default."""
    args = ["mkvpropedit", fs_path]
    for n in audio_ordinals:
        flag = "1" if n == target_ordinal else "0"
        args += ["--edit", f"track:a{n}", "--set", f"flag-default={flag}"]
    return args


async def swap_default_audio_track(
    fs_path: str,
    target_ordinal: int,
    audio_ordinals: list[int],
    timeout_s: float = 30.0,
) -> None:
    """Make `target_ordinal` (1-based audio track) the sole default audio track
    of `fs_path` in place. Raises TrackSwapError on missing tool, bad input,
    timeout, or a hard mkvpropedit error (exit ≥ 2)."""
    if not mkvpropedit_available():
        raise TrackSwapError("mkvpropedit not found — install mkvtoolnix in the image")
    if target_ordinal not in audio_ordinals:
        raise TrackSwapError(f"target audio ordinal {target_ordinal} not in {audio_ordinals}")
    args = build_mkvpropedit_args(fs_path, target_ordinal, audio_ordinals)
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise TrackSwapError("mkvpropedit timed out")
    # 0 = ok, 1 = warnings (still applied), ≥2 = error.
    if proc.returncode and proc.returncode >= 2:
        detail = (stderr.decode(errors="replace") or stdout.decode(errors="replace"))[:300]
        low = detail.lower()
        if "writing" in low or "write-protected" in low or "read-only" in low or "permission" in low:
            raise TrackSwapWriteError(f"mkvpropedit could not write the file: {detail}")
        raise TrackSwapError(f"mkvpropedit exit {proc.returncode}: {detail}")
    if proc.returncode == 1:
        log.warning(
            "mkvpropedit applied with warnings on %s: %s",
            fs_path,
            stderr.decode(errors="replace")[:200],
        )
