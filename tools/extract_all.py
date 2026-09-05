#!/usr/bin/env python3
"""
Unified extraction: decode every archive member with the IDICOMP decoder
(raw fallback for the rare non-IDICOMP entry). Supersedes the Phase-1 raw pass.

Output: out/<ARCHIVE_STEM>/<member-name>
For v1 (POD BAY) archives whose members have no real name, a name is inferred
and an extension is sniffed from the decoded content.

Usage:
  python extract_all.py "D:\\content\\useni4" --out vol_example/content
"""
from __future__ import annotations

import argparse
import json
import os
import time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import idicomp
from extract_raw import _iter_entries


def sniff_ext(b: bytes) -> str:
    if b[:6] in (b"GIF89a", b"GIF87a"):
        return "gif"
    if b[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if b[:3] == b"\xff\xd8\xff":
        return "jpg"
    if b[:5] == b"%PDF-":
        return "pdf"
    head = b[:512].lstrip().lower()
    if head[:5] == b"<?xml":
        return "svg" if b"<svg" in b[:2048].lower() else "xml"
    if head[:5] == b"<html" or b"<html" in head[:64]:
        return "htm"
    if b"<workunit>" in b[:128].lower():
        return "epl"
    if b"workunit=" in b[:128].lower() or b"wcf" in b[:16].lower():
        return "wcf"
    return "bin"


def extract_one(p: Path, out_dir: Path) -> tuple[Counter, list]:
    """Decode a single archive into out_dir/<stem>/. Self-contained so it can
    run in a worker process; every archive writes only into its own dir."""
    stats = Counter()
    errors = []
    try:
        data = p.read_bytes()
        entries = list(_iter_entries(data, p.stem))
    except Exception as e:
        errors.append((p.name, f"parse: {e}"))
        return stats, errors
    arc_out = out_dir / p.stem
    arc_out.mkdir(parents=True, exist_ok=True)
    for name, doff, dsize in entries:
        if doff <= 0 or dsize <= 0 or doff + dsize > len(data):
            stats["_bad_entry"] += 1
            continue
        payload = data[doff:doff + dsize]
        try:
            dec = idicomp.decode(payload)
        except Exception as e:
            errors.append((p.name, f"{name}: {e}"))
            stats["_decode_err"] += 1
            continue
        if dec is None:            # not IDICOMP -> store raw
            dec = payload
            stats["_raw"] += 1
        if "." not in name:        # v1 inferred name -> add sniffed ext
            name = f"{name}.{sniff_ext(dec)}"
        (arc_out / name).write_bytes(dec)
        stats[name.rsplit(".", 1)[-1].lower()] += 1
    return stats, errors


# module-level worker (picklable): unpack args, call extract_one
def _worker(args: tuple[Path, Path]) -> tuple[dict, list]:
    stats, errors = extract_one(*args)
    return dict(stats), errors


def extract_locale(locale_dir: Path, out_dir: Path, limit: int = 0, jobs: int = 1) -> dict:
    arcs = sorted(locale_dir.glob("*.arc"), key=lambda p: p.name.upper())
    if limit:
        arcs = arcs[:limit]
    stats = Counter()
    errors = []
    t0 = time.time()

    if jobs <= 1:
        for n, p in enumerate(arcs, 1):
            s, e = extract_one(p, out_dir)
            stats.update(s)
            errors.extend(e)
            if n % 500 == 0:
                dt = time.time() - t0
                print(f"  ...{n}/{len(arcs)} archives  ({dt:.0f}s, {n/dt:.1f} arc/s)")
    else:
        payloads = [(p, out_dir) for p in arcs]
        done = 0
        # chunksize amortizes IPC over this many archives per task hand-off
        with ProcessPoolExecutor(max_workers=jobs) as ex:
            for s, e in ex.map(_worker, payloads, chunksize=16):
                stats.update(s)
                errors.extend(e)
                done += 1
                if done % 500 == 0:
                    dt = time.time() - t0
                    print(f"  ...{done}/{len(arcs)} archives  ({dt:.0f}s, {done/dt:.1f} arc/s)")

    stats["_seconds"] = int(time.time() - t0)
    stats["_jobs"] = jobs
    return {"stats": dict(stats), "errors": errors, "archives": len(arcs)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("locale_dir", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--jobs", type=int, default=0,
                    help="Parallel worker processes (default 0). "
                         "Use 0 for os.cpu_count(). Archives are independent, "
                         "so this scales well; the work is CPU-bound decoding.")
    args = ap.parse_args()
    jobs = args.jobs if args.jobs != 0 else (os.cpu_count() or 1)
    res = extract_locale(args.locale_dir, args.out, args.limit, jobs)
    (args.out / "_extract_stats.json").write_text(json.dumps(res, indent=1, default=str))
    print("\n== extract_all complete")
    print("archives:", res["archives"], " errors:", len(res["errors"]))
    for k, v in sorted(res["stats"].items()):
        print(f"   {k:14s} {v}")
    for e in res["errors"][:10]:
        print("  ERR", e)


if __name__ == "__main__":
    main()
