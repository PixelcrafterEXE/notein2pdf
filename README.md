# notein2pdf

Convert [Notein](https://notein.app) notes into **vector PDFs** — outside the app, in bulk.

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

## Install
```bash
python -m venv .venv
.venv/bin/pip install -e ".[dev]"     # drop [dev] if you don't need the tests / compare tools
```
Requires Python ≥ 3.10 and [PyMuPDF](https://pymupdf.readthedocs.io). Optional: `ffmpeg` (audio), poppler's
`pdftoppm` (only for the validation tools).

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

## Repository layout
```
src/notein2pdf/    converter.py (note → PDF), batch.py (folder mirror), CLI entry points
tools/             validation helpers: compare_pages.py, compare_unbounded.py, inspect_in.py
tests/             synthetic tests (run anywhere) + fixture tests against real notes (local only)
docs/              FORMAT.md (reverse-engineered file format), CLOUD_SYNC.md (Drive sync behaviour)
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
