"""Book-scoped entity indexes for Ford EVTM wiring viewers.

The adapters in this module intentionally know nothing about HTML.  They turn
the explicit relationships in XML/SVG and Jet/MDB books into one deterministic
entity graph that can be tested independently from the viewer generator.
"""
from __future__ import annotations

import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Iterable


ENTITY_TYPES = ("component", "connector", "ground", "splice", "fuse", "harness", "circuit")


def clean(value: Any) -> str:
    """Normalize source text without changing its displayed spelling/case."""
    if value is None:
        return ""
    return " ".join(str(value).replace("\xa0", " ").split())


def normalized(value: Any) -> str:
    return clean(value).upper()


def entity_key(kind: str, name: Any) -> str:
    return f"{kind}:{normalized(name)}"


def _text(node: ET.Element | None, name: str) -> str:
    if node is None:
        return ""
    child = next((c for c in node if c.tag.lower() == name.lower()), None)
    return clean("".join(child.itertext())) if child is not None else ""


def _attrs(node: ET.Element) -> dict[str, str]:
    return {k.lower(): clean(v) for k, v in node.attrib.items()}


def _page_key(cell: Any, page: Any) -> str:
    c, p = clean(cell), clean(page)
    # A few decoded indexes render zero-padded numbers as "0 4". Treat
    # whitespace inside an otherwise numeric token as formatting, not data.
    if c.replace(" ", "").isdigit():
        c = c.replace(" ", "")
    if p.replace(" ", "").isdigit():
        p = p.replace(" ", "")
    if not c or not p:
        return ""
    return c.zfill(3) + p.zfill(3)


def _row_get(row: dict, *names: str) -> str:
    lower = {str(k).lower(): v for k, v in row.items()}
    for name in names:
        value = lower.get(name.lower())
        if value not in (None, ""):
            return clean(value)
    return ""


