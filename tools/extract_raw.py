#!/usr/bin/env python3
"""
Phase 1: extract RAW binary payloads (GIF/JPG/PNG/PDF) from a content locale.

Robustness note: the IDICOMP wrapper before a payload is not always a fixed
11 bytes (e.g. shared icons like maglasst.GIF carry 2 extra bytes before the
image). So instead of skipping a fixed header, we locate the real binary magic
inside the entry's data slice and extract from there to the entry end. This is
lossless for stored (uncompressed) binaries and immune to header-size variance.

Tokenized text members (htm/xml/svg/epl/wcf) are skipped here; they need the
IDICOMP decoder (Phase 2/3).

Usage:
  python extract_raw.py "D:\\content\\useni4" --out vol_example/content
"""
from __future__ import annotations

import argparse
import struct
from collections import Counter
from pathlib import Path

MAGIC_V2 = b"BAY POD\x02"
MAGIC_V1 = b"POD BAY\x01"

# binary magics we treat as raw/stored. ordered longest-first where relevant.
MAGICS = [
    (b"GIF89a", "gif"),
    (b"GIF87a", "gif"),
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"\xff\xd8\xff", "jpg"),
    (b"%PDF-", "pdf"),
]


def _u24le(b: bytes, off: int) -> int:
    return b[off + 1] | (b[off + 2] << 8) | (b[off + 3] << 16)


def _iter_entries(data: bytes, stem: str):
    """Yield (name, data_offset, data_size) for v1/v2 archives."""
    magic = data[:8]
    if magic == MAGIC_V2:
        nf = _u24le(data, 0x08)
        tnb = _u24le(data, 0x0C)
        toc = 0x10
        name_start = toc + nf * 16 + 1
        names = data[name_start:name_start + tnb]
        for i in range(nf):
            b = toc + i * 16
            noff = _u24le(data, b)
            nlen = _u24le(data, b + 4)
            doff = struct.unpack_from("<I", data, b + 9)[0]
            dsize = _u24le(data, b + 12)
            yield names[noff:noff + nlen].decode("ascii", "replace"), doff, dsize
    elif magic == MAGIC_V1:
        nf = _u24le(data, 0x08)
        toc = 0x0C
        # The v1 record's data_size is a u16 and truncates for entries > 64 KB.
        # Derive true sizes from the gap to the next entry's offset instead.
        offs = [struct.unpack_from("<I", data, toc + i * 15 + 9)[0] for i in range(nf)]
        order = sorted(range(nf), key=lambda i: offs[i])
        sizes = [0] * nf
        for j, i in enumerate(order):
            end = offs[order[j + 1]] if j + 1 < len(order) else len(data)
            sizes[i] = end - offs[i]
        for i in range(nf):
            yield f"{stem}_{i:04d}", offs[i], sizes[i]
    else:
        raise ValueError(f"unknown magic {magic!r}")


def _find_raw(slice_: bytes):
    """Return (ext, start_index) if a binary magic is found near the slice head."""
    # magic should appear within the first ~24 bytes (after IDICOMP wrapper)
    window = slice_[:32]
    best = None
    for sig, ext in MAGICS:
        idx = window.find(sig)
        if idx != -1 and (best is None or idx < best[1]):
            best = (ext, idx)
    return best


def extract_locale(locale_dir: Path, out_dir: Path, limit: int = 0) -> dict:
    arcs = sorted(locale_dir.glob("*.arc"), key=lambda p: p.name.upper())
    if limit:
        arcs = arcs[:limit]

    stats = Counter()
    bad = []
    for n, p in enumerate(arcs, 1):
        try:
            data = p.read_bytes()
        except Exception as e:
            bad.append((p.name, f"read: {e}"))
            continue
        arc_out = out_dir / p.stem
        wrote = 0
        for name, doff, dsize in _iter_entries(data, p.stem):
            if doff <= 0 or dsize <= 0 or doff + dsize > len(data):
                continue
            sl = data[doff:doff + dsize]
            found = _find_raw(sl)
            if not found:
                continue  # tokenized text -> Phase 2/3
            ext, start = found
            payload = sl[start:]
            if ext == "gif":
                # trim to GIF trailer if present (some entries pad)
                t = payload.rfind(b"\x3b")
                if t != -1:
                    payload = payload[:t + 1]
            arc_out.mkdir(parents=True, exist_ok=True)
            (arc_out / name).write_bytes(payload)
            stats[ext] += 1
            wrote += 1
        if wrote:
            stats["_archives_with_raw"] += 1
        if n % 500 == 0:
            print(f"  ...{n}/{len(arcs)}")
    return {"stats": dict(stats), "bad": bad, "archives": len(arcs)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("locale_dir", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    res = extract_locale(args.locale_dir, args.out, args.limit)
    print("\n== raw extraction complete")
    print("archives scanned:", res["archives"])
    for k, v in sorted(res["stats"].items()):
        print(f"   {k:24s} {v}")
    if res["bad"]:
        print("errors:", res["bad"][:10])


if __name__ == "__main__":
    main()
