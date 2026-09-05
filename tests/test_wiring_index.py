import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))

from wiring_index import (  # noqa: E402
    build_mdb_index,
    build_xml_index,
    clean,
    entity_key,
)


def put(root, name, text):
    (root / name).write_text(text, encoding="utf-8")


def test_normalization_preserves_display_but_unifies_identity():
    assert clean("  ABS\xa0 control   module ") == "ABS control module"
    assert entity_key("component", " Abs\xa0Control Module ") == "component:ABS CONTROL MODULE"


def test_xml_adapter_builds_rich_cross_references(tmp_path):
    pages = {
        "010001": {"cell": "010", "page": "001", "title": "Grounds", "svg": "TST010001.SVG"},
        "054002": {"cell": "054", "page": "002", "title": "Climate", "svg": "TST054002.SVG"},
        "151004": {"cell": "151", "page": "004", "title": "Locations", "svg": "TST151004.SVG"},
        "151007": {"cell": "151", "page": "007", "title": "Locations", "svg": "TST151007.SVG"},
    }
    for name in ("TST010001.SVG", "TST054002.SVG", "TST151004.SVG", "TST151007.SVG",
                 "face.svg"):
        put(tmp_path, name, "<svg/>")
    put(tmp_path, "components.xml", "<items><item>ABS CONTROL MODULE</item></items>")
    put(tmp_path, "connectors.xml", "<items><item>C100</item></items>")
    put(tmp_path, "fuses.xml", "<items><item>F10</item></items>")
    put(tmp_path, "tstcomponent_index.xml", """<index><entry>
      <item>ABS\u00a0control module</item><location_desc>engine bay</location_desc>
      <page>0 4</page><gridref>A1</gridref><qual>4.0L</qual><conn>C100</conn>
    </entry></index>""")
    put(tmp_path, "tstconnector_index.xml", """<index>
      <entry><item>C100</item><location_desc>engine, LH</location_desc><page>4</page><gridref>E7</gridref><qual>4.0L</qual></entry>
      <entry><item>c100</item><location_desc>engine, RH</location_desc><page>7</page><gridref>F6</gridref><qual>4.6L</qual></entry>
      <entry><item>C999</item><location_desc>missing chart</location_desc><page>99</page><gridref>Z9</gridref><qual/></entry>
    </index>""")
    put(tmp_path, "tstharness_index.xml", """<index><entry><item>12B637</item>
      <location_desc>Engine control harness</location_desc><page>4</page><gridref>B2</gridref><qual>4.0L</qual>
    </entry></index>""")
    put(tmp_path, "TSTconn_search_C.xml", """<search><values><item name='C100'><pages>
      <ref_page><cell>010</cell><page>001</page><title>Grounds</title><subtitle/><qual/></ref_page>
      <ref_page><cell>054</cell><page>002</page><title>Climate</title><subtitle/><qual>4.0L</qual></ref_page>
    </pages></item></values></search>""")
    put(tmp_path, "TSTcomp_search_A.xml", """<search><values><item name='ABS CONTROL MODULE'><pages>
      <ref_page><cell>054</cell><page>002</page><title>Climate</title><subtitle/><qual/></ref_page>
    </pages></item></values></search>""")
    # This fallback duplicates a search ref and must not override/search-duplicate it.
    put(tmp_path, "connector_ref.xml", "<items><item>C100|010|001</item></items>")
    put(tmp_path, "TSTallcon.xml", """<search><values><conn><name>C100</name>
      <desc>A/C CLUTCH FIELD COIL</desc><face_view>tstcfc100</face_view></conn></values></search>""")
    put(tmp_path, "tstcfc100.xml", """<Faceviews><Connector CNumber='C100' Gender='MALE'
      COLOR='BK' BASE_PART_NUMBER='19D798' des='A/C CLUTCH FIELD COIL'><Faces>
      <Face FPN='F4SB-14A464-ABA' File='face.svg' HarnessId='12B637'/></Faces><Pins>
      <Pin Cavity='1' CircuitNumber='1205' Color='BK' Guage='14' Function='Ground' Used='1'/>
      <Pin Cavity='2' CircuitNumber='883' Color='PK-LB' Guage='14' Function='Relay output' Qualifier='4.0L' Used='1'/>
      </Pins></Connector></Faceviews>""")
    put(tmp_path, "TST010001.xml", """<page><connector_collection><conn><name>C100</name>
      <loc>engine, LH</loc><locpage>4</locpage><zone>E7</zone><loc_view>TST151004</loc_view>
      <face_view>tstcfc100</face_view></conn></connector_collection>
      <fuse_collection><fuse><name>f10</name><loc>junction box</loc><locpage>99</locpage></fuse></fuse_collection></page>""")

    entities, warnings = build_xml_index(tmp_path, "TST", pages)
    c100 = entities["connector:C100"]
    assert c100["description"] == "A/C CLUTCH FIELD COIL"
    assert {x["qualifier"] for x in c100["locations"] if x.get("qualifier")} == {"4.0L", "4.6L"}
    assert [r["page"] for r in c100["refs"]] == ["010001", "054002"]
    assert c100["face_asset"] == "face.svg"
    assert c100["base_part"] == "19D798"
    assert c100["harness_id"] == "12B637"
    assert len(c100["pins"]) == 2
    assert "component:ABS CONTROL MODULE" in c100["related"]
    assert "harness:12B637" in c100["related"]
    assert "circuit:1205" in c100["related"]
    assert entities["circuit:1205"]["endpoints"][0]["cavity"] == "1"
    assert "connector:C100" in pages["010001"]["entities"]["connector"]
    assert "harness:12B637" in pages["151004"]["entities"]["harness"]
    assert entities["fuse:F10"]["name"] == "F10"
    assert any("connector:C999: missing location target 151099" in w for w in warnings)
    assert any("fuse:F10: missing location target 151099" in w for w in warnings)


