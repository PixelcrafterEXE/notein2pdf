"""Compare converter output with the Notein app's own PDF export of the same notes.

Fixtures live in tests/fixtures/<name>/{note.in,app.pdf} (git-ignored, personal). Missing fixtures -> skipped.
"""
import shutil
from pathlib import Path

import pymupdf
import pytest

from notein2pdf import converter as conv

FIX = Path(__file__).parent / "fixtures"
# name -> minimum coverage (both directions) on every page; export_test is an infinite canvas (smoke test only)
PAGED = {"sheet_2": 0.985, "gute_nachricht": 0.995, "frauen_regiern": 0.99}


def _have(name):
    return (FIX / name / "note.in").exists() and (FIX / name / "app.pdf").exists()


@pytest.mark.skipif(shutil.which("pdftoppm") is None, reason="needs poppler's pdftoppm")
@pytest.mark.parametrize("name,minimum", sorted(PAGED.items()))
def test_matches_app_export(name, minimum, tmp_path):
    if not _have(name):
        pytest.skip(f"fixture {name} not present")
    from compare_pages import compare
    out = tmp_path / "out.pdf"
    conv.convert(str(FIX / name / "note.in"), str(out))
    assert pymupdf.open(out).page_count == pymupdf.open(FIX / name / "app.pdf").page_count
    for page, a_ink, m_ink, ca, cb in compare(str(FIX / name / "app.pdf"), str(out)):
        if a_ink == 0 and m_ink == 0:
            continue                                         # blank page in both
        assert ca >= minimum and cb >= minimum, f"{name} page {page}: {ca:.3f}/{cb:.3f}"


def test_infinite_canvas_note_converts_with_layers_audio_and_annotation(tmp_path):
    if not _have("export_test"):
        pytest.skip("fixture export_test not present")
    out = tmp_path / "Export_Test.pdf"
    conv.convert(str(FIX / "export_test" / "note.in"), str(out))
    doc = pymupdf.open(out)
    assert doc.page_count == 1
    assert len(doc.get_ocgs()) >= 3
    assert len(list(doc[0].annots())) == 1                   # the sticky-note annotation
    assert len(list(tmp_path.glob("*.mp3"))) == 2            # both embedded recordings
