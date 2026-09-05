#!/usr/bin/env python3
"""
BAY POD archive extractor for legacy .arc files.

Supports two archive formats:

BAY POD v2 ("BAY POD\\x02"):
  [8]  magic
  [4]  num_files     (padded u24le: skip byte 0, read 3 LE)
  [4]  total_name_bytes (same encoding)
  [16 * num_files]  TOC records
  [1]  null separator
  [total_name_bytes] filename table (concatenated ASCII names)
  [...]  data segments

  TOC record (16 bytes):
    [1 pad][3 name_offset LE][1 pad][3 name_length LE]
    [1 pad][4 data_offset u32 LE][3 data_size LE]

POD BAY v1 ("POD BAY\\x01"):
  [8]  magic
  [4]  num_files (padded u24le)
  [15 * num_files]  TOC records (no filename table)
  [...]  data segments

  TOC record (15 bytes):
    [9]  metadata (dBASE pointers / internal IDs)
    [4]  data_offset (u32 LE)
    [2]  data_size   (u16 LE)

Data segments start with "\\x01IDICOMP" (8 bytes) + 3 unknown bytes = 11 byte
header, followed by the payload. The payload may be raw (binary files) or
IDICOMP token-encoded (text files like HTM, EPL, SVG).
"""

from __future__ import annotations

import argparse
import json
import logging
import struct
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)

MAGIC_V2 = b"BAY POD\x02"
MAGIC_V1 = b"POD BAY\x01"
IDICOMP_SIG = b"\x01IDICOMP"
IDICOMP_HEADER_SIZE = 11  # 01 + IDICOMP + 3 unknown bytes


def _u24le(data: bytes, offset: int) -> int:
    """Read a padded u24le: skip data[offset], read 3 bytes LE."""
    return data[offset + 1] | (data[offset + 2] << 8) | (data[offset + 3] << 16)


@dataclass
class FileEntry:
    name: str
    data_offset: int
    data_size: int
    has_idicomp_header: bool = False

    @property
    def payload_offset(self) -> int:
        return self.data_offset + IDICOMP_HEADER_SIZE if self.has_idicomp_header else self.data_offset

    @property
    def payload_size(self) -> int:
        return self.data_size - IDICOMP_HEADER_SIZE if self.has_idicomp_header else self.data_size


@dataclass
class Archive:
    path: Path
    version: int          # 1 or 2
    num_files: int
    total_name_bytes: int  # 0 for v1
    entries: List[FileEntry] = field(default_factory=list)
    _data: bytes = field(default=b"", repr=False)

    def payload(self, entry: FileEntry) -> bytes:
        return self._data[entry.payload_offset:entry.data_offset + entry.data_size]


def _detect_idicomp(data: bytes, offset: int) -> bool:
    return offset + 8 <= len(data) and data[offset:offset + 8] == IDICOMP_SIG


def _infer_filename(data: bytes, entry_data_offset: int, entry_data_size: int,
                    arc_stem: str, index: int) -> str:
    """Guess a filename from the payload content."""
    payload_start = entry_data_offset
    if _detect_idicomp(data, entry_data_offset):
        payload_start += IDICOMP_HEADER_SIZE

    snippet = data[payload_start:payload_start + 16]

    if snippet[:3] == b"GIF":
        ext = "gif"
    elif snippet[:4] == b"%PDF":
        ext = "pdf"
    elif snippet[:5] == b"<?xml":
        ext = "xml"
    elif snippet[:4] == b"\x89PNG":
        ext = "png"
    elif snippet[:2] in (b"\xff\xd8",):
        ext = "jpg"
    elif b"<html" in snippet.lower() or b"<HTML" in snippet:
        ext = "htm"
    elif snippet[:5] == b"; wcf" or snippet[:11] == b"wcf_version":
        ext = "wcf"
    elif b"<workunit>" in data[payload_start:payload_start + 64]:
        ext = "epl"
    else:
        ext = "bin"

    return f"{arc_stem}_{index:04d}.{ext}"


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------

