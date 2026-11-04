# The Notein native format (reverse-engineered)

A native export (`.in`, or a file in Notein's Drive sync folder) is a plain **zip**:

| entry | content |
|---|---|
| `note_meta.json` | `id`, `title`, `parentId` (folder), `inTrashBin`, `lastModificationTime`, `totalPagesCount`, `unboundedNote`, … |
| `note_database_note_<id>_db` (+ `-wal`, `-shm`) | Android Room **SQLite** database (copy all three files together before opening) |
| `note_pdf_<name>.pdf` | the imported PDF, if any (may be password-protected: password in `PdfInfoEntity.password`) |
| `note_image_<id>.png/.svg/…` | images placed on pages |
| `note_audio_<id>.m4a/.mp3` | recordings (`AudioFileEntity` has the display names) |
| `note_label.json`, `note_extra.json`, `note_in_flag.json`, `notepdf_lost_info.json` | labels / flags (not needed for rendering) |

Folders are separate zips containing `folder_<title>` (JSON: `id`, `title`, `parentId`), `folder_labels.json` and a thumbnail.

## Database (tables used)
- `NoteContentEntity.page_list` — JSON list of page ids in order; `page_layer_list` — layer ids **bottom → top**.
- `PageEntity` — `paper_spec` (width/height px; A4 = 1240×1754 = 150 dpi), `paper_theme` (JSON: `PdfPaperTheme` with `pageNum`
  + `pdfInfo`, or `LambdaPaperTheme` with `baseTheme.color` and `paperStyle`: `BlankPaperStyle`, `NewDotPaperStyle`,
  `UnboundedSquarePaperStyle`, …), `unbounded` (infinite canvas). Pages not in `page_list` are history/trash.
- `PageLayerEntity` — `visible`, `name`. Ids `paper_theme` (background) and `default` are implicit.
  **Items whose `layer_id` is not in `page_layer_list` are orphaned and not rendered by the app.**
- `StrokeEntity` — `record_json` (`type`, `color` ARGB int, `width`, `points[{x,y,p,action}]`) **or** `ink_stroke_blob`
  (protobuf, brush `notein-pencil-v3`: field 10 = packed float32 x,y; field 13 = 3×3 matrix with the page translation;
  field 4 = brush size; ink colour is *not* reliably in field 5). Stroke types: 0 pen, 2 highlighter (multiply),
  5 fine pen, 11 eraser-like, 100 (ours) = decoded pencil blobs.
- `ShapeEntity` — `type` 7 line, 3 rectangle/polygon (4 corner points), 17 ellipse (inscribed in the 4-point box);
  `blend_mode` 13 = multiply.
- `ImageEntity`, `TextBoxEntity` (HTML text), `TableBlockEntity` (columns/rows/cells JSON), `QuoteEntity` (type 6 = sticky-note
  annotation; text in `QuoteTextAnnoEntity`), `CommentEntity`, `OutlineEntity`, `HyperLinkEntity`, `AudioFileEntity`,
  `AudioSyncRecordEntity`, `GlyphEntity`/`Beautify*` (handwriting beautification).

## Rendering facts established against the app's PDF export
- Coordinates are page pixels at 150 dpi → PDF points × 0.48. Imported PDF pages are scaled to the page width.
- Draw order: layer order, then `creation_time`. Highlighters use Multiply blending (not on dark paper).
- Black ink stays black on dark paper in the export (no inversion).
- Dotted paper: step = (width − left − right padding)/30 (39 px on A4), first column at the left padding,
  first row half a step below the top padding. Infinite-canvas grid step = `requiredItemSpace` (37.5 px).
- Hidden layers are not exported by the app.

## Structure of an imported-PDF note (what `pdf2notein` generates)
- DB: WAL mode, page size 4096, `user_version` 31, Room `identity_hash c7edc3e9…` (all from `data/schema_v31.json`).
- `NoteContentEntity`: one row; `page_list` = the PDF pages' ids in order; `page_layer_list` = `["paper_theme","default"]`;
  `default_page_id` = a hidden blank A4 page (`PageEntity.source = 0`).
- `PageEntity` (`source = 2`): `paper_spec` width 1240, height floor(1240·h/w) (±1 px vs the app on some pages), name `PDF`;
  `paper_theme` = `PdfPaperTheme` with `pageNum` and `pdfInfo`. Deleted pages keep their PDF page; they are just absent from `page_list`.
- `PdfInfoEntity` (id, `pdf_path` = `/data/user/0/…/files/documents/<title>_notein_pdf_<uuid>.pdf`, `password`), two `PageLayerEntity` rows.
- Zip members (fixed 1980-01-01 timestamps, deflate): DB, `note_extra.json`, `note_in_flag.json`, `note_label.json`,
  `note_meta.json`, `note_pdf_<name>.pdf`, empty `notepdf_lost_info.json`.
