"""Data for the HTML report: one verdict per claim, evidence excerpts, reference status and
suggested edits."""

from __future__ import annotations

import base64
import json
import re
import time
import urllib.parse
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

import pl_docx
import pl_http
import pl_text
from pl_docx_read import PICTURE_TYPES
from pl_verify import _evidence_text
from pl_workspace import Work, jload

# Order used to pick one verdict for a claim that cites several sources (best first).
RANK = [
    "supported",
    "partially_supported",
    "overstated",
    "unverifiable",
    "unsupported",
    "contradicted",
]

# Verdicts that mean a cited source does not back the claim.
FAILING = ("unsupported", "contradicted")
# Verdicts that say something about the claim, as opposed to "could not be checked".
JUDGED = ("supported", "partially_supported", "overstated", "unsupported", "contradicted")


def evidence_basis(verdict: str, items: list[dict]) -> str:
    """How much of the source the claim's verdict rests on: full_text, abstract or none.

    Looks at the sources that gave the claim its verdict. One of them read in full is
    enough; an abstract alone is partial text, and the full paper is what settles it.
    """
    levels = {i["evidence_level"] for i in items if i["verdict"] == verdict}
    if "full_text" in levels:
        return "full_text"
    return "abstract" if "abstract" in levels else "none"


VERDICT_LABEL = {
    "supported": "Supported",
    "partially_supported": "Partly supported",
    "overstated": "Overstated",
    "unsupported": "Not supported",
    "contradicted": "Contradicted",
    "unverifiable": "Can't verify",
    "uncited": "Needs a citation",
    "pending": "Not checked",
}
KIND_LABEL = {
    "rewrite": "Suggested rewording",
    "citation": "Citation fix",
    "reference": "Reference list fix",
}
VERDICT_COLOR = {
    "supported": "1E7B4F",
    "partially_supported": "B26B00",
    "overstated": "B26B00",
    "unsupported": "C0392B",
    "contradicted": "C0392B",
    "unverifiable": "6B7280",
    "uncited": "6D28D9",
    "pending": "6B7280",
}


def word_diff(a: str, b: str) -> list:
    """Phrase-level diff ops [["=", text] | ["-", text] | ["+", text]] matching the Word export's
    grouping."""
    out, pos = [], 0
    for st, en, rep in pl_docx.word_edits(a, b):
        if st > pos:
            out.append(["=", a[pos:st]])
        if en > st:
            out.append(["-", a[st:en]])
        if rep:
            out.append(["+", rep])
        pos = en
    if pos < len(a):
        out.append(["=", a[pos:]])
    return out


def aggregate(item_verdicts: list[str]) -> str:
    """One verdict for a claim from the verdicts of its sources."""
    if not item_verdicts:
        return "pending"
    s = set(item_verdicts)
    if "supported" in s and "contradicted" in s:
        return "partially_supported"
    for v in RANK:
        if v in s:
            return v
    return "unverifiable"


def _clean_raw(raw: str) -> str:
    """A reference as written, without editorial notes in square brackets or _italic_ marks."""
    raw = re.sub(
        r"\s*\[(?:detail unverified|Report|Working paper|Vendor-commissioned|Open access|read as)"
        r"[^\]]*\]",
        "",
        raw or "",
    )
    return re.sub(r"_([^_]+)_", r"\1", raw).strip()


def _search_urls(ref: dict, entry: dict) -> dict:
    """Google Scholar and WorldCat search links for a reference that has no direct link."""
    title = re.sub(r"\s*\([^)]*$", "", (ref.get("title") or "").strip())
    raw = _clean_raw(ref.get("raw") or "")
    if not title:
        m = re.search(
            r"\(\d{4}[a-z]?\)\.\s*(.+?)[.?]\s",
            re.sub(r"\s*\((?:[^()]*?(?:Trans|Eds?|ed)\.[^()]*)\)", "", raw),
        )
        title = m.group(1) if m else raw[:120]
    who = (
        (entry.get("citation") or "").split("(")[0].replace(" et al.", "").replace("&", "").strip()
    )
    q = f"{title} {who}".strip()
    return {
        "scholar": "https://scholar.google.com/scholar?q=" + urllib.parse.quote(q),
        "worldcat": "https://search.worldcat.org/search?q=" + urllib.parse.quote(q),
    }


