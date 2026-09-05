import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))

import build_wiring  # noqa: E402
from build_wiring import (  # noqa: E402
    VIEWER_HTML,
    VIEWER_PDF_HTML,
    _pdf_for,
    build_book,
    build_pdf_book,
    cdata,
    parse_items,
    tag,
)


PAGE_XML = """<page>
<ground_collection>
<ground><name>G100</name><loc>engine compartment</loc><locpage>2</locpage>
<zone>A3</zone><loc_view>EBK151002</loc_view></ground>
<ground><name>G100</name><loc>engine compartment</loc><locpage>2</locpage>
<zone>A3</zone><loc_view>EBK151002</loc_view></ground>
<ground><name>G101</name><loc>engine, front</loc><zone>D6</zone>
<loc_view>MISSING</loc_view></ground>
</ground_collection>
<splice_collection>
<splice><name>S113</name><loc>Wiring harness near C175</loc></splice>
<splice><name></name><loc>unnamed - skip me</loc></splice>
</splice_collection>
</page>"""


def test_parse_items_dedups_and_resolves_loc_view(tmp_path):
    (tmp_path / "EBK151002.SVG").write_text("<svg/>")
    items = parse_items(PAGE_XML, "ground", tmp_path)
    assert [i["n"] for i in items] == ["G100", "G101"]
    assert items[0]["locv"] == "EBK151002.SVG"
    assert items[0]["z"] == "A3"
    assert "locv" not in items[1]  # unresolvable view omitted, entry kept


def test_parse_items_requires_name(tmp_path):
    items = parse_items(PAGE_XML, "splice", tmp_path)
    assert [i["n"] for i in items] == ["S113"]
    assert items[0]["loc"] == "Wiring harness near C175"


def test_pdf_for_maps_tif_stem_to_pdf(tmp_path):
    (tmp_path / "e41en006.pdf").write_text("x")
    names = {p.name.lower(): p.name for p in tmp_path.iterdir()}
    assert _pdf_for(tmp_path, "E41en006.TIF", names) == "e41en006.pdf"
    assert _pdf_for(tmp_path, "absent.TIF", names) == ""
    assert _pdf_for(tmp_path, "", names) == ""


def test_tag_and_cdata():
    assert tag("<title><![CDATA[Power/SJB]]></title>", "title") == "<![CDATA[Power/SJB]]>"
    assert cdata("<![CDATA[Power/SJB]]>") == "Power/SJB"
    assert cdata("plain") == "plain"


def test_pdf_viewer_passes_dynamic_names_through_dataset():
    assert "this.dataset.name" in VIEWER_PDF_HTML
    assert 'showItem(\\""+kind+"\\",\\""+esc(n)' not in VIEWER_PDF_HTML


def test_svg_viewer_accepts_file_origin_hotspot_bridge_messages():
    assert "tso-wiring-hotspot-v1" in VIEWER_HTML
    assert "tso-wiring-wheel-v1" in VIEWER_HTML
    assert "d.deltaY<0?1.1:0.9" in VIEWER_HTML
    assert "data-tso-wiring-direct" in VIEWER_HTML
    assert "WiringIndexUI.hotspotKey" in VIEWER_HTML
    assert "d.id.length>512" in VIEWER_HTML


def load_wiring(path):
    text = path.read_text()
    assert text.startswith("window.WIRING=") and text.endswith(";")
    return json.loads(text[len("window.WIRING="):-1])


def test_svg_viewer_build_is_deterministic_and_contains_entity_ui(tmp_path):
    book = "E9Z"
    (tmp_path / f"{book}010001.SVG").write_text("<svg/>")
    (tmp_path / f"{book}151002.SVG").write_text("<svg/>")
    (tmp_path / "face.svg").write_text("<svg/>")
    (tmp_path / f"{book}010001.xml").write_text("""<page><title>Grounds</title><type>SVG</type>
      <connector_collection><conn><name>C100</name><loc>engine</loc><locpage>2</locpage>
      <zone>A1</zone><loc_view>E9Z151002</loc_view><face_view>e9zcfc100</face_view></conn></connector_collection>
      </page>""")
    (tmp_path / "connectors.xml").write_text("<items><item>C100</item></items>")
    (tmp_path / "E9Zconn_search_C.xml").write_text("""<search><values><item name='C100'><pages>
      <ref_page><cell>010</cell><page>001</page><title>Grounds</title><qual/></ref_page>
      </pages></item></values></search>""")
    (tmp_path / "E9Zallcon.xml").write_text("""<search><values><conn><name>C100</name>
      <desc>Test connector</desc><face_view>e9zcfc100</face_view></conn></values></search>""")
    (tmp_path / "e9zcfc100.xml").write_text("""<Faceviews><Connector CNumber='C100'>
      <Faces><Face File='face.svg'/></Faces><Pins><Pin Cavity='1' CircuitNumber='10' Used='1'/>
      </Pins></Connector></Faceviews>""")

    first = build_book(tmp_path, book)
    data1 = (tmp_path / "wiring_data.js").read_bytes()
    second = build_book(tmp_path, book)
    data2 = (tmp_path / "wiring_data.js").read_bytes()
    assert first == second
    assert data1 == data2
    data = load_wiring(tmp_path / "wiring_data.js")
    assert data["entities"]["connector:C100"]["face_asset"] == "face.svg"
    assert data["entities"]["circuit:10"]["endpoints"][0]["connector"] == "connector:C100"
    assert data["pages"]["010001"]["entities"]["connector"] == ["connector:C100"]
    assert "harness" not in {e["type"] for e in data["entities"].values()}
    html = (tmp_path / "_wire.html").read_text()
    assert "wiring_index_ui.css" in html
    assert "WiringIndexUI.init()" in html
    assert "key.length==6" in html  # legacy six-digit hashes remain accepted
    assert (tmp_path / "wiring_index_ui.js").exists()


