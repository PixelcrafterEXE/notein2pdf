"""Embed the original Notein export inside the PDF so the conversion is losslessly reversible.

A PDF can't carry stroke ids, timestamps or raw pressure samples, so a note can't be rebuilt bit-for-bit from the
drawing alone. Instead the converter attaches the original `.in` to the PDF (an ordinary PDF file attachment — every
viewer still shows the normal vector rendering) together with its SHA-256 and a digest of the page contents.
`pdf2notein` extracts it again: the result is byte-identical to the original export.
"""
import hashlib
import json

EMBED_NAME = "notein-source.in"
FORMAT = 1


def pages_digest(doc):
    """SHA-256 over the decoded content streams of all pages (detects edits made to the PDF after export)."""
    h = hashlib.sha256()
    for page in doc:
        h.update(page.read_contents())
        h.update(b"\x00page\x00")
    return h.hexdigest()


def embed_source(doc, src_path, meta=None):
    data = open(src_path, "rb").read()
    info = {"format": FORMAT, "sha256": hashlib.sha256(data).hexdigest(), "size": len(data),
            "pages_sha256": pages_digest(doc)}
    if meta:
        info.update({k: meta.get(k) for k in ("id", "title", "lastModificationTime") if meta.get(k) is not None})
    title = (meta or {}).get("title") or "note"
    doc.embfile_add(EMBED_NAME, data, filename=f"{title}.in", ufilename=f"{title}.in", desc=json.dumps(info))
    return info


def read_embedded(doc):
    """Return (info_dict, bytes) of the embedded original export, or None if the PDF has none."""
    if EMBED_NAME not in doc.embfile_names():
        return None
    entry = doc.embfile_info(EMBED_NAME)
    try:
        info = json.loads(entry.get("description") or entry.get("desc") or "{}")
    except ValueError:
        info = {}
    return info, doc.embfile_get(EMBED_NAME)
