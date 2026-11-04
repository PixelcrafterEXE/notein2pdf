"""Back-converter (pdf2notein): lossless restore from the embedded original, and import of plain PDFs."""
import hashlib
import json
import sqlite3
import tempfile
import zipfile
from pathlib import Path

import pymupdf
import pytest

from conftest import stroke
from notein2pdf import back, converter as conv, provenance

FIX = Path(__file__).parent / "fixtures"
REAL = ["sheet_2", "gute_nachricht", "frauen_regiern", "export_test"]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def open_db(note_path):
    """Open the SQLite DB of a .in zip (copying -wal/-shm alongside)."""
    z = zipfile.ZipFile(note_path)
    base = next(n for n in z.namelist() if n.startswith("note_database_note_") and n.endswith("_db"))
    d = Path(tempfile.mkdtemp())
    for suf, out in (("", "n.db"), ("-wal", "n.db-wal"), ("-shm", "n.db-shm")):
        if base + suf in z.namelist():
            (d / out).write_bytes(z.read(base + suf))
    return z, sqlite3.connect(d / "n.db"), (d / "n.db")


# ---------------------------------------------------------------- restore: bit-perfect
def test_roundtrip_is_byte_identical_synthetic(note_factory, tmp_path):
    note = note_factory(strokes=[("default", stroke([(100, 100), (400, 300)]))])
    pdf, back_in = tmp_path / "n.pdf", tmp_path / "back.in"
    conv.convert(str(note), str(pdf))
    info = back.extract_source(str(pdf), str(back_in))
    assert back_in.read_bytes() == Path(note).read_bytes()
    assert sha(back_in) == sha(note) == info["sha256"]
    assert info["modified"] is False


@pytest.mark.parametrize("name", REAL)
def test_roundtrip_is_byte_identical_real_notes(name, tmp_path):
    src = FIX / name / "note.in"
    if not src.exists():
        pytest.skip("fixture not present")
    pdf, back_in = tmp_path / "n.pdf", tmp_path / "back.in"
    conv.convert(str(src), str(pdf))
    back.extract_source(str(pdf), str(back_in))
    assert sha(back_in) == sha(src)


def test_embedding_does_not_change_what_viewers_show(note_factory, tmp_path):
    note = note_factory(strokes=[("default", stroke([(100, 100), (400, 300), (700, 150)]))])
    with_, without = tmp_path / "a.pdf", tmp_path / "b.pdf"
    conv.convert(str(note), str(with_), embed_source=True)
    conv.convert(str(note), str(without), embed_source=False)
    a, b = pymupdf.open(with_), pymupdf.open(without)
    assert a[0].get_pixmap().samples == b[0].get_pixmap().samples
    assert provenance.read_embedded(b) is None
    assert back.extract_source(str(without), str(tmp_path / "x.in")) is None      # nothing to restore


def test_pdf_edits_after_export_are_flagged(note_factory, tmp_path):
    note = note_factory(strokes=[("default", stroke([(100, 100), (400, 300)]))])
    pdf, back_in = tmp_path / "n.pdf", tmp_path / "back.in"
    conv.convert(str(note), str(pdf))
    doc = pymupdf.open(pdf)
    doc[0].insert_text((50, 50), "edited in a viewer")
    doc.saveIncr()
    info = back.extract_source(str(pdf), str(back_in))
    assert info["modified"] is True                                    # edits detected...
    assert back_in.read_bytes() == Path(note).read_bytes()             # ...but the original is restored intact


def test_corrupt_or_unverifiable_attachment_is_refused(note_factory, tmp_path):
    note = note_factory()
    pdf = tmp_path / "n.pdf"
    conv.convert(str(note), str(pdf))
    doc = pymupdf.open(pdf)
    info = json.loads(doc.embfile_info(provenance.EMBED_NAME)["description"])
    info["sha256"] = "0" * 64
    doc.embfile_upd(provenance.EMBED_NAME, desc=json.dumps(info))
    doc.saveIncr()
    with pytest.raises(ValueError, match="corrupt"):
        back.extract_source(str(pdf), str(tmp_path / "x.in"))


# ---------------------------------------------------------------- import: plain PDF -> new note
def make_pdf(path, sizes=((595, 842), (595, 842), (499, 720))):
    doc = pymupdf.open()
    for i, (w, h) in enumerate(sizes):
        doc.new_page(width=w, height=h).insert_text((72, 72), f"page {i + 1}", fontsize=24)
    doc.save(path)
    return path