class FakeJetDb:
    def __init__(self, _path):
        pass

    def read_table(self, name):
        tables = {
            "CELLS": [
                {"CELL": "010", "PAGE": "001", "CELLTYPE": "SVG", "TITLE": "Grounds", "FILENAME": "p1.tif"},
                {"CELL": "150", "PAGE": "001", "CELLTYPE": "CON", "TITLE": "Connector faces", "SUBTITLE": "C100", "FILENAME": "face.tif"},
                {"CELL": "151", "PAGE": "001", "CELLTYPE": "LOC", "TITLE": "Locations", "FILENAME": "loc.tif"},
            ],
            # Face fields are intentionally blank: older books commonly use
            # the cell-150 CELLS subtitle as the explicit connector mapping.
            "CONN": [{"NAME": "C100", "LOCATION": "engine", "LOCPAGE": "1"}],
            "CONNREF": [{"NAME": "C100", "CELL": "010", "PAGE": "001"}],
            "Comp": [{"NAME": "motor", "CONN_NAME": "C100", "LOCPAGE": "1"}],
            "Compref": [{"NAME": "motor", "CELL": "010", "PAGE": "001"}],
            "grnd": [], "grndref": [], "splice": [], "splcref": [], "Fuse": [], "Fuseref": [],
            "Pageref": [{"CELL": "010", "PAGE": "001", "NAME": "See face", "REFCELL": "150", "REFPAGE": "001"}],
        }
        if name not in tables:
            raise KeyError(name)
        return tables[name]


def test_pdf_viewer_build_uses_same_entity_model(tmp_path, monkeypatch):
    (tmp_path / "e9x.mdb").write_bytes(b"fake")
    for name in ("p1.pdf", "face.pdf", "loc.pdf"):
        (tmp_path / name).write_bytes(b"pdf")
    monkeypatch.setattr(build_wiring, "JetDb", FakeJetDb)
    assert build_pdf_book(tmp_path, "E9X")
    data = load_wiring(tmp_path / "wiring_data.js")
    assert data["mode"] == "pdf"
    assert data["entities"]["connector:C100"]["face_page"] == "150001"
    assert "connector:C100" in data["entities"]["component:MOTOR"]["related"]
    assert set(e["type"] for e in data["entities"].values()) == {"component", "connector"}
    assert data["pages"]["010001"]["refs"] == [["See face", "150001"]]


@pytest.mark.skipif(not shutil.which("node"), reason="node is needed to evaluate viewer hotspot parsing")
def test_hotspot_id_grammars_resolve_to_canonical_entities():
    script = r"""
global.window=global;
global.WIRING={entities:{
 'component:ABS CONTROL MODULE':{type:'component',name:'ABS CONTROL MODULE'},
 'connector:C135':{type:'connector',name:'C135'},
 'ground:G100':{type:'ground',name:'G100'},
 'splice:S100':{type:'splice',name:'S100'},
 'fuse:F10':{type:'fuse',name:'F10'},
 'harness:12B637':{type:'harness',name:'12B637'}
}};
require('./tools/wiring_index_ui.js');
let ids=['COMP_X~DATA~ABS CONTROL MODULE','CONN_C135_16','GROUND_G100~INDEX~G100',
 'SPLICE_S100~INDEX~S100','FUSE_F10_Sheet_2','ITEM_12B637_TEXT'];
process.stdout.write(JSON.stringify(ids.map(x=>WiringIndexUI.hotspotKey(x))));
"""
    result = subprocess.run(["node", "-e", script], cwd=Path(__file__).parent.parent,
                            check=True, capture_output=True, text=True)
    assert json.loads(result.stdout) == [
        "component:ABS CONTROL MODULE", "connector:C135", "ground:G100",
        "splice:S100", "fuse:F10", "harness:12B637",
    ]


@pytest.mark.skipif(not shutil.which("node"), reason="node is needed to evaluate viewer escaping")
def test_entity_selection_escapes_data_keys_without_inline_javascript():
    source = (Path(__file__).parent.parent / "tools" / "wiring_index_ui.js").read_text()
    script = r"""
const fs=require('fs'),vm=require('vm');
global.window=global;
let source=fs.readFileSync('./tools/wiring_index_ui.js','utf8');
source=source.replace('global.WiringIndexUI={',
                      'global.__keyAttr=keyAttr;global.WiringIndexUI={');
vm.runInThisContext(source);
process.stdout.write(__keyAttr("component:DRIVER'S <AIR&BAG>"));
"""
    result = subprocess.run(["node", "-e", script], cwd=Path(__file__).parent.parent,
                            check=True, capture_output=True, text=True)
    assert result.stdout == " data-wire-key='component:DRIVER&#39;S &lt;AIR&amp;BAG&gt;'"
    assert source.count("+keyAttr(") == 4
    assert "getAttribute('data-wire-key')" in source
    assert "onclick='WiringIndexUI.select(" not in source
