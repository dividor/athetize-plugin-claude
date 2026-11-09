"""Build Word comments and anchor them on tracked changes for pl_docx."""

from __future__ import annotations

import re

from pl_docx_package import W_NS, XML_SPACE, W, parse_xml

_BAD_XML = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff]")


def _xml_safe(text: str) -> str:
    """Replace characters that XML 1.0 does not allow with spaces."""
    return _BAD_XML.sub(" ", text or "")


def comments_root(comments_xml, etree):
    """The document's w:comments element, or a new empty one when it has no comments part."""
    if comments_xml is not None:
        return parse_xml(comments_xml, etree)
    return etree.Element(W + "comments", nsmap={"w": W_NS})


def first_free_comment_id(root, croot) -> int:
    """One more than the highest comment id used in the comments part or the document body."""
    cids = [
        int(c.get(W + "id")) for c in croot.iter(W + "comment") if (c.get(W + "id") or "").isdigit()
    ]
    for el in root.iter(W + "commentRangeStart", W + "commentReference"):
        v = el.get(W + "id")
        if v and v.isdigit():
            cids.append(int(v))
    return max(cids) + 1 if cids else 0


def _anchor_comment(p, elems, cid: str, etree):
    """Wrap elems (in document order) with commentRangeStart/End and add the reference run."""
    order = {id(e): i for i, e in enumerate(p.iter())}
    elems = sorted(elems, key=lambda e: order.get(id(e), 0))
    first, last = elems[0], elems[-1]
    st = etree.Element(W + "commentRangeStart")
    st.set(W + "id", cid)
    first.getparent().insert(first.getparent().index(first), st)
    en = etree.Element(W + "commentRangeEnd")
    en.set(W + "id", cid)
    par = last.getparent()
    par.insert(par.index(last) + 1, en)
    ref_run = etree.Element(W + "r")
    ref = etree.SubElement(ref_run, W + "commentReference")
    ref.set(W + "id", cid)
    par.insert(par.index(en) + 1, ref_run)


def _comment_run(para, run, etree) -> None:
    """Add one run (a string or a formatting dict) to a comment paragraph, unless it is empty."""
    spec: dict = {"t": run} if isinstance(run, str) else run
    if not spec.get("t"):
        return
    r = etree.SubElement(para, W + "r")
    rpr = etree.SubElement(r, W + "rPr")
    if spec.get("b"):
        etree.SubElement(rpr, W + "b")
    if spec.get("i"):
        etree.SubElement(rpr, W + "i")
    if spec.get("color"):
        col = etree.SubElement(rpr, W + "color")
        col.set(W + "val", spec["color"])
    if spec.get("size"):
        sz = etree.SubElement(rpr, W + "sz")
        sz.set(W + "val", str(spec["size"]))
    t = etree.SubElement(r, W + "t")
    t.text = _xml_safe(spec["t"])
    t.set(XML_SPACE, "preserve")


def _comment_paragraph(c, k: int, para_spec, etree) -> None:
    """Add paragraph number k of a comment; a plain string in the first paragraph is bold."""
    runs: list = [{"t": para_spec, "b": k == 0}] if isinstance(para_spec, str) else list(para_spec)
    para = etree.SubElement(c, W + "p")
    ppr = etree.SubElement(para, W + "pPr")
    sp = etree.SubElement(ppr, W + "spacing")
    sp.set(W + "after", "40")
    if k == 0:
        r0 = etree.SubElement(para, W + "r")
        etree.SubElement(r0, W + "annotationRef")
    for run in runs:
        _comment_run(para, run, etree)


def _comment_el(cid: str, author: str, date: str, paragraphs: list, etree):
    """Build a w:comment. Each paragraph is a plain string, or a list of runs.

    A run is a string or a dict
    {"t": text, "b": bold, "i": italic, "color": "RRGGBB", "size": half-points}.
    An empty list gives a blank line.
    """
    c = etree.Element(W + "comment", nsmap={"w": W_NS})
    c.set(W + "id", cid)
    c.set(W + "author", author)
    c.set(W + "date", date)
    c.set(
        W + "initials", "".join(w[0] for w in re.findall(r"[A-Za-z]+", author)[:2]).upper() or "A"
    )
    for k, para_spec in enumerate(paragraphs):
        _comment_paragraph(c, k, para_spec, etree)
    return c
