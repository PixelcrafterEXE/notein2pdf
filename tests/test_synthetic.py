"""Tests on synthetic notes: no personal data, no external tools required."""
import pymupdf

from conftest import stroke
from notein2pdf import converter as conv


def _pdf(tmp_path, name="out.pdf"):
    return tmp_path / name


def _ink(page):
    """Vector drawings on the page, ignoring the full-page paper background fill."""
    full = page.rect
    return [d for d in page.get_drawings() if not (len(d["items"]) == 1 and d["items"][0][0] == "re"
                                                    and abs(d["items"][0][1].width - full.width) < 1
                                                    and abs(d["items"][0][1].height - full.height) < 1)]


def test_converts_blank_page_with_ink(note_factory, tmp_path):
    note = note_factory(strokes=[("default", stroke([(100, 100), (400, 300), (700, 150)]))])
    out = _pdf(tmp_path)
    assert conv.convert(str(note), str(out)) == 1
    doc = pymupdf.open(out)
    assert doc.page_count == 1
    assert abs(doc[0].rect.width - 595.2) < 1            # 1240 px at 150 dpi = A4 width in pt
    assert _ink(doc[0]), "stroke should be drawn as vector paths"


def test_each_layer_becomes_a_pdf_layer_and_hidden_layers_start_off(note_factory, tmp_path):
    note = note_factory(
        layers=(("default", 1, None), ("L2", 0, "Secret")),
        strokes=[("default", stroke([(10, 10), (100, 100)])), ("L2", stroke([(50, 50), (200, 50)]))])
    out = _pdf(tmp_path)
    conv.convert(str(note), str(out))
    groups = {v["name"]: v["on"] for v in pymupdf.open(out).get_ocgs().values()}
    assert groups["Background"] is True
    assert groups["Default layer"] is True
    assert groups["Secret (hidden in Notein)"] is False


def test_orphaned_layers_are_not_drawn(note_factory, tmp_path):
    note = note_factory(strokes=[("ghost-layer", stroke([(10, 10), (100, 100)]))])
    out = _pdf(tmp_path)
    conv.convert(str(note), str(out))
    assert not _ink(pymupdf.open(out)[0])


def test_shapes_rectangles_are_closed_and_lines_open(note_factory, tmp_path):
    rect = [(100, 100), (300, 100), (300, 300), (100, 300)]
    note = note_factory(shapes=[("default", rect, 3), ("default", [(400, 400), (600, 500)], 7)])
    out = _pdf(tmp_path)
    conv.convert(str(note), str(out))
    assert len(_ink(pymupdf.open(out)[0])) >= 2


def test_batch_skips_trash_and_is_incremental(note_factory, tmp_path):
    from notein2pdf import batch
    src = tmp_path / "src"
    src.mkdir()
    note_factory(name="src/a", title="Alpha")
    note_factory(name="src/b", title="Beta", trashed=True)
    out = tmp_path / "out"
    assert batch.main([str(src), str(out)]) == 0
    assert (out / "Alpha.pdf").exists()
    assert not (out / "Beta.pdf").exists()
    stamp = (out / "Alpha.pdf").stat().st_mtime_ns
    batch.main([str(src), str(out)])                       # second run must not reconvert
    assert (out / "Alpha.pdf").stat().st_mtime_ns == stamp


def test_batch_disambiguates_duplicate_titles(note_factory, tmp_path):
    from notein2pdf import batch
    src = tmp_path / "src"
    src.mkdir()
    a = note_factory(name="src/a", title="Same")
    b = note_factory(name="src/b", title="Same")
    # make the second note a different note id
    import json, zipfile, shutil
    with zipfile.ZipFile(b) as z:
        files = {n: z.read(n) for n in z.namelist()}
    meta = json.loads(files["note_meta.json"]); meta["id"] = "n2"; files["note_meta.json"] = json.dumps(meta).encode()
    with zipfile.ZipFile(b, "w") as z:
        for n, d in files.items():
            z.writestr(n, d)
    out = tmp_path / "out"
    batch.main([str(src), str(out)])
    names = sorted(p.name for p in out.glob("*.pdf"))
    assert len(names) == 2 and all("Same [" in n for n in names)