class WiringIndex:
    """Mutable normalized entity graph, finalized to compact JSON data."""

    def __init__(self, pages: dict[str, dict]):
        self.pages = pages
        self.entities: dict[str, dict] = {}
        self.warnings: list[str] = []

    def ensure(self, kind: str, name: Any, **fields: Any) -> str:
        name = clean(name)
        if not name or kind not in ENTITY_TYPES:
            return ""
        key = entity_key(kind, name)
        ent = self.entities.setdefault(key, {"type": kind, "name": name})
        # Prefer the first non-empty source spelling, but merge richer metadata.
        for field, value in fields.items():
            value = clean(value)
            if value and not ent.get(field):
                ent[field] = value
        return key

    def alias(self, key: str, value: Any) -> None:
        value = clean(value)
        if key and value and normalized(value) != normalized(self.entities[key]["name"]):
            self.entities[key].setdefault("aliases", []).append(value)

    def location(self, key: str, *, description: Any = "", qualifier: Any = "",
                 page: Any = "", grid: Any = "", zone: Any = "",
                 target: Any = "") -> None:
        if not key:
            return
        page = clean(page)
        target = clean(target) or (_page_key("151", page) if page else "")
        loc = {k: clean(v) for k, v in {
            "description": description, "qualifier": qualifier, "page": page,
            "grid": grid, "zone": zone, "target": target,
        }.items() if clean(v)}
        if target:
            loc["available"] = target in self.pages
            if target in self.pages:
                kind = self.entities[key]["type"]
                self.pages[target].setdefault("entities", {}).setdefault(kind, []).append(key)
            else:
                self.warnings.append(f"{key}: missing location target {target}")
        if loc:
            self.entities[key].setdefault("locations", []).append(loc)

    def reference(self, key: str, page: Any, qualifier: Any = "",
                  title: Any = "", subtitle: Any = "") -> None:
        page = clean(page)
        if not key or not page:
            return
        ref = {"page": page, "available": page in self.pages}
        for field, value in (("qualifier", qualifier), ("title", title), ("subtitle", subtitle)):
            value = clean(value)
            if value:
                ref[field] = value
        refs = self.entities[key].setdefault("refs", [])
        # Search files are authoritative and carry titles; page-local metadata
        # often repeats the same relation without them. Merge that exact
        # page/qualifier relationship while preserving qualifier variants.
        existing = next((r for r in refs if r["page"] == page and
                         normalized(r.get("qualifier", "")) == normalized(qualifier)), None)
        if existing is not None:
            for field in ("title", "subtitle"):
                if ref.get(field) and not existing.get(field):
                    existing[field] = ref[field]
        else:
            refs.append(ref)
        if page in self.pages:
            kind = self.entities[key]["type"]
            self.pages[page].setdefault("entities", {}).setdefault(kind, []).append(key)
        else:
            self.warnings.append(f"{key}: missing diagram target {page}")

    def relate(self, left: str, right: str, *, inferred: bool = False) -> None:
        if not left or not right or left == right:
            return
        for a, b in ((left, right), (right, left)):
            relation: Any = {"key": b, "inferred": True} if inferred else b
            self.entities[a].setdefault("related", []).append(relation)

    def pin(self, connector: str, pin: dict[str, Any]) -> None:
        if not connector:
            return
        row = {k: clean(v) for k, v in pin.items() if clean(v)}
        if not row:
            return
        self.entities[connector].setdefault("pins", []).append(row)
        circuit = row.get("circuit", "")
        if circuit and row.get("used", "1") != "0":
            circuit_key = self.ensure("circuit", circuit)
            endpoint = {"connector": connector}
            for field in ("cavity", "color", "gauge", "function", "qualifier"):
                if row.get(field):
                    endpoint[field] = row[field]
            self.entities[circuit_key].setdefault("endpoints", []).append(endpoint)
            self.relate(connector, circuit_key)

    @staticmethod
    def _dedup(values: Iterable[Any]) -> list[Any]:
        out, seen = [], set()
        for value in values:
            marker = repr(_canonical(value))
            if marker not in seen:
                seen.add(marker)
                out.append(value)
        return out

    def finalize(self) -> dict[str, dict]:
        for page in self.pages.values():
            memberships = page.get("entities", {})
            for kind in list(memberships):
                memberships[kind] = sorted(set(memberships[kind]))
                if not memberships[kind]:
                    del memberships[kind]
            if not memberships:
                page.pop("entities", None)
        for key, ent in self.entities.items():
            for field in ("aliases", "locations", "refs", "related", "pins", "endpoints"):
                if field in ent:
                    ent[field] = self._dedup(ent[field])
            if "refs" in ent:
                ent["refs"].sort(key=lambda r: (r["page"], normalized(r.get("qualifier", ""))))
            if "locations" in ent:
                ent["locations"].sort(key=lambda r: (
                    r.get("target", ""), normalized(r.get("qualifier", "")),
                    normalized(r.get("description", "")), r.get("grid", "")))
            if "related" in ent:
                ent["related"].sort(key=lambda r: r.get("key", "") if isinstance(r, dict) else r)
            if "pins" in ent:
                ent["pins"].sort(key=lambda r: _natural(r.get("cavity", "")))
            if "endpoints" in ent:
                ent["endpoints"].sort(key=lambda r: (r.get("connector", ""), _natural(r.get("cavity", ""))))
        self.warnings = sorted(set(self.warnings))
        return {key: self.entities[key] for key in sorted(self.entities)}


def _natural(value: str) -> tuple:
    return tuple(int(p) if p.isdigit() else p for p in re.split(r"(\d+)", value.upper()))


def _canonical(value: Any) -> Any:
    if isinstance(value, dict):
        return tuple((k, _canonical(v)) for k, v in sorted(value.items()))
    if isinstance(value, list):
        return tuple(_canonical(v) for v in value)
    if isinstance(value, str):
        return normalized(value)
    return value


def _xml(path: Path) -> ET.Element | None:
    # Callers only reach here for a file they intend to read, so a failure is a
    # source file we are silently dropping from the index - surface it instead.
    try:
        return ET.parse(path).getroot()
    except (ET.ParseError, OSError, UnicodeError) as exc:
        print(f"  warning: could not parse {path.name}: {exc}", file=sys.stderr, flush=True)
        return None


def _files(book_dir: Path) -> dict[str, Path]:
    return {p.name.lower(): p for p in book_dir.iterdir() if p.is_file()}


def _asset(files: dict[str, Path], value: str, suffix: str = ".svg") -> str:
    value = clean(value)
    if not value:
        return ""
    name = value if Path(value).suffix else value + suffix
    path = files.get(name.lower())
    return path.name if path else ""


