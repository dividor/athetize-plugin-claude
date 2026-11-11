"""Read and write the parts of a .docx file (a zip of XML files) for pl_docx."""

from __future__ import annotations

import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import NamedTuple, Optional

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
W = "{%s}" % W_NS
XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"
W14 = "http://schemas.microsoft.com/office/word/2010/wordml"
REL_COMMENTS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/comments"
CT_COMMENTS = "application/vnd.openxmlformats-officedocument.wordprocessingml.comments+xml"

DOCUMENT_PART = "word/document.xml"
COMMENTS_PART = "word/comments.xml"
RELS_PART = "word/_rels/document.xml.rels"
CONTENT_TYPES_PART = "[Content_Types].xml"


class Parts(NamedTuple):
    """Raw bytes of the parts pl_docx works on; a part missing from the file is None."""

    document: bytes
    comments: Optional[bytes]
    rels: Optional[bytes]
    content_types: Optional[bytes]


def parse_xml(data: bytes, etree):
    """Parse XML from the user's document without expanding entities or using the network."""
    return etree.fromstring(data, etree.XMLParser(resolve_entities=False, no_network=True))


def to_bytes(root, etree) -> bytes:
    """Serialise an XML tree the way Word expects a package part to start."""
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)


def read_parts(src) -> Parts:
    """Read the main document and, where present, the comments, rels and content-types parts."""
    with zipfile.ZipFile(src) as z:
        xml = z.read(DOCUMENT_PART)
        names = set(z.namelist())
        comments_xml = z.read(COMMENTS_PART) if COMMENTS_PART in names else None
        rels_xml = z.read(RELS_PART) if RELS_PART in names else None
        ct_xml = z.read(CONTENT_TYPES_PART) if CONTENT_TYPES_PART in names else None
    return Parts(xml, comments_xml, rels_xml, ct_xml)


def _rels_with_comments(rels_xml: bytes, etree) -> bytes:
    """Add a relationship from the document to comments.xml under the first free rId."""
    rels = parse_xml(rels_xml, etree)
    rns = rels.nsmap.get(None)
    used = {r.get("Id") for r in rels}
    rid = next(f"rId{n}" for n in range(1, 10000) if f"rId{n}" not in used)
    rel = etree.SubElement(rels, "{%s}Relationship" % rns)
    rel.set("Id", rid)
    rel.set("Type", REL_COMMENTS)
    rel.set("Target", "comments.xml")
    return to_bytes(rels, etree)


def _content_types_with_comments(ct_xml: bytes, etree) -> bytes:
    """Declare the content type of comments.xml."""
    ct = parse_xml(ct_xml, etree)
    cns = ct.nsmap.get(None)
    ov = etree.SubElement(ct, "{%s}Override" % cns)
    ov.set("PartName", "/word/comments.xml")
    ov.set("ContentType", CT_COMMENTS)
    return to_bytes(ct, etree)


def comment_parts(parts: Parts, croot, added_comments: int, etree) -> dict[str, bytes]:
    """Parts to add or replace because comments were added (none when no comment was added).

    A document that had no comments part before also gets it registered in the rels and
    content-types parts, when it has both.
    """
    extra: dict[str, bytes] = {}
    if not added_comments:
        return extra
    extra[COMMENTS_PART] = to_bytes(croot, etree)
    if parts.comments is None and parts.rels is not None and parts.content_types is not None:
        extra[RELS_PART] = _rels_with_comments(parts.rels, etree)
        extra[CONTENT_TYPES_PART] = _content_types_with_comments(parts.content_types, etree)
    return extra


def _copy_members(zin, zout, new_xml: bytes, extra: dict[str, bytes]) -> None:
    """Copy every file of zin to zout, swapping in the new document and any replaced parts."""
    for item in zin.infolist():
        if item.filename.endswith("/"):
            continue
        if item.filename == DOCUMENT_PART:
            data = new_xml
        elif item.filename in extra:
            data = extra.pop(item.filename)
        else:
            data = zin.read(item.filename)
        zout.writestr(item, data)


def write_package(src, out, new_xml: bytes, extra: dict[str, bytes]) -> None:
    """Write a copy of src to out with the new document XML and the extra parts."""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d) / "out.docx"
        with zipfile.ZipFile(src) as zin, zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
            _copy_members(zin, zout, new_xml, extra)
            for name, data in extra.items():
                zout.writestr(name, data)
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(tmp, out)
