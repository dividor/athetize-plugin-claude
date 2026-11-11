"""Read a Word manuscript's paragraphs together with their layout.

Besides its text, each paragraph says what the report needs to show the paper the way it
was written: whether all of it is bold, its largest type size, the bold, italic, subscript
and superscript stretches, its place in a table, whether it is centred, and any picture
that comes just before it. Authors often mark headings with bold type and no style, so
the style name alone is not enough. The shape of each table (merged cells, column widths,
ruled or not) is returned beside the paragraphs.
"""

from __future__ import annotations

import re
import unicodedata
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from pl_xml import parse_xml

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
_R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_WP = "{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}"
_RELS = "{http://schemas.openxmlformats.org/package/2006/relationships}"
_SPACES = (W + "tab", W + "br", W + "cr")
_MARKS = ("b", "i", "sub", "sup")
# Picture types a report can show, by file ending.
PICTURE_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
}
_EMU_PER_PIXEL = 9525


def paragraph_style(p: ET.Element) -> str:
    """Name of the Word paragraph style, or "" when the paragraph has none."""
    ppr = p.find(W + "pPr")
    ps = ppr.find(W + "pStyle") if ppr is not None else None
    return (ps.get(W + "val") or "") if ps is not None else ""


def paragraph_text(p: ET.Element) -> str:
    """Visible text of a Word paragraph; tabs and line breaks become spaces."""
    parts = []
    for el in p.iter():
        if el.tag == W + "t":
            parts.append(el.text or "")
        elif el.tag in _SPACES:
            parts.append(" ")
    return re.sub(r"\s+", " ", "".join(parts)).strip()


def _on(rpr: ET.Element, tag: str) -> bool:
    """True when a run property such as bold is present and not switched off."""
    el = rpr.find(W + tag)
    return el is not None and el.get(W + "val") not in ("0", "false", "none")


def _run_marks(run: ET.Element) -> frozenset:
    """Which of bold, italic, subscript and superscript a run of text carries."""
    rpr = run.find(W + "rPr")
    if rpr is None:
        return frozenset()
    marks = {m for m in ("b", "i") if _on(rpr, m)}
    raised = rpr.find(W + "vertAlign")
    where = raised.get(W + "val") if raised is not None else ""
    if where in ("subscript", "superscript"):
        marks.add(where[:3])
    return frozenset(marks)


def _run_size(run: ET.Element) -> int:
    """Type size of a run in half-points; 0 when the run leaves it to the style."""
    rpr = run.find(W + "rPr")
    sz = rpr.find(W + "sz") if rpr is not None else None
    val = sz.get(W + "val") if sz is not None else ""
    return int(val) if val and val.isdigit() else 0


def _pieces(p: ET.Element, parents: dict) -> list[tuple[str, frozenset, int]]:
    """Visible text of a paragraph as (text, marks, size) pieces, in reading order."""
    out = []
    for el in p.iter():
        if el.tag == W + "t":
            text = el.text or ""
        elif el.tag in _SPACES:
            text = " "
        else:
            continue
        run = parents.get(el)
        if run is not None and run.tag == W + "r":
            out.append((text, _run_marks(run), _run_size(run)))
        else:
            out.append((text, frozenset(), 0))
    return out


def _styled(pieces: list) -> list[tuple[str, frozenset]]:
    """The paragraph as ingest stores it, one (character, marks) pair at a time.

    Follows the same steps as the stored text: runs of white space become one space, the
    ends are trimmed, characters are normalised (NFKC) and soft hyphens dropped.
    """
    chars = [(ch, marks) for text, marks, _ in pieces for ch in text]
    out: list[tuple[str, frozenset]] = []
    prev_space = True
    i = 0
    while i < len(chars):
        j = i + 1
        while j < len(chars) and unicodedata.combining(chars[j][0]):
            j += 1
        cluster = "".join(ch for ch, _ in chars[i:j])
        for ch in unicodedata.normalize("NFKC", cluster).replace("­", ""):
            if ch.isspace():
                if prev_space:
                    continue
                ch = " "
            out.append((ch, chars[i][1]))
            prev_space = ch == " "
        i = j
    while out and out[-1][0] == " ":
        out.pop()
    return out


