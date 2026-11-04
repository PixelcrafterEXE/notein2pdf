"""Convert a Notein native export (.in) into a PDF.

Usage: notein2pdf input.in output.pdf [--no-embed]   (or: python -m notein2pdf input.in output.pdf)
--no-embed: do not attach the original .in to the PDF (smaller file, but not reversible by pdf2notein)
Requires: pymupdf (pip install pymupdf)

The .in file is a zip holding an SQLite (Room) database, the original PDF (for
annotated PDFs), images and JSON metadata. Strokes are drawn as vectors on top
of the original PDF page or a generated paper background.
"""
import json
import math, os, shutil, sqlite3, sys, tempfile, zipfile
import pymupdf as fitz

from . import provenance

UNBOUNDED_MARGIN = 100         # px around the ink on infinite-canvas pages (measured on the app's own export)
UNBOUNDED_MAX_AREA = 3 * 1024 * 1024   # pt^2 cap on infinite-canvas pages (app scales larger ones down)
PX_TO_PT = 72 / 150          # Notein pages are 1240 px wide = A4 at 150 dpi
warnings = []
_OC = None          # xref of the PDF layer (optional content group) currently drawn into
_DARK = False       # dark paper: lighten black ink, no multiply highlighter
_OX = _OY = 0.0   # page-space origin shift (unbounded canvases)


def argb(v):
    v &= 0xFFFFFFFF
    return ((v >> 16 & 255) / 255, (v >> 8 & 255) / 255, (v & 255) / 255), (v >> 24 & 255) / 255


def open_db(extract_dir):
    names = os.listdir(extract_dir)
    base = next(n for n in names if n.startswith("note_database_note_") and n.endswith("_db"))
    # copy to neutral names so sqlite finds the -wal/-shm sidecar files
    for suffix in ("", "-wal", "-shm"):
        src = os.path.join(extract_dir, base + suffix)
        if os.path.exists(src):
            shutil.copy(src, os.path.join(extract_dir, "n.db" + suffix))
    con = sqlite3.connect(os.path.join(extract_dir, "n.db"))
    con.row_factory = sqlite3.Row
    return con


def paper_background(page, theme, scale, pw, ph):
    """Fill page colour and draw dots/lines/grid if we recognise the style."""
    try:
        t = json.loads(theme) if theme else {}
    except Exception:
        t = {}
    base = t.get("baseTheme", {})
    global _DARK
    rgb, a = argb(base.get("color", -1))
    _DARK = (0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]) < 0.35
    page.draw_rect(page.rect, color=None, fill=rgb, oc=_OC)
    st = t.get("paperStyle", {}).get("type", "")
    ps = t.get("paperStyle", {})
    kind = st.rsplit(".", 1)[-1]
    if kind in ("BlankPaperStyle", ""):
        return
    if kind == "UnboundedSquarePaperStyle":
        step = ps.get("requiredItemSpace", 37.5)
        sh = page.new_shape()
        x = _OX % step           # lattice is anchored at the canvas origin, which sits at (_OX, _OY) on the page
        while x < pw:
            sh.draw_line((x * scale, 0), (x * scale, ph * scale)); x += step
        y = _OY % step
        while y < ph:
            sh.draw_line((0, y * scale), (pw * scale, y * scale)); y += step
        sh.finish(oc=_OC, color=(0.36, 0.36, 0.36) if _DARK else (0.75, 0.75, 0.75), width=0.45)
        sh.commit()
        return
    if kind == "UnboundedDotPaperStyle":
        # same lattice as UnboundedSquarePaperStyle (requiredItemSpace), with a dot at each intersection
        step = ps.get("requiredItemSpace", 37.5)
        sh = page.new_shape()
        y = _OY % step
        while y < ph:
            x = _OX % step
            while x < pw:
                sh.draw_circle((x * scale, y * scale), 0.62)
                x += step
            y += step
        sh.finish(oc=_OC, color=None, fill=(0.45, 0.45, 0.45) if _DARK else (0.80, 0.78, 0.74))
        sh.commit()
        return
    if kind in ("NewDotPaperStyle", "DotPaperStyle"):
        l, r_ = ps.get("leftPadding", 34.5), ps.get("rightPadding", 34.5)
        t, b_ = ps.get("topPadding", 37.5), ps.get("bottomPadding", 37.5)
        step = (pw - l - r_) / 30.0                    # measured on the app's own PDF: 39.0 px on A4 (1240 px)
        sh = page.new_shape()
        y = t + step / 2
        while y < ph - b_:
            x = l
            while x < pw - r_ + 0.01:
                sh.draw_circle((x * scale, y * scale), 0.62)
                x += step
            y += step
        sh.finish(oc=_OC, color=None, fill=(0.80, 0.78, 0.74))
        sh.commit()
    else:
        warnings.append(f"unsupported paper style {kind} (background colour only)")