# --------------------------------------------------------------------------
# Report data
# --------------------------------------------------------------------------
@dataclass
class _Sources:
    """The workspace files that ``build_data`` reads, loaded once."""

    work: Work
    refs: dict
    manifest: dict
    verdicts: dict[str, list[dict]]
    paras: dict
    text_cache: dict[str, str] = field(default_factory=dict)


def _stored_excerpt(e: dict) -> dict:
    """An excerpt made from the text saved with the verdict, without surrounding context."""
    return {
        "before": "",
        "match": e.get("text", ""),
        "after": "",
        "page": e.get("page"),
        "mode": e.get("match"),
    }


def _excerpts(src: _Sources, v: dict | None, rid: str, entry: dict) -> list[dict]:
    """Evidence excerpts for one verdict, with context from the source text when it is unchanged."""
    if not v:
        return []
    evs = v.get("evidences") or ([v["evidence"]] if v.get("evidence") else [])
    if not evs:
        return []
    if v.get("evidence_hash") != entry.get("evidence_hash"):
        return [_stored_excerpt(e) for e in evs]
    if rid not in src.text_cache:
        src.text_cache[rid] = _evidence_text(src.work, entry)
    text = src.text_cache[rid]
    out = []
    for e in evs:
        if text and 0 <= e.get("start", -1) < e.get("end", -1) <= len(text):
            cw = pl_text.context_window(text, e["start"], e["end"])
        else:
            cw = {"before": "", "match": e.get("text", ""), "after": ""}
        cw.update(page=e.get("page"), mode=e.get("match"))
        out.append(cw)
    return out


def _item_quote(v: dict | None):
    """The quote shown for one source: the text found in the source, else the verifier's quote."""
    if v is None:
        return ""  # not checked yet
    return (v.get("evidence") or {}).get("text") or v.get("evidence_quote")


def _claim_item(src: _Sources, claim_id: str, rid: str) -> dict:
    """Report entry for one reference cited by a claim: its verdict, evidence and links."""
    v = next((x for x in src.verdicts.get(claim_id, []) if x["ref_id"] == rid), None)
    entry = src.manifest.get(rid, {})
    ref = src.refs.get(rid, {})
    return {
        "ref_id": rid,
        "citation": entry.get("citation") or ref.get("raw", rid),
        "link": entry.get("link"),
        "raw": _clean_raw(ref.get("raw", "")),
        "search": None if entry.get("link") else _search_urls(ref, entry),
        "local_file": entry.get("local_file"),
        "verdict": v["verdict"] if v else "pending",
        "confidence": v["confidence"] if v else None,
        "analysis": v["analysis"] if v else "Not checked yet.",
        "quote": _item_quote(v),
        "page": (v.get("evidence") or {}).get("page") if v else None,
        "match": (v.get("evidence") or {}).get("match") if v else None,
        "excerpts": _excerpts(src, v, rid, entry),
        "evidence_level": (v or {}).get("evidence_level") or entry.get("evidence_level", "none"),
        "flags": (v or {}).get("flags", []),
        "retracted": bool(entry.get("retracted")),
        "exists": entry.get("exists"),
        "why_missing": missing_reasons(entry) if entry.get("status") in NEEDS_SOURCE else [],
    }


def _claim_verdict(cites: list, items: list[dict]) -> str:
    """One verdict for a claim: "uncited", "pending", or the combined verdict of its sources."""
    verdict = (
        "uncited"
        if not cites
        else aggregate([i["verdict"] for i in items if i["verdict"] != "pending"])
    )
    if cites and all(i["verdict"] == "pending" for i in items):
        verdict = "pending"
    return verdict


