#!/usr/bin/env python3
"""
Minimal FoxPro table reader for source navigation databases
(<source>/data/<MODULE>/{s_,v_,tb,rc,...}USEN.dbf + .fpt memo files).

These tables provide titles and entry files for generated navigation.
"""
from __future__ import annotations

import struct
from pathlib import Path

ENC = "cp1252"


class Fpt:
    """FoxPro memo file: 512-byte header, block size at offset 6 (u16 BE),
    each memo = 8-byte header (type u32 BE, length u32 BE) + data."""

    def __init__(self, path: Path):
        self._data = path.read_bytes()
        self._bs = struct.unpack(">H", self._data[6:8])[0]

    def get(self, index: int) -> str:
        off = index * self._bs
        _typ, ln = struct.unpack(">II", self._data[off:off + 8])
        return self._data[off + 8:off + 8 + ln].decode(ENC, errors="replace")


def read_dbf(path: Path) -> list[dict]:
    """Read all records of a .dbf, resolving memo ('M') fields through the
    sibling .fpt when present. Deleted records (flag '*') are skipped."""
    data = path.read_bytes()
    nrec, hdrlen, reclen = struct.unpack_from("<IHH", data, 4)

    fields = []
    off = 32
    while off < hdrlen - 1 and data[off] != 0x0D:
        raw = data[off:off + 32]
        name = raw[:11].split(b"\0")[0].decode(errors="replace")
        ftype = chr(raw[11])
        flen = raw[16]
        fields.append((name, ftype, flen))
        off += 32

    fpt = None
    if any(f[1] == "M" for f in fields):
        for cand in (path.with_suffix(".fpt"), path.with_suffix(".FPT")):
            if cand.exists():
                fpt = Fpt(cand)
                break

    rows = []
    pos = hdrlen
    for _ in range(nrec):
        rec = data[pos:pos + reclen]
        pos += reclen
        if not rec or rec[0:1] == b"*":
            continue
        o = 1
        row = {}
        for name, ftype, flen in fields:
            v = rec[o:o + flen].decode(ENC, errors="replace").strip()
            o += flen
            if ftype == "M":
                v = fpt.get(int(v)) if (fpt and v.isdigit() and int(v) > 0) else ""
            row[name] = v
        rows.append(row)
    return rows


def coverage(data_dir: Path, module: str, locale: str = "USEN") -> list[dict]:
    """Join v_<locale> (year/model/qualifier -> sectcode) with s_<locale>
    (sectcode -> title/dest). Returns one row per vehicle/section pair:
    {year, model, qualifier, title, dest}.

    Source sets may split data by category, so a given source may not carry
    every module. A missing module directory is empty coverage, not an error."""
    mdir = data_dir / module
    if not mdir.is_dir():
        return []
    sections = {}
    for r in read_dbf(mdir / f"s_{locale}.dbf"):
        sections[r["SECTCODE"]] = (r["DESC"].strip(), r["DEST"].strip())
    out = []
    for r in read_dbf(mdir / f"v_{locale}.dbf"):
        title, dest = sections.get(r["SECTCODE"], ("", ""))
        if not dest:
            continue
        out.append({
            "year": r["YEAR"], "model": r["MODEL"],
            "qualifier": r.get("QUALIFIER", ""),
            "title": title, "dest": dest,
        })
    return out


def tsb_table(data_dir: Path, locale: str = "USEN") -> list[dict]:
    """One row per bulletin: article number, titles, entry file, date,
    supersession info. Empty if this disc carries no TSB directory."""
    out = []
    if not (data_dir / "TSB").is_dir():
        return out
    for r in read_dbf(data_dir / "TSB" / f"tb{locale}.dbf"):
        titles = [t.strip() for t in r["TITLES"].replace("\r\n", "\n").split("|")]
        out.append({
            "year": r["YEAR"], "book": r["BOOK"], "article": r["ARTICLE"],
            "titles": [t for t in titles if t],
            "filename": r["FILENAME"], "date": r["DATE"],
            "super": r.get("SUPER", ""),
        })
    return out


def recall_table(data_dir: Path, locale: str = "USEN") -> list[dict]:
    """One row per recall/field service action: id, title, entry file, date.
    Empty if this disc carries no RECALL directory."""
    out = []
    if not (data_dir / "RECALL").is_dir():
        return out
    for r in read_dbf(data_dir / "RECALL" / f"rc{locale}.dbf"):
        out.append({
            "recall": r["RECALL"], "year": r["YEAR"],
            "title": r["TITLE"].strip(),
            "filename": r["FILENAME"], "date": r["DATE"],
        })
    return out
