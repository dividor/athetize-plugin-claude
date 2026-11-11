"""Safe XML parsing for athetize: untrusted Word and JATS files go through defusedxml."""

from __future__ import annotations

from xml.etree import ElementTree as ET


def parse_xml(data: bytes) -> ET.Element:
    """Parse XML bytes with defusedxml and return the root element.

    defusedxml refuses the XML tricks (entity expansion, external entities) that the standard
    parser accepts. Malformed XML still raises `xml.etree.ElementTree.ParseError`.
    """
    try:
        from defusedxml import ElementTree as DET
    except ImportError as exc:
        raise RuntimeError(
            "Reading Word and XML files needs defusedxml: pip install defusedxml "
            "(add --break-system-packages if needed)"
        ) from exc
    return DET.fromstring(data)
