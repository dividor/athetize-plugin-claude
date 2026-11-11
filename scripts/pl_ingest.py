"""Turn a manuscript into a list of paragraphs with stable ids (paper.json)."""

from __future__ import annotations

import re
from pathlib import Path

import pl_text

REF_HEAD = re.compile(
    r"^\W*(references?|bibliography|works cited|literature cited|reference list|sources cited)\W*$",
    re.I,
)
AFTER_REF = re.compile(
    r"^\W*(appendix|appendices|supplementa\w*|supporting information|acknowledg\w*|"
    r"author contributions?|funding|"
    r"conflicts? of interest|competing interests?|data availability|declarations?)\b",
    re.I,
)
NUMBERED_HEAD = re.compile(r"^(\d+(\.\d+)*\.?|[IVX]+\.)\s+\S")

# Section names that are headings but never the title of a paper.
SECTION_NAME = re.compile(
    r"^\W*(abstract|summary|introduction|background|keywords?|highlights|"
    r"(table of )?contents)\W*$",
    re.I,
)
# A word: two or more letters together. Text without one holds nothing to check.
_WORD = re.compile(r"[^\W\d_]{2,}")

# File types whose headings are not marked up, so a short "References" line counts as a heading.
_PLAIN_TEXT_EXTS = (".pdf", ".md", ".markdown", ".txt", ".tex", "")


def _cannot_be_heading(line: str):
    """True for an empty line, a long line, or one ending like a sentence (unless numbered)."""
    return (
        not line
        or len(line) > 80
        or line.endswith((".", ",", ";", ":"))
        and not NUMBERED_HEAD.match(line)
    )


def _is_numbered_heading(line: str):
    """True for a short numbered line such as "2.1 Methods" that does not end with a full stop."""
    return NUMBERED_HEAD.match(line) and len(line.split()) <= 10 and not line.endswith(".")


def _is_short_capitals(line: str) -> bool:
    """True for a line of one to eight words written entirely in capitals."""
    words = [w for w in re.split(r"\s+", line) if w]
    return 1 <= len(words) <= 8 and line == line.upper() and any(c.isalpha() for c in line)


def looks_like_heading(line: str) -> bool:
    line = line.strip()
    if _cannot_be_heading(line):
        return False
    if REF_HEAD.match(line) or AFTER_REF.match(line):
        return True
    if _is_numbered_heading(line):
        return True
    if _is_short_capitals(line):
        return True
    return False


def _clean_md_inline(s: str) -> str:
    s = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", s)
    s = re.sub(r"\[([^\]]+)\]\((?:[^)]+)\)", r"\1", s)
    s = s.replace("**", "").replace("__", "").replace("`", "")
    s = re.sub(r"\\([\[\]\\*_#])", r"\1", s)
    return re.sub(r"\s+", " ", s).strip()


def parse_markdown(text: str) -> list[dict]:
    paras: list[dict] = []
    buf: list[str] = []

    def flush():
        if buf:
            t = _clean_md_inline(" ".join(buf))
            if t:
                paras.append({"text": t, "style": ""})
            buf.clear()

    for raw in text.splitlines():
        line = raw.rstrip()
        if not line.strip():
            flush()
            continue
        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        if m:
            flush()
            paras.append(
                {"text": _clean_md_inline(m.group(2)), "style": f"Heading{len(m.group(1))}"}
            )
            continue
        if re.match(r"^\s*([-*+]|\d+[.)]|\[\d+\])\s+", line):
            flush()
            buf.append(re.sub(r"^\s*([-*+]\s+)", "", line))
            flush()
            continue
        if re.match(r"^\s*(---|\*\*\*|___)\s*$", line) or line.lstrip().startswith("|"):
            flush()
            continue
        buf.append(line.strip())
    flush()
    return paras


def parse_plain(text: str) -> list[dict]:
    paras = []
    for block in re.split(r"\n\s*\n", text):
        t = re.sub(r"\s+", " ", block).strip()
        if t:
            paras.append({"text": t, "style": ""})
    return paras


