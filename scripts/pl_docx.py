"""Apply suggested edits to a .docx manuscript as tracked changes (w:ins / w:del).

Needs lxml (pip install lxml). Existing tracked changes in the manuscript are left as
they are; paragraph ids are matched the same way pl_ingest numbers them (every
non-empty w:p in document order, visible w:t text only).

Comment building lives in pl_docx_comments and zip handling in pl_docx_package; their
names are re-exported here.
"""

from __future__ import annotations

import copy
import difflib
import re
import time
import unicodedata
from pathlib import Path

import pl_docx_package
import pl_text
from pl_docx_comments import (
    _BAD_XML,
    _anchor_comment,
    _comment_el,
    _xml_safe,
    comments_root,
    first_free_comment_id,
)
from pl_docx_package import CT_COMMENTS, REL_COMMENTS, W14, W_NS, XML_SPACE, W

__all__ = [
    "CT_COMMENTS",
    "REL_COMMENTS",
    "W",
    "W14",
    "W_NS",
    "XML_SPACE",
    "_BAD_XML",
    "_anchor_comment",
    "_comment_el",
    "_xml_safe",
    "apply",
    "word_edits",
]

_TEXT_TAGS = (W + "t", W + "tab", W + "br", W + "cr")
_PARAGRAPH_NOT_FOUND = "paragraph not found in the .docx (was the manuscript edited after ingest?)"


def _etree():
    try:
        from lxml import etree

        return etree
    except ImportError as e:
        raise RuntimeError(
            "Exporting tracked changes needs lxml: pip install lxml "
            "(add --break-system-packages if needed)"
        ) from e


def _in_deleted(el) -> bool:
    p = el.getparent()
    while p is not None:
        if p.tag in (W + "del", W + "moveFrom"):
            return True
        if p.tag == W + "p":
            return False
        p = p.getparent()
    return False


def _para_text(p) -> str:
    parts = []
    for el in p.iter():
        if el.tag == W + "t":
            parts.append(el.text or "")
        elif el.tag in (W + "tab", W + "br", W + "cr"):
            parts.append(" ")
    return re.sub(r"\s+", " ", "".join(parts)).strip()


def _stored_text(p) -> str:
    """The paragraph's text as ingest stores it, for telling whether it is the same one."""
    return pl_text.clean_extracted(_para_text(p)).replace("\n", " ")


def _split_run(r, content) -> None:
    """Replace run r by one copy per text-bearing child, each keeping the run's formatting."""
    parent = r.getparent()
    idx = parent.index(r)
    new_runs = []
    for c in content:
        nr = copy.deepcopy(r)
        for child in list(nr):
            if child.tag != W + "rPr":
                nr.remove(child)
        nr.append(copy.deepcopy(c))
        new_runs.append(nr)
    parent.remove(r)
    for k, nr in enumerate(new_runs):
        parent.insert(idx + k, nr)


def _split_multi_content_runs(p):
    """Give every live run at most one text-bearing child, so runs can be split cleanly."""
    for r in list(p.iter(W + "r")):
        if _in_deleted(r):
            continue
        content = [c for c in r if c.tag in _TEXT_TAGS]
        if len(content) <= 1:
            continue
        _split_run(r, content)


def _segments(p):
    """List of (run, content_el, raw_start, raw_end) for live text in the paragraph."""
    segs = []
    pos = 0
    for el in p.iter():
        if el.tag not in _TEXT_TAGS:
            continue
        if _in_deleted(el):
            continue
        run = el.getparent()
        if run is None or run.tag != W + "r":
            continue
        txt = (el.text or "") if el.tag == W + "t" else " "
        segs.append((run, el, pos, pos + len(txt)))
        pos += len(txt)
    return segs


def _raw_text(segs) -> str:
    return "".join(((s[1].text or "") if s[1].tag == W + "t" else " ") for s in segs)


def _clusters(raw: str):
    """Yield (start, end) of each character in raw together with its combining marks."""
    i = 0
    while i < len(raw):
        j = i + 1
        while j < len(raw) and unicodedata.combining(raw[j]):
            j += 1
        yield i, j
        i = j