def _report_claim(src: _Sources, c: dict, para: dict, span: tuple[int, int]) -> dict:
    """Report entry for one claim, with an item for each reference it cites."""
    cites = c.get("cites", [])
    items = []
    flagged_retracted = False
    for rid in cites:
        item = _claim_item(src, c["id"], rid)
        if item["retracted"]:
            flagged_retracted = True
        items.append(item)
    verdict = _claim_verdict(cites, items)
    basis = evidence_basis(verdict, items)
    return {
        "id": c["id"],
        "para": c["para"],
        "start": span[0],
        "end": span[1],
        "text": para["text"][span[0] : span[1]],
        "type": c.get("type", "empirical"),
        "cites": cites,
        "verdict": verdict,
        "evidence_basis": basis,
        # a result reached from an abstract alone stands only until the full text is read
        "provisional": verdict in JUDGED and basis == "abstract",
        # sources that do not back the claim, kept visible when another source does better
        "failing_refs": [i["ref_id"] for i in items if i["verdict"] in FAILING],
        "items": items,
        "retracted": flagged_retracted,
        "note": c.get("note", ""),
    }


def _report_claims(src: _Sources, claims: list[dict]) -> tuple[list[dict], list[str]]:
    """Report entries for the checkworthy claims found in body text, plus the ones skipped."""
    out_claims = []
    problems = []
    for c in claims:
        if not c.get("checkworthy", True):
            continue
        para = src.paras.get(c["para"])
        if not para or para["kind"] == "reference":
            continue
        span = pl_text.find_span(para["text"], c["text"])
        if not span:
            problems.append(f"{c['id']}: text not found; skipped")
            continue
        out_claims.append(_report_claim(src, c, para, span))
    return out_claims, problems


def _report_suggestion(sg: dict, paras: dict) -> dict:
    """Report entry for one suggested edit, with its word-level diff."""
    return {
        "id": sg["id"],
        "kind": sg.get("kind", "rewrite"),
        "claim_id": sg.get("claim_id"),
        "para": sg.get("para"),
        "refs": sg.get("refs", []),
        "rationale": sg.get("rationale", ""),
        "original": sg.get("original", ""),
        "suggested": sg.get("suggested", ""),
        "add_refs": sg.get("add_refs", []),
        "diff": word_diff(sg.get("original", ""), sg.get("suggested", "")),
        "where": (
            "Reference list" if paras.get(sg.get("para"), {}).get("kind") == "reference" else "Text"
        ),
    }


def _claim_suggestion_ids(c: dict, sugs: list[dict]) -> list:
    """Ids of the suggestions for a claim, or for a reference the claim cites."""
    return [
        x["id"]
        for x in sugs
        if x["claim_id"] == c["id"]
        or (
            x["kind"] in ("citation", "reference")
            and not x["claim_id"]
            and set(x["refs"]) & set(c["cites"])
        )
    ]


def _report_notes(notes: list[dict], paras: dict) -> list[dict]:
    """Report entries for the reviewer notes that point at a known paragraph."""
    out_notes: list[dict] = []
    for n in notes:
        para = paras.get(n.get("para"))
        if not para:
            continue
        span = pl_text.find_span(para["text"], n["quote"]) if n.get("quote") else None
        out_notes.append(
            {
                "id": n.get("id") or f"n{len(out_notes) + 1}",
                "para": n["para"],
                "kind": n.get("kind", "consistency"),
                "severity": n.get("severity", "info"),
                "message": n.get("message", ""),
                "start": span[0] if span else None,
                "end": span[1] if span else None,
            }
        )
    return out_notes


# Statuses of a reference whose full text is still wanted.
NEEDS_SOURCE = ("missing", "abstract_only")
# Name of the download list that ``request-sources`` writes next to the report.
REQUEST_FILE = "source_request.md"


def sources_needed(total: int, lacking: dict[str, str], claims: list[dict]) -> dict:
    """Counts for asking the person for sources.

    ``total`` is the number of references in the paper and ``lacking`` maps the id of each
    reference without full text to its status. ``claims_relying`` counts each claim once,
    however many of those references it cites.
    """
    return {
        "references": total,
        "need_source": len(lacking),
        "abstract_only": sum(1 for s in lacking.values() if s == "abstract_only"),
        "no_text": sum(1 for s in lacking.values() if s == "missing"),
        "claims_relying": sum(1 for c in claims if any(r in lacking for r in c.get("cites", []))),
    }


