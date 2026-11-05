# Known issues: unsupported or unverified rendering

Inventory from scanning every non-trashed note of the author's library (2026-11-05). Counts are what occurs in
that library. "Needs reference" means the note file holds no geometry: an app export (Export | Share -> Standard
PDF, plus the native `.in`) of a note using the feature is needed to measure it. See `tests/fixtures/README.md`
for the fixture layout. Do not guess geometry; the first infinite-grid attempt was visibly wrong until measured
against an app export.

## Page backgrounds (`PageEntity.paper_theme`)

| Paper | Pages / notes | Status |
|---|---|---|
| Imported PDF page (`PdfPaperTheme`) | 23,225 / 783 | works |
| `BlankPaperStyle` | 1,450 / 848 | works |
| `NewDotPaperStyle` (A4 dots) | 66 / 22 | works, checked against app export |
| `UnboundedSquarePaperStyle` | 5 / 2 | works; margin 100, area-capped scale and canvas-origin lattice match the app export (~0.1 pt) |
| `UnboundedDotPaperStyle` | 14 / 6 | works; same lattice as the square grid. **Dot size/colour unverified** (no app export of a dotted infinite page) |
| `UnboundedLinePaperStyle` | 1 / 1 | **not rendered.** Likely horizontal lines at `requiredItemSpace` (37.5) with the square grid's phase; unverified |
| `NewSquarePaperStyle` (A4 grid) | 20 / 11 | **not rendered** (background colour only). Needs reference: paddings are stored, grid spacing is not |
| `DotTwoColumnPaperStyle` | 9 / 1 | **not rendered.** Needs reference |
| `BlankTwoColumnPaperStyle` | 1 / 1 | not recognised (warning). Unknown whether the app draws a column divider |
| `PdfSetPaperTheme` (`music_v2`, staff paper) | 2 / 1 | **not rendered.** Built-in template that the app downloads; it is not inside the note file. Needs reference |
| `LocalUriPaperTheme` (local image background) | 21 / 2 | **not rendered** |
| `LocalResPaperTheme` (built-in image background) | 1 / 1 | **not rendered** |

## Shapes (`ShapeEntity.type`, 983 total)

| Type | Count | Status |
|---|---|---|
| 7 (line) | 554 | works |
| 3 (rectangle / polygon) | 236 | works |
| 17 (ellipse) | 1 | works |
| 6, 0, 2, 8, 21, 1, 5, 4, 9, 12, 14 | 192 (6: 66, 0: 47, 2: 29, 8: 18, 21: 11, 1: 10, 5: 5, 4: 3, 9/12/14: 1 each) | **drawn as an open polyline** through the stored points, with a warning. Wrong for closed shapes (a 4-point box loses its last side). Meaning of the type numbers unknown (probably triangle, diamond, arrow, star, ...). Needs reference |

## Unverified or unhandled

- Stroke types 5, 10, 11 (about 26 of 83,000 sampled strokes) are drawn like ordinary pen strokes; they may be a
  different brush.
- Note-to-note links (`DualLinkSourceEntity` / `DualLinkRefEntity`, ~100 rows in 26 notes): no handling, dropped.
- `CommentEntity` (2 rows in 1 note): no handling.
- `OutlineEntity` (bookmarks, 8,305 rows in 181 notes): code exists but is marked unverified in `converter.py`.
- Infinite-canvas geometry was measured on a single app export (`export_test`); the area cap
  (3 Mi pt^2) and margin (100) are inferred from that one data point.
- Large infinite-canvas pages with thousands of strokes still take tens of seconds (4,618 strokes: ~45 s).

## Sync service

- `tests/test_office.py::test_markdown_goes_through_onlyoffice_by_default` failed once on a full run; not
  investigated.
- Each sync cycle takes ~50 s, mostly the Drive listing with rclone's shared client id (being retired during
  2026). A personal Google OAuth client id for the Drive remote, and a single combined listing (or the Drive
  Changes API), would cut this.
- The `notein2pdf-backups/` folder on Nextcloud holds the flat, possibly collided backups from the first sync;
  review and delete manually.
