"""
retime_tune_stratified.py
=========================
Extends the global retime_tune.py sweep (issue #359 / PR #404) with
per-language stratification to answer issue #408:
  "Do CPS-optimal RetimeParams diverge sharply by language?"

Usage
-----
    python scripts/retime_tune_stratified.py \
        --ledger path/to/subs_generated.jsonl \
        --output results/retime_stratified.json \
        [--min-cues 50]          # skip langs with < N cues (noisy estimate)

The script:
  1. Groups subs by detected/target language (ISO 639-1 code).
  2. Runs the same grid-search sweep as retime_tune.py over each group.
  3. Emits per-language optimal params + a divergence report.
  4. Prints a human-readable summary for the issue comment.

No GPU required — deterministic SRT post-pass, CPU only.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
from collections import defaultdict
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Iterator

# ---------------------------------------------------------------------------
# Language family helpers
# ---------------------------------------------------------------------------

# CJK languages: each character is semantically dense; lower CPS targets.
CJK_LANGS = {"zh", "zh-hans", "zh-hant", "ja", "ko"}

# RTL languages: Arabic / Hebrew / Persian — similar info-density to Latin
# but some timing conventions differ slightly.
RTL_LANGS = {"ar", "he", "fa", "ur"}

# Everything else falls into the Latin/Cyrillic/Greek bucket.
def lang_family(lang: str) -> str:
    """Map an ISO 639-1 code to a broad family used for guidance grouping."""
    base = lang.split("-")[0].lower()
    if base in CJK_LANGS or lang.lower() in CJK_LANGS:
        return "cjk"
    if base in RTL_LANGS:
        return "rtl"
    return "latin"


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RetimeParams:
    target_cps: float       # characters per second ceiling
    min_cue_ms: int         # minimum cue duration in milliseconds
    min_gap_ms: int         # minimum inter-cue gap in milliseconds
    max_cue_ms: int         # maximum cue duration in milliseconds

    def __str__(self) -> str:
        return (
            f"RetimeParams(target_cps={self.target_cps}, "
            f"min_cue_ms={self.min_cue_ms}, "
            f"min_gap_ms={self.min_gap_ms}, "
            f"max_cue_ms={self.max_cue_ms})"
        )


# Current global default (shipped in PR #404)
GLOBAL_DEFAULT = RetimeParams(
    target_cps=17,
    min_cue_ms=1000,
    min_gap_ms=100,
    max_cue_ms=7000,
)

# ---------------------------------------------------------------------------
# Industry-grounded CPS limits by language family
# (sourced from Netflix Timed Text Style Guides, academic lit, EBU R25)
# ---------------------------------------------------------------------------
#
# Key data points:
#   Latin/Cyrillic/Greek  : Netflix adult cap = 20 CPS; comfortable = 17 CPS
#   Korean                : Netflix cap = 12 CPS
#   Simplified Chinese    : Netflix cap = 9 CPS
#   Traditional Chinese   : Netflix cap = 9 CPS
#   Japanese              : Netflix cap = 4 CPS (full-width, dense kanji)
#   Arabic/Hebrew         : no official Netflix CPS; EBU suggests ~17 CPS
#                           (character density similar to Latin; treat same)
#
# Because the re-timer enforces a *ceiling* (not an exact target), we set
# target_cps to the industry comfortable reading speed, not the hard cap.
# This matches the global default's philosophy (17 < Netflix's 20 cap).

LANGUAGE_PARAMS: dict[str, RetimeParams] = {
    # ---- CJK ---------------------------------------------------------------
    "ja": RetimeParams(
        target_cps=4,
        min_cue_ms=1000,
        min_gap_ms=83,      # 2 frames @ 24 fps — standard JP broadcast gap
        max_cue_ms=7000,
    ),
    "zh": RetimeParams(      # covers zh-hans, zh-hant (same CPS cap)
        target_cps=9,
        min_cue_ms=1000,
        min_gap_ms=100,
        max_cue_ms=7000,
    ),
    "ko": RetimeParams(
        target_cps=12,
        min_cue_ms=1000,
        min_gap_ms=100,
        max_cue_ms=7000,
    ),
    # ---- RTL (treat like Latin for CPS; keep same timing defaults) ----------
    "ar": RetimeParams(
        target_cps=17,
        min_cue_ms=1000,
        min_gap_ms=100,
        max_cue_ms=7000,
    ),
    "he": RetimeParams(
        target_cps=17,
        min_cue_ms=1000,
        min_gap_ms=100,
        max_cue_ms=7000,
    ),
    # ---- Latin / default (matches GLOBAL_DEFAULT) ---------------------------
    # All unrecognised lang codes fall through to GLOBAL_DEFAULT at runtime.
}

# Aliases
LANGUAGE_PARAMS["zh-hans"] = LANGUAGE_PARAMS["zh"]
LANGUAGE_PARAMS["zh-hant"] = LANGUAGE_PARAMS["zh"]


def get_params(lang: str) -> RetimeParams:
    """Return the best RetimeParams for *lang*, falling back to global."""
    if not lang:
        return GLOBAL_DEFAULT
    lc = lang.lower()
    if lc in LANGUAGE_PARAMS:
        return LANGUAGE_PARAMS[lc]
    base = lc.split("-")[0]
    if base in LANGUAGE_PARAMS:
        return LANGUAGE_PARAMS[base]
    return GLOBAL_DEFAULT


# ---------------------------------------------------------------------------
# SRT / cue helpers
# ---------------------------------------------------------------------------

_SRT_TIMECODE = re.compile(
    r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})"
    r"\s*-->\s*"
    r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})"
)


@dataclass
class Cue:
    start_ms: int
    end_ms: int
    text: str

    @property
    def duration_ms(self) -> int:
        return self.end_ms - self.start_ms

    @property
    def char_count(self) -> int:
        # Strip inline tags for measurement
        return len(re.sub(r"<[^>]+>", "", self.text).replace("\n", ""))

    @property
    def cps(self) -> float:
        dur_s = self.duration_ms / 1000
        return self.char_count / dur_s if dur_s > 0 else float("inf")

    def is_critical(self, target_cps: float) -> bool:
        return self.cps > target_cps and self.char_count > 0


def _tc_to_ms(h, m, s, ms) -> int:
    return int(h) * 3_600_000 + int(m) * 60_000 + int(s) * 1_000 + int(ms)


def parse_srt(text: str) -> list[Cue]:
    """Parse an SRT string into a list of Cue objects."""
    cues: list[Cue] = []
    for block in re.split(r"\n{2,}", text.strip()):
        lines = block.strip().splitlines()
        if len(lines) < 2:
            continue
        m = None
        for line in lines:
            m = _SRT_TIMECODE.match(line.strip())
            if m:
                break
        if not m:
            continue
        start = _tc_to_ms(*m.groups()[:4])
        end   = _tc_to_ms(*m.groups()[4:])
        body_lines = [
            l for l in lines
            if not _SRT_TIMECODE.match(l.strip()) and not l.strip().isdigit()
        ]
        cues.append(Cue(start, end, "\n".join(body_lines)))
    return cues


# ---------------------------------------------------------------------------
# Retimer (mirrors the logic in the main SRT re-timer, PR #404)
# ---------------------------------------------------------------------------

def retime(cues: list[Cue], params: RetimeParams) -> list[Cue]:
    """
    Apply RetimeParams to a list of cues, returning adjusted copies.
    Mirrors the deterministic post-pass in the shipped re-timer.
    """
    result: list[Cue] = []
    for i, cue in enumerate(cues):
        start = cue.start_ms
        end   = cue.end_ms

        # 1. Enforce min duration
        if (end - start) < params.min_cue_ms:
            end = start + params.min_cue_ms

        # 2. Enforce max duration
        if (end - start) > params.max_cue_ms:
            end = start + params.max_cue_ms

        # 3. Enforce CPS ceiling (extend end if too fast)
        dur_s = (end - start) / 1000
        needed_s = cue.char_count / params.target_cps if params.target_cps > 0 else 0
        if needed_s > dur_s:
            end = start + int(needed_s * 1000)
            # Re-apply max
            end = min(end, start + params.max_cue_ms)

        # 4. Enforce gap with previous cue (push end back if overlaps)
        if result:
            prev_end = result[-1].end_ms
            if start < prev_end + params.min_gap_ms:
                # Gap violation — clamp end to avoid overlap beyond what we
                # can fix without merging (mirrors shipped behaviour)
                end = min(end, start + cue.duration_ms)

        result.append(Cue(start, end, cue.text))
    return result


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

@dataclass
class SweepMetrics:
    lang: str
    n_cues: int
    critical_rate_before: float   # fraction of cues exceeding target_cps
    critical_rate_after: float
    new_overlaps: int
    params: RetimeParams

    def delta(self) -> float:
        return self.critical_rate_before - self.critical_rate_after


def evaluate(
    cues: list[Cue],
    params: RetimeParams,
    lang: str,
) -> SweepMetrics:
    before_critical = sum(1 for c in cues if c.is_critical(params.target_cps))
    retimed = retime(cues, params)
    after_critical = sum(1 for c in retimed if c.is_critical(params.target_cps))

    # Count new overlaps introduced
    new_overlaps = 0
    for i in range(1, len(retimed)):
        if retimed[i].start_ms < retimed[i - 1].end_ms:
            new_overlaps += 1

    n = len(cues)
    return SweepMetrics(
        lang=lang,
        n_cues=n,
        critical_rate_before=before_critical / n if n else 0,
        critical_rate_after=after_critical / n if n else 0,
        new_overlaps=new_overlaps,
        params=params,
    )


# ---------------------------------------------------------------------------
# Grid search (same approach as global retime_tune.py)
# ---------------------------------------------------------------------------

PARAM_GRID = {
    "target_cps":  [4, 7, 9, 12, 15, 17, 20],
    "min_cue_ms":  [833, 1000, 1200],
    "min_gap_ms":  [83, 100, 120],
    "max_cue_ms":  [6000, 7000, 8000],
}


def grid_search(
    cues: list[Cue],
    lang: str,
    family: str,
) -> tuple[RetimeParams, SweepMetrics]:
    """
    Search PARAM_GRID to find params that minimise critical-CPS rate
    with zero new overlaps. Restricts target_cps search to the range
    appropriate for the language family so we don't waste iterations.
    """
    # Restrict target_cps range per family
    if family == "cjk":
        cps_range = [c for c in PARAM_GRID["target_cps"] if c <= 12]
    else:
        cps_range = [c for c in PARAM_GRID["target_cps"] if c >= 12]

    best_params: RetimeParams | None = None
    best_metrics: SweepMetrics | None = None

    for cps in cps_range:
        for min_cue in PARAM_GRID["min_cue_ms"]:
            for gap in PARAM_GRID["min_gap_ms"]:
                for max_cue in PARAM_GRID["max_cue_ms"]:
                    p = RetimeParams(cps, min_cue, gap, max_cue)
                    m = evaluate(cues, p, lang)
                    if m.new_overlaps > 0:
                        continue  # hard constraint: never introduce overlaps
                    if best_metrics is None or m.delta() > best_metrics.delta():
                        best_params = p
                        best_metrics = m

    # Fallback: if all grid points produce overlaps, use industry default
    if best_params is None:
        fallback = get_params(lang)
        best_params = fallback
        best_metrics = evaluate(cues, fallback, lang)

    return best_params, best_metrics


# ---------------------------------------------------------------------------
# Ledger reader
# ---------------------------------------------------------------------------

def iter_ledger(path: Path) -> Iterator[tuple[str, list[Cue]]]:
    """
    Yield (lang, cues) pairs from the subs_generated JSONL ledger.
    Expects each line: {"lang": "en", "srt": "<SRT text>", ...}
    """
    with path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            lang = record.get("lang") or record.get("language") or ""
            srt  = record.get("srt") or record.get("content") or ""
            if srt:
                yield lang.lower(), parse_srt(srt)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(description="Stratified RetimeParams sweep")
    ap.add_argument("--ledger",    required=True, help="subs_generated.jsonl path")
    ap.add_argument("--output",    default="results/retime_stratified.json")
    ap.add_argument("--min-cues",  type=int, default=50,
                    help="Min cues per language to include in sweep")
    args = ap.parse_args()

    ledger = Path(args.ledger)
    if not ledger.exists():
        raise FileNotFoundError(f"Ledger not found: {ledger}")

    # --- Group by language --------------------------------------------------
    lang_cues: dict[str, list[Cue]] = defaultdict(list)
    for lang, cues in iter_ledger(ledger):
        lang_cues[lang].extend(cues)

    total_cues = sum(len(v) for v in lang_cues.values())
    print(f"\n=== Stratified RetimeParams Sweep ===")
    print(f"Ledger       : {ledger}")
    print(f"Languages    : {len(lang_cues)}")
    print(f"Total cues   : {total_cues:,}")
    print(f"Min-cues gate: {args.min_cues}\n")

    # --- Per-language sweep -------------------------------------------------
    results: list[dict] = []
    divergent_langs: list[str] = []

    for lang, cues in sorted(lang_cues.items()):
        if len(cues) < args.min_cues:
            print(f"  SKIP {lang:6s}  ({len(cues)} cues < {args.min_cues})")
            continue

        family = lang_family(lang)
        opt_params, opt_metrics = grid_search(cues, lang, family)
        global_metrics = evaluate(cues, GLOBAL_DEFAULT, lang)

        # Does the optimal target_cps diverge from the global default (17)?
        diverges = opt_params.target_cps != GLOBAL_DEFAULT.target_cps
        if diverges:
            divergent_langs.append(lang)

        tag = "  DIVERGES  " if diverges else "  same default"
        print(
            f"  {lang:8s} family={family:6s} n={len(cues):5,}  "
            f"critical {global_metrics.critical_rate_before*100:5.1f}% -> "
            f"opt {opt_metrics.critical_rate_after*100:5.1f}%  "
            f"target_cps={opt_params.target_cps:4.0f}  {tag}"
        )

        results.append({
            "lang":                    lang,
            "family":                  family,
            "n_cues":                  len(cues),
            "critical_rate_before":    round(global_metrics.critical_rate_before, 4),
            "critical_rate_global":    round(global_metrics.critical_rate_after,  4),
            "critical_rate_optimal":   round(opt_metrics.critical_rate_after,     4),
            "new_overlaps_optimal":    opt_metrics.new_overlaps,
            "diverges_from_global":    diverges,
            "optimal_params":          asdict(opt_params),
            "global_params":           asdict(GLOBAL_DEFAULT),
        })

    # --- Divergence summary -------------------------------------------------
    print(f"\n=== Divergence Summary ===")
    if divergent_langs:
        print(f"Languages with divergent optimal params: {divergent_langs}")
        print("→ Recommend shipping per-language RetimeParams matrix.")
    else:
        print("No material divergence detected.")
        print("→ Global default holds; document and close #408.")

    # Print the recommended matrix (industry-grounded)
    print("\n=== Recommended per-language RetimeParams matrix ===")
    print("(industry-grounded; override with sweep results if corpus differs)\n")
    printed = set()
    for lang, p in sorted(LANGUAGE_PARAMS.items()):
        canonical = lang.split("-")[0]
        if canonical in printed:
            continue
        printed.add(canonical)
        print(f"  {canonical:8s}  {p}")
    print(f"  {'*':8s}  {GLOBAL_DEFAULT}  # default (Latin/Cyrillic/etc.)")

    # --- Write JSON output --------------------------------------------------
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "global_default":    asdict(GLOBAL_DEFAULT),
        "per_language":      LANGUAGE_PARAMS_serialisable(),
        "sweep_results":     results,
        "divergent_langs":   divergent_langs,
        "total_cues":        total_cues,
    }
    out.write_text(json.dumps(payload, indent=2))
    print(f"\nResults written → {out}")


def LANGUAGE_PARAMS_serialisable() -> dict:
    seen = {}
    for k, v in LANGUAGE_PARAMS.items():
        base = k.split("-")[0]
        if base not in seen:
            seen[base] = asdict(v)
    return seen


if __name__ == "__main__":
    main()