def _norm_with_map(raw: str):
    """The paragraph text as it is stored at ingest, with where each character came from.

    Ingest normalises text (NFKC, soft hyphens dropped, runs of spaces collapsed), so the
    stored text can differ from Word's: an angstrom sign becomes a capital A with a ring, a
    ligature becomes two letters. Returns that text and, for each of its characters, the
    (start, end) of the raw characters it stands for; a ligature's letters share one span.
    """
    out, spans = [], []
    prev_space = True
    for start, end in _clusters(raw):
        for ch in unicodedata.normalize("NFKC", raw[start:end]).replace("\u00ad", ""):
            if ch.isspace():
                if prev_space:
                    continue
                ch = " "
            out.append(ch)
            spans.append((start, end))
            prev_space = ch == " "
    return "".join(out), spans


def _raw_range(spans: list, norm: str, a: int, b: int, rep: str):
    """Raw (start, end, replacement) for replacing norm[a:b] with rep.

    When the range cuts through letters that share one raw character, it is widened to take
    in the whole character and the letters it keeps are added to the replacement.
    """
    if not spans:
        return 0, 0, rep
    while 0 < a < len(spans) and spans[a] == spans[a - 1]:
        a -= 1
        rep = norm[a] + rep
    while 0 < b < len(spans) and spans[b] == spans[b - 1]:
        rep += norm[b]
        b += 1
    if a == b:
        at = spans[a][0] if a < len(spans) else spans[-1][1]
        return at, at, rep
    return spans[a][0], spans[b - 1][1], rep


def _split_at(p, pos: int):
    """Ensure a run boundary at raw text offset pos."""
    for run, el, s, e in _segments(p):
        if s < pos < e and el.tag == W + "t":
            k = pos - s
            text = el.text or ""
            left = copy.deepcopy(run)
            lt = left.find(W + "t")
            lt.text = text[:k]
            lt.set(XML_SPACE, "preserve")
            el.text = text[k:]
            el.set(XML_SPACE, "preserve")
            parent = run.getparent()
            parent.insert(parent.index(run), left)
            return


class _Ids:
    def __init__(self, root):
        mx = 0
        for el in root.iter():
            v = el.get(W + "id")
            if v and v.isdigit():
                mx = max(mx, int(v))
        self.n = mx + 1000

    def next(self) -> str:
        self.n += 1
        return str(self.n)


def _change_el(tag: str, author: str, date: str, ids: _Ids, etree):
    """Make an empty tracked-change element (w:del or w:ins) with its id, author and date."""
    el = etree.Element(W + tag)
    el.set(W + "id", ids.next())
    el.set(W + "author", author)
    el.set(W + "date", date)
    return el


def _delete_runs(covered, author: str, date: str, ids: _Ids, etree):
    """Wrap each covered run in a w:del.

    Returns (the w:del elements, the last one made, the first run formatting found).
    """
    made = []
    anchor_after = None
    rpr_src = None
    for run, el, s, e in covered:
        d = _change_el("del", author, date, ids, etree)
        parent = run.getparent()
        parent.insert(parent.index(run), d)
        parent.remove(run)
        d.append(run)
        made.append(d)
        for t in run.iter(W + "t"):
            t.tag = W + "delText"
            t.set(XML_SPACE, "preserve")
        anchor_after = d
        rpr_src = rpr_src if rpr_src is not None else run.find(W + "rPr")
    return made, anchor_after, rpr_src


def _insertion_neighbours(segs, start: int):
    """For a pure insertion: the live text starting at `start` and the live text ending there.

    Returns (before, after, formatting of whichever run the insertion will sit next to).
    """
    before = next((s for s in segs if s[2] == start and s[3] > s[2]), None)
    after = next((s for s in reversed(segs) if s[3] == start), None)
    ref_run = before[0] if before else (after[0] if after else None)
    rpr_src = ref_run.find(W + "rPr") if ref_run is not None else None
    return before, after, rpr_src


def _insertion_el(new_text: str, rpr_src, author: str, date: str, ids: _Ids, etree):
    """Build a w:ins holding one run with new_text, formatted like rpr_src when given."""
    ins = _change_el("ins", author, date, ids, etree)
    r = etree.SubElement(ins, W + "r")
    if rpr_src is not None:
        r.append(copy.deepcopy(rpr_src))
    t = etree.SubElement(r, W + "t")
    t.text = _xml_safe(new_text)
    t.set(XML_SPACE, "preserve")
    return ins


