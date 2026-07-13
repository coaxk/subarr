#!/usr/bin/env python3
"""#407 read-only CLI: sweep the multilingual chunk-confidence threshold T over
the accrued audio-lang audit corpus.

  python scripts/multilang_tune.py --db /data/subarr.db

Read-only: never runs subgen/detection, only reads stored chunks_conf."""

import argparse
import sys

from subarr.multilang_tune import (
    corpus_from_audit_store,
    format_report,
    multilang_sweep,
    prob_distribution,
    t_grid,
)


def main() -> int:
    ap = argparse.ArgumentParser(description="Sweep multilingual chunk-confidence threshold T (read-only).")
    ap.add_argument("--db", required=True, help="subarr DB path; corpus = audio_lang_audit.chunks_conf rows")
    ap.add_argument("--limit", type=int, default=0, help="cap corpus size (0 = all)")
    args = ap.parse_args()

    limit = args.limit or None  # 0 -> gather everything
    corpus = corpus_from_audit_store(args.db, limit=limit)
    if not corpus:
        print("empty corpus - nothing to sweep", file=sys.stderr)
        return 1

    print(format_report(multilang_sweep(corpus, t_grid()), prob_distribution(corpus)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