class FakeDb:
    def __init__(self, tables):
        self.tables = tables

    def read_table(self, name):
        if name not in self.tables:
            raise KeyError(name)
        return self.tables[name]


def test_mdb_adapter_supports_all_explicit_tables_and_omits_empty_types():
    pages = {
        "010001": {"cell": "010", "page": "001", "title": "Grounds"},
        "150002": {"cell": "150", "page": "002", "title": "Faces"},
        "151003": {"cell": "151", "page": "003", "title": "Locations"},
    }
    tables = {
        "Comp": [{"NAME": "ABS module", "QUALIFIER": "Cobra", "LOCATION": "engine",
                  "CONN_NAME": "C135", "LOCPAGE": "3", "ZONE": "A1", "PARTNO": "2C219"}],
        "CONN": [{"NAME": "C135", "LOCATION": "engine", "LOCPAGE": "3", "ZONE": "A1",
                  "CONN_CELL": "150", "CONN_PAGE": "2", "COLOR": "BK", "TERMINAL": "F"}],
        "grnd": [{"NAME": "G100", "LOCATION": "radiator support", "LOCPAGE": "3"}],
        "splice": [{"NAME": "S100", "LOCATION": "main harness", "LOCPAGE": "3"}],
        "Fuse": [{"Name": "F10", "Qualifier": "20A", "Location": "CJB", "Locpage": "3"}],
        "Compref": [{"NAME": "ABS module", "CELL": "010", "PAGE": "001"}],
        "CONNREF": [{"NAME": "C135", "CELL": "010", "PAGE": "001"}],
        "grndref": [{"NAME": "G100", "CELL": "010", "PAGE": "001"}],
        "splcref": [{"NAME": "S100", "CELL": "010", "PAGE": "001"}],
        "Fuseref": [{"NAME": "F10", "CELL": "010", "PAGE": "001"}],
    }
    entities, warnings = build_mdb_index(FakeDb(tables), pages)
    assert not warnings
    assert {e["type"] for e in entities.values()} == {"component", "connector", "ground", "splice", "fuse"}
    assert "connector:C135" in entities["component:ABS MODULE"]["related"]
    assert entities["connector:C135"]["face_page"] == "150002"
    assert entities["connector:C135"]["color"] == "BK"
    assert entities["component:ABS MODULE"]["base_part"] == "2C219"
    assert "connector:C135" in pages["150002"]["entities"]["connector"]
    for key in ("component:ABS MODULE", "connector:C135", "ground:G100", "splice:S100", "fuse:F10"):
        assert entities[key]["refs"][0]["page"] == "010001"