def _place_insertion(p, ins, anchor_after, before, after) -> None:
    """Put ins after the deleted text, else next to its neighbour, else at the paragraph end."""
    if anchor_after is not None:
        parent = anchor_after.getparent()
        parent.insert(parent.index(anchor_after) + 1, ins)
    elif before:
        parent = before[0].getparent()
        parent.insert(parent.index(before[0]), ins)
    elif after:
        parent = after[0].getparent()
        parent.insert(parent.index(after[0]) + 1, ins)
    else:
        p.append(ins)


def _edit(p, start: int, end: int, new_text: str, author: str, date: str, ids: _Ids, etree):
    """Replace raw [start, end) in paragraph p with new_text as a tracked change.

    Returns the w:del/w:ins elements made.
    """
    _split_at(p, start)
    _split_at(p, end)
    segs = _segments(p)
    covered = [s for s in segs if s[2] >= start and s[3] <= end and s[3] > s[2]]
    made, anchor_after, rpr_src = _delete_runs(covered, author, date, ids, etree)
    if not new_text:
        return made
    before = after = None
    if anchor_after is None:
        # pure insertion: put it before the run starting at `start`, else after the run ending there
        before, after, rpr_src = _insertion_neighbours(segs, start)
    ins = _insertion_el(new_text, rpr_src, author, date, ids, etree)
    _place_insertion(p, ins, anchor_after, before, after)
    made.append(ins)
    return made


def _delete_paragraph_mark(p, author, date, ids, etree):
    """Mark the paragraph mark deleted so accepting the change removes the now-empty paragraph."""
    ppr = p.find(W + "pPr")
    if ppr is None:
        ppr = etree.Element(W + "pPr")
        p.insert(0, ppr)
    rpr = ppr.find(W + "rPr")
    if rpr is None:
        rpr = etree.SubElement(ppr, W + "rPr")
        # rPr must come after most pPr children but before sectPr/pPrChange
        for tag in ("sectPr", "pPrChange"):
            el = ppr.find(W + tag)
            if el is not None:
                ppr.remove(rpr)
                ppr.insert(ppr.index(el), rpr)
                break
    rpr.insert(0, _change_el("del", author, date, ids, etree))


_TOK = re.compile(r"\s+|\w+(?:[’'-]\w+)*|[^\w\s]")


def _token_offsets(tokens: list[str]) -> list[int]:
    """Start offset of each token in the joined string, plus the total length at the end."""
    offs, pos = [], 0
    for tok in tokens:
        offs.append(pos)
        pos += len(tok)
    offs.append(pos)
    return offs


def word_edits(original: str, suggested: str):
    """Minimal word-level edits as (orig_start, orig_end, replacement), offsets into original."""
    a = _TOK.findall(original)
    b = _TOK.findall(suggested)
    offs = _token_offsets(a)
    sm = difflib.SequenceMatcher(a=a, b=b, autojunk=False)
    edits: list[tuple[int, int, str]] = []
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            continue
        edits.append((offs[i1], offs[i2], "".join(b[j1:j2])))
    # merge edits separated only by whitespace so changes read as phrases
    merged: list[tuple[int, int, str]] = []
    for e in edits:
        if merged and original[merged[-1][1] : e[0]].strip() == "":
            gap = original[merged[-1][1] : e[0]]
            merged[-1] = (merged[-1][0], e[1], merged[-1][2] + gap + e[2])
        else:
            merged.append(e)
    return merged


class _Job:
    """What the stages of apply() share while working through one document."""

    def __init__(self, etree, croot, author: str, date: str, notes: dict, ids: _Ids, next_cid: int):
        self.etree = etree
        self.croot = croot
        self.author = author
        self.date = date
        self.notes = notes
        self.ids = ids
        self.next_cid = next_cid
        self.added_comments = 0
        self.results: list[dict] = []


def _not_applied(s: dict, reason: str) -> dict:
    """Result entry for a suggestion that could not be applied."""
    return {"id": s["id"], "applied": False, "reason": reason}


def _index_paragraphs(root) -> dict:
    """Map paragraph ids (p1, p2, ...) to the non-empty w:p elements in document order."""
    body_ps = [p for p in root.iter(W + "p") if _para_text(p)]
    return {f"p{i + 1}": p for i, p in enumerate(body_ps)}


def _group_by_paragraph(suggestions: list[dict]) -> dict[str, list[dict]]:
    """Group suggestions by the paragraph id they apply to, keeping their order."""
    by_para: dict[str, list[dict]] = {}
    for s in suggestions:
        by_para.setdefault(s["para"], []).append(s)
    return by_para


