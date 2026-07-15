#!/usr/bin/env python
"""#364 LID threshold validation -- live extraction CLI.

Runs INSIDE the subarr-next container (needs the silero VAD + lang95 models,
the media mount, and subarr's Sonarr connection). Builds a labelled corpus from
Sonarr, extracts real 15s speech windows from the correct-language audio track,
runs the silero-lang95 LID once per window, then sweeps the thresholds offline
via subarr.lid_tune.

Usage (piped in, so no bind-mount needed):
    cat scripts/lid_tune.py | wsl docker exec -i subarr-next python - pilot
    cat scripts/lid_tune.py | wsl docker exec -i subarr-next python - full

Writes raw records to /tmp/lid_records.json and prints the report to stdout.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed

import httpx

from subarr import config, lid
from subarr.forced_segment import assemble_windows, clip_audio, detect_utterances
from subarr.forced_segment_lid import _read_wav_f32
from subarr.lid_tune import (
    conf_grid,
    en_grid,
    format_report,
    recommend,
    select_audio_stream,
    sweep,
)

# Sonarr originalLanguage name -> acceptable audio-track ISO tag variants
# (iso639-2/B, iso639-2/T, and iso639-1). English is the negative class.
LANG_TAGS: dict[str, set[str]] = {
    "english": {"eng", "en"},
    "french": {"fre", "fra", "fr"},
    "spanish": {"spa", "es"},
    "italian": {"ita", "it"},
    "german": {"ger", "deu", "de"},
    "polish": {"pol", "pl"},
    "dutch": {"dut", "nld", "nl"},
    "danish": {"dan", "da"},
    "swedish": {"swe", "sv"},
    "norwegian": {"nor", "nob", "nno", "no"},
    "japanese": {"jpn", "ja"},
    "serbian": {"srp", "sr"},
    "finnish": {"fin", "fi"},
    "russian": {"rus", "ru"},
    "korean": {"kor", "ko"},
    "portuguese": {"por", "pt"},
    "hebrew": {"heb", "he", "iw"},
    "turkish": {"tur", "tr"},
    "icelandic": {"isl", "ice", "is"},
    "romanian": {"rum", "ron", "ro"},
    "czech": {"cze", "ces", "cs"},
}
_ENGLISH_TAGS = {"eng", "en"}


def arr_to_fs(path: str, arr_prefix: str, media_root: str) -> str:
    p = path
    if p.startswith(arr_prefix):
        p = p[len(arr_prefix) :].lstrip("/")
    return os.path.join(media_root, p)


def probe_audio_streams(fs_path: str) -> list[dict]:
    """Audio streams in order -> [{"index": audio_relative_pos, "lang": tag|None}].
    The index is the 0:a:N position ffmpeg -map wants, NOT the absolute index."""
    out = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "a",
            "-show_entries",
            "stream_tags=language",
            "-of",
            "json",
            fs_path,
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    try:
        streams = json.loads(out.stdout).get("streams", [])
    except (ValueError, TypeError):
        return []
    result = []
    for i, s in enumerate(streams):
        lang = (s.get("tags") or {}).get("language")
        result.append({"index": i, "lang": lang})
    return result


def _duration_s(fs_path: str) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nk=1:nw=1", fs_path],
        capture_output=True,
        text=True,
        timeout=60,
    )
    try:
        return float(out.stdout.strip())
    except (ValueError, TypeError):
        return 0.0


def extract_records(
    fs_path: str, truth: str, lang_key: str, expected_tags: set[str], *, max_windows: int = 12
) -> list[dict]:
    """One episode -> up to max_windows labelled window records. Skips the file
    (returns []) if the correct-language audio track cannot be identified, or if
    a foreign file's only track is an English dub (would mislabel)."""
    streams = probe_audio_streams(fs_path)
    if not streams:
        return []
    idx = select_audio_stream(streams, expected_tags)
    if idx is None:
        return []  # ambiguous multi-track, no tag match -> skip to keep labels clean
    sel_tag = (streams[idx].get("lang") or "").lower()
    if truth == "foreign" and sel_tag in _ENGLISH_TAGS:
        return []  # foreign show but the selected track is an English dub -> skip

    dur = _duration_s(fs_path)
    if dur <= 0:
        return []
    seg_start = min(300.0, dur * 0.30)  # skip intro/recap
    seg_dur = min(300.0, max(60.0, dur * 0.50))

    records: list[dict] = []
    with tempfile.TemporaryDirectory(prefix="lidtune-") as tmp:
        seg = os.path.join(tmp, "seg.wav")
        try:
            clip_audio(fs_path, seg_start, seg_start + seg_dur, seg, track=idx)
        except Exception:
            return []
        utts = detect_utterances(seg)  # single-track wav -> track 0
        windows = assemble_windows(utts, 15.0)[:max_windows]
        if not windows:
            return []
        samples = _read_wav_f32(seg)
        for w_start, w_end, _idxs in windows:
            a, b = int(w_start * 16000), int(w_end * 16000)
            seg_samples = samples[a:b]
            if len(seg_samples) < 16000:  # < 1s of audio, skip
                continue
            verdict = lid.classify_samples(seg_samples)
            if verdict is None:
                continue
            top_lang, top_prob, en_prob = verdict
            records.append(
                {
                    "truth": truth,
                    "lang": lang_key,
                    "top_lang": top_lang,
                    "top_prob": float(top_prob),
                    "english_prob": float(en_prob),
                }
            )
    return records


