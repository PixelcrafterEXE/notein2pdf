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