def build_xml_index(book_dir: Path, book: str, pages: dict[str, dict]) -> tuple[dict, list[str]]:
    idx, files = WiringIndex(pages), _files(book_dir)
    book_l = book.lower()

    # Global name lists provide a complete browse index even when an item has
    # neither a location nor a schematic reference.
    global_lists = {
        "components.xml": "component", "connectors.xml": "connector",
        "grounds.xml": "ground", "splices.xml": "splice", "fuses.xml": "fuse",
    }
    for filename, kind in global_lists.items():
        root = _xml(files[filename]) if filename in files else None
        if root is not None:
            for node in root.iter():
                if node.tag.lower() == "item":
                    idx.ensure(kind, "".join(node.itertext()))

    # Rich location indexes. Component entries also declare connector links.
    rich = {
        "component": "component", "connector": "connector", "ground": "ground",
        "splice": "splice", "fuse": "fuse", "harness": "harness",
    }
    for stem, kind in rich.items():
        path = files.get(f"{book_l}{stem}_index.xml")
        root = _xml(path) if path else None
        if root is None:
            continue
        for entry in (n for n in root.iter() if n.tag.lower() == "entry"):
            key = idx.ensure(kind, _text(entry, "item"))
            idx.location(key, description=_text(entry, "location_desc"),
                         qualifier=_text(entry, "qual"), page=_text(entry, "page"),
                         grid=_text(entry, "gridref"))
            if kind == "component":
                for child in entry:
                    if child.tag.lower() == "conn":
                        conn = idx.ensure("connector", "".join(child.itertext()))
                        idx.relate(key, conn)

    # Search chunks are the authoritative reverse references.
    search_names = {"comp": "component", "conn": "connector", "ground": "ground",
                    "splice": "splice", "fuse": "fuse"}
    searched: set[str] = set()
    for path in sorted(book_dir.iterdir(), key=lambda p: p.name.lower()):
        m = re.match(rf"{re.escape(book_l)}(comp|conn|ground|splice|fuse)_search_.*\.xml$",
                     path.name.lower())
        if not m:
            continue
        kind, root = search_names[m.group(1)], _xml(path)
        if root is None:
            continue
        searched.add(kind)
        for item in (n for n in root.iter() if n.tag.lower() == "item"):
            name = clean(item.attrib.get("name") or _text(item, "name"))
            key = idx.ensure(kind, name)
            for ref in (n for n in item.iter() if n.tag.lower() == "ref_page"):
                page = _page_key(_text(ref, "cell"), _text(ref, "page"))
                idx.reference(key, page, _text(ref, "qual"), _text(ref, "title"),
                              _text(ref, "subtitle"))

    # Older books may only have pipe-separated ref files.
    fallback = {"component": "component_ref.xml", "connector": "connector_ref.xml",
                "ground": "ground_ref.xml", "splice": "splice_ref.xml",
                "fuse": "fuse_ref.xml"}
    for kind, filename in fallback.items():
        if kind in searched or filename not in files:
            continue
        root = _xml(files[filename])
        if root is None:
            continue
        for item in (n for n in root.iter() if n.tag.lower() == "item"):
            parts = [clean(v) for v in "".join(item.itertext()).split("|")]
            if len(parts) >= 3:
                idx.reference(idx.ensure(kind, parts[0]), _page_key(parts[1], parts[2]))

    # allcon gives canonical descriptions and points to the connector-face XML.
    face_ids: dict[str, str] = {}
    allcon = files.get(f"{book_l}allcon.xml")
    root = _xml(allcon) if allcon else None
    if root is not None:
        for conn in (n for n in root.iter() if n.tag.lower() == "conn"):
            key = idx.ensure("connector", _text(conn, "name"), description=_text(conn, "desc"))
            if key and _text(conn, "face_view"):
                face_ids[key] = _text(conn, "face_view")

    # Page XML fills page membership gaps and adds page-local location variants.
    for path in sorted(book_dir.iterdir(), key=lambda p: p.name.lower()):
        m = re.match(rf"{re.escape(book_l)}(\d{{3}})(\d{{3}})\.xml$", path.name.lower())
        if not m or (m.group(1) + m.group(2)) not in pages:
            continue
        page, root = m.group(1) + m.group(2), _xml(path)
        if root is None:
            continue
        for tag_name, kind in (("conn", "connector"), ("ground", "ground"),
                               ("splice", "splice"), ("fuse", "fuse")):
            for node in (n for n in root.iter() if n.tag.lower() == tag_name):
                key = idx.ensure(kind, _text(node, "name"))
                if not key:
                    continue
                idx.reference(key, page)
                target = ""
                loc_view = _text(node, "loc_view")
                lm = re.search(r"151(\d{3})$", loc_view, re.I)
                if lm:
                    target = "151" + lm.group(1)
                idx.location(key, description=_text(node, "loc"), page=_text(node, "locpage"),
                             zone=_text(node, "zone"), target=target)
                if kind == "connector" and _text(node, "face_view"):
                    face_ids.setdefault(key, _text(node, "face_view"))

    # Face XML is the authoritative source for part, harness, and pin metadata.
    for key, face_id in sorted(face_ids.items()):
        face_xml = files.get((face_id + ".xml").lower())
        root = _xml(face_xml) if face_xml else None
        if root is None:
            asset = _asset(files, face_id)
            if asset:
                idx.entities[key]["face_asset"] = asset
            continue
        connector = next((n for n in root.iter() if n.tag.lower() == "connector"), None)
        if connector is None:
            continue
        ca = _attrs(connector)
        for source, target in (("des", "description"), ("color", "color"),
                               ("gender", "gender"), ("base_part_number", "base_part")):
            if ca.get(source) and not idx.entities[key].get(target):
                idx.entities[key][target] = ca[source]
        face = next((n for n in connector.iter() if n.tag.lower() == "face"), None)
        if face is not None:
            fa = _attrs(face)
            if fa.get("fpn"):
                idx.entities[key]["face_part"] = fa["fpn"]
            asset = _asset(files, fa.get("file", ""))
            if asset:
                idx.entities[key]["face_asset"] = asset
            elif fa.get("file"):
                idx.entities[key]["face_asset"] = fa["file"]
                idx.entities[key]["face_available"] = False
                idx.warnings.append(f"{key}: missing connector face asset {fa['file']}")
            if fa.get("harnessid"):
                harness = idx.ensure("harness", fa["harnessid"])
                idx.entities[key]["harness_id"] = fa["harnessid"]
                idx.relate(key, harness)
        for pin in (n for n in connector.iter() if n.tag.lower() == "pin"):
            pa = _attrs(pin)
            idx.pin(key, {"cavity": pa.get("cavity"), "circuit": pa.get("circuitnumber"),
                          "color": pa.get("color"), "gauge": pa.get("guage") or pa.get("gauge"),
                          "function": pa.get("function"), "qualifier": pa.get("qualifier"),
                          "used": pa.get("used", "1")})

    return idx.finalize(), idx.warnings


