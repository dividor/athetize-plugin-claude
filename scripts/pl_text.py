"""Text utilities for athetize: extraction, tokenising, quote location, BM25 passages.

Standard library only, apart from defusedxml for reading untrusted XML. PDF extraction uses
PyMuPDF if installed, else the `pdftotext` command line tool, else pypdf.

Quote location lives in `pl_quote` and passage retrieval in `pl_passages`; their names are
re-exported here so callers can keep using `pl_text.<name>`.
"""

from __future__ import annotations

import difflib
import hashlib
import importlib.util
import logging
import re
import shutil
import subprocess
import tempfile
import unicodedata
import zipfile
from html.parser import HTMLParser
from pathlib import Path
from xml.etree import ElementTree as ET

from pl_passages import _SENT_END, _STOP, bm25_rank, chunk_passages, context_window, page_at
from pl_quote import _NEGATIONS, TOKEN_RE, find_span, locate_quote, tokenize
from pl_xml import parse_xml

__all__ = [
    "TOKEN_RE",
    "W",
    "_NEGATIONS",
    "_SENT_END",
    "_STOP",
    "_VisibleText",
    "bm25_rank",
    "chunk_passages",
    "clean_extracted",
    "context_window",
    "extract_file_text",
    "find_span",
    "html_to_text",
    "jats_to_text",
    "locate_quote",
    "norm_title",
    "ocr_available",
    "ocr_pdf_text",
    "page_at",
    "parse_xml",
    "read_docx_paragraphs",
    "pdf_extractor",
    "read_pdf_text",
    "sha256_file",
    "short_hash",
    "title_similarity",
    "tokenize",
]

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


# --------------------------------------------------------------------------
# Hashing and cleaning
# --------------------------------------------------------------------------
def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def short_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def clean_extracted(text: str) -> str:
    """Normalise extracted text without changing its overall structure.

    Keeps form feeds (page breaks) so page numbers can be recovered.
    """
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("\u00ad", "")  # soft hyphen
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# --------------------------------------------------------------------------
# Readers
# --------------------------------------------------------------------------
def _docx_paragraph_style(p: ET.Element) -> str:
    """Name of the Word paragraph style, or "" when the paragraph has none."""
    style = ""
    ppr = p.find(W + "pPr")
    if ppr is not None:
        ps = ppr.find(W + "pStyle")
        if ps is not None:
            style = ps.get(W + "val") or ""
    return style


def _docx_paragraph_text(p: ET.Element) -> str:
    """Visible text of a Word paragraph; tabs and line breaks become spaces."""
    parts = []
    for el in p.iter():
        if el.tag == W + "t":
            parts.append(el.text or "")
        elif el.tag == W + "tab":
            parts.append(" ")
        elif el.tag in (W + "br", W + "cr"):
            parts.append(" ")
    return re.sub(r"\s+", " ", "".join(parts)).strip()


def read_docx_paragraphs(path: Path) -> list[dict]:
    with zipfile.ZipFile(path) as z:
        xml = z.read("word/document.xml")
    root = parse_xml(xml)
    out = []
    for p in root.iter(W + "p"):
        style = _docx_paragraph_style(p)
        text = _docx_paragraph_text(p)
        if text:
            out.append({"text": text, "style": style})
    return out


def pdf_extractor() -> str | None:
    """The PDF text extractor that will be used here, fastest first; None when there is none.

    PyMuPDF is the quickest. pypdf is the one Claude's own PDF skill uses, so it is there
    wherever that skill works, and it is the last choice because it is the slowest.
    """
    if importlib.util.find_spec("fitz") is not None:
        return "pymupdf"
    if shutil.which("pdftotext"):
        return "pdftotext"
    if importlib.util.find_spec("pypdf") is not None:
        return "pypdf"
    return None


def _pdftotext(path: Path) -> str:
    res = subprocess.run(
        ["pdftotext", "-enc", "UTF-8", str(path), "-"],
        capture_output=True,
        timeout=180,
    )
    if res.returncode == 0:
        return res.stdout.decode("utf-8", "replace")
    raise RuntimeError("pdftotext failed: " + res.stderr.decode("utf-8", "replace")[:200])


def _pypdf_text(path: Path) -> str:
    import pypdf

    # pypdf reports every small flaw in a file as a warning; only real errors matter here
    logging.getLogger("pypdf").setLevel(logging.ERROR)
    return "\f".join(page.extract_text() or "" for page in pypdf.PdfReader(str(path)).pages)