def _report_sources_needed(work: Work, refs: dict, manifest: dict) -> dict:
    """The report's missing-sources summary.

    When a download list was written it also names that list and the folder the person was
    asked to save papers in.
    """
    lacking = {}
    for rid in refs:
        status = manifest.get(rid, {}).get("status")
        if status in NEEDS_SOURCE:
            lacking[rid] = status
    summary = sources_needed(len(refs), lacking, work.claims())
    listed = work.p(REQUEST_FILE).exists()
    summary["request_file"] = REQUEST_FILE if listed else None
    summary["folder"] = (jload(work.p("source_request.json"), {}) or {}).get("folder")
    return summary


# What a recorded download error means, in words for the person. First match wins per error.
_WHY_PATTERNS = (
    (
        r"unpaywall skipped",
        "open-access copies were not looked up on Unpaywall (no contact email set)",
    ),
    (r"HTTP 40[13] for (\S+)", "{0} refused an automatic download"),
    (r"HTTP 404 for (\S+)", "{0} had no such page"),
    (r"HTTP 429 for (\S+)", "{0} was too busy to answer"),
    (r"HTTP 5\d\d for (\S+)", "{0} had an error"),
    (r"network error for (\S+)", "{0} could not be reached"),
    (r"did not return a PDF|too little text", "an open link did not lead to the full text"),
    (r"did not match|not the cited work", "a copy that was found did not match the reference"),
    (
        r"links from the matched record were not used",
        "the database record was doubtful, so its links were not used",
    ),
)


def missing_reasons(entry: dict) -> list[str]:
    """Why a reference has no full text, from what its download attempt recorded.

    Returns short phrases, most telling first, without repeats. With nothing recorded and no
    open copy listed, the plain fact is that no open-access copy was found.
    """
    reasons: list[str] = []
    for error in entry.get("errors") or []:
        for pattern, wording in _WHY_PATTERNS:
            found = re.search(pattern, error)
            if found:
                text = wording.format(*found.groups())
                if text not in reasons:
                    reasons.append(text)
                break
    if not entry.get("oa_urls"):
        reasons.insert(0, "no open-access copy is listed for it")
    return reasons[:4]


# The page of each citation database that shows the record for a DOI.
_RECORD_PAGES = {
    "crossref": "https://search.crossref.org/search/works?from_ui=yes&q={doi}",
    "openalex": "https://openalex.org/works?filter=doi:{doi}",
}


def record_url(matched: dict | None) -> str | None:
    """Where the database record a reference was matched to can be seen; None without a DOI."""
    page = _RECORD_PAGES.get((matched or {}).get("source") or "")
    doi = (matched or {}).get("doi")
    if not page or not doi:
        return None
    return page.format(doi=urllib.parse.quote(doi, safe="/"))


def _report_ref(rid: str, r: dict, e: dict, out_claims: list[dict], sugs: list[dict]) -> dict:
    """Report entry for one reference: how it was resolved and which claims cite it."""
    return {
        "id": rid,
        "raw": _clean_raw(r.get("raw") or r.get("title") or rid),
        "citation": e.get("citation") or r.get("raw") or rid,
        "search": None if e.get("link") else _search_urls(r, e),
        "status": e.get("status", "unresolved"),
        "exists": e.get("exists", "unchecked"),
        "link": e.get("link"),
        "retracted": bool(e.get("retracted")),
        "retraction_notes": e.get("retraction_notes", []),
        "discrepancies": e.get("discrepancies", []),
        "matched_source": (e.get("matched") or {}).get("source"),
        "matched_url": record_url(e.get("matched")),
        "why_missing": missing_reasons(e) if e.get("status") in NEEDS_SOURCE else [],
        "local_file": e.get("local_file"),
        "evidence_level": e.get("evidence_level", "none"),
        "match_score": e.get("match_score"),
        "matched_title": (e.get("matched") or {}).get("title"),
        "errors": e.get("errors", []),
        "cited_by": [c["id"] for c in out_claims if rid in c["cites"]],
        "suggestions": [x["id"] for x in sugs if x["kind"] == "reference" and rid in x["refs"]],
    }


# What a paragraph carries into the report besides its text: how it was laid out.
_PARAGRAPH_LAYOUT = ("level", "fmt", "tbl", "align", "figures", "figures_after")
# The pictures of one report, together, are kept under this many bytes.
MAX_PICTURE_BYTES = 12_000_000