def _tex_body_text(text: str) -> str:
    """Strip LaTeX markup from the document body, leaving #H#/#T#/#B# markers on special blocks."""
    text = re.sub(r"(?<!\\)%.*", "", text)
    body = text.split("\\begin{document}", 1)[-1].split("\\end{document}", 1)[0]
    body = re.sub(
        r"\\(cite[a-z]*|citep|citet)\*?(\[[^\]]*\])*\{([^}]*)\}",
        lambda m: "[" + m.group(3).replace(" ", "") + "]",
        body,
    )
    body = re.sub(r"\\(?:text(?:it|bf|rm|sc|tt)|emph|textsuperscript)\{([^}]*)\}", r"\1", body)
    body = re.sub(
        r"\\(?:section|subsection|subsubsection|chapter)\*?\{([^}]*)\}", r"\n\n#H# \1\n\n", body
    )
    body = re.sub(r"\\title\{([^}]*)\}", r"\n\n#T# \1\n\n", body)
    body = re.sub(r"\\bibitem(?:\[[^\]]*\])?\{([^}]*)\}", r"\n\n#B# [\1] ", body)
    body = re.sub(
        r"\\(begin|end)\{(?:figure|table|equation|align|tikzpicture)\*?\}.*?(?=\\end\{|$)",
        " ",
        body,
        flags=re.S,
    )
    body = re.sub(
        r"\\(begin|end|label|maketitle|centering|noindent|newpage|clearpage|bibliographystyle|"
        r"bibliography|usepackage)\b(\{[^}]*\})?",
        "",
        body,
    )
    body = re.sub(r"\\[a-zA-Z]+\*?(\[[^\]]*\])?(\{([^}]*)\})?", lambda m: m.group(3) or "", body)
    return (
        body.replace("~", " ")
        .replace("\\&", "&")
        .replace("\\%", "%")
        .replace("``", '"')
        .replace("''", '"')
    )


def _tex_paragraph(t: str) -> dict:
    """Turn one cleaned LaTeX block into a paragraph, reading its #H#/#T#/#B# marker if any."""
    if t.startswith("#H# "):
        return {"text": t[4:].strip(), "style": "Heading1"}
    if t.startswith("#T# "):
        return {"text": t[4:].strip(), "style": "Title"}
    if t.startswith("#B# "):
        return {"text": t[4:].strip(), "style": "BibItem"}
    return {"text": t, "style": ""}


def parse_tex(text: str) -> list[dict]:
    body = _tex_body_text(text)
    paras = []
    for block in re.split(r"\n\s*\n", body):
        t = re.sub(r"\s+", " ", block).strip()
        if not t:
            continue
        paras.append(_tex_paragraph(t))
    return paras


def _pdf_block_lines(block: str) -> list[str]:
    """Non-empty lines of a PDF text block, without lines that are only a page number."""
    return [
        ln.strip()
        for ln in block.split("\n")
        if ln.strip() and not re.fullmatch(r"\d{1,4}", ln.strip())
    ]


def _join_wrapped_lines(lines: list[str]) -> str:
    """Join wrapped lines into one paragraph, rejoining words hyphenated across a line break."""
    joined = ""
    for ln in lines:
        if joined.endswith("-") and ln[:1].islower():
            joined = joined[:-1] + ln
        else:
            joined = (joined + " " + ln).strip()
    return joined


def parse_pdf(path: Path) -> list[dict]:
    raw = pl_text.clean_extracted(pl_text.read_pdf_text(path))
    paras = []
    for block in re.split(r"\n\s*\n", raw.replace("\f", "\n\n")):
        lines = _pdf_block_lines(block)
        if not lines:
            continue
        joined = _join_wrapped_lines(lines)
        style = "Heading1" if len(lines) == 1 and looks_like_heading(lines[0]) else ""
        paras.append({"text": joined, "style": style})
    return paras


def _read_manuscript(path: Path, ext: str) -> tuple[list[dict], list[dict]]:
    """Read the manuscript with the parser for its file type: its paragraphs and its tables.

    Only a Word file says how its tables are laid out; every other type gives none.
    """
    if ext == ".docx":
        return pl_text.read_docx(path)
    if ext == ".pdf":
        return parse_pdf(path), []
    if ext in (".md", ".markdown"):
        return parse_markdown(path.read_text("utf-8", "replace")), []
    if ext == ".tex":
        return parse_tex(path.read_text("utf-8", "replace")), []
    if ext in (".txt", ""):
        return parse_plain(path.read_text("utf-8", "replace")), []
    raise ValueError(f"Unsupported file type: {ext}. Use .docx, .pdf, .md, .tex or .txt")


# A heading set in bold type, with no heading style, is at most this long.
_BOLD_HEADING_MAX = 100


def _is_bold_heading(p: dict, text: str) -> bool:
    """True for a short line set entirely in bold, outside any table, that is not a sentence.

    Many authors mark headings this way in Word and never apply a heading style.
    """
    return (
        bool(p.get("bold_all"))
        and "tbl" not in p
        and len(text) <= _BOLD_HEADING_MAX
        and bool(_WORD.search(text))
        and (not text.endswith((".", ",", ";", ":")) or bool(NUMBERED_HEAD.match(text)))
    )