def read_pdf_text(path: Path) -> str:
    """Text of a PDF, pages separated by form feeds, from the extractor found here."""
    how = pdf_extractor()
    if how == "pymupdf":
        import fitz

        with fitz.open(str(path)) as doc:
            return "\f".join(page.get_text("text") for page in doc)
    if how == "pdftotext":
        return _pdftotext(path)
    if how == "pypdf":
        return _pypdf_text(path)
    raise RuntimeError(
        "No PDF text extractor found. Install one: pip install pymupdf or pip install pypdf "
        "(add --break-system-packages if needed), or install poppler-utils (pdftotext)."
    )


def ocr_available() -> bool:
    return bool(shutil.which("tesseract") and shutil.which("pdftoppm"))


def ocr_pdf_text(path: Path, max_pages: int = 60, dpi: int = 200) -> str:
    """OCR a scanned PDF with pdftoppm + tesseract. Pages are joined with form feeds."""
    if not ocr_available():
        raise RuntimeError("OCR needs tesseract and pdftoppm (poppler-utils).")
    with tempfile.TemporaryDirectory() as d:
        subprocess.run(
            ["pdftoppm", "-r", str(dpi), "-l", str(max_pages), "-png", str(path), f"{d}/pg"],
            capture_output=True,
            timeout=600,
            check=False,
        )
        pages = []
        for img in sorted(Path(d).glob("pg*.png")):
            res = subprocess.run(
                ["tesseract", str(img), "-", "--psm", "1"], capture_output=True, timeout=180
            )
            pages.append(res.stdout.decode("utf-8", "replace"))
        return "\f".join(pages)


class _VisibleText(HTMLParser):
    SKIP = {"script", "style", "nav", "header", "footer", "noscript", "svg", "form"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip += 1
        if tag in {"p", "div", "br", "li", "h1", "h2", "h3", "h4", "tr", "section"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self._skip:
            self._skip -= 1
        if tag in {"p", "div", "li", "h1", "h2", "h3", "h4", "tr", "section"}:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def html_to_text(markup: str) -> str:
    parser = _VisibleText()
    parser.feed(markup)
    text = "".join(parser.parts)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()


def _element_text(el: ET.Element) -> str:
    """All text inside an XML element, on one line."""
    return re.sub(r"\s+", " ", "".join(el.itertext())).strip()


def _jats_body_chunks(body: ET.Element) -> list[str]:
    """Non-empty paragraphs, titles and captions inside a JATS <body>, in document order."""
    chunks = []
    for sec in body.iter():
        st = sec.tag.split("}")[-1]
        if st in ("p", "title", "caption"):
            t = _element_text(sec)
            if t:
                chunks.append(t)
    return chunks


def jats_to_text(xml_bytes: bytes) -> str:
    """Extract readable text from JATS XML (Europe PMC full text)."""
    root = parse_xml(xml_bytes)
    chunks: list[str] = []
    for el in root.iter():
        tag = el.tag.split("}")[-1]
        if tag == "article-title" and not chunks:
            chunks.append(_element_text(el))
        elif tag == "abstract":
            chunks.append(_element_text(el))
        elif tag == "body":
            chunks.extend(_jats_body_chunks(el))
    return "\n\n".join(c for c in chunks if c)


def _pdf_file_text(path: Path) -> str:
    """Text of a PDF; a PDF with very little text is OCRed when the OCR tools are installed."""
    text = clean_extracted(read_pdf_text(path))
    if len(text) < 2500 and ocr_available():
        ocr = clean_extracted(ocr_pdf_text(path))
        if len(ocr) > len(text):
            return ocr
    return text


def _xml_file_text(path: Path) -> str:
    """Text of an .xml source: read as JATS, or as HTML when it is not well-formed XML."""
    data = path.read_bytes()
    try:
        return clean_extracted(jats_to_text(data))
    except ET.ParseError:
        return clean_extracted(html_to_text(data.decode("utf-8", "replace")))


def extract_file_text(path: Path) -> str:
    """Extract text from a user-provided or downloaded source file (OCR for scanned PDFs)."""
    ext = path.suffix.lower()
    if ext == ".pdf":
        return _pdf_file_text(path)
    if ext == ".docx":
        return clean_extracted("\n\n".join(p["text"] for p in read_docx_paragraphs(path)))
    if ext in (".html", ".htm"):
        return clean_extracted(html_to_text(path.read_text("utf-8", "replace")))
    if ext == ".xml":
        return _xml_file_text(path)
    return clean_extracted(path.read_text("utf-8", "replace"))


# --------------------------------------------------------------------------
# Title similarity (reference matching)
# --------------------------------------------------------------------------
def norm_title(t: str) -> str:
    return " ".join(tok for tok, _, _ in tokenize(unicodedata.normalize("NFKC", t or "")))


def title_similarity(a: str, b: str) -> float:
    na, nb = norm_title(a), norm_title(b)
    if not na or not nb:
        return 0.0
    return difflib.SequenceMatcher(None, na, nb, autojunk=False).ratio()