def _report_paragraph(p: dict) -> dict:
    """A paragraph as the report shows it: its text, its kind and its layout."""
    shown = {"id": p["id"], "text": p["text"], "kind": p["kind"]}
    shown.update({key: p[key] for key in _PARAGRAPH_LAYOUT if key in p})
    return shown


def _report_figures(work: Work, paragraphs: list[dict]) -> dict[str, str]:
    """The paper's pictures, each as a data address the report page can show.

    They are read from the private copy of the Word manuscript. A picture of a type a
    browser cannot show, or one that would take the report past MAX_PICTURE_BYTES, is
    left out, and the page says a picture is not shown in its place.
    """
    wanted = []
    for p in paragraphs:
        for figure in p.get("figures", []) + p.get("figures_after", []):
            if figure["file"] not in wanted:
                wanted.append(figure["file"])
    if not wanted:
        return {}
    shown: dict[str, str] = {}
    room = MAX_PICTURE_BYTES
    with zipfile.ZipFile(work.p("manuscript.docx")) as z:
        for name in wanted:
            kind = PICTURE_TYPES.get(Path(name).suffix.lower())
            if not kind or z.getinfo(name).file_size > room:
                continue
            data = z.read(name)
            room -= len(data)
            shown[name] = f"data:{kind};base64," + base64.b64encode(data).decode("ascii")
    return shown


def word_export_name(paper: dict, fallback: str = "manuscript.docx") -> str:
    """File name of the Word copy with suggested edits, written beside the report."""
    return Path(paper.get("source_file") or fallback).stem + "_athetize_suggestions.docx"


def build_data(work: Work, with_suggestions: bool = True) -> dict:
    """Everything the report page shows, as one JSON-ready dict."""
    paper = work.paper()
    refs = {r["id"]: r for r in work.refs()}
    manifest = work.manifest().get("refs", {})
    verdicts: dict[str, list[dict]] = {}
    for i in work.verdicts().get("items", []):
        verdicts.setdefault(i["claim_id"], []).append(i)

    paras = {p["id"]: p for p in paper["paragraphs"]}
    src = _Sources(work=work, refs=refs, manifest=manifest, verdicts=verdicts, paras=paras)
    out_claims, problems = _report_claims(src, work.claims())

    sugs = []
    if with_suggestions:
        for sg in work.suggestions():
            sugs.append(_report_suggestion(sg, paras))
    for c in out_claims:
        c["suggestions"] = _claim_suggestion_ids(c, sugs)

    out_notes = _report_notes(work.notes(), paras)
    out_refs = [
        _report_ref(rid, r, manifest.get(rid, {}), out_claims, sugs) for rid, r in refs.items()
    ]

    # the reference list is shown too: its entries can carry suggested fixes
    body_paras = [_report_paragraph(p) for p in paper["paragraphs"]]
    word = word_export_name(paper)
    return {
        "meta": {
            "title": paper.get("title") or paper.get("source_file"),
            "source_file": paper.get("source_file"),
            "generated": time.strftime("%Y-%m-%d %H:%M"),
            "version": pl_http.VERSION,
            "homepage": pl_http.HOMEPAGE,
            "word_export": word if work.p(word).exists() else None,
        },
        "paragraphs": body_paras,
        "tables": paper.get("tables", []),
        "figures": _report_figures(work, body_paras),
        "suggestions": sugs,
        "claims": out_claims,
        "notes": out_notes,
        "refs": out_refs,
        "sources_needed": _report_sources_needed(work, refs, manifest),
        "problems": problems,
    }


def safe_json_for_html(obj) -> str:
    """JSON text that is safe to place inside a <script> element."""
    s = json.dumps(obj, ensure_ascii=False)
    return (
        s.replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def build_report(work: Work, out: Path | None = None) -> Path:
    """Write the HTML report (the template with the report data put in) and return its path."""
    template = (Path(__file__).resolve().parent.parent / "templates" / "report.html").read_text(
        "utf-8"
    )
    data = build_data(work)
    html = template.replace("/*__ATHETIZE_DATA__*/null", safe_json_for_html(data))
    out = out or work.p("report.html")
    Path(out).write_text(html, "utf-8")
    return Path(out)
