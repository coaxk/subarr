#!/usr/bin/env python3
"""#359 off-app tuning CLI: sweep RetimeParams across subarr's subgen corpus.

  python scripts/retime_tune.py --db /data/subarr.db      # from the subs_generated ledger
  python scripts/retime_tune.py --dir /path/to/srts       # from a folder of .srt files

Read-only: never writes subtitles or the DB."""

import argparse
import sys

from subarr.retime_tune import (
    corpus_from_dir,
    corpus_from_ledger,
    format_report,
    param_grid,
    retime_sweep,
)


def main() -> int:
    ap = argparse.ArgumentParser(description="Sweep RetimeParams across an SRT corpus.")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--db", help="subarr DB path; corpus = completed subs_generated rows")
    src.add_argument("--dir", help="folder of .srt files (bypasses the ledger)")
    ap.add_argument("--limit", type=int, default=0, help="cap corpus size (0 = all)")
    args = ap.parse_args()

    corpus = corpus_from_dir(args.dir) if args.dir else corpus_from_ledger(args.db)
    if args.limit:
        corpus = corpus[: args.limit]
    if not corpus:
        print("empty corpus — nothing to sweep", file=sys.stderr)
        return 1
    texts = [t for _, t in corpus]
    print(f"corpus: {len(texts)} subs\n")
    print(format_report(retime_sweep(texts, param_grid())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
