"""PDF -> Notein (.in): the back-converter.

Two modes, chosen automatically:

1. **Restore** (the PDF was made by `notein2pdf`): the original export is attached to the PDF; it is extracted,
   verified against its SHA-256 and written out **byte-for-byte identical** to the original `.in`.
   Edits made to the PDF afterwards are *not* merged (``extract_source`` reports them via ``modified``).
2. **Import** (any other PDF): a brand-new Notein note is built around the PDF - the same structure Notein itself
   creates when you import a PDF (one page per PDF page, empty ink layers). The database is generated from the schema
   template in ``data/schema_v31.json`` (DDL only), so it is structurally identical to the app's own databases.

Usage: pdf2notein INPUT OUTPUT.in [--import] [--title T] [--parent-id UUID] [--user-id UUID] [--like NOTE.in]
       [--md-engine onlyoffice|latex|weasyprint]
INPUT may also be an Office document (.docx .pptx .xlsx .odt …, converted with OnlyOffice) or Markdown (.md): it is
converted to PDF first and then imported as a new note.
"""
import hashlib
import json
import math
import os
import re
import sqlite3
import sys
import tempfile
import time
import uuid
import zipfile
from importlib import resources

import pymupdf

from . import office, provenance

APP_FILES = "/data/user/0/com.orion.notein.global/files/"
THEME = "com.orion.notein.domain.models.papertheme."
ICONS = {"iconHorizontal": 2131233370, "iconVertical": 2131233371}      # Android resource ids of a recent app build
APP_VERSION_CODE = "308"
FIXED_ZIP_TIME = (1980, 1, 1, 0, 0, 0)                                    # what the app writes
PAGE_WIDTH_PX = 1240                                                      # A4 at 150 dpi


def _j(obj, sort=False):
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False, sort_keys=sort)