def add_smooth(sh, pts):
    """Add a smooth path through pts: quadratic Beziers between segment midpoints (control = the point)."""
    n = len(pts)
    if n < 3:
        sh.draw_polyline(pts)
        return
    P = [fitz.Point(*q) for q in pts]
    mid = lambda a, b: fitz.Point((a.x + b.x) / 2, (a.y + b.y) / 2)
    cur = P[0]
    sh.draw_line(P[0], mid(P[0], P[1]))
    cur = mid(P[0], P[1])
    for i in range(1, n - 1):
        end = mid(P[i], P[i + 1])
        c1 = fitz.Point(cur.x + 2 / 3 * (P[i].x - cur.x), cur.y + 2 / 3 * (P[i].y - cur.y))
        c2 = fitz.Point(end.x + 2 / 3 * (P[i].x - end.x), end.y + 2 / 3 * (P[i].y - end.y))
        sh.draw_bezier(cur, c1, c2, end)
        cur = end
    sh.draw_line(cur, P[-1])


def _adapt(rgb, kind):
    """Ink colours are exported unchanged (the app does not invert black ink on dark paper in its PDF)."""
    return rgb


def _varint(b, i):
    r = sh = 0
    while True:
        x = b[i]; i += 1
        r |= (x & 127) << sh; sh += 7
        if x < 128:
            return r, i


def _pb(b):
    i, f = 0, {}
    while i < len(b):
        k, i = _varint(b, i)
        fn, wt = k >> 3, k & 7
        if wt == 0:
            v, i = _varint(b, i)
        elif wt == 1:
            v = b[i:i + 8]; i += 8
        elif wt == 2:
            n, i = _varint(b, i); v = b[i:i + n]; i += n
        elif wt == 5:
            v = b[i:i + 4]; i += 4
        else:
            raise ValueError("wire type %d" % wt)
        f.setdefault(fn, []).append(v)
    return f