def _sonarr():
    s = config.settings
    url = str(s.sonarr_url or "").rstrip("/")
    key = s.sonarr_api_key or ""
    return url, key


def _episodefile_path(url: str, key: str, series_id: int, arr_prefix: str, media_root: str) -> str | None:
    efs = httpx.get(
        f"{url}/api/v3/episodefile",
        params={"seriesId": series_id},
        headers={"X-Api-Key": key},
        timeout=60,
    ).json()
    # prefer a decently sized, mid-list file (skip specials/samples at the edges)
    sized = [(ef.get("size", 0) or 0, ef.get("path")) for ef in efs if ef.get("path")]
    sized = [sp for sp in sized if sp[0] > 50_000_000]  # > 50 MB -> real episode
    if not sized:
        return None
    sized.sort()
    _, path = sized[len(sized) // 2]
    fs = arr_to_fs(path, arr_prefix, media_root)
    return fs if os.path.exists(fs) else None


def build_corpus(english_shows: int, foreign_per_lang: int) -> list[tuple[str, str, int, str]]:
    """-> list of (truth, lang_key, series_id, title) picks."""
    url, key = _sonarr()
    series = httpx.get(f"{url}/api/v3/series", headers={"X-Api-Key": key}, timeout=60).json()
    english, by_lang = [], {}
    for sh in series:
        lang = ((sh.get("originalLanguage") or {}).get("name") or "").lower()
        files = (sh.get("statistics") or {}).get("episodeFileCount", 0) or 0
        if files <= 0:
            continue
        if lang == "english":
            english.append((files, sh["id"], sh.get("title", "?")))
        elif lang in LANG_TAGS:
            by_lang.setdefault(lang, []).append((files, sh["id"], sh.get("title", "?")))
    english.sort(reverse=True)
    picks = [("english", "english", sid, t) for _, sid, t in english[:english_shows]]
    for lang, shows in sorted(by_lang.items()):
        shows.sort(reverse=True)
        for _, sid, t in shows[:foreign_per_lang]:
            picks.append(("foreign", lang, sid, t))
    return picks


def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else "pilot"
    if mode == "full":
        english_shows, foreign_per_lang, workers = 60, 3, 10
    else:  # pilot
        english_shows, foreign_per_lang, workers = 5, 1, 6

    if not lid.lid_available():
        print("LID model not available; run lid.ensure_available() first", file=sys.stderr)
        sys.exit(1)

    s = config.settings
    url, key = _sonarr()
    arr_prefix = str(s.arr_path_prefix).rstrip("/")
    media_root = str(s.media_root).rstrip("/")

    picks = build_corpus(english_shows, foreign_per_lang)
    print(
        f"[corpus] {len(picks)} shows "
        f"({sum(1 for p in picks if p[0] == 'english')} english, "
        f"{sum(1 for p in picks if p[0] == 'foreign')} foreign) | mode={mode}",
        file=sys.stderr,
    )

    # resolve one episode path per show
    jobs = []
    for truth, lang_key, sid, title in picks:
        fs = _episodefile_path(url, key, sid, arr_prefix, media_root)
        if fs:
            jobs.append((fs, truth, lang_key, title))
        else:
            print(f"[skip] no readable episode: {title}", file=sys.stderr)

    records: list[dict] = []
    done = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {
            ex.submit(extract_records, fs, truth, lang_key, LANG_TAGS[lang_key]): (title, lang_key)
            for fs, truth, lang_key, title in jobs
        }
        for fut in as_completed(futs):
            title, lang_key = futs[fut]
            done += 1
            try:
                recs = fut.result()
            except Exception as e:  # noqa: BLE001
                print(f"[err] {title}: {e}", file=sys.stderr)
                recs = []
            records.extend(recs)
            print(f"[{done}/{len(jobs)}] {lang_key:10s} {len(recs):2d} windows  {title}", file=sys.stderr)

    with open("/tmp/lid_records.json", "w") as f:
        json.dump(records, f)

    cells = sweep(records, conf_grid(), en_grid())
    rec = recommend(cells, max_fp_rate=0.05)  # <=5% spurious forced subs on english
    print(format_report(records, cells, default=(0.5, 0.25), rec_cell=rec))
    # also report the default cell explicitly
    default_cell = next((c for c in cells if c.min_conf == 0.5 and c.max_en == 0.25), None)
    if default_cell:
        print(
            f"\nDEFAULT (0.5, 0.25): fp_rate={default_cell.fp_rate:.3f} "
            f"recall={default_cell.recall:.3f} "
            f"(fp {default_cell.false_positives}/{default_cell.n_english}, "
            f"tp {default_cell.true_positives}/{default_cell.n_foreign})"
        )


if __name__ == "__main__":
    main()