def load_schema(version=31):
    with resources.files("notein2pdf").joinpath("data", f"schema_v{version}.json").open(encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------- mode 1: restore
def extract_source(pdf_path, out_path):
    """Write the original .in embedded in `pdf_path` to `out_path`. Returns an info dict, or None if there is none.

    info: sha256, size, id, title, modified (True if the PDF's pages changed after export)."""
    doc = pymupdf.open(pdf_path)
    found = provenance.read_embedded(doc)
    if found is None:
        return None
    info, data = found
    digest = hashlib.sha256(data).hexdigest()
    if not info.get("sha256"):
        raise ValueError("embedded note has no integrity metadata; refusing to restore unverified data")
    if digest != info["sha256"]:
        raise ValueError("embedded note is corrupt (SHA-256 mismatch)")
    with open(out_path, "wb") as f:
        f.write(data)
    info = dict(info)
    info["sha256"] = digest
    info["modified"] = bool(info.get("pages_sha256")) and provenance.pages_digest(doc) != info["pages_sha256"]
    return info


# ---------------------------------------------------------------- mode 2: import a plain PDF
def _insert(con, table, row):
    cols = ", ".join(f"`{k}`" for k in row)
    con.execute(f"insert into `{table}` ({cols}) values ({', '.join('?' * len(row))})", list(row.values()))


def _page_dims(rect):
    """Page size in Notein pixels: always 1240 wide, height = floor(1240 * h / w). Measured on 25 real pages:
    exact on 22, one pixel smaller on 3 (the app rounds up there). The app scales the PDF by width, so it is harmless."""
    w, h = PAGE_WIDTH_PX, int(math.floor(PAGE_WIDTH_PX * rect.height / rect.width))
    return w, h


def pdf_to_note(pdf_path, out_path, title=None, parent_id=None, user_id="", now_ms=None, schema=None):
    """Build a new Notein export (.in) that imports `pdf_path` as a note. Returns the note_meta dict."""
    doc = pymupdf.open(pdf_path)
    if doc.needs_pass:
        raise ValueError("password-protected PDFs are not supported by the importer")
    if doc.page_count == 0:
        raise ValueError("PDF has no pages")
    schema = schema or load_schema()
    now = int(now_ms or time.time() * 1000)
    title = title or os.path.splitext(os.path.basename(pdf_path))[0]
    note_id, pdfinfo_id, default_page = (str(uuid.uuid4()) for _ in range(3))
    pdf_name = "%s_notein_pdf_%s.pdf" % (re.sub(r"[\\/]", "_", title), uuid.uuid4())
    pdf_app_path = APP_FILES + "documents/" + pdf_name
    tn_dir = "/storage/emulated/0/Android/data/com.orion.notein.global/files/"
    page_ids = [str(uuid.uuid4()) for _ in range(doc.page_count)]

    tmp = tempfile.mkdtemp(prefix="pdf2notein_")
    dbfile = os.path.join(tmp, "n.db")
    con = sqlite3.connect(dbfile)
    con.execute("pragma page_size=%d" % schema["page_size"])
    con.execute("pragma journal_mode=%s" % schema["journal_mode"])
    for ddl in schema["ddl"]:
        con.execute(ddl)
    con.execute("insert into android_metadata values (?)", (schema["android_locale"],))
    con.execute("insert into room_master_table (id, identity_hash) values (?, ?)", tuple(schema["room_master"]))

    _insert(con, "NoteContentEntity", {
        "id": note_id, "page_list": _j(page_ids), "page_layer_list": _j(["paper_theme", "default"]),
        "outline_list": "[]", "default_page_id": default_page, "pdf_info_id": pdfinfo_id, "saved_page_index": 0,
        "activated_page_layer_id": "default", "zoom": 1.0, "unbounded_page_offset_x": 0.0,
        "unbounded_page_offset_y": 0.0, "page_offset_json": '{"offset_x":0.0,"offset_y":0.0}',
        "unbounded_note": 0, "creation_time": now, "last_modification_time": now,
        "extras": '{"mMap":{"app_version_code":"%s"}}' % APP_VERSION_CODE})
    _insert(con, "PdfInfoEntity", {"id": pdfinfo_id, "pdf_path": pdf_app_path, "tn_path": "", "password": None})
    for lid in ("paper_theme", "default"):
        _insert(con, "PageLayerEntity", {"id": lid, "note_id": note_id, "visible": 1, "extras": None, "name": None})

    for i, (pid, page) in enumerate(zip(page_ids, doc)):
        w, h = _page_dims(page.rect)
        orient = "VERTICAL" if page.rect.height >= page.rect.width else "HORIZONTAL"
        spec = {"height": h, "info": "", "isPercentage": False, "name": "PDF", "width": w, **ICONS}
        theme = {"type": THEME + "PdfPaperTheme", "name": "", "orientation": orient, "pageNum": i,
                 "pdfInfo": {"id": pdfinfo_id, "pdfPath": pdf_app_path, "tnPath": ""}}
        _insert(con, "PageEntity", {
            "id": pid, "note_id": note_id, "paper_spec": _j(spec, sort=True), "page_orientation": orient,
            "paper_theme": _j(theme), "padding_color": -1 if i == 0 else 0,
            "paper_padding": '{"bottom":0,"left":0,"right":0,"top":0}', "padding_style_json": None,
            "creation_time": now, "last_modification_time": now, "tn_path": f"{tn_dir}{note_id}_{pid}",
            "unbounded": 0, "source": 2, "recover_note_id": "", "recover_note_title": "", "recover_index": -1,
            "is_in_trash_bin": 0, "history_page_id_list": None, "rotation_direction_index": 0,
            "is_bookmarked": 0, "bookmark_time": 0})
    # the hidden blank "default page" the app keeps for adding pages
    blank_spec = {"height": 1754, "info": "210 x 297mm", "isPercentage": False, "name": "A4",
                  "preciseHeight": 1753.937, "preciseWidth": 1240.1575, "width": 1240, **ICONS}
    blank_theme = {"type": THEME + "LambdaPaperTheme",
                   "baseTheme": {"type": THEME + "ColorPaperTheme", "color": -5443},
                   "category": {"id": 4097, "name": "基本"}, "name": "", "orientation": "VERTICAL",
                   "paperStyle": {"type": THEME + "paperstyle.BlankPaperStyle", "paperHeight": 1263.0,
                                  "paperWidth": 892.5}}
    _insert(con, "PageEntity", {
        "id": default_page, "note_id": note_id, "paper_spec": _j(blank_spec, sort=True), "page_orientation": "VERTICAL",
        "paper_theme": _j(blank_theme), "padding_color": 0, "paper_padding": '{"bottom":0,"left":0,"right":0,"top":0}',
        "padding_style_json": None, "creation_time": now, "last_modification_time": now,
        "tn_path": f"{tn_dir}{note_id}_{default_page}", "unbounded": 0, "source": 0, "recover_note_id": "",
        "recover_note_title": "", "recover_index": -1, "is_in_trash_bin": 0, "history_page_id_list": None,
        "rotation_direction_index": 0, "is_bookmarked": 0, "bookmark_time": 0})
    con.execute("pragma user_version=%d" % schema["user_version"])
    con.commit()
    con.execute("pragma wal_checkpoint(TRUNCATE)")
    con.close()

    w0, h0 = _page_dims(doc[0].rect)
    meta = {"creationTime": now, "favorite": False, "id": note_id, "inTrashBin": False, "lastModificationTime": now,
            "lastOpenedTime": now, "lockAdded": False, "parentId": parent_id,
            "pdfInfo": {"id": pdfinfo_id, "pdfPath": "", "tnPath": ""}, "title": title, "tnHeight": 195,
            "tnPath": f"{tn_dir}{note_id}_{page_ids[0]}", "tnWidth": int(round(195 * w0 / h0)),
            "totalPagesCount": doc.page_count, "unboundedNote": False, "userId": user_id, "usingOwnPdf": True}
    members = [
        (f"note_database_note_{note_id}_db", open(dbfile, "rb").read()),
        ("note_extra.json", b'{"OooO00o":45,"OooO0O0":%d}' % schema["user_version"]),
        ("note_in_flag.json", b'{"OooO00o":true,"OooO0O0":true,"OooO0OO":true,"OooO0Oo":true,"OooO0o0":false}'),
        ("note_label.json", b'{"note_labels":[],"page_id_to_labels_map":{}}'),
        ("note_meta.json", _j(meta, sort=True).encode("utf-8")),
        ("note_pdf_" + pdf_name, open(pdf_path, "rb").read()),
        ("notepdf_lost_info.json", b""),
    ]
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in members:
            zi = zipfile.ZipInfo(name, FIXED_ZIP_TIME)
            zi.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(zi, data)
    os.remove(dbfile)
    return meta


# ---------------------------------------------------------------- CLI
def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)

    def opt(name):
        if name in argv:
            i = argv.index(name)
            val = argv[i + 1]
            del argv[i:i + 2]
            return val
        return None
    force_import = "--import" in argv
    argv = [a for a in argv if a != "--import"]
    title, parent, user, like = opt("--title"), opt("--parent-id"), opt("--user-id"), opt("--like")
    md_engine = opt("--md-engine") or "onlyoffice"
    if len(argv) != 2:
        print(__doc__)
        return 2
    src, dst = argv
    if os.path.splitext(src)[1].lower() != ".pdf":                      # Office / Markdown -> PDF first
        tmp_pdf = os.path.join(tempfile.mkdtemp(prefix="pdf2notein_"), "doc.pdf")
        engine = office.to_pdf(src, tmp_pdf, md_engine=md_engine)
        print(f"converted {os.path.basename(src)} to PDF with {engine}")
        title = title or os.path.splitext(os.path.basename(src))[0]
        src, force_import = tmp_pdf, True
    if like and not user:
        user = json.loads(zipfile.ZipFile(like).read("note_meta.json")).get("userId", "")
    if not force_import:
        info = extract_source(src, dst)
        if info is not None:
            print(f"restored original note '{info.get('title', '?')}' -> {dst} (sha256 {info['sha256'][:16]}…, "
                  f"{info['size']} bytes, verified byte-identical to the original export)")
            if info["modified"]:
                print("warning: the PDF was modified after it was exported; those edits are NOT contained in the "
                      "restored note")
            return 0
    meta = pdf_to_note(src, dst, title=title, parent_id=parent, user_id=user or "")
    print(f"imported PDF as new note '{meta['title']}' ({meta['totalPagesCount']} pages) -> {dst}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
