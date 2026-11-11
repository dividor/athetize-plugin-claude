"""Build the sample paper as a Word file from its Markdown text.

The sample is kept in the repository as text and pictures; this turns them into
``banana_sample.docx``, laid out like a paper, with a SAMPLE watermark on every page.
Standard library only.

    python3 examples/bananas/make_docx.py [OUT.docx]
"""

from __future__ import annotations

import re
import struct
import sys
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

HERE = Path(__file__).resolve().parent
SOURCE = HERE / "banana_sample.md"
# The figures were drawn at this many pixels to the inch.
FIGURE_DPI = 200
_EMU_PER_INCH = 914400

_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_NAMESPACES = (
    f'xmlns:w="{_W}" xmlns:r="{_R}" '
    'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" '
    'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
    'xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture" '
    'xmlns:v="urn:schemas-microsoft-com:vml" xmlns:o="urn:schemas-microsoft-com:office:office"'
)
_XML = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
_REL_TYPES = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_PACKAGE_RELS = "http://schemas.openxmlformats.org/package/2006/relationships"

CONTENT_TYPES = (
    f"{_XML}<Types "
    'xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" '
    'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Default Extension="png" ContentType="image/png"/>'
    '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-'
    'officedocument.wordprocessingml.document.main+xml"/>'
    '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-'
    'officedocument.wordprocessingml.styles+xml"/>'
    '<Override PartName="/word/header1.xml" ContentType="application/vnd.openxmlformats-'
    'officedocument.wordprocessingml.header+xml"/>'
    "</Types>"
)
ROOT_RELS = (
    f'{_XML}<Relationships xmlns="{_PACKAGE_RELS}">'
    f'<Relationship Id="rId1" Type="{_REL_TYPES}/officeDocument" Target="word/document.xml"/>'
    "</Relationships>"
)


def _style(style_id: str, name: str, para: str, run: str, based_on: str = "Normal") -> str:
    """One paragraph style."""
    base = f'<w:basedOn w:val="{based_on}"/>' if style_id != "Normal" else ""
    return (
        f'<w:style w:type="paragraph" w:styleId="{style_id}"><w:name w:val="{name}"/>{base}'
        f"<w:pPr>{para}</w:pPr><w:rPr>{run}</w:rPr></w:style>"
    )


STYLES = (
    f'{_XML}<w:styles xmlns:w="{_W}"><w:docDefaults><w:rPrDefault><w:rPr>'
    '<w:rFonts w:ascii="Times New Roman" w:hAnsi="Times New Roman" w:cs="Times New Roman"/>'
    '<w:sz w:val="22"/></w:rPr></w:rPrDefault></w:docDefaults>'
    + _style("Normal", "Normal", '<w:spacing w:after="140" w:line="276" w:lineRule="auto"/>', "")
    + _style(
        "Title",
        "Title",
        '<w:spacing w:after="160"/><w:jc w:val="center"/>',
        '<w:b/><w:sz w:val="36"/>',
    )
    + _style(
        "Heading1",
        "heading 1",
        '<w:keepNext/><w:spacing w:before="280" w:after="100"/>',
        '<w:b/><w:sz w:val="26"/>',
    )
    + _style("Byline", "Byline", '<w:spacing w:after="60"/><w:jc w:val="center"/>', "<w:i/>")
    + _style(
        "Caption",
        "caption",
        '<w:spacing w:after="240"/><w:jc w:val="center"/>',
        '<w:i/><w:sz w:val="19"/>',
    )
    + _style("Header", "header", "", "")
    + "</w:styles>"
)

# A page header that draws the word SAMPLE across each page, the way Word's own watermark does.
HEADER = (
    f'{_XML}<w:hdr {_NAMESPACES}><w:p><w:pPr><w:pStyle w:val="Header"/></w:pPr><w:r><w:pict>'
    '<v:shapetype id="_x0000_t136" coordsize="21600,21600" o:spt="136" adj="10800" '
    'path="m@7,l@8,m@5,21600l@6,21600e"><v:formulas><v:f eqn="sum #0 0 10800"/>'
    '<v:f eqn="prod #0 2 1"/><v:f eqn="sum 21600 0 @1"/><v:f eqn="sum 0 0 @2"/>'
    '<v:f eqn="sum 21600 0 @3"/><v:f eqn="if @0 @3 0"/><v:f eqn="if @0 21600 @1"/>'
    '<v:f eqn="if @0 0 @2"/><v:f eqn="if @0 @4 21600"/><v:f eqn="mid @5 @6"/>'
    '<v:f eqn="mid @8 @5"/><v:f eqn="mid @7 @8"/><v:f eqn="mid @6 @7"/>'
    '<v:f eqn="sum @6 0 @5"/></v:formulas><v:path textpathok="t" o:connecttype="custom" '
    'o:connectlocs="@9,0;@10,10800;@11,21600;@12,10800" o:connectangles="270,180,90,0"/>'
    '<v:textpath on="t" fitshape="t"/><o:lock v:ext="edit" text="t" shapetype="t"/>'
    '</v:shapetype><v:shape id="SampleWatermark" o:spid="_x0000_s2049" type="#_x0000_t136" '
    'style="position:absolute;margin-left:0;margin-top:0;width:440pt;height:132pt;rotation:315;'
    "z-index:-251658752;mso-position-horizontal:center;"
    "mso-position-horizontal-relative:margin;mso-position-vertical:center;"
    'mso-position-vertical-relative:margin" o:allowincell="f" fillcolor="#c8ced3" '
    'stroked="f"><v:fill opacity=".45"/><v:textpath style="font-family:&quot;Arial&quot;;'
    'font-size:1pt;font-weight:bold" string="SAMPLE"/></v:shape></w:pict></w:r></w:p></w:hdr>'
)


