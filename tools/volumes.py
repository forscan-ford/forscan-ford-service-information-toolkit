#!/usr/bin/env python3
"""
Shared helper for discovering output volume directories.

A volume is any `vol_*` directory under the project root that has a
`content/` subdirectory (i.e. extract_all.py has run against it). This lets
every pipeline stage operate on however many volumes exist instead of a
hardcoded pair - volume names are chosen by whoever runs tso_convert.bat,
not baked into the tools.
"""
from __future__ import annotations

from pathlib import Path


def discover_volumes(root: Path) -> list[str]:
    return sorted(
        d.name for d in root.glob("vol_*")
        if d.is_dir() and (d / "content").is_dir()
    )
