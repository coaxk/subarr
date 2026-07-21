"""#364 off-app tuning aid: validate the forced-segment LID thresholds
(`lid_min_confidence`/`lid_max_english_prob`, live defaults 0.5/0.25) against a
labelled corpus of real windows. Read-only, CPU-only. Reuses the production
predicate `forced_segment.window_is_foreign` so the sweep can never diverge from
the live detector.

A window record is a dict:
    {"truth": "english"|"foreign", "lang": <iso>, "top_lang": str|None,
     "top_prob": float, "english_prob": float}

`truth` is the ground-truth label (from the audio track's language); `top_lang`/
`top_prob`/`english_prob` are the raw silero-lang95 verdict for that window.
"""

from __future__ import annotations

from dataclasses import dataclass

from .forced_segment import ForcedSegmentParams, window_is_foreign


def conf_grid() -> list[float]:
    """lid_min_confidence candidates. Live default is 0.5."""
    return [0.3, 0.4, 0.5, 0.6, 0.7, 0.8]


def en_grid() -> list[float]:
    """lid_max_english_prob candidates. Live default is 0.25."""
    return [0.10, 0.15, 0.20, 0.25, 0.30, 0.40]


@dataclass(frozen=True)
class ThresholdCell:
    min_conf: float
    max_en: float
    n_english: int  # true-english windows in the corpus
    n_foreign: int  # true-foreign windows in the corpus
    false_positives: int  # english windows flagged foreign (the costly error)
    true_positives: int  # foreign windows flagged foreign

    @property
    def fp_rate(self) -> float:
        return self.false_positives / self.n_english if self.n_english else 0.0

    @property
    def recall(self) -> float:
        return self.true_positives / self.n_foreign if self.n_foreign else 0.0


def _flag(rec: dict, min_conf: float, max_en: float) -> bool:
    """Apply the LIVE predicate at the given thresholds to one window record."""
    params = ForcedSegmentParams(lid_min_confidence=min_conf, lid_max_english_prob=max_en)
    return window_is_foreign(
        rec.get("top_lang"),
        float(rec.get("top_prob", 0.0)),
        float(rec.get("english_prob", 0.0)),
        params,
    )


def evaluate(records: list[dict], min_conf: float, max_en: float) -> ThresholdCell:
    """Count false positives (english flagged foreign) and true positives
    (foreign flagged foreign) at one threshold pair."""
    n_en = n_fr = fp = tp = 0
    for rec in records:
        flagged = _flag(rec, min_conf, max_en)
        if rec.get("truth") == "english":
            n_en += 1
            if flagged:
                fp += 1
        else:
            n_fr += 1
            if flagged:
                tp += 1
    return ThresholdCell(min_conf, max_en, n_en, n_fr, fp, tp)


def sweep(
    records: list[dict], confs: list[float] | None = None, ens: list[float] | None = None
) -> list[ThresholdCell]:
    """Evaluate every (min_conf, max_en) combination in the grids."""
    confs = confs if confs is not None else conf_grid()
    ens = ens if ens is not None else en_grid()
    return [evaluate(records, c, e) for c in confs for e in ens]


def per_language_recall(records: list[dict], min_conf: float, max_en: float) -> dict[str, tuple[int, int]]:
    """(hit, total) per foreign language at one threshold pair. English is
    excluded (it has no recall, only a false-positive rate)."""
    out: dict[str, tuple[int, int]] = {}
    for rec in records:
        if rec.get("truth") != "foreign":
            continue
        lang = rec.get("lang", "?")
        hit, tot = out.get(lang, (0, 0))
        tot += 1
        if _flag(rec, min_conf, max_en):
            hit += 1
        out[lang] = (hit, tot)
    return out


def recommend(cells: list[ThresholdCell], *, max_fp_rate: float) -> ThresholdCell | None:
    """The highest-recall cell whose false-positive rate stays within budget.
    A false positive writes a bogus forced sub onto an English file, so FP is the
    hard constraint and recall is maximised within it. Ties break toward the more
    conservative cell (higher confidence floor, then lower english ceiling)."""
    eligible = [c for c in cells if c.fp_rate <= max_fp_rate]
    if not eligible:
        return None
    return max(eligible, key=lambda c: (c.recall, c.min_conf, -c.max_en))


def select_audio_stream(streams: list[dict], expected_tags: set[str]) -> int | None:
    """Pick the audio stream to feed the LID. Prefer a stream whose language tag
    matches the expected language (avoids grabbing an English dub of a foreign
    show); if there is exactly one stream, use it; otherwise the file is ambiguous
    and the caller should skip it to keep labels clean.

    `streams`: [{"index": int, "lang": str|None}]. `expected_tags`: acceptable
    ISO tag variants for the expected language, e.g. {"deu", "ger", "de"}.
    """
    tags = {t.lower() for t in expected_tags}
    for s in streams:
        lang = (s.get("lang") or "").lower()
        if lang and lang in tags:
            return s["index"]
    if len(streams) == 1:
        return streams[0]["index"]
    return None


def format_report(
    records: list[dict],
    cells: list[ThresholdCell],
    *,
    default: tuple[float, float] = (0.5, 0.25),
    rec_cell: ThresholdCell | None = None,
) -> str:
    """Human-readable summary: corpus size, the sweep grid (fp_rate vs recall),
    per-language recall at the current default, and the recommendation."""
    n_en = sum(1 for r in records if r.get("truth") == "english")
    n_fr = sum(1 for r in records if r.get("truth") != "english")
    lines = ["# forced-segment LID threshold sweep (read-only; live default min_conf=0.5, max_en=0.25)"]
    lines.append(f"corpus: {n_en} english windows | {n_fr} foreign windows | {len(records)} total")
    lines.append("")
    lines.append("min_conf  max_en   fp_rate   recall   (fp/en, tp/fr)")
    for c in cells:
        lines.append(
            f"  {c.min_conf:.2f}     {c.max_en:.2f}    {c.fp_rate:6.3f}   {c.recall:6.3f}   "
            f"({c.false_positives}/{c.n_english}, {c.true_positives}/{c.n_foreign})"
        )

    d_conf, d_en = default
    lines.append("")
    lines.append(f"per-language recall at the default ({d_conf}, {d_en}):")
    plr = per_language_recall(records, d_conf, d_en)
    for lang in sorted(plr):
        hit, tot = plr[lang]
        pct = (hit / tot * 100) if tot else 0.0
        lines.append(f"  {lang:8s} {hit:3d}/{tot:<3d}  {pct:5.1f}%")

    lines.append("")
    if rec_cell is not None:
        lines.append(
            f"recommendation: min_conf={rec_cell.min_conf}, max_en={rec_cell.max_en} "
            f"(fp_rate={rec_cell.fp_rate:.3f}, recall={rec_cell.recall:.3f})"
        )
    else:
        lines.append("recommendation: none within the false-positive budget")
    return "\n".join(lines)