def build_mdb_index(db: Any, pages: dict[str, dict]) -> tuple[dict, list[str]]:
    idx = WiringIndex(pages)
    tables = (("component", "Comp", "Compref"), ("connector", "CONN", "CONNREF"),
              ("ground", "grnd", "grndref"), ("splice", "splice", "splcref"),
              ("fuse", "Fuse", "Fuseref"))
    for kind, table, _ in tables:
        try:
            rows = db.read_table(table)
        except KeyError:
            continue
        for row in rows:
            key = idx.ensure(kind, _row_get(row, "NAME"),
                             description=_row_get(row, "DESCRIPTION", "DESC"))
            if not key:
                continue
            loc_page = _row_get(row, "LOCPAGE")
            idx.location(key, description=_row_get(row, "LOCATION"),
                         qualifier=_row_get(row, "QUALIFIER"), zone=_row_get(row, "ZONE"),
                         page=loc_page)
            ent = idx.entities[key]
            part = _row_get(row, "PARTNO")
            if part and not ent.get("base_part"):
                ent["base_part"] = part
            if kind == "component":
                connector = idx.ensure("connector", _row_get(row, "CONN_NAME"))
                idx.relate(key, connector)
            if kind == "connector":
                ent.setdefault("color", _row_get(row, "COLOR"))
                ent.setdefault("terminal", _row_get(row, "TERMINAL"))
            face = _page_key(_row_get(row, "CONN_CELL", "HCCF_CELL"),
                             _row_get(row, "CONN_PAGE", "HCCF_PAGE"))
            if face:
                ent["face_page"] = face
                ent["face_available"] = face in pages
                if face in pages:
                    pages[face].setdefault("entities", {}).setdefault(kind, []).append(key)
                else:
                    idx.warnings.append(f"{key}: missing connector face target {face}")

    for kind, _, table in tables:
        try:
            rows = db.read_table(table)
        except KeyError:
            continue
        for row in rows:
            key = idx.ensure(kind, _row_get(row, "NAME"))
            page = _page_key(_row_get(row, "CELL"), _row_get(row, "PAGE"))
            idx.reference(key, page, _row_get(row, "QUALIFIER"))

    return idx.finalize(), idx.warnings
