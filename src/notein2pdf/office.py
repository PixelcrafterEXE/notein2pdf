"""Convert Office documents and Markdown to PDF - OnlyOffice first.

Office files (.docx .doc .odt .rtf .pptx .ppt .odp .xlsx .xls .ods .csv .txt) go through OnlyOffice's headless
converter ``x2t`` (shipped with ONLYOFFICE Desktop Editors, no cold start: ~0.3-0.5 s per file).
Markdown goes pandoc -> .docx -> x2t (same engine, handles math); ``md_engine="latex"`` / ``"weasyprint"`` use pandoc's
own PDF engines instead. LibreOffice is only used as a *fallback* if OnlyOffice is not installed.

Environment: NOTEIN_ONLYOFFICE_DIR (default /opt/onlyoffice/desktopeditors).
"""
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

OFFICE_EXT = {".docx", ".doc", ".odt", ".rtf", ".pptx", ".ppt", ".odp", ".xlsx", ".xls", ".ods", ".csv", ".txt"}
MARKDOWN_EXT = {".md", ".markdown"}
SUPPORTED_EXT = OFFICE_EXT | MARKDOWN_EXT | {".pdf"}
FORMAT_PDF = 513                                                  # x2t's numeric id of the PDF output format


class ConversionError(RuntimeError):
    pass


def onlyoffice_dir():
    return Path(os.environ.get("NOTEIN_ONLYOFFICE_DIR", "/opt/onlyoffice/desktopeditors"))


def has_onlyoffice():
    return (onlyoffice_dir() / "converter" / "x2t").exists()


def _all_fonts_js():
    p = Path.home() / ".local/share/onlyoffice/desktopeditors/data/fonts/AllFonts.js"
    return p if p.exists() else None


def _onlyoffice_to_pdf(src, dst):
    base = onlyoffice_dir()
    work = Path(tempfile.mkdtemp(prefix="x2t_"))
    try:
        (work / "tmp").mkdir()
        fonts = _all_fonts_js()
        params = f"""<?xml version="1.0" encoding="utf-8"?>
<TaskQueueDataConvert xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:xsd="http://www.w3.org/2001/XMLSchema">
  <m_sFileFrom>{Path(src).resolve()}</m_sFileFrom>
  <m_sFileTo>{Path(dst).resolve()}</m_sFileTo>
  <m_nFormatTo>{FORMAT_PDF}</m_nFormatTo>
  <m_bIsNoBase64>true</m_bIsNoBase64>
  <m_sTempDir>{work / "tmp"}</m_sTempDir>
  <m_sFontDir>{base / "fonts"}</m_sFontDir>
  <m_sThemeDir>{base / "editors/sdkjs/slide/themes"}</m_sThemeDir>
  {f"<m_sAllFontsPath>{fonts}</m_sAllFontsPath>" if fonts else ""}
</TaskQueueDataConvert>
"""
        (work / "params.xml").write_text(params, encoding="utf-8")
        r = subprocess.run([str(base / "converter" / "x2t"), str(work / "params.xml")], capture_output=True, text=True,
                           timeout=300)
        if r.returncode != 0 or not Path(dst).exists():
            raise ConversionError(f"OnlyOffice x2t failed (rc={r.returncode}): {(r.stdout + r.stderr).strip()[-300:]}")
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _libreoffice_to_pdf(src, dst):
    exe = shutil.which("soffice") or shutil.which("libreoffice")
    if not exe:
        raise ConversionError("neither OnlyOffice nor LibreOffice is installed")
    out = Path(tempfile.mkdtemp(prefix="lo_"))
    try:
        r = subprocess.run([exe, "--headless", "--convert-to", "pdf", "--outdir", str(out), str(src)],
                           capture_output=True, text=True, timeout=300)
        made = out / (Path(src).stem + ".pdf")
        if r.returncode != 0 or not made.exists():
            raise ConversionError(f"LibreOffice failed: {(r.stdout + r.stderr).strip()[-300:]}")
        shutil.move(str(made), dst)
    finally:
        shutil.rmtree(out, ignore_errors=True)


def office_to_pdf(src, dst):
    """Office document -> PDF. Returns the engine used ('onlyoffice' or 'libreoffice')."""
    if has_onlyoffice():
        _onlyoffice_to_pdf(src, dst)
        return "onlyoffice"
    _libreoffice_to_pdf(src, dst)
    return "libreoffice (fallback: OnlyOffice not found)"


def markdown_to_pdf(src, dst, md_engine="onlyoffice"):
    pandoc = shutil.which("pandoc")
    if not pandoc:
        raise ConversionError("pandoc is required for Markdown")
    if md_engine in ("latex", "xelatex", "weasyprint"):
        engine = "xelatex" if md_engine in ("latex", "xelatex") else "weasyprint"
        r = subprocess.run([pandoc, str(src), "-o", str(dst), f"--pdf-engine={engine}"], capture_output=True, text=True)
        if r.returncode != 0:
            raise ConversionError(f"pandoc/{engine} failed: {r.stderr.strip()[-300:]}")
        return f"pandoc+{engine}"
    work = Path(tempfile.mkdtemp(prefix="md_"))
    try:
        docx = work / (Path(src).stem + ".docx")
        r = subprocess.run([pandoc, str(src), "-o", str(docx), "--resource-path", str(Path(src).resolve().parent)],
                           capture_output=True, text=True)
        if r.returncode != 0:
            raise ConversionError(f"pandoc failed: {r.stderr.strip()[-300:]}")
        return "pandoc+" + office_to_pdf(docx, dst)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def to_pdf(src, dst, md_engine="onlyoffice"):
    """Convert any supported document to PDF at `dst`. A PDF is copied unchanged. Returns a short engine description."""
    ext = Path(src).suffix.lower()
    if ext not in SUPPORTED_EXT:
        raise ConversionError(f"unsupported file type '{ext}' (supported: {', '.join(sorted(SUPPORTED_EXT))})")
    if ext == ".pdf":
        shutil.copyfile(src, dst)
        return "copy"
    if ext in MARKDOWN_EXT:
        return markdown_to_pdf(src, dst, md_engine)
    return office_to_pdf(src, dst)