def decode_blob_stroke(blob):
    """'notein-pencil-v3' protobuf stroke -> dict like record_json (or None)."""
    import struct
    f = _pb(blob)
    xy = struct.unpack("<%df" % (len(f[10][0]) // 4), f[10][0])
    m = struct.unpack("<9f", f[13][0])           # affine: x' = m0*x + m2 ... (scale ~1)
    sx = f12 = struct.unpack("<9f", f[12][0])
    ax = f12[0] * m[0]; ay = f12[4] * m[4]       # inverse scales cancel to ~1
    pts = [{"x": xy[i] * ax + m[2], "y": xy[i + 1] * ay + m[5], "p": 0.5, "action": 2}
           for i in range(0, len(xy) - 1, 2)]
    col = f[5][0] >> 32                           # Compose-style ULong colour: top 32 bits = ARGB
    return {"type": 100, "color": col - (1 << 32) if col >= 1 << 31 else col,
            "width": struct.unpack("<f", f[4][0])[0] * PENCIL_WIDTH_FACTOR, "points": pts}


PENCIL_WIDTH_FACTOR = 0.85   # 'notein-pencil': wider and translucent in the app
PENCIL_OPACITY = 0.55
PEN_WIDTH_FACTOR = float(os.environ.get("NOTEIN_PEN_FACTOR", 0.55))   # calibrated against the app's own PDF export
PRESSURE_MIN = float(os.environ.get("NOTEIN_PRESSURE_MIN", 0.6))   # width = base * (PRESSURE_MIN + PRESSURE_GAIN * pressure)
PRESSURE_GAIN = float(os.environ.get("NOTEIN_PRESSURE_GAIN", 0.8))


def _multiply_gs(page):
    """Register a Multiply-blend ExtGState on the page; returns its name or None."""
    try:
        doc = page.parent
        if not hasattr(doc, "_mul_xref"):
            doc._mul_xref = doc.get_new_xref()
            doc.update_object(doc._mul_xref, "<</Type/ExtGState/BM/Multiply/CA 1/ca 1>>")
        res = doc.xref_get_key(page.xref, "Resources")
        ref = "%d 0 R" % doc._mul_xref
        if res[0] == "xref":
            rx = int(res[1].split()[0])
            eg = doc.xref_get_key(rx, "ExtGState")
            if eg[0] == "xref":
                doc.xref_set_key(int(eg[1].split()[0]), "GSmul", ref)
            else:
                doc.xref_set_key(rx, "ExtGState/GSmul", ref)
        else:
            doc.xref_set_key(page.xref, "Resources/ExtGState/GSmul", ref)
        return "GSmul"
    except Exception as e:
        warnings.append("multiply blend unavailable: %s" % e)
        return None
HIGHLIGHT_OPACITY = 1.0


def draw_stroke(page, s, scale):
    pts = s["points"]
    if not pts:
        return
    rgb, alpha = argb(s["color"])
    kind = s["type"]
    rgb = _adapt(rgb, kind)
    width = max(s["width"] * scale * (1.0 if kind in (2, 100) else PEN_WIDTH_FACTOR), 0.2)
    if kind == 100:                                          # pencil: smooth translucent line
        sh = page.new_shape()
        add_smooth(sh, [((p["x"] + _OX) * scale, (p["y"] + _OY) * scale) for p in pts])
        sh.finish(oc=_OC, color=rgb, width=width, stroke_opacity=PENCIL_OPACITY, closePath=False, lineCap=1, lineJoin=1)
        sh.commit()
        return
    if kind == 2:  # highlighter
        sh = page.new_shape()
        add_smooth(sh, [((p["x"] + _OX) * scale, (p["y"] + _OY) * scale) for p in pts])
        sh.finish(oc=_OC, color=rgb, width=width, stroke_opacity=(0.5 if _DARK else HIGHLIGHT_OPACITY), closePath=False,
                  lineCap=1, lineJoin=1)
        gs = None if _DARK else _multiply_gs(page)
        if gs:
            sh.totalcont = "/%s gs\n" % gs + sh.totalcont
        sh.commit()
        return
    if len(pts) == 1 or all(p["x"] == pts[0]["x"] and p["y"] == pts[0]["y"] for p in pts):
        sh = page.new_shape()
        r = max(width * (0.5 + pts[0].get("p", 0.5)) / 2, 0.15)
        sh.draw_circle(((pts[0]["x"] + _OX) * scale, (pts[0]["y"] + _OY) * scale), r)
        sh.finish(oc=_OC, color=None, fill=rgb)
        sh.commit()
        return
    # pressure-sensitive: group consecutive segments with the same quantised width
    def wq(p):
        return round(width * (PRESSURE_MIN + PRESSURE_GAIN * min(max(p, 0), 1)), 2)
    sh = page.new_shape()
    run, cur = [], None
    for i, p in enumerate(pts):
        w = wq(p.get("p", 0.5))
        xy = ((p["x"] + _OX) * scale, (p["y"] + _OY) * scale)
        if cur is None or w != cur:
            if run and len(run) > 1:
                add_smooth(sh, run)
                sh.finish(oc=_OC, color=rgb, width=cur, closePath=False, lineCap=1, lineJoin=1)
            run = [run[-1]] if run else []
            cur = w
        run.append(xy)
    if len(run) > 1:
        add_smooth(sh, run)
        sh.finish(oc=_OC, color=rgb, width=cur, closePath=False, lineCap=1, lineJoin=1)
    sh.commit()


def draw_shape(page, row, scale):
    pts = json.loads(row["points"])
    if len(pts) < 2:
        return
    rgb, _ = argb(row["color"])
    rgb = _adapt(rgb, 0)
    xy = [((p["x"] + _OX) * scale, (p["y"] + _OY) * scale) for p in pts]
    width = max(row["width"] * scale * (PEN_WIDTH_FACTOR if row["width"] < 10 else 1.0), 0.2)
    sh = page.new_shape()
    stype = row["type"]
    if stype == 17 and len(xy) >= 3:                       # ellipse inscribed in the 4-point box
        xs, ys = [q[0] for q in xy], [q[1] for q in xy]
        sh.draw_oval(fitz.Rect(min(xs), min(ys), max(xs), max(ys)))
        closed = True
    elif stype == 3 and len(xy) >= 3:                      # rectangle / polygon
        sh.draw_polyline(xy + [xy[0]])
        closed = True
    else:                                                  # 7 = line, anything else: open polyline
        sh.draw_polyline(xy)
        closed = False
        if stype != 7:
            warnings.append("shape type %s drawn as polyline" % stype)
    fill = None
    if row["opacity_of_fill_color"]:
        fill = rgb
    sh.finish(oc=_OC, color=rgb, fill=fill, fill_opacity=(row["opacity_of_fill_color"] or 0) / 255.0 if fill else 1,
              width=width, closePath=closed, lineCap=1, lineJoin=1)
    if row["blend_mode"] == 13 and not _DARK:                  # multiply (highlighter-like shapes)
        gs = _multiply_gs(page)
        if gs:
            sh.totalcont = "/%s gs\n" % gs + sh.totalcont
    sh.commit()


def content_bounds(con, pid, hidden, known=None):
    xs0, ys0, xs1, ys1 = [], [], [], []
    for t in ("StrokeEntity", "ShapeEntity", "ImageEntity", "TextBoxEntity", "TableBlockEntity", "GlyphEntity"):
        for r in con.execute(f"select left,top,right,bottom,layer_id from {t} where page_id=?", (pid,)):
            if r[4] in hidden or r[0] is None:
                continue
            if known is not None and r[4] not in known and r[4] not in ("default", "paper_theme"):
                continue
            xs0.append(r[0]); ys0.append(r[1]); xs1.append(r[2]); ys1.append(r[3])
    if not xs0:
        return None
    return min(xs0), min(ys0), max(xs1), max(ys1)


def _inline_svg_css(text):
    import re
    rules = {}
    for sel, body in re.findall(r"([^{}]+)\{([^}]*)\}", "".join(re.findall(r"<style[^>]*>(.*?)</style>", text, re.S))):
        for name in re.findall(r"\.([\w-]+)", sel):
            rules.setdefault(name, []).append(body.strip().rstrip(";"))
    def repl(m):
        css = ";".join(b for n in m.group(1).split() for b in rules.get(n, []))
        return 'style="%s"' % css
    return re.sub(r'class="([^"]*)"', repl, text)


def insert_svg(page, rect, path):
    svg = fitz.open("svg", _inline_svg_css(open(path, encoding="utf-8").read()).encode("utf-8"))
    src = fitz.open("pdf", svg.convert_to_pdf())
    page.show_pdf_page(rect, src, 0, oc=_OC)


def html_text(s):
    import re, html
    s = re.sub(r"<style.*?</style>", "", s, flags=re.S)
    s = re.sub(r"</(p|div|li)>|<br\s*/?>", "\n", s)
    return html.unescape(re.sub(r"<[^>]+>", "", s)).strip()


def draw_table(page, row, scale):
    cols = [c["width"] for c in json.loads(row["columns_json"])]
    rows = [r["height"] for r in json.loads(row["rows_json"])]
    x0, y0 = row["left"], row["top"]
    sh = page.new_shape()
    xs = [x0]
    for w in cols:
        xs.append(xs[-1] + w)
    ys = [y0]
    for h in rows:
        ys.append(ys[-1] + h)
    for x in xs:
        sh.draw_line(((x + _OX) * scale, (ys[0] + _OY) * scale), ((x + _OX) * scale, (ys[-1] + _OY) * scale))
    for y in ys:
        sh.draw_line(((xs[0] + _OX) * scale, (y + _OY) * scale), ((xs[-1] + _OX) * scale, (y + _OY) * scale))
    sh.finish(oc=_OC, color=(0.6, 0.6, 0.6), width=0.5)
    sh.commit()
    pass


def copy_source_page(out, src_pdf, pno, pdf_cache, name):
    """Append page `pno` of the imported PDF to `out`, surviving damaged PDFs.
    Fallbacks: (1) normal copy, (2) without links/annotations, (3) after repairing the source with a
    garbage-collecting rewrite, (4) page rendered as an image. Returns the (possibly repaired) source."""
    try:
        out.insert_pdf(src_pdf, from_page=pno, to_page=pno)
        return src_pdf
    except Exception as e1:
        try:
            out.insert_pdf(src_pdf, from_page=pno, to_page=pno, links=False, annots=False)
            warnings.append("source PDF page copied without links/annotations (%s)" % e1)
            return src_pdf
        except Exception:
            pass
        try:
            repaired = fitz.open("pdf", src_pdf.tobytes(garbage=4, clean=True, deflate=True))
            pdf_cache[name] = repaired
            out.insert_pdf(repaired, from_page=pno, to_page=pno, links=False, annots=False)
            warnings.append("source PDF was damaged and was repaired (%s)" % e1)
            return repaired
        except Exception:
            pass
        pg = src_pdf[pno]
        pix = pg.get_pixmap(dpi=150)
        new = out.new_page(width=pg.rect.width, height=pg.rect.height)
        new.insert_image(new.rect, pixmap=pix)
        warnings.append("source PDF page %d rendered as an image (%s)" % (pno + 1, e1))
        return src_pdf


def add_page_annotations(con, page, pid, scale):
    """Quotes -> PDF annotations (type 6 'annotation' = sticky note); hyperlinks -> URI links."""
    try:
        for q in con.execute("select * from QuoteEntity where page_id=?", (pid,)):
            rects = json.loads(q["rect_list"] or "[]")
            if not rects:
                continue
            texts = []
            for aid in json.loads(q["anno_item_id_list"] or "[]"):
                row = con.execute("select text from QuoteTextAnnoEntity where id=?", (aid,)).fetchone()
                if row and row["text"]:
                    texts.append(row["text"])
            r0 = rects[0]
            x, y = (r0["left"] + _OX) * scale, (r0["top"] + _OY) * scale
            if q["type"] == 6:
                a = page.add_text_annot(fitz.Point(x, y), "\n".join(texts) or "annotation", icon="Comment")
                try:
                    a.set_colors(stroke=argb(q["bg_color"])[0]); a.update()
                except Exception:
                    pass
            else:
                warnings.append("quote type %s drawn as a highlight (unverified)" % q["type"])
                col, al = argb(q["bg_color"])
                if al > 0:
                    for r in rects:
                        rc = fitz.Rect((r["left"] + _OX) * scale, (r["top"] + _OY) * scale,
                                       (r["right"] + _OX) * scale, (r["bottom"] + _OY) * scale)
                        h = page.add_highlight_annot(rc)
                        h.set_colors(stroke=col); h.update()
    except sqlite3.Error:
        pass
    try:
        for h in con.execute("select * from HyperLinkEntity where page_id=?", (pid,)):
            dest = h["destination"] or ""
            if h["left"] is None:
                continue
            rc = fitz.Rect((h["left"] + _OX) * scale, (h["top"] + _OY) * scale,
                           (h["right"] + _OX) * scale, (h["bottom"] + _OY) * scale)
            if dest.startswith(("http://", "https://", "mailto:")):
                page.insert_link({"kind": fitz.LINK_URI, "from": rc, "uri": dest})
            else:
                warnings.append("hyperlink to non-URL destination skipped (unverified type %s)" % h["type"])
    except sqlite3.Error:
        pass


def add_outline(con, out, page_ids):
    """OutlineEntity -> PDF bookmarks (unverified: no test note contains outline entries)."""
    try:
        rows = con.execute("select * from OutlineEntity").fetchall()
    except sqlite3.Error:
        return
    if not rows:
        return
    byid = {r["id"]: r for r in rows}
    def depth(r):
        d, seen = 1, set()
        while r["parent_id"] in byid and r["parent_id"] not in seen:
            seen.add(r["parent_id"]); r = byid[r["parent_id"]]; d += 1
        return d
    toc = []
    for r in sorted(rows, key=lambda r: r["creation_time"] or 0):
        if r["page_id"] in page_ids:
            toc.append([depth(r), r["title"] or "Untitled", page_ids.index(r["page_id"]) + 1])
    if toc:
        try:
            if toc[0][0] != 1:
                toc[0][0] = 1
            out.set_toc(toc)
        except Exception as e:
            warnings.append("outline skipped: %s" % e)


def export_audio(con, tmp, dst):
    """Write each embedded recording next to the PDF as an .mp3 (mp3 copied as-is, others via ffmpeg)."""
    import re, subprocess
    names = {}
    try:
        for r in con.execute("select id,audio_name from AudioFileEntity"):
            names[r["id"]] = r["audio_name"]
    except sqlite3.Error:
        pass
    stem = os.path.splitext(dst)[0]
    written = []
    for f in sorted(os.listdir(tmp)):
        m = re.match(r"note_audio_(.+)\.(\w+)$", f)
        if not m:
            continue
        aid, ext = m.groups()
        label = re.sub(r'[\\/:*?"<>|]+', "_", names.get(aid, aid)).strip() or aid
        target = "%s - %s.mp3" % (stem, label)
        if ext.lower() == "mp3":
            shutil.copy(os.path.join(tmp, f), target)
        else:
            r = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", os.path.join(tmp, f),
                                "-vn", "-codec:a", "libmp3lame", "-q:a", "2", target])
            if r.returncode != 0:
                warnings.append("audio %s: ffmpeg failed" % f)
                continue
        written.append(target)
    return written