def _ranges(styled: list) -> list[list]:
    """[start, end, mark] for each stretch of the text that carries a mark."""
    found = []
    for mark in _MARKS:
        start = None
        for i, (_, marks) in enumerate(styled + [("", frozenset())]):
            if mark in marks and start is None:
                start = i
            elif mark not in marks and start is not None:
                found.append([start, i, mark])
                start = None
    return sorted(found)


def _table_cell(p: ET.Element, parents: dict, tables: dict) -> list | None:
    """[table number, row, column] of the outermost table cell a paragraph sits in."""
    table = row = cell = None
    last_row = last_cell = None
    el = parents.get(p)
    while el is not None:
        if el.tag == W + "tc":
            last_cell = el
        elif el.tag == W + "tr":
            last_row = el
        elif el.tag == W + "tbl":
            table, row, cell = el, last_row, last_cell
        el = parents.get(el)
    if table is None or row is None or cell is None:
        return None
    rows = [r for r in table if r.tag == W + "tr"]
    cells = [c for c in row if c.tag == W + "tc"]
    if row not in rows or cell not in cells:
        return None
    return [tables.setdefault(table, len(tables)), rows.index(row), cells.index(cell)]


def _picture_files(z: zipfile.ZipFile) -> dict[str, str]:
    """Relationship id of each picture kept inside the document, mapped to its file there."""
    name = "word/_rels/document.xml.rels"
    if name not in z.namelist():
        return {}
    files = {}
    for rel in parse_xml(z.read(name)).iter(_RELS + "Relationship"):
        target = "word/" + (rel.get("Target") or "").lstrip("/")
        if (rel.get("Type") or "").endswith("/image") and target in z.namelist():
            files[rel.get("Id") or ""] = target
    return files


def _pictures_in(p: ET.Element, files: dict, seen: set) -> list[dict]:
    """Pictures drawn in a paragraph, each with its size on the page in pixels."""
    sizes = [(e.get("cx") or "", e.get("cy") or "") for e in p.iter(_WP + "extent")]
    found = []
    for n, blip in enumerate(p.iter(_A + "blip")):
        if blip in seen or blip.get(_R + "embed") not in files:
            continue
        seen.add(blip)
        picture = {"file": files[blip.get(_R + "embed")]}
        if n < len(sizes) and all(v.isdigit() for v in sizes[n]):
            picture["w"] = round(int(sizes[n][0]) / _EMU_PER_PIXEL)
            picture["h"] = round(int(sizes[n][1]) / _EMU_PER_PIXEL)
        found.append(picture)
    return found


def _cell_shape(cell: ET.Element) -> tuple[int, str]:
    """How many columns a table cell spans, and its part in a cell merged down the rows."""
    tcpr = cell.find(W + "tcPr")
    span = tcpr.find(W + "gridSpan") if tcpr is not None else None
    merge = tcpr.find(W + "vMerge") if tcpr is not None else None
    width = span.get(W + "val") if span is not None else ""
    return (
        int(width) if width and width.isdigit() else 1,
        (merge.get(W + "val") or "continue") if merge is not None else "",
    )


def _table_rows(table: ET.Element) -> list[list[list[int]]]:
    """Every cell of a table as [columns spanned, rows spanned], row by row.

    A cell that Word merged into the one above it spans 0 rows: it is not drawn.
    """
    rows = []
    growing: dict[int, list[int]] = {}
    for tr in (r for r in table if r.tag == W + "tr"):
        row, col = [], 0
        for tc in (c for c in tr if c.tag == W + "tc"):
            span, merge = _cell_shape(tc)
            if merge == "continue" and col in growing:
                growing[col][1] += 1
                cell = [span, 0]
            else:
                cell = [span, 1]
                growing.pop(col, None)
                if merge == "restart":
                    growing[col] = cell
            row.append(cell)
            col += span
        rows.append(row)
    return rows


def _draws_borders(holder: ET.Element | None) -> bool | None:
    """Whether table properties ask for ruled lines; None when they do not say."""
    borders = holder.find(W + "tblBorders") if holder is not None else None
    if borders is None:
        return None
    return any(b.get(W + "val") not in ("none", "nil") for b in borders)


def _ruled_styles(z: zipfile.ZipFile) -> set[str]:
    """Names of the table styles in the document that draw ruled lines."""
    if "word/styles.xml" not in z.namelist():
        return set()
    return {
        style.get(W + "styleId") or ""
        for style in parse_xml(z.read("word/styles.xml")).iter(W + "style")
        if style.get(W + "type") == "table" and _draws_borders(style.find(W + "tblPr"))
    }


