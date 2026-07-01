"""#359 off-app tuning: sweep RetimeParams across a subtitle corpus and report
pooled readability deltas. Pure + deterministic core; corpus adapters + CLI
elsewhere. The manual bootstrap of the federated-tuning loop (#124)."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from statistics import mean, median

from .paths import PathOutsideRootError
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
    # Absolute count of cues > MAX_DURATION_S, NOT a delta introduced by re-timing:
    # the retimer caps at max_cue_ms and never shortens, so pre-existing over-long
    # cues also show up in the baseline row.
    too_long: int
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
        # Absolute count (not a re-timing delta): retimer caps at max_cue_ms and
        # never shortens, so long cues predate the sweep and appear in baseline too.
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
    # subsyncarr writes <base>.<lang>.<engine>.srt, so the engine token is
    # dot-delimited. Match the delimited form so a show titled e.g.
    # "The Alass Chronicles.en.srt" is not mistaken for a re-sync variant.
    lower = srt_name.lower()
    return any(f".{m}." in lower for m in _SYNC_MARKERS)


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


def corpus_from_dir(path: str, *, limit: int | None = None) -> list[tuple[str, str]]:
    """Every *.srt in a folder (excluding subsyncarr variants + unparseable).
    Stops gathering once `limit` rows are collected (None = all)."""
    out: list[tuple[str, str]] = []
    for p in sorted(Path(path).glob("*.srt")):
        if limit is not None and len(out) >= limit:
            break
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
    from .paths import canonical_to_fs

    return canonical_to_fs(canonical_path)


def corpus_from_ledger(
    db_path: str, *, resolve=None, cue_tol: int = 1, limit: int | None = None
) -> list[tuple[str, str]]:
    """Corpus from the subs_generated ledger: each completed row → its original
    subgen sidecar. Skips files whose on-disk cue_count differs from the recorded
    aftercare cue_count by more than cue_tol (a Bazarr provider sub replaced it).
    Stops gathering once `limit` rows are collected (None = all). The `resolve=`
    seam is best-effort per row: a resolver that raises (e.g. PathOutsideRootError
    on a traversal-escaping canonical) skips that row rather than aborting."""
    resolve = resolve or _default_resolve
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT canonical_path FROM subs_generated WHERE completed_at IS NOT NULL"
        ).fetchall()
        out: list[tuple[str, str]] = []
        for (canon,) in rows:
            if limit is not None and len(out) >= limit:
                break
            try:
                video = resolve(canon)
            except (OSError, PathOutsideRootError):
                continue
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


def _rank_key(r: SweepRow) -> tuple:
    # best = lowest %over-critical, then lowest median CPS, then least screen-time added.
    return (r.pct_over_critical, r.median_cps, r.mean_added_ms)


def format_report(rows: list[SweepRow]) -> str:
    baseline = next((r for r in rows if r.params is None), None)
    treated = sorted((r for r in rows if r.params is not None), key=_rank_key)
    lines = ["# re-timer sweep (ranked; baseline = no re-timing)"]
    if baseline:
        lines.append(
            f"baseline: subs={baseline.subs} median_cps={baseline.median_cps:.1f} "
            f"%>25={baseline.pct_over_critical:.1%} %>20={baseline.pct_over_comfortable:.1%} "
            f"micro={baseline.micro_cues} too_long={baseline.too_long}"
        )
    for r in treated:
        p = r.params
        lines.append(
            f"target_cps={p.target_cps:<4} min_cue={p.min_cue_ms:<5} | "
            f"median_cps={r.median_cps:.1f} %>25={r.pct_over_critical:.1%} "
            f"%>20={r.pct_over_comfortable:.1%} micro={r.micro_cues} too_long={r.too_long} "
            f"changed={r.subs_changed}/{r.subs} +{r.mean_added_ms:.0f}ms/sub"
        )
    return "\n".join(lines)