def convert(src, dst, embed_source=True):
    global _OX, _OY, _OC
    tmp = tempfile.mkdtemp(prefix="notein2pdf_")
    try:
        with zipfile.ZipFile(src) as z:
            z.extractall(tmp)
        con = open_db(tmp)
        files = os.listdir(tmp)
        meta = json.load(open(os.path.join(tmp, "note_meta.json"))) if "note_meta.json" in files else {}
        note = con.execute("select * from NoteContentEntity").fetchone()
        page_ids = json.loads(note["page_list"])
        hidden = {r["id"] for r in con.execute("select id from PageLayerEntity where visible=0")}
        pdf_cache = {}
        out = fitz.open()
        layer_rows = {r["id"]: r for r in con.execute("select * from PageLayerEntity")}
        layer_order = json.loads(note["page_layer_list"] or "[]")
        oc_cache = {}

        def get_oc(lid):
            """One PDF layer (optional content group) per Notein layer; hidden layers start switched off."""
            if lid not in oc_cache:
                row = layer_rows.get(lid)
                if lid == "paper_theme":
                    name = "Background"
                elif lid == "default":
                    name = "Default layer"
                elif row is not None:
                    name = row["name"] or "Layer"
                else:
                    name = "Unlisted layer " + str(lid)[:8]
                on = True if row is None else bool(row["visible"])
                if not on:
                    name += " (hidden in Notein)"
                oc_cache[lid] = out.add_ocg(name, on=on)
            return oc_cache[lid]

        for lid in layer_order:
            get_oc(lid)
        for pid in page_ids:
            pg = con.execute("select * from PageEntity where id=?", (pid,)).fetchone()
            if pg is None:
                warnings.append(f"page {pid} missing")
                continue
            spec = json.loads(pg["paper_spec"] or "{}")
            pw, ph = spec.get("width", 1240), spec.get("height", 1754)
            theme = pg["paper_theme"]
            tj = json.loads(theme) if theme else {}
            if tj.get("type", "").endswith("PdfPaperTheme"):
                name = os.path.basename(tj["pdfInfo"]["pdfPath"])
                if name not in pdf_cache:
                    pdf_cache[name] = fitz.open(os.path.join(tmp, "note_pdf_" + name)) \
                        if os.path.exists(os.path.join(tmp, "note_pdf_" + name)) else None
                    d_ = pdf_cache[name]
                    if d_ is not None and d_.needs_pass:
                        # the app stores the PDF's password in PdfInfoEntity; unlock the same way
                        pws = [r[0] for r in con.execute("select password from PdfInfoEntity where id=? or pdf_path like ?",
                                                         (tj["pdfInfo"].get("id"), "%" + name)) if r[0]]
                        pws += [r[0] for r in con.execute("select password from PdfInfoEntity") if r[0] and r[0] not in pws]
                        if not any(d_.authenticate(p_) for p_ in pws):
                            warnings.append("source PDF is password-protected and no stored password worked")
                _OX = _OY = 0.0
                src_pdf = pdf_cache[name]
                if src_pdf is None:
                    warnings.append(f"source pdf {name} missing")
                    page = out.new_page(width=pw * PX_TO_PT, height=ph * PX_TO_PT)
                    scale = PX_TO_PT
                else:
                    src_pdf = copy_source_page(out, src_pdf, tj["pageNum"], pdf_cache, name)
                    page = out[-1]
                    scale = page.rect.width / pw
            else:
                _OX = _OY = 0.0
                if pg["unbounded"]:
                    b = content_bounds(con, pid, set(), set(layer_order))
                    m = UNBOUNDED_MARGIN
                    if b:
                        _OX, _OY = m - b[0], m - b[1]
                        pw, ph = b[2] - b[0] + 2 * m, b[3] - b[1] + 2 * m
                # the app shrinks big infinite-canvas exports so the page area stays <= 3 Mi pt^2
                scale = min(PX_TO_PT, math.sqrt(UNBOUNDED_MAX_AREA / (pw * ph)))
                page = out.new_page(width=pw * scale, height=ph * scale)
                _OC = get_oc("paper_theme")
                paper_background(page, theme, scale, pw, ph)
                _OC = None

            # collect every drawable item, then paint bottom-to-top: layer order, then creation time
            order = json.loads(note["page_layer_list"] or "[]")
            rank = {lid: i for i, lid in enumerate(order)}
            default_rank = rank.get("default", len(order))
            items = []
            for kind, table in (("image", "ImageEntity"), ("stroke", "StrokeEntity"), ("shape", "ShapeEntity"),
                                ("text", "TextBoxEntity"), ("table", "TableBlockEntity")):
                for r in con.execute(f"select * from {table} where page_id=?", (pid,)):
                    if r["layer_id"] not in rank and r["layer_id"] not in ("default", "paper_theme"):
                        warnings.append("items on orphaned layer (not in layer list) skipped, as the app does")
                        continue
                    items.append((rank.get(r["layer_id"], default_rank), r["creation_time"] or 0, kind, r))
            items.sort(key=lambda t: (t[0], t[1]))
            for _, _, kind, r in items:
                _OC = get_oc(r["layer_id"])
                try:
                    if kind == "image":
                        f = os.path.join(tmp, "note_image_" + os.path.basename(r["uri"]))
                        if not os.path.exists(f):
                            warnings.append(f"image {os.path.basename(r['uri'])} missing")
                            continue
                        rect = fitz.Rect((r["left"] + _OX) * scale, (r["top"] + _OY) * scale,
                                         (r["right"] + _OX) * scale, (r["bottom"] + _OY) * scale)
                        try:
                            if f.lower().endswith(".svg"):
                                insert_svg(page, rect, f)
                            else:
                                page.insert_image(rect, filename=f, oc=_OC,
                                                  rotate=int(r["rotation"]) if r["rotation"] else 0)
                        except Exception as e:
                            warnings.append("image %s skipped: %s" % (os.path.basename(f), e))
                    elif kind == "stroke":
                        if r["record_json"]:
                            draw_stroke(page, json.loads(r["record_json"]), scale)
                        elif r["ink_stroke_blob"]:
                            try:
                                draw_stroke(page, decode_blob_stroke(r["ink_stroke_blob"]), scale)
                            except Exception as e:
                                warnings.append("binary stroke skipped: %s" % e)
                    elif kind == "shape":
                        draw_shape(page, r, scale)
                    elif kind == "text":
                        rect = fitz.Rect((r["left"] + _OX) * scale, (r["top"] + _OY) * scale,
                                         (r["right"] + _OX) * scale, (r["bottom"] + _OY) * scale)
                        rgb, _ = argb(r["default_text_color"])
                        import re as _re
                        m = _re.search(r"color:\s*#([0-9a-fA-F]{6})", r["text"] or "")
                        if m:
                            h = m.group(1); rgb = tuple(int(h[k:k+2], 16) / 255 for k in (0, 2, 4))
                        page.insert_textbox(rect, html_text(r["text"]), fontsize=max(r["text_size"] * scale, 4), color=rgb, oc=_OC)
                    elif kind == "table":
                        draw_table(page, r, scale)
                except Exception as e:
                    warnings.append("item skipped (%s): %s" % (kind, e))
            _OC = None
            add_page_annotations(con, page, pid, scale)
        add_outline(con, out, page_ids)
        out.set_metadata({"title": meta.get("title", ""), "producer": "notein2pdf"})
        if embed_source:                      # makes the conversion exactly reversible (see provenance.py)
            provenance.embed_source(out, src, meta)
        out.save(dst, garbage=3, deflate=True)
        for a in export_audio(con, tmp, dst):
            print("audio:", a)
        return len(out)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main(argv=None):
    """CLI: notein2pdf INPUT.in OUTPUT.pdf"""
    argv = sys.argv[1:] if argv is None else argv
    embed = "--no-embed" not in argv
    argv = [a for a in argv if a != "--no-embed"]
    if len(argv) != 2:
        print(__doc__)
        return 2
    n = convert(argv[0], argv[1], embed_source=embed)
    print(f"wrote {argv[1]} ({n} pages)")
    for w in sorted(set(warnings)):
        print("warning:", w, f"(x{warnings.count(w)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
