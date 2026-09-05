import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))

from build_catalog import CATEGORY_TITLE  # noqa: E402
from build_site import CATNAMES  # noqa: E402


EXPECTED_DVD_NAMES = {
    "workshop": "Workshop Manuals",
    "wiring": "Wiring Diagrams",
    "tsb": "TSBs",
    "recall": "Field Service Actions (Recalls)",
    "pced": "PC/ED",
    "calib": "Engine/Emission Facts",
}


def test_catalog_uses_original_dvd_module_names():
    for category, name in EXPECTED_DVD_NAMES.items():
        assert CATEGORY_TITLE[category] == name


def test_site_uses_original_dvd_module_names():
    for category, name in EXPECTED_DVD_NAMES.items():
        assert CATNAMES[category] == name