def _table_shape(table: ET.Element, ruled: set[str]) -> dict:
    """What the report needs to draw a table: its cells, column widths and whether it is ruled."""
    props = table.find(W + "tblPr")
    borders = _draws_borders(props)
    if borders is None:
        style = props.find(W + "tblStyle") if props is not None else None
        borders = style is not None and style.get(W + "val") in ruled
    widths = [g.get(W + "w") or "" for g in table.iter(W + "gridCol")]
    shape: dict = {"rows": _table_rows(table), "borders": borders}
    if widths and all(w.isdigit() for w in widths):
        shape["cols"] = [int(w) for w in widths]
    return shape


def _layout(p: ET.Element, parents: dict, tables: dict, pieces: list, centred: set[str]) -> dict:
    """What the report needs to lay the paragraph out as the author did."""
    styled = _styled(pieces)
    info: dict = {}
    inked = [marks for ch, marks in styled if ch != " "]
    if inked and all("b" in marks for marks in inked):
        info["bold_all"] = True
    size = max((size for _, _, size in pieces), default=0)
    if size:
        info["size"] = size
    marks = _ranges(styled)
    if marks:
        info["fmt"] = marks
        info["fmt_text"] = "".join(ch for ch, _ in styled)
    cell = _table_cell(p, parents, tables)
    if cell:
        info["tbl"] = cell
    if _is_centred(p, centred):
        info["align"] = "center"
    return info


def _is_centred(p: ET.Element, centred: set[str]) -> bool:
    """True when a paragraph is centred, by its own setting or else by its style."""
    ppr = p.find(W + "pPr")
    jc = ppr.find(W + "jc") if ppr is not None else None
    if jc is not None:
        return jc.get(W + "val") == "center"
    return paragraph_style(p) in centred


def _centred_styles(z: zipfile.ZipFile) -> set[str]:
    """Names of the paragraph styles that centre their text, directly or through their parent."""
    if "word/styles.xml" not in z.namelist():
        return set()
    own: dict[str, str | None] = {}
    parent: dict[str, str] = {}
    for style in parse_xml(z.read("word/styles.xml")).iter(W + "style"):
        if style.get(W + "type") != "paragraph":
            continue
        name = style.get(W + "styleId") or ""
        ppr = style.find(W + "pPr")
        jc = ppr.find(W + "jc") if ppr is not None else None
        own[name] = jc.get(W + "val") if jc is not None else None
        based = style.find(W + "basedOn")
        if based is not None:
            parent[name] = based.get(W + "val") or ""
    centred = set()
    for name in own:
        at, steps = name, 0
        while own.get(at) is None and at in parent and steps < len(own):
            at, steps = parent[at], steps + 1
        if own.get(at) == "center":
            centred.add(name)
    return centred


def read_docx(path: Path) -> tuple[list[dict], list[dict]]:
    """Every paragraph of a Word file that has text, and the shape of each of its tables.

    A paragraph's "tbl" is [table, row, cell]: the table's place in the second list, then
    the cell's place in that table's rows.
    """
    with zipfile.ZipFile(path) as z:
        root = parse_xml(z.read("word/document.xml"))
        files = _picture_files(z)
        ruled = _ruled_styles(z)
        centred = _centred_styles(z)
    parents = {child: parent for parent in root.iter() for child in parent}
    tables: dict = {}
    seen: set = set()
    waiting: list[dict] = []
    out: list[dict] = []
    for p in root.iter(W + "p"):
        waiting += _pictures_in(p, files, seen)
        text = paragraph_text(p)
        if not text:
            continue
        entry: dict = {"text": text, "style": paragraph_style(p)}
        entry.update(_layout(p, parents, tables, _pieces(p, parents), centred))
        if waiting:
            # a picture has no text of its own, so it travels with the paragraph after it
            entry["figures"], waiting = waiting, []
        out.append(entry)
    if waiting and out:
        out[-1]["figures_after"] = waiting
    return out, [_table_shape(table, ruled) for table in tables]


def read_docx_paragraphs(path: Path) -> list[dict]:
    """Every paragraph of a Word file that has text, with its style and its layout."""
    return read_docx(path)[0]