def _heading_level(style: str, text: str) -> int:
    """Depth of a heading: 1 for a main section, 2 for a section inside it, and so on.

    Taken from the heading style when it carries a number, otherwise from the heading's own
    numbering ("3.1" is one level below "3.").
    """
    styled = re.match(r"heading\s*(\d)", style)
    if styled and styled.group(1) != "0":
        return int(styled.group(1))
    numbered = NUMBERED_HEAD.match(text)
    if numbered and numbered.group(1)[0].isdigit():
        return min(numbered.group(1).rstrip(".").count(".") + 1, 6)
    return 1


# Layout a paragraph keeps from the manuscript, so the report can show it as written.
_LAYOUT_KEYS = ("tbl", "align", "figures", "figures_after")


def _layout(p: dict, text: str) -> dict:
    """The layout of a paragraph that the report shows: table cell, centring, type, pictures."""
    kept = {key: p[key] for key in _LAYOUT_KEYS if key in p}
    if p.get("fmt") and p.get("fmt_text") == text:
        # the stretches are counted on the stored text, so they are kept only when it is the same
        kept["fmt"] = p["fmt"]
    return kept


def _is_heading(ext: str, style: str, text: str):
    """True when the paragraph is styled as a heading or title, or (Word only) reads like one."""
    return (
        style.startswith("heading")
        or style.startswith("title")
        or (
            ext in (".docx",)
            and len(text) <= 80
            and looks_like_heading(text)
            and not text.endswith(".")
        )
    )


def _paragraph_kind(ext: str, style: str, text: str, in_refs: bool) -> tuple[str, bool]:
    """Classify a paragraph as heading, body, reference or other.

    Returns (kind, in_refs), where in_refs says whether the reference list is still open
    after this paragraph.
    """
    kind = "heading" if _is_heading(ext, style, text) else "body"
    if kind == "body" and not in_refs and not _WORD.search(text):
        # a table mark, a rule or a lone number: shown in the report, never sent for checking
        return "other", in_refs
    if kind == "heading":
        if REF_HEAD.match(text):
            in_refs = True
        elif in_refs and AFTER_REF.match(text):
            in_refs = False
    elif ext in _PLAIN_TEXT_EXTS and len(text) <= 60 and REF_HEAD.match(text):
        kind, in_refs = "heading", True
    elif in_refs:
        kind = "reference"
    if style.startswith("bibitem"):
        kind = "reference"
    return kind, in_refs


# A heading can only be the title when it is among the first few paragraphs.
_TITLE_WITHIN = 5


def _is_title(style: str, kind: str, text: str, position: int):
    """True when the paragraph can serve as the paper title.

    A numbered heading ("1. Introduction") or a plain section name is a section, not a
    title, and so is any heading further down the paper.
    """
    if style.startswith("title"):
        return True
    if kind != "heading" or position >= _TITLE_WITHIN or REF_HEAD.match(text):
        return False
    return not NUMBERED_HEAD.match(text) and not SECTION_NAME.match(text)


def _document_name(path: Path) -> str:
    """The document's own name, used as the title of a paper that marks none."""
    return path.stem


def _paragraph(p: dict, ext: str, in_refs: bool) -> tuple[dict, bool, bool]:
    """One manuscript paragraph as it is stored, without its id.

    Returns (paragraph, in_refs, marked): whether the reference list is still open after it,
    and whether it counts as a heading by its style or wording, bold type aside.
    """
    text = pl_text.clean_extracted(p["text"]).replace("\n", " ")
    style = (p.get("style") or "").lower()
    kind, in_refs = _paragraph_kind(ext, style, text, in_refs)
    marked = kind == "heading"
    if kind == "body" and _is_bold_heading(p, text):
        kind = "heading"
    entry: dict = {"text": text, "kind": kind}
    if kind == "heading":
        entry["level"] = _heading_level(style, text)
    entry.update(_layout(p, text))
    return entry, in_refs, marked


def ingest(path: Path) -> dict:
    ext = path.suffix.lower()
    raw, tables = _read_manuscript(path, ext)

    paragraphs: list[dict] = []
    in_refs = False
    title = ""
    for p in raw:
        entry, in_refs, marked = _paragraph(p, ext, in_refs)
        style = (p.get("style") or "").lower()
        # a line that is only set in bold is shown as a heading but never taken as the title
        if not title and marked and _is_title(style, "heading", entry["text"], len(paragraphs)):
            title = entry["text"]
        paragraphs.append({"id": f"p{len(paragraphs) + 1}", **entry})
    if not title:
        # no guessing from the opening line: an unmarked first line is often not the title
        title = _document_name(path)
    paper = {
        "source_file": path.name,
        "sha256": pl_text.sha256_file(path),
        "title": title,
        "paragraphs": paragraphs,
    }
    if tables:
        paper["tables"] = tables
    return paper
