#!/usr/bin/env python3
"""
THE single link-staticizing pass. Rewrites every dynamic/absolute reference in
decoded content HTML so the static site navigates entirely offline. Idempotent;
safe to re-run. No other tool rewrites content links.

Endpoint handling matches legacy ASP endpoint references by substring:

  *colframeset.asp?leftside=A&rightside=B   -> _2col.html?l=A&r=B
     (covers 2colframeset.asp AND pced_2colframeset.asp - same handler)
  front.asp?...                             -> _front.html
  idisp_frame.asp / dodisp.asp?...          -> _fig.html?...   (figure viewer;
     query construction in content JS is preserved verbatim)
  ep_main/edirect/eframe.asp - keyed (book=&cell=) AND positional
     (?RTYPE=DIAGRAM=<cell>=<book>=...)     -> ../BOOK/_wire.html#CELL
  /tpscontent|/tpsreposit|/tsorep/<loc>/<F> -> F (same book) or ../ARCHIVE/F
     (archive resolved by workunit lookup: longest existing
      archive-name prefix of the filename, case-insensitive)

Files are read and written as latin-1: byte-preserving for every byte we do
not explicitly rewrite (content is windows-1252; substitutions are ASCII).
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from volumes import discover_volumes  # noqa: E402

ENC = "latin-1"

COLFRAME = re.compile(
    r'/tpsasps/[A-Za-z0-9_]*colframeset\.asp\?leftside=([^&"\']+)&(?:amp;)?rightside=([^"\'&]+)', re.I)
FRONT = re.compile(r'/tpsasps/front\.asp\?[^"\']*', re.I)
FIG = re.compile(r'/tpsasps/(?:idisp_frame|dodisp)\.asp\?', re.I)
EP_MAIN = re.compile(r'/tpsasps/(?:wiringsvg/)?(?:ep_main|edirect|eframe)\.asp\?([^"\'>]*)', re.I)
REPO = re.compile(r'/(?:tpscontent|tpsreposit|tsorep)/[A-Za-z0-9]+/([A-Za-z0-9_.\-]+)', re.I)
QS = re.compile(r'(\w+)=([^&]*)')
# bare relative refs: legacy content may address workunit files in one flat
# namespace (/tsorep/<locale>/<file>), so content may reference another
# archive's file by bare name (e.g. a TSB citing the bulletin it supersedes).
# Our layout is one dir per archive, so those need ../ARCHIVE/ prefixes.
BAREREF = re.compile(
    r'''((?:href|src)\s*=\s*)(?:"([A-Za-z0-9_.\-]+)"|'([A-Za-z0-9_.\-]+)'|([A-Za-z0-9_.\-]+\.[A-Za-z0-9]+))''',
    re.I)


def rewrite_ep_main(m: re.Match, archives_upper: dict[str, str]) -> str:
    q = m.group(1).replace("&amp;", "&")
    qs = {k.lower(): v for k, v in QS.findall(q)}
    book = qs.get("book", "").upper()
    cell = qs.get("cell", "")
    if not book and qs.get("rtype", "").count("=") >= 2:
        # positional form: RTYPE=DIAGRAM=<cell>=<book>=<legacy>=<vehicle>=...
        parts = qs["rtype"].split("=")
        cell, book = parts[1], parts[2].upper()
    if book not in archives_upper:
        return m.group(0)
    cell = (cell.lstrip("0") or "0") if cell else ""
    frag = f"#{cell}" if cell else ""
    return f"../{archives_upper[book]}/_wire.html{frag}"


def rewrite_repo(m: re.Match, current_names: set[str], archives_upper: dict[str, str]) -> str:
    fname = m.group(1)
    if fname.lower() in current_names:
        return fname
    up = fname.upper()
    best = ""
    for a in archives_upper:            # longest archive-name prefix wins
        if up.startswith(a) and len(a) > len(best):
            best = a
    if best:
        return f"../{archives_upper[best]}/{fname}"
    return fname


def rewrite_bareref(m: re.Match, current_names: set[str], this_name_upper: str,
                    archives_upper: dict[str, str],
                    members_by_arch: dict[str, set[str]]) -> str:
    name = m.group(2) or m.group(3) or m.group(4)
    if "." not in name or name.lower() in current_names:
        return m.group(0)
    up = name.upper()
    best = ""
    for a in archives_upper:
        if up.startswith(a) and len(a) > len(best):
            best = a
    if not best or best == this_name_upper:
        return m.group(0)
    if name.lower() not in members_by_arch.get(best, set()):
        return m.group(0)
    quote = '"' if m.group(2) else ("'" if m.group(3) else '"')
    return f"{m.group(1)}{quote}../{archives_upper[best]}/{name}{quote}"


def process(root: Path):
    for vol in discover_volumes(root):
        cdir = root / vol / "content"
        if not cdir.exists():
            continue
        archive_dirs = [d for d in cdir.iterdir() if d.is_dir()]
        archives_upper = {d.name.upper(): d.name for d in archive_dirs}
        members_by_arch = {
            d.name.upper(): {p.name.lower() for p in d.iterdir()}
            for d in archive_dirs
        }
        changed = scanned = 0
        for d in archive_dirs:
            current_names = members_by_arch[d.name.upper()]
            for p in d.iterdir():
                if p.suffix.lower() != ".htm":
                    continue
                scanned += 1
                t = orig = p.read_text(encoding=ENC)
                t = COLFRAME.sub(lambda m: f"_2col.html?l={m.group(1)}&r={m.group(2)}", t)
                t = FRONT.sub("_front.html", t)
                t = FIG.sub("_fig.html?", t)
                t = EP_MAIN.sub(lambda m: rewrite_ep_main(m, archives_upper), t)
                t = REPO.sub(lambda m: rewrite_repo(m, current_names, archives_upper), t)
                t = BAREREF.sub(lambda m: rewrite_bareref(
                    m, current_names, d.name.upper(), archives_upper, members_by_arch), t)
                if t != orig:
                    p.write_text(t, encoding=ENC)
                    changed += 1
        print(f"{vol}: scanned {scanned} htm files, rewrote {changed}")


if __name__ == "__main__":
    process(Path("."))
