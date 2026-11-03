"""Shared helpers: build a minimal synthetic Notein export (.in zip) so tests need no personal data."""
import json
import sqlite3
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))      # lets tests import tools/compare_pages.py

SCHEMA = [
    "create table NoteContentEntity (id text, page_list text, page_layer_list text, outline_list text, default_page_id text)",
    "create table PageEntity (id text, note_id text, paper_spec text, paper_theme text, unbounded integer)",
    "create table PageLayerEntity (id text, note_id text, visible integer, extras text, name text)",
    "create table StrokeEntity (id text, page_id text, layer_id text, creation_time integer, record_json text,"
    " ink_stroke_json text, ink_stroke_blob blob, left real, top real, right real, bottom real)",
    "create table ShapeEntity (id text, page_id text, color integer, width real, points text, type integer,"
    " blend_mode integer, layer_id text, opacity_of_fill_color integer, creation_time integer,"
    " left real, top real, right real, bottom real)",
    "create table ImageEntity (id text, uri text, layer_id text, bounds text, rotation real, page_id text,"
    " creation_time integer, left real, top real, right real, bottom real)",
    "create table TextBoxEntity (id text, page_id text, text text, text_size real, layer_id text,"
    " default_text_color integer, creation_time integer, left real, top real, right real, bottom real)",
    "create table TableBlockEntity (id text, page_id text, columns_json text, rows_json text, cells_json text,"
    " layer_id text, creation_time integer, left real, top real, right real, bottom real)",
]

BLANK_THEME = {
    "baseTheme": {"type": "ColorPaperTheme", "color": -1},
    "paperStyle": {"type": "x.BlankPaperStyle"},
}


def stroke(points, color=-16777216, width=2.4, kind=0):
    return json.dumps({"id": "s", "type": kind, "color": color, "width": width,
                       "points": [{"x": x, "y": y, "p": 0.5, "action": 2} for x, y in points]})


def make_note(path, strokes=(), layers=(("default", 1, None),), shapes=(), title="Synthetic note", trashed=False):
    """strokes: iterable of (layer_id, record_json). Returns the path."""
    path = Path(path)
    tmp = path.with_suffix(".db")
    if tmp.exists():
        tmp.unlink()
    con = sqlite3.connect(tmp)
    for stmt in SCHEMA:
        con.execute(stmt)
    layer_list = ["paper_theme"] + [l[0] for l in layers]
    con.execute("insert into NoteContentEntity values ('n1', ?, ?, '[]', 'p1')",
                (json.dumps(["p1"]), json.dumps(layer_list)))
    con.execute("insert into PageEntity values ('p1', 'n1', ?, ?, 0)",
                (json.dumps({"width": 1240, "height": 1754}), json.dumps(BLANK_THEME)))
    con.execute("insert into PageLayerEntity values ('paper_theme', 'n1', 1, null, null)")
    for lid, visible, name in layers:
        con.execute("insert into PageLayerEntity values (?, 'n1', ?, null, ?)", (lid, visible, name))
    for i, (lid, rec) in enumerate(strokes):
        con.execute("insert into StrokeEntity values (?, 'p1', ?, ?, ?, null, null, 0, 0, 0, 0)",
                    (f"st{i}", lid, i, rec))
    for i, (lid, pts, kind) in enumerate(shapes):
        con.execute("insert into ShapeEntity values (?, 'p1', -16777216, 2.0, ?, ?, -1, ?, 0, ?, 0, 0, 0, 0)",
                    (f"sh{i}", json.dumps([{"x": x, "y": y} for x, y in pts]), kind, lid, 100 + i))
    con.commit()
    con.close()
    meta = {"id": "n1", "title": title, "inTrashBin": trashed, "lastModificationTime": 1,
            "totalPagesCount": 1, "parentId": None}
    with zipfile.ZipFile(path, "w") as z:
        z.write(tmp, "note_database_note_n1_db")
        z.writestr("note_meta.json", json.dumps(meta))
    tmp.unlink()
    return path


@pytest.fixture
def note_factory(tmp_path):
    def factory(name="note.in", **kw):
        return make_note(tmp_path / name, **kw)
    return factory