def test_import_builds_a_valid_note_that_converts_back_to_the_same_pdf(tmp_path):
    src = make_pdf(tmp_path / "doc.pdf")
    note = tmp_path / "new.in"
    meta = back.pdf_to_note(str(src), str(note), title="My doc", parent_id="folder-1", user_id="u1")
    assert meta["totalPagesCount"] == 3 and meta["parentId"] == "folder-1" and meta["usingOwnPdf"] is True

    z, con, dbpath = open_db(note)
    names = z.namelist()
    assert names[0].startswith("note_database_note_") and "note_meta.json" in names
    assert any(n.startswith("note_pdf_My doc_notein_pdf_") for n in names)
    assert all(i.date_time == (1980, 1, 1, 0, 0, 0) for i in z.infolist())         # as the app writes them
    # database identical in structure to the app's: same DDL, user_version, WAL header, Room identity hash
    schema = back.load_schema()
    assert [r[0] for r in con.execute("select sql from sqlite_master where sql is not null order by rowid")] == schema["ddl"]
    assert con.execute("pragma user_version").fetchone()[0] == 31
    assert con.execute("select identity_hash from room_master_table").fetchone()[0] == schema["room_master"][1]
    assert dbpath.read_bytes()[18:20] == b"\x02\x02"                               # WAL journal mode
    pages = json.loads(con.execute("select page_list from NoteContentEntity").fetchone()[0])
    assert len(pages) == 3
    assert con.execute("select count(*) from PageEntity").fetchone()[0] == 4      # 3 PDF pages + hidden blank default
    spec = json.loads(con.execute("select paper_spec from PageEntity where id=?", (pages[2],)).fetchone()[0])
    assert (spec["width"], spec["height"]) == (1240, 1789)                          # 499x720 pt -> floor(1240*720/499)

    # the converter reads our note and reproduces the source PDF page-for-page
    out = tmp_path / "round.pdf"
    assert conv.convert(str(note), str(out), embed_source=False) == 3
    a, b = pymupdf.open(src), pymupdf.open(out)
    for pa, pb in zip(a, b):
        assert abs(pa.rect.width - pb.rect.width) < 0.01 and abs(pa.rect.height - pb.rect.height) < 0.01
        assert pa.get_pixmap().samples == pb.get_pixmap().samples


def test_cli_chooses_restore_or_import(note_factory, tmp_path, capsys):
    note = note_factory()
    pdf = tmp_path / "n.pdf"
    conv.convert(str(note), str(pdf))
    assert back.main([str(pdf), str(tmp_path / "r.in")]) == 0
    assert "byte-identical" in capsys.readouterr().out
    plain = make_pdf(tmp_path / "plain.pdf", sizes=((595, 842),))
    assert back.main([str(plain), str(tmp_path / "i.in"), "--title", "Plain"]) == 0
    assert "imported PDF as new note 'Plain'" in capsys.readouterr().out
    assert back.main([str(pdf), str(tmp_path / "f.in"), "--import"]) == 0            # force import even with a source


def test_import_matches_a_real_imported_note_structurally(tmp_path):
    """Compare against a note the app itself created from a PDF (fixture gute_nachricht)."""
    real = FIX / "gute_nachricht" / "note.in"
    if not real.exists():
        pytest.skip("fixture not present")
    rz, rcon, rdb = open_db(real)
    pdfname = next(n for n in rz.namelist() if n.startswith("note_pdf_"))
    src = tmp_path / "src.pdf"
    src.write_bytes(rz.read(pdfname))
    new = tmp_path / "new.in"
    back.pdf_to_note(str(src), str(new), title="Eine gute Nachricht - Danger Dan")
    nz, ncon, ndb = open_db(new)

    ddl = "select sql from sqlite_master where sql is not null order by rowid"
    assert [r[0] for r in ncon.execute(ddl)] == [r[0] for r in rcon.execute(ddl)]          # identical schema
    for pragma in ("user_version", "page_size", "journal_mode"):
        assert ncon.execute(f"pragma {pragma}").fetchone() == rcon.execute(f"pragma {pragma}").fetchone()
    assert ncon.execute("select * from room_master_table").fetchall() == rcon.execute("select * from room_master_table").fetchall()
    assert ndb.read_bytes()[:100][16:24] == rdb.read_bytes()[:100][16:24]                  # same header flags

    def norm(theme_or_spec):
        d = json.loads(theme_or_spec)
        d.pop("iconHorizontal", None); d.pop("iconVertical", None)
        if "pdfInfo" in d:
            d["pdfInfo"] = {"id": "X", "pdfPath": "X", "tnPath": d["pdfInfo"]["tnPath"]}
        return d

    def pdf_pages(con):
        pl = json.loads(con.execute("select page_list from NoteContentEntity").fetchone()[0])
        return [con.execute("select paper_spec,paper_theme,page_orientation,source,paper_padding,padding_color,"
                            "rotation_direction_index,unbounded,recover_index,is_in_trash_bin from PageEntity where id=?",
                            (p,)).fetchone() for p in pl]
    rp, np_ = pdf_pages(rcon), pdf_pages(ncon)
    # the app keeps the PDF page of a page the user deleted (6 PDF pages, 5 note pages); import gives every PDF page
    assert len(rp) == 5 and len(np_) == pymupdf.open(src).page_count == 6
    for r, n in zip(rp, np_):                                              # the 5 pages both notes have
        rs, ns = norm(r[0]), norm(n[0])
        assert abs(rs.pop("height") - ns.pop("height")) <= 1               # app rounds the height differently on ~10 % of pages
        assert rs == ns and norm(r[1]) == norm(n[1])                       # same paper spec and PDF theme otherwise
        assert r[2:] == n[2:]
    # hidden default page: same theme/spec as the app's own
    q = "select paper_spec,paper_theme,source from PageEntity where source=0"
    r0, n0 = rcon.execute(q).fetchone(), ncon.execute(q).fetchone()
    assert norm(r0[0]) == norm(n0[0]) and r0[1] == n0[1]
    # note_meta keys and the fixed side files
    rm, nm = json.loads(rz.read("note_meta.json")), json.loads(nz.read("note_meta.json"))
    assert set(nm) == set(rm) - {"sortKey"}
    for side in ("note_extra.json", "note_in_flag.json", "note_label.json", "notepdf_lost_info.json"):
        assert nz.read(side) == rz.read(side)