def _locate(items: list[dict], norm: str, results: list[dict]) -> list:
    """Find each suggestion's original text in the paragraph.

    Returns (start, end, original, suggestion), with positions in the normalised text, for
    those found exactly once; the others are added to results as not applied.
    """
    located = []
    for s in items:
        orig = re.sub(r"\s+", " ", s["original"]).strip()
        k = norm.find(orig)
        if k < 0 or norm.find(orig, k + 1) >= 0:
            results.append(_not_applied(s, "text not found exactly once in the paragraph"))
            continue
        located.append((k, k + len(orig), orig, s))
    return located


def _add_comment(job: _Job, p, made: list, paragraphs: list) -> None:
    """Anchor a new comment with the given paragraphs on the change elements just made."""
    cid = str(job.next_cid)
    job.next_cid += 1
    _anchor_comment(p, made, cid, job.etree)
    job.croot.append(_comment_el(cid, job.author, job.date, paragraphs, job.etree))
    job.added_comments += 1


def _apply_one(job: _Job, p, start: int, orig: str, s: dict, text: tuple) -> None:
    """Apply one located suggestion to paragraph p, with its comment when it has a note.

    ``start`` is where the original begins in the normalised text; ``text`` is that text
    with its map back to the raw characters.
    """
    norm, spans = text
    sug = re.sub(r"\s+", " ", s["suggested"]).strip()
    made: list = []
    for a, b, rep in sorted(word_edits(orig, sug), key=lambda e: -e[0]):
        raw_a, raw_b, rep = _raw_range(spans, norm, start + a, start + b, rep)
        made += _edit(p, raw_a, raw_b, rep, job.author, job.date, job.ids, job.etree) or []
    if made and job.notes.get(s["id"]):
        _add_comment(job, p, made, job.notes[s["id"]])
    if not sug and orig == norm.strip():
        _delete_paragraph_mark(p, job.author, job.date, job.ids, job.etree)


def _apply_located(job: _Job, p, located: list, text: tuple) -> None:
    """Apply located suggestions from the end of the paragraph backwards, skipping overlaps."""
    located.sort(key=lambda x: -x[0])
    last_start = None
    for start, end, orig, s in located:
        if last_start is not None and end > last_start:
            job.results.append(_not_applied(s, "overlaps another suggestion in the same paragraph"))
            continue
        _apply_one(job, p, start, orig, s, text)
        job.results.append({"id": s["id"], "applied": True})
        last_start = start


def _apply_paragraph(job: _Job, p, items: list[dict]) -> None:
    """Locate every suggestion for paragraph p first, then apply them."""
    _split_multi_content_runs(p)
    segs = _segments(p)
    text = _norm_with_map(_raw_text(segs))
    located = _locate(items, text[0], job.results)
    _apply_located(job, p, located, text)


def apply(
    src: Path,
    out: Path,
    paper: dict,
    suggestions: list[dict],
    author: str = "Athetize",
    notes: dict | None = None,
) -> list[dict]:
    """Write a copy of src with each suggestion applied as tracked changes.

    notes maps a suggestion id to a list of paragraphs; each becomes a Word comment
    anchored on that edit. Returns per-suggestion results.
    """
    etree = _etree()
    notes = notes or {}
    parts = pl_docx_package.read_parts(src)
    root = pl_docx_package.parse_xml(parts.document, etree)
    croot = comments_root(parts.comments, etree)
    next_cid = first_free_comment_id(root, croot)
    pid = _index_paragraphs(root)
    paper_text = {pp["id"]: pp["text"] for pp in paper.get("paragraphs", [])}
    ids = _Ids(root)
    date = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    job = _Job(etree, croot, author, date, notes, ids, next_cid)
    for para, items in _group_by_paragraph(suggestions).items():
        p = pid.get(para)
        if p is None or _stored_text(p) != paper_text.get(para):
            job.results.extend(_not_applied(s, _PARAGRAPH_NOT_FOUND) for s in items)
            continue
        _apply_paragraph(job, p, items)
    new_xml = pl_docx_package.to_bytes(root, etree)
    extra = pl_docx_package.comment_parts(parts, croot, job.added_comments, etree)
    pl_docx_package.write_package(src, out, new_xml, extra)
    return job.results
