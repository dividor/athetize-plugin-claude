"""Citation markers in the paper text: author-year linking to references and claim coverage."""

from __future__ import annotations

import re

import pl_text
from pl_workspace import Work, jsave

_NAME = r"[A-Z][A-Za-z\u00C0-\u017F'\-]+"
_NAME_TAIL = r"(?:\s(?:et al\.|and|&)\s?[A-Z]?[A-Za-z\u00C0-\u017F'\-]*)?"

# "(... 2020a; ...)": the inside of a pair of brackets that holds a year.
_GROUP_RX = re.compile(r"\(([^()]*\b(?:19|20)\d{2}[a-z]?[^()]*)\)")
# "Surname et al. (2020a": a name followed by a year in brackets.
_NARRATIVE_RX = re.compile(
    "(" + _NAME + _NAME_TAIL + ")" r"(?:\u2019s|'s)?\s?\(((?:19|20)\d{2}[a-z]?)"
)

_CITE_PATTERNS = [
    re.compile(
        r"\((?:[^()]*?(?:\b[A-Z][A-Za-z\u00C0-\u017F'\-]+|[A-Z]{2,})[^()]*?,?\s"
        r"(?:19|20)\d{2}[a-z]?(?:,\s*(?:19|20)\d{2}[a-z]?)*)(?:;[^()]*)?\)"
    ),
    re.compile(
        r"\b" + _NAME + _NAME_TAIL + r"(?:,\s[A-Z][A-Za-z'\-]+)*\s?(?:\u2019s|'s)?\s?"
        r"\((?:19|20)\d{2}[a-z]?(?:,\s*(?:19|20)\d{2})?\)"
    ),
    re.compile(r"\[\d+(?:[\u2013\-,\s]+\d+)*\]"),
]
_SENT_SPLIT = re.compile(r"(?<=[.!?])[\"\u201d\u2019)]*\s+(?=[A-Z\u201c\"(])")


# --------------------------------------------------------------------------
# Author-year linking
# --------------------------------------------------------------------------
def _ref_keys(refs: list[dict]) -> list[tuple[str, int | None, str, str]]:
    """(first-surname-or-corporate-name, year, letter suffix, id) for author-year matching."""
    keys = []
    for r in refs:
        raw = r.get("raw") or ""
        m = re.match(r"\s*(.+?)\s*(?:\(Eds?\.\)\.\s*)?\((\d{4})([a-z]?)|\s*(.+?)\s*\(n\.d\.\)", raw)
        if not m:
            continue
        who = m.group(1) or m.group(4) or ""
        first = who.split(",")[0].strip().rstrip(".")
        year = int(m.group(2)) if m.group(2) else None
        keys.append((first.casefold(), year, m.group(3) or "", r["id"]))
    return keys


def _marker_name(head: str) -> str | None:
    """Author name from the text in front of the year in one bracketed citation, if there is one."""
    # corporate authors keep their full name; personal authors use the first surname
    corp = re.match(r"\s*([A-Z][^,]*?(?:\s[A-Z][^,]*)*)\s*,?\s*$", head)
    words = re.findall(r"[A-Z][A-Za-z\u00C0-\u017F'\-]{1,}", head)
    if not words:
        return None
    several_people = "et al" in head or "&" in head or " and " in head
    if corp and len(words) > 1 and not several_people:
        return corp.group(1)
    return words[0]


def _part_candidates(part: str) -> list[tuple[str, int, str]]:
    """(name, year, letter suffix) for each year in one ";"-separated piece of a bracketed group."""
    part = re.sub(r"^\s*(?:e\.g\.,|see also|see|cf\.)\s*", "", part.strip(), flags=re.I)
    years = re.findall(r"\b((?:19|20)\d{2})([a-z]?)\b", part)
    if not years:
        return []
    name = _marker_name(part[: part.find(years[0][0])])
    if name is None:
        return []
    return [(name, int(y), suf) for y, suf in years]


def _marker_candidates(text: str) -> list[tuple[str, int, str]]:
    """Each (name, year, letter suffix) cited in a claim: bracketed groups first, then narrative."""
    groups = _GROUP_RX.findall(text)
    narrative = _NARRATIVE_RX.findall(text)
    cands: list[tuple[str, int, str]] = []
    for g in groups:
        for part in g.split(";"):
            cands += _part_candidates(part)
    for name, y in narrative:
        cands.append((name, int(y[:4]), y[4:]))
    return cands


def _surname(name: str) -> str:
    """Strip "et al.", co-authors and leading initials from a cited name."""
    name = re.sub(r"\s+(et al\.|and|&).*$", "", name.strip()).strip().rstrip(",")
    return re.sub(r"^(?:[A-Z]\.\s*)+", "", name)  # "G. Liu" -> "Liu"


