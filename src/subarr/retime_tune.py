"""#359 off-app tuning: sweep RetimeParams across a subtitle corpus and report
pooled readability deltas. Pure + deterministic core; corpus adapters + CLI
elsewhere. The manual bootstrap of the federated-tuning loop (#124)."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from statistics import mean, median

from .subtitle_readability import (
    CRITICAL_CPS,
    MAX_CPS,
    MAX_DURATION_S,
    MIN_DURATION_S,
    Cue,
    parse_srt,
)
from .subtitle_retime import RetimeParams, retime_srt


def param_grid() -> list[RetimeParams]:
    """The sweep grid: target_cps x min_cue_ms; gap/max held at Netflix values."""
    return [
        RetimeParams(target_cps=tc, min_cue_ms=mc, min_gap_ms=100, max_cue_ms=7000)
        for tc in (15.0, 17.0, 20.0)
        for mc in (833, 1000, 1200)
    ]


@dataclass(frozen=True)
class SweepRow:
    params: RetimeParams | None  # None = baseline (no re-timing)
    subs: int
    subs_changed: int
    median_cps: float
    pct_over_critical: float  # cues > CRITICAL_CPS (25)
    pct_over_comfortable: float  # cues > MAX_CPS (20)
    micro_cues: int  # cues < MIN_DURATION_S
    too_long: int  # cues > MAX_DURATION_S
    mean_added_ms: float  # mean screen-time added per sub


def _metrics(cues: list[Cue]) -> dict:
    live = [c for c in cues if c.duration_s > 0]
    cpses = [c.cps for c in live]
    n = len(cpses) or 1
    return {
        "median_cps": median(cpses) if cpses else 0.0,
        "pct_over_critical": sum(1 for x in cpses if x > CRITICAL_CPS) / n,
        "pct_over_comfortable": sum(1 for x in cpses if x > MAX_CPS) / n,
        "micro_cues": sum(1 for c in live if c.duration_s < MIN_DURATION_S),
        "too_long": sum(1 for c in live if c.duration_s > MAX_DURATION_S),
    }


def _dur_ms(cues: list[Cue]) -> int:
    return sum(c.end_ms - c.start_ms for c in cues)


def retime_sweep(texts: list[str], grid: list[RetimeParams]) -> list[SweepRow]:
    """Baseline row (params=None) first, then one row per grid combo. Metrics are
    pooled across all corpus cues; mean_added_ms is averaged per sub."""
    parsed = [parse_srt(t) for t in texts]
    before = [c for cues in parsed for c in cues]
    bm = _metrics(before)
    rows = [
        SweepRow(
            None,
            len(texts),
            0,
            bm["median_cps"],
            bm["pct_over_critical"],
            bm["pct_over_comfortable"],
            bm["micro_cues"],
            bm["too_long"],
            0.0,
        )
    ]
    for params in grid:
        after: list[Cue] = []
        added: list[int] = []
        changed = 0
        for text, b in zip(texts, parsed):
            new = retime_srt(text, params)
            a = parse_srt(new)
            after.extend(a)
            if new != text:
                changed += 1
            added.append(_dur_ms(a) - _dur_ms(b))
        m = _metrics(after)
        rows.append(
            SweepRow(
                params,
                len(texts),
                changed,
                m["median_cps"],
                m["pct_over_critical"],
                m["pct_over_comfortable"],
                m["micro_cues"],
                m["too_long"],
                mean(added) if added else 0.0,
            )
        )
    return rows


# subsyncarr writes engine-suffixed re-syncs of an existing sub; those are
# timing-shifted derivatives with identical CPS — never the subgen original.
_SYNC_MARKERS = ("ffsubsync", "alass", "autosubsync", "subsync")


def _is_sync_variant(srt_name: str) -> bool:
    return any(m in srt_name.lower() for m in _SYNC_MARKERS)


def _original_sidecar(video_full: Path) -> Path | None:
    """The subgen-original .srt next to the video: prefer <stem>.en.srt, else the
    first <stem>*.srt that is NOT a subsyncarr engine variant."""
    try:
        stem, parent = video_full.stem, video_full.parent
        preferred = parent / f"{stem}.en.srt"
        if preferred.exists():
            return preferred
        for p in sorted(parent.glob(f"{stem}*.srt")):
            if not _is_sync_variant(p.name):
                return p
    except OSError:
        pass
    return None


def corpus_from_dir(path: str) -> list[tuple[str, str]]:
    """Every *.srt in a folder (excluding subsyncarr variants + unparseable)."""
    out: list[tuple[str, str]] = []
    for p in sorted(Path(path).glob("*.srt")):
        if _is_sync_variant(p.name):
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if parse_srt(text):
            out.append((p.name, text))
    return out


def _default_resolve(canonical_path: str) -> Path | None:
    from .paths import PathOutsideRootError, canonical_to_fs

    try:
        return canonical_to_fs(canonical_path)
    except (OSError, PathOutsideRootError):
        return None


def corpus_from_ledger(db_path: str, *, resolve=None, cue_tol: int = 1) -> list[tuple[str, str]]:
    """Corpus from the subs_generated ledger: each completed row → its original
    subgen sidecar. Skips files whose on-disk cue_count differs from the recorded
    aftercare cue_count by more than cue_tol (a Bazarr provider sub replaced it)."""
    resolve = resolve or _default_resolve
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT canonical_path FROM subs_generated WHERE completed_at IS NOT NULL"
        ).fetchall()
        out: list[tuple[str, str]] = []
        for (canon,) in rows:
            video = resolve(canon)
            if not video:
                continue
            srt = _original_sidecar(video)
            if not srt or not srt.exists():
                continue
            try:
                text = srt.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            cues = parse_srt(text)
            if not cues:
                continue
            rec = conn.execute(
                "SELECT cue_count FROM aftercare_results WHERE canonical_path = ? ORDER BY id DESC LIMIT 1",
                (canon,),
            ).fetchone()
            if rec and rec[0] is not None and abs(len(cues) - int(rec[0])) > cue_tol:
                continue  # replaced (likely a Bazarr provider download)
            out.append((canon, text))
        return out
    finally:
        conn.close()
