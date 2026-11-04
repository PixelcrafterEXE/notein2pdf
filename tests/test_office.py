"""Office/Markdown -> PDF (OnlyOffice first) and the one-step import into a Notein note."""
import shutil
import subprocess
import zipfile

import pymupdf
import pytest

from notein2pdf import back, converter as conv, office

pytestmark = pytest.mark.skipif(not office.has_onlyoffice() or shutil.which("pandoc") is None,
                                reason="needs ONLYOFFICE Desktop Editors (x2t) and pandoc")

MD = "# Übung 3 – Größenänderung\n\nText mit ä ö ü ß und einer Liste:\n\n- eins\n- zwei\n\n| A | B |\n|---|---|\n| 1 | 2 |\n"


def text(pdf):
    """All text with whitespace removed (OnlyOffice kerns some glyphs apart, which extraction reports as spaces)."""
    return "".join(w for p in pymupdf.open(pdf) for w in p.get_text().split())


@pytest.fixture
def md_file(tmp_path):
    f = tmp_path / "uebung.md"
    f.write_text(MD, encoding="utf-8")
    return f


@pytest.mark.parametrize("ext", ["docx", "pptx", "odt"])
def test_office_formats_use_onlyoffice(ext, md_file, tmp_path):
    src = tmp_path / f"doc.{ext}"
    subprocess.run(["pandoc", str(md_file), "-o", str(src)], check=True)
    out = tmp_path / "out.pdf"
    assert office.to_pdf(src, out) == "onlyoffice"
    doc = pymupdf.open(out)
    assert doc.page_count >= 1 and "ONLYOFFICE" in doc.metadata["producer"]
    assert "Größenänderung" in text(out)


def test_markdown_goes_through_onlyoffice_by_default(md_file, tmp_path):
    out = tmp_path / "md.pdf"
    assert office.to_pdf(md_file, out) == "pandoc+onlyoffice"
    assert "Größenänderung" in text(out)


def test_pdf_passthrough_and_unsupported_type(tmp_path):
    pdf = tmp_path / "a.pdf"
    d = pymupdf.open(); d.new_page(); d.save(pdf)
    assert office.to_pdf(pdf, tmp_path / "b.pdf") == "copy"
    with pytest.raises(office.ConversionError):
        office.to_pdf(tmp_path / "x.exe", tmp_path / "x.pdf")


def test_pdf2notein_imports_a_word_file_in_one_step(md_file, tmp_path, capsys):
    docx = tmp_path / "Lecture notes.docx"
    subprocess.run(["pandoc", str(md_file), "-o", str(docx)], check=True)
    note = tmp_path / "out.in"
    assert back.main([str(docx), str(note), "--parent-id", "f1"]) == 0
    assert "with onlyoffice" in capsys.readouterr().out
    names = zipfile.ZipFile(note).namelist()
    assert any(n.startswith("note_pdf_Lecture notes_notein_pdf_") for n in names)       # title = file name
    pdf = tmp_path / "back.pdf"
    assert conv.convert(str(note), str(pdf), embed_source=False) >= 1                   # the note converts again
    assert "Größenänderung" in text(pdf)
