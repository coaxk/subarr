#!/usr/bin/env python3
"""Phase 2 arm runner (#171): transcribe the clip corpus with one subgen build.

  python scripts/phase2_run_arm.py --arm arm3-run1 --url http://localhost:9008

Posts each corpus audio file to POST /asr and saves the returned SRT under
``study/out/<arm>/``. One arm per invocation; the caller decides which build is
listening on ``--url``.

Resumable: a clip whose SRT already exists is skipped, so a long run that dies
partway can be restarted without redoing GPU work.

⚠️ Every request pins task and output explicitly rather than relying on the
server's env defaults. The shipped default is TRANSCRIBE_OR_TRANSLATE=translate,
and a study where one arm translated and another did not would be comparing
two different jobs while appearing to compare two segmenters.

⚠️ Language is deliberately NOT forced. Auto-detect variance is part of the
run-to-run noise the calibration exists to measure, and forcing a language
would hide it rather than remove it.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import httpx

DEFAULT_AUDIO = "study/audio"
DEFAULT_OUT = "study/out"


def server_config(client: httpx.Client, url: str) -> dict:
    """Record what the server actually is, so the report can prove the arms
    differed only where intended."""
    r = client.get(f"{url}/queue", timeout=30)
    r.raise_for_status()
    q = r.json()
    return {
        "version": q.get("version"),
        "model": q.get("model"),
        "compute_type": q.get("compute_type"),
        "patch_rev": q.get("subarr_subgen_patch_rev"),
        "release_tag": q.get("subarr_subgen_release_tag"),
        "capabilities": q.get("capabilities", {}),
    }


class NotAnSrt(RuntimeError):
    """The server answered, but with something that is not a subtitle file."""


def transcribe(client: httpx.Client, url: str, audio: Path, *, task: str) -> str:
    with open(audio, "rb") as fh:
        r = client.post(
            f"{url}/asr",
            params={"task": task, "output": "srt"},
            files={"audio_file": (audio.name, fh, "audio/wav")},
            timeout=3600,
        )
    r.raise_for_status()
    body = r.text

    # ⚠️ A 200 is not proof of a transcription. This endpoint answers a bad
    # request with HTTP 200 and a JSON error body -- `{"status":"error",
    # "message":"provide either ?path= or an audio_file upload"}` -- so
    # raise_for_status() sails straight past it. Left unchecked, a 36-clip run
    # completes "successfully" with 36 error files, and the metrics then score
    # zero cues as 0.0% over 25 CPS: a beautifully clean, entirely fake result.
    stripped = body.lstrip()
    if not stripped:
        raise NotAnSrt("empty response body")
    if stripped.startswith("{"):
        try:
            detail = json.loads(stripped).get("message", stripped[:200])
        except ValueError:
            detail = stripped[:200]
        raise NotAnSrt(f"server returned JSON, not SRT: {detail}")
    if "-->" not in body:
        raise NotAnSrt(f"no cue timings in a {len(body)}-byte response")
    return body


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--arm", required=True, help="arm label, e.g. arm3-run1")
    ap.add_argument("--url", default="http://localhost:9008", help="subgen base URL")
    ap.add_argument("--audio", default=DEFAULT_AUDIO)
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--task", default="transcribe", choices=["transcribe", "translate"])
    args = ap.parse_args()

    audio_dir = Path(args.audio)
    clips = sorted(audio_dir.glob("*.wav"))
    if not clips:
        print(f"FATAL: no .wav files under {audio_dir} -- build the corpus first.", file=sys.stderr)
        return 1

    out_dir = Path(args.out) / args.arm
    out_dir.mkdir(parents=True, exist_ok=True)

    with httpx.Client() as client:
        try:
            cfg = server_config(client, args.url)
        except Exception as exc:
            print(f"FATAL: cannot reach subgen at {args.url}: {exc}", file=sys.stderr)
            return 1

        print(f"arm={args.arm}  task={args.task}  clips={len(clips)}")
        print(f"server: {json.dumps({k: v for k, v in cfg.items() if k != 'capabilities'})}")
        (out_dir / "server_config.json").write_text(
            json.dumps({**cfg, "task": args.task, "clip_count": len(clips)}, indent=2),
            encoding="utf-8",
            newline="\n",
        )

        done = failed = skipped = 0
        started = time.monotonic()
        for i, clip in enumerate(clips, 1):
            dest = out_dir / f"{clip.stem}.srt"
            if dest.exists():
                skipped += 1
                continue
            t0 = time.monotonic()
            try:
                srt = transcribe(client, args.url, clip, task=args.task)
            except Exception as exc:
                print(f"  [{i:2}/{len(clips)}] FAILED {clip.name}: {exc}", file=sys.stderr)
                failed += 1
                continue
            dest.write_text(srt, encoding="utf-8", newline="\n")
            done += 1
            print(f"  [{i:2}/{len(clips)}] {time.monotonic() - t0:6.1f}s  {clip.stem[:56]}")

    elapsed = time.monotonic() - started
    print(
        f"\n{args.arm}: {done} transcribed, {skipped} already present, {failed} failed "
        f"in {elapsed / 60:.1f} min"
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
