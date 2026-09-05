#!/usr/bin/env python3
"""
Recover member filenames for POD BAY v1 archives.

v1 containers carry no filename table, so extraction produced synthesized
names (<CODE>_<index>.<ext>). Source names can often be recovered:

  1. The archive's own HTML references filenames (frameset frames,
     hrefs, stylesheet links), the coverage DB names the entry file, and the
     .epl declares <filename>. Together these give the candidate name set.
  2. v1 TOCs are ordered alphabetically by source name, case-insensitive.

So: sort candidates case-insensitively and align them against the members in
TOC order, requiring extension-class compatibility (a css-as-.htm sniffs as
.bin, etc.). Members with no candidate keep their synthesized name; candidates
not present in the archive (e.g. shared stock images served elsewhere) are
skipped. After renaming, the directory's local references are re-resolved: if
the resolved-reference count did not improve, the rename is rolled back.

Run AFTER extract_all.py and BEFORE build_site.py. Idempotent (renamed dirs
no longer match the synthesized pattern, so they are skipped).
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from volumes import discover_volumes  # noqa: E402

ENC = "latin-1"

REF = re.compile(r'''(?:href|src)\s*=\s*(?:"([^"]+)"|'([^']+)'|([A-Za-z0-9_.\-]+))''', re.I)
SYNTH = None  # per-archive regex


def local_refs(text: str) -> set[str]:
    out = set()
    for m in REF.finditer(text):
        ref = (m.group(1) or m.group(2) or m.group(3) or "").strip()
        ref = ref.split("#")[0].split("?")[0]
        if not ref or "/" in ref or "\\" in ref or ":" in ref:
            continue
        if "." not in ref:
            continue
        out.add(ref)
    return out


# member extensions come from content sniffing; these sniffs are reliable
RELIABLE = {"gif", "pdf", "png", "jpg", "xml", "epl", "wcf"}


def compatible(cand: str, member_ext: str) -> bool:
    ce = cand.rsplit(".", 1)[-1].lower()
    if ce == member_ext:
        return True
    if member_ext == "htm" and ce in ("htm", "html"):
        return True
    if member_ext == "bin":                # unsniffable: css-in-htm, js, txt...
        return ce not in RELIABLE
    return False


def resolved_count(d: Path) -> tuple[int, int]:
    """(#resolved, #total) local references across the dir's htm files."""
    names = {p.name.lower() for p in d.iterdir()}
    res = tot = 0
    for p in d.iterdir():
        if p.suffix.lower() not in (".htm", ".html"):
            continue
        for r in local_refs(p.read_text(encoding=ENC, errors="replace")):
            tot += 1
            if r.lower() in names:
                res += 1
    return res, tot


def recover_dir(d: Path, entry_names: dict[str, str],
                archives_upper: dict[str, str]) -> dict[str, str] | None:
    code = d.name
    synth = re.compile(rf"^{re.escape(code)}_(\d{{4}})\.(\w+)$", re.I)

    def owning_archive(name: str) -> str | None:
        """Longest archive-name prefix of a filename (the workunit rule)."""
        up = name.upper()
        best = ""
        for a in archives_upper:
            if up.startswith(a) and len(a) > len(best):
                best = a
        return best or None
    members = []
    for p in d.iterdir():
        m = synth.match(p.name)
        if m:
            members.append((int(m.group(1)), p))
    if not members:
        return None
    members.sort()

    # candidate real names: content references + coverage entry + epl/wcf
    cands: dict[str, str] = {}          # lower -> preferred spelling

    def add(name: str):
        # a name whose stem belongs to a DIFFERENT workunit is a cross-archive
        # reference (e.g. a TSB citing the bulletin it supersedes), never a
        # member of this archive
        owner = owning_archive(name)
        if owner and owner != code.upper():
            return
        cands.setdefault(name.lower(), name)

    entry = entry_names.get(code.upper())
    if entry:
        add(entry)
    for _, p in members:
        if p.suffix.lower() in (".htm", ".epl"):
            t = p.read_text(encoding=ENC, errors="replace")
            for r in local_refs(t):
                add(r)
            fm = re.search(r"<filename>\s*([A-Za-z0-9_.\-]+)\s*</filename>", t, re.I)
            if fm:
                add(fm.group(1))
        if p.suffix.lower() == ".epl":
            add(f"{code}.epl")
        if p.suffix.lower() == ".wcf":
            add(f"{code}.wcf")
    if entry:  # entry spelling wins over any lowercase ref to itself
        cands[entry.lower()] = entry

    ordered = sorted(cands.values(), key=str.lower)

    # monotone alignment: members (TOC order) vs candidates (alphabetical)
    before = resolved_count(d)
    mapping: dict[str, str] = {}
    i = j = 0
    existing_lower = {p.name.lower() for p in d.iterdir()}
    while i < len(members) and j < len(ordered):
        _, p = members[i]
        cand = ordered[j]
        if compatible(cand, p.suffix.lstrip(".").lower()):
            if cand.lower() not in existing_lower or cand.lower() == p.name.lower():
                mapping[p.name] = cand
            i += 1
            j += 1
        elif any(compatible(cand, m[1].suffix.lstrip(".").lower()) for m in members[i + 1:]):
            i += 1                       # member has no recoverable name
        else:
            j += 1                       # candidate not shipped in archive

    if not mapping:
        return None
    for old, new in mapping.items():
        (d / old).rename(d / new)
    after = resolved_count(d)
    if after[0] < before[0]:             # regression: roll back
        for old, new in mapping.items():
            (d / new).rename(d / old)
        return None
    return mapping


def main():
    root = Path(".")
    for vol in discover_volumes(root):
        voldir = root / vol
        cdir = voldir / "content"
        if not cdir.exists():
            continue
        inv = json.load(open(voldir / "inventory.json"))
        v1 = {a["archive"].rsplit(".", 1)[0].upper()
              for a in inv["archives"] if a.get("version") == 1}
        archives_upper = {d.name.upper(): d.name
                          for d in cdir.iterdir() if d.is_dir()}
        entry_names: dict[str, str] = {}
        covp = voldir / "coverage.json"
        if covp.exists():
            cov = json.load(open(covp, encoding="utf-8"))
            for doc in cov.get("tsb", []) + cov.get("recall", []):
                fn = doc["filename"]
                entry_names[Path(fn).stem.upper()] = fn
        renamed_dirs = renamed_files = 0
        report = {}
        for d in sorted(cdir.iterdir()):
            if not d.is_dir() or d.name.upper() not in v1:
                continue
            mapping = recover_dir(d, entry_names, archives_upper)
            if mapping:
                renamed_dirs += 1
                renamed_files += len(mapping)
                report[d.name] = mapping
        (voldir / "v1_names.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
        print(f"{vol}: recovered names in {renamed_dirs} v1 archives "
              f"({renamed_files} files); map -> {vol}/v1_names.json")


if __name__ == "__main__":
    main()
