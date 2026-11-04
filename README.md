# notein2pdf

Convert [Notein](https://notein.app) notes into **vector PDFs** — and back — outside the app, in bulk.

Notein only exports one note at a time and only in a proprietary format. This tool reads that format
(the native `.in` export, which is also exactly what Notein's Google Drive sync stores, just without
a file extension) and rebuilds each note as a PDF: imported PDFs with your handwriting on top, or
blank/dotted/grid pages, with strokes, shapes, images, text boxes and more.

## Features
- **Faithful**: validated page-by-page against the app's own "Standard" PDF export (≥ 99 % ink coverage both ways
  on the test notes). Pen pressure, highlighters (multiply blend), pencil, shapes, SVG/PNG/JPEG images,
  dotted and infinite-canvas paper.
- **Layers kept**: every Notein layer is a PDF layer (optional content group) you can show/hide in a viewer;
  layers hidden in Notein start switched off.
- **Annotations**: Notein "annotation" quotes → PDF sticky notes; URL hyperlinks; bookmarks.
- **Audio**: embedded recordings are written next to the PDF as `.mp3` (needs `ffmpeg` on `PATH`).
- **Bulk mirror**: `notein2pdf-batch` converts a whole folder of exports, rebuilds the folder tree from
  Notein's folder records, skips trashed notes, and only re-converts notes that changed.
- Handles password-protected imported PDFs (Notein stores the password in the note) and damaged PDFs.

## Back to Notein: `pdf2notein`
```bash
pdf2notein note.pdf restored.in            # PDF made by notein2pdf  -> the ORIGINAL export, byte-for-byte
pdf2notein report.pdf new_note.in          # any other PDF           -> a new Notein note (import)
```
`pdf2notein` also takes **Office documents and Markdown** directly: `.docx .doc .odt .rtf .pptx .ppt .odp .xlsx .xls .ods .csv .txt`
are converted with **OnlyOffice's headless converter (`x2t`, ~0.3–0.5 s per file)**, Markdown goes pandoc → docx → OnlyOffice
(`--md-engine latex|weasyprint` to use pandoc's own engines). LibreOffice is only a logged fallback when OnlyOffice is absent.

A PDF's drawing cannot reproduce a note bit-for-bit (no stroke ids, timestamps or raw pressure), so `notein2pdf` attaches
the original `.in` to every PDF it writes (`--no-embed` to skip; viewers show the same page, the file just gets bigger).
`pdf2notein` extracts it again and checks its SHA-256 — tested byte-identical on all real notes. If the PDF was edited
after export, you are warned: those edits are not merged. For PDFs without an attachment, a new Notein note is built
around the PDF (one page per PDF page, empty ink layers) using a database generated from the app's own schema
(`src/notein2pdf/data/schema_v31.json`, DDL only; refresh with `tools/extract_schema.py`). Importing the result into
the Notein app is the one step these tests cannot cover.

## Install
```bash
python -m venv .venv
.venv/bin/pip install -e ".[dev]"     # drop [dev] if you don't need the tests / compare tools
```
Requires Python ≥ 3.10 and [PyMuPDF](https://pymupdf.readthedocs.io). Optional: `ffmpeg` (audio), poppler's
`pdftoppm` (only for the validation tools), ONLYOFFICE Desktop Editors + `pandoc` (Office/Markdown input).

## Use
```bash
# one note
notein2pdf "My Note.in" out.pdf

# a folder of exports (.in files or extensionless cloud-sync files), mirrored into OUT_DIR
notein2pdf-batch SRC_DIR OUT_DIR              # [--force] [--include-trash] [--shard=0/2]
```
To get your notes out of Notein: *Export | Share → Native* per note, or sync to Google Drive and download the
`NoteInDataSync` folder (see [docs/CLOUD_SYNC.md](docs/CLOUD_SYNC.md)). `--shard=I/N` lets you run several
processes over one folder (each one needs ~1 GB RAM for big imported PDFs).

### Automatic sync: Notein → Nextcloud
`notein2pdf-sync` runs `notein2pdf-batch`'s logic continuously as a one-way background service, keeping a
Nextcloud folder up to date with (a chosen part of) your Notein library - new/changed notes converted and
uploaded, renames/moves handled as cheap server-side moves, trashed notes removed (to Nextcloud's own trash).
It never writes to Notein or Drive. See [docs/SYNC_SERVICE.md](docs/SYNC_SERVICE.md) for setup (systemd units
included under `systemd/`) and [docs/CLOUD_SYNC.md](docs/CLOUD_SYNC.md) for why the reverse direction - getting
a note *into* Notein from a PC - isn't supported as an automated service (only a manual, one-note-at-a-time
recipe, [docs/IMPORT_INTO_NOTEIN.md](docs/IMPORT_INTO_NOTEIN.md)).

## Repository layout
```
src/notein2pdf/    converter.py (note → PDF), back.py (PDF → note), provenance.py (embedded original),
                   office.py (Office/Markdown → PDF), batch.py (folder mirror), sync_service.py
                   (Notein → Nextcloud daemon), notein_tree.py (shared folder-tree helpers),
                   CLI entry points, data/ (Room schema template)
tools/             compare_pages.py, compare_unbounded.py, inspect_in.py, extract_schema.py
tests/             synthetic tests (run anywhere) + fixture tests against real notes (local only)
systemd/           user-level .service/.timer for notein2pdf-sync
docs/              FORMAT.md (file format), CLOUD_SYNC.md (Drive sync behaviour), SYNC_SERVICE.md,
                   IMPORT_INTO_NOTEIN.md
output/            default place for converted PDFs — git-ignored
```

## Tests
```bash
.venv/bin/python -m pytest            # synthetic tests always run; fixture tests skip if tests/fixtures is empty
```
Fixture tests need real notes exported twice (native + the app's PDF); see `tests/fixtures/README.md`.
The fixtures are personal data and are **git-ignored**.

## Known gaps
Implemented from the data model but unverified (no test note uses them): table cell text, quote types other
than "annotation", non-URL hyperlinks, outline bookmarks, landscape/rotated pages, paper styles other than
blank / dots / infinite grid. Tuning knobs (env vars): `NOTEIN_PEN_FACTOR`, `NOTEIN_PRESSURE_MIN`,
`NOTEIN_PRESSURE_GAIN`. Not affiliated with Notein.