def _parse_v2(data: bytes, path: Path) -> Archive:
    num_files = _u24le(data, 0x08)
    total_name_bytes = _u24le(data, 0x0C)
    toc_start = 0x10
    toc_end = toc_start + num_files * 16
    name_start = toc_end + 1

    if name_start + total_name_bytes > len(data):
        raise ValueError("Name table extends beyond file")

    arc = Archive(path=path, version=2, num_files=num_files,
                  total_name_bytes=total_name_bytes, _data=data)

    for i in range(num_files):
        base = toc_start + i * 16
        noff = _u24le(data, base)
        nlen = _u24le(data, base + 4)
        doff = struct.unpack_from("<I", data, base + 9)[0]
        dsize = _u24le(data, base + 12)

        name = data[name_start + noff:name_start + noff + nlen].decode("ascii", "replace")
        arc.entries.append(FileEntry(
            name=name, data_offset=doff, data_size=dsize,
            has_idicomp_header=_detect_idicomp(data, doff),
        ))

    return arc


def _parse_v1(data: bytes, path: Path) -> Archive:
    num_files = _u24le(data, 0x08)
    toc_start = 0x0C
    arc_stem = path.stem

    arc = Archive(path=path, version=1, num_files=num_files,
                  total_name_bytes=0, _data=data)

    for i in range(num_files):
        base = toc_start + i * 15
        doff = struct.unpack_from("<I", data, base + 9)[0]
        dsize = struct.unpack_from("<H", data, base + 13)[0]

        name = _infer_filename(data, doff, dsize, arc_stem, i)
        arc.entries.append(FileEntry(
            name=name, data_offset=doff, data_size=dsize,
            has_idicomp_header=_detect_idicomp(data, doff),
        ))

    return arc


def parse_archive(path: Path) -> Archive:
    """Parse a .arc file (v1 or v2) and return the Archive."""
    data = path.read_bytes()
    if len(data) < 12:
        raise ValueError(f"File too small: {len(data)} bytes")

    magic = data[:8]
    if magic == MAGIC_V2:
        return _parse_v2(data, path)
    elif magic == MAGIC_V1:
        return _parse_v1(data, path)
    else:
        raise ValueError(f"Unknown magic: {magic!r}")


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------

def extract_archive(arc: Archive, out_dir: Path, *, dry_run: bool = False) -> dict:
    """Extract all files from an archive into out_dir."""
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "archive": arc.path.name,
        "version": arc.version,
        "num_files": arc.num_files,
        "files": [],
    }

    for entry in arc.entries:
        payload = arc.payload(entry)
        manifest["files"].append({
            "name": entry.name,
            "size": len(payload),
            "has_idicomp_header": entry.has_idicomp_header,
        })

        if not dry_run:
            (out_dir / entry.name).write_bytes(payload)

    if not dry_run:
        (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))

    return manifest


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> None:
    parser = argparse.ArgumentParser(description="BAY POD .arc extractor")
    parser.add_argument("input", type=Path, help="Single .arc or directory")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--batch", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    if args.input.is_dir():
        if not args.batch:
            logger.error("Use --batch to process a directory")
            raise SystemExit(1)
        arcs = sorted(args.input.glob("*.arc"), key=lambda p: p.name.upper())
    else:
        arcs = [args.input]

    if args.limit > 0:
        arcs = arcs[:args.limit]

    ok = fail = 0
    for i, path in enumerate(arcs, 1):
        if len(arcs) > 1:
            logger.info("Processing %d/%d: %s", i, len(arcs), path.name)
        try:
            arc = parse_archive(path)
            dest = args.out / arc.path.stem
            m = extract_archive(arc, dest, dry_run=args.dry_run)
            logger.info("%s: %d files extracted", path.name, len(m["files"]))
            ok += 1
        except Exception:
            logger.exception("Failed: %s", path)
            fail += 1

    if len(arcs) > 1:
        logger.info("Done: %d ok, %d failed", ok, fail)


if __name__ == "__main__":
    main()