def runs(text: str) -> str:
    """Word runs for a line of text; a stretch between asterisks is set in italics."""
    out = []
    for i, part in enumerate(re.split(r"\*([^*]+)\*", text)):
        if part:
            italic = "<w:rPr><w:i/></w:rPr>" if i % 2 else ""
            out.append(f'<w:r>{italic}<w:t xml:space="preserve">{escape(part)}</w:t></w:r>')
    return "".join(out)


def paragraph(text: str, style: str = "Normal") -> str:
    """One paragraph in the given style."""
    return f'<w:p><w:pPr><w:pStyle w:val="{style}"/></w:pPr>{runs(text)}</w:p>'


def png_size(data: bytes) -> tuple[int, int]:
    """Width and height in pixels of a PNG picture."""
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG picture")
    width, height = struct.unpack(">II", data[16:24])
    return width, height


def picture(rel_id: str, number: int, size: tuple[int, int]) -> str:
    """A centred paragraph that holds one picture, at the size it was drawn."""
    cx, cy = (round(px * _EMU_PER_INCH / FIGURE_DPI) for px in size)
    return (
        '<w:p><w:pPr><w:keepNext/><w:jc w:val="center"/></w:pPr><w:r><w:drawing>'
        f'<wp:inline distT="0" distB="0" distL="0" distR="0"><wp:extent cx="{cx}" cy="{cy}"/>'
        f'<wp:docPr id="{number}" name="Figure {number}"/><a:graphic>'
        '<a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">'
        f'<pic:pic><pic:nvPicPr><pic:cNvPr id="{number}" name="Figure {number}"/>'
        f'<pic:cNvPicPr/></pic:nvPicPr><pic:blipFill><a:blip r:embed="{rel_id}"/>'
        "<a:stretch><a:fillRect/></a:stretch></pic:blipFill><pic:spPr><a:xfrm>"
        f'<a:off x="0" y="0"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm>'
        '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr></pic:pic>'
        "</a:graphicData></a:graphic></wp:inline></w:drawing></w:r></w:p>"
    )


_FIGURE = re.compile(r"!\[(.*)\]\(([^)]+)\)$")


def blocks(markdown: str) -> list[tuple[str, str, str]]:
    """The paper as (kind, text, picture file) blocks: title, heading, byline, figure, text."""
    out = []
    for chunk in re.split(r"\n\s*\n", markdown.strip()):
        line = " ".join(chunk.split())
        figure = _FIGURE.match(line)
        if figure:
            out.append(("figure", figure.group(1), figure.group(2)))
        elif line.startswith("## "):
            out.append(("heading", line[3:], ""))
        elif line.startswith("# "):
            out.append(("title", line[2:], ""))
        elif line.startswith(("Authors:", "Affiliations:")):
            out.append(("byline", line.split(":", 1)[1].strip(), ""))
        else:
            out.append(("text", line, ""))
    return out


_STYLE_OF = {"title": "Title", "heading": "Heading1", "byline": "Byline", "text": "Normal"}


def document_xml(parts: list[tuple[str, str, str]], sizes: dict[str, tuple[int, int]]) -> str:
    """The main document: every block in order, then the page setup with the watermark header."""
    body = []
    names = list(sizes)
    for kind, text, name in parts:
        if kind == "figure":
            number = names.index(name) + 1
            body.append(picture(f"rIdFig{number}", number, sizes[name]))
            body.append(paragraph(text, "Caption"))
        else:
            body.append(paragraph(text, _STYLE_OF[kind]))
    page = (
        '<w:sectPr><w:headerReference w:type="default" r:id="rIdHeader"/>'
        '<w:pgSz w:w="11906" w:h="16838"/>'
        '<w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440" '
        'w:header="708" w:footer="708" w:gutter="0"/></w:sectPr>'
    )
    return f"{_XML}<w:document {_NAMESPACES}><w:body>{''.join(body)}{page}</w:body></w:document>"


def document_rels(names: list[str]) -> str:
    """Relationships of the main document: its styles, its header and its pictures."""
    rels = [
        f'<Relationship Id="rIdStyles" Type="{_REL_TYPES}/styles" Target="styles.xml"/>',
        f'<Relationship Id="rIdHeader" Type="{_REL_TYPES}/header" Target="header1.xml"/>',
    ]
    for number, name in enumerate(names, 1):
        rels.append(
            f'<Relationship Id="rIdFig{number}" Type="{_REL_TYPES}/image" '
            f'Target="media/{name}"/>'
        )
    return f'{_XML}<Relationships xmlns="{_PACKAGE_RELS}">{"".join(rels)}</Relationships>'


def build(source: Path = SOURCE, out: Path | None = None) -> Path:
    """Write the Word file for the sample paper and return where it was written."""
    parts = blocks(source.read_text("utf-8"))
    pictures = {name: (source.parent / name).read_bytes() for kind, _, name in parts if name}
    sizes = {name: png_size(data) for name, data in pictures.items()}
    out = out or source.with_suffix(".docx")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", CONTENT_TYPES)
        z.writestr("_rels/.rels", ROOT_RELS)
        z.writestr("word/document.xml", document_xml(parts, sizes))
        z.writestr("word/_rels/document.xml.rels", document_rels(list(pictures)))
        z.writestr("word/styles.xml", STYLES)
        z.writestr("word/header1.xml", HEADER)
        for name, data in pictures.items():
            z.writestr(f"word/media/{name}", data)
    return out


if __name__ == "__main__":
    print(build(out=Path(sys.argv[1]) if len(sys.argv) > 1 else None))