def _name_matches(same_year: list, nm: str) -> list:
    """Keys whose name equals ``nm``; failing that, starts with it; failing that, starts it."""
    return (
        [k for k in same_year if k[0] == nm]
        or [k for k in same_year if k[0].startswith(nm)]
        or [k for k in same_year if nm.startswith(k[0])]
    )


def _matching_keys(keys: list, nm: str, year: int, suf: str) -> list:
    """Reference keys of that year that match the cited name, narrowed by the year's letter."""
    hits = _name_matches([k for k in keys if k[1] == year], nm)
    if len(hits) > 1 and suf:
        hits = [k for k in hits if k[2] == suf] or hits
    return hits


def link_cites(work: Work, write: bool = True) -> dict:
    """Add missing reference ids to claims from their author-year citation markers.

    Matches "(Surname et al., 2020; Other & Name, 2019)" and "Surname (2020)" against the first
    author (or corporate author) and year of each refs.json entry. Only adds ids, never removes.
    """
    refs = work.refs()
    keys = _ref_keys(refs)
    claims = work.claims()
    added, unmatched = [], []
    for c in claims:
        for cited, year, suf in _marker_candidates(c.get("text", "")):
            name = _surname(cited)
            hits = _matching_keys(keys, name.casefold(), year, suf)
            if len(hits) == 1:
                rid = hits[0][3]
                if rid not in c.get("cites", []):
                    c.setdefault("cites", []).append(rid)
                    added.append((c["id"], rid))
            elif not hits:
                unmatched.append((c["id"], f"{name} {year}"))
    if write and added:
        jsave(work.p("claims.json"), claims)
    return {"added": len(added), "links": added[:60], "unmatched_markers": unmatched[:60]}


# --------------------------------------------------------------------------
# Coverage of citation markers by claims
# --------------------------------------------------------------------------
def _claim_spans(claims: list[dict], paras: dict) -> dict[str, list[tuple[int, int]]]:
    """Character span of each claim inside its paragraph, grouped by paragraph id."""
    spans: dict[str, list[tuple[int, int]]] = {}
    for c in claims:
        para = paras.get(c.get("para"))
        if not para:
            continue
        sp = pl_text.find_span(para["text"], c.get("text", ""))
        if sp:
            spans.setdefault(c["para"], []).append(sp)
    return spans


def _sentence_bounds(text: str) -> list[tuple[int, int]]:
    """(start, end) of each sentence of a paragraph."""
    bounds, pos = [], 0
    for sent in _SENT_SPLIT.split(text):
        st = text.find(sent, pos)
        if st >= 0:
            bounds.append((st, st + len(sent)))
            pos = st + len(sent)
    return bounds


def _markers(text: str) -> list[tuple[int, int, str]]:
    """(start, end, text) of every citation marker in a paragraph, in reading order."""
    marks: list[tuple[int, int, str]] = []
    for rx in _CITE_PATTERNS:
        marks += [(m.start(), m.end(), m.group(0)) for m in rx.finditer(text)]
    marks.sort()
    return marks


def _uncovered_in_para(p: dict, spans: dict, missing: list[dict], seen: set) -> int:
    """Record the markers of one paragraph that no claim covers; return how many markers it has."""
    text = p["text"]
    bounds = _sentence_bounds(text)
    total = 0
    for ms, me, mtxt in _markers(text):
        total += 1
        if any(a <= ms and me <= b for a, b in spans.get(p["id"], [])):
            continue
        sb = next(((a, b) for a, b in bounds if a <= ms < b), (ms, me))
        key = (p["id"], sb)
        if key in seen:
            missing[-1]["markers"].append(mtxt)
            continue
        seen.add(key)
        missing.append({"para": p["id"], "sentence": text[sb[0] : sb[1]], "markers": [mtxt]})
    return total


def coverage(work: Work) -> dict:
    """Find citation markers in body paragraphs that sit outside every claim span.

    Reports the sentence around each uncovered marker, once per sentence.
    """
    paper = work.paper()
    paras = {p["id"]: p for p in paper["paragraphs"]}
    spans = _claim_spans(work.claims(), paras)
    missing: list[dict] = []
    seen: set = set()
    total = 0
    for p in paper["paragraphs"]:
        if p["kind"] != "body":
            continue
        total += _uncovered_in_para(p, spans, missing, seen)
    return {
        "citation_markers": total,
        "uncovered_sentences": len(missing),
        "uncovered_markers": sum(len(m["markers"]) for m in missing),
        "items": missing,
    }
