#!/usr/bin/env python3
"""
Extract source navigation databases (FoxPro coverage tables under
<source>/data) into vol_XX/coverage.json. This is the authoritative source for:

  workshop/pced/evtm/calib : year + model (+ engine) -> title + entry dest
  tsb / recall             : per-document entry file, titles, dates

build_site.py consumes coverage.json; .epl-derived catalog.json remains the
fallback for anything the DBFs don't cover.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from navdb import coverage, recall_table, tsb_table  # noqa: E402

MODULES = ("SERVICE", "PCED", "EVTM", "CALIB")


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description="Extract source navigation coverage DBFs into coverage.json."
    )
    ap.add_argument("--root", type=Path, default=Path("."),
                    help="Project root containing the vol_* output directories."
                         " Default: current directory.")
    ap.add_argument("--vol", action="append", default=[],
                    help="Output volume name, e.g. vol_05_06. Repeatable.")
    ap.add_argument("--data", action="append", default=[],
                    help="Source data directory for the matching --vol"
                         " (for example: D:\\data)."
                         " Must be given once per --vol, in the same order.")
    args = ap.parse_args()
    if len(args.vol) != len(args.data):
        ap.error("--vol and --data must be given the same number of times, paired in order")
    if not args.vol:
        ap.error("at least one --vol/--data pair is required")
    return args


def main():
    args = parse_args()
    root = args.root
    for vol, data_dir in zip(args.vol, args.data):
        data_dir = Path(data_dir)
        if not (root / vol).exists() or not data_dir.exists():
            raise SystemExit(f"missing required path for {vol}: {root / vol} or {data_dir}")
        cov = {"data_dir": str(data_dir)}
        for mod in MODULES:
            rows = coverage(data_dir, mod)
            cov[mod.lower()] = rows
            print(f"{vol}: {mod} {len(rows)} coverage rows")
        cov["tsb"] = tsb_table(data_dir)
        cov["recall"] = recall_table(data_dir)
        print(f"{vol}: tsb {len(cov['tsb'])}, recall {len(cov['recall'])}")
        out = root / vol / "coverage.json"
        out.write_text(json.dumps(cov, indent=1), encoding="utf-8")
        print(f"wrote {out}")


if __name__ == "__main__":
    main()
