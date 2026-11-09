"""Checks that the workspace files (refs, claims, notes, suggestions) agree with the paper."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import pl_cites
import pl_text
from pl_workspace import Work, jsave

CLAIM_TYPES = ("empirical", "numeric", "quote", "background", "method", "definition")


@dataclass
class _Check:
    """What one validation run knows so far and the problems it has found."""

    paras: dict
    fix: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    ref_ids: set = field(default_factory=set)
    changed: bool = False


def _check_refs(chk: _Check, refs: list[dict]) -> None:
    """Every reference needs a usable, unique id and something to look it up by."""
    for r in refs:
        rid = r.get("id")
        if not rid or not re.fullmatch(r"[A-Za-z0-9_.-]+", str(rid)):
            chk.errors.append(f"refs.json: bad id {rid!r} (use letters, digits, _ . -)")
            continue
        if rid in chk.ref_ids:
            chk.errors.append(f"refs.json: duplicate id {rid}")
        chk.ref_ids.add(rid)
        if not (r.get("title") or r.get("raw") or r.get("doi")):
            chk.errors.append(f"refs.json: {rid} needs at least title, raw or doi")


def _claim_place(chk: _Check, c: dict, cid: str) -> tuple[dict, tuple[int, int]] | None:
    """The body paragraph a claim sits in and its span there; None (with an error) if not found."""
    para = chk.paras.get(c.get("para"))
    if not para:
        chk.errors.append(f"{cid}: unknown paragraph {c.get('para')!r}")
        return None
    if para["kind"] == "reference":
        chk.errors.append(f"{cid}: paragraph {para['id']} is in the reference list")
        return None
    text = c.get("text", "")
    span = pl_text.find_span(para["text"], text)
    if not span:
        chk.errors.append(f"{cid}: text is not a substring of {para['id']}: {text[:70]!r}")
        return None
    return para, span


def _check_claim_whitespace(chk: _Check, c: dict, cid: str, exact: str) -> None:
    """The claim text should equal the paragraph text it was matched to, spaces included."""
    if exact != c.get("text", ""):
        if chk.fix:
            c["text"] = exact
            chk.changed = True
        else:
            chk.warnings.append(f"{cid}: whitespace differs from paragraph; run validate --fix")


def _check_claim_cites(chk: _Check, c: dict, cid: str) -> None:
    """A claim's cites must be a list of known reference ids, and its type a known one."""
    cites = c.get("cites", [])
    if not isinstance(cites, list):
        chk.errors.append(f"{cid}: cites must be a list")
        cites = []
    for r in cites:
        if r not in chk.ref_ids:
            chk.errors.append(f"{cid}: cites unknown reference {r!r}")
    if c.get("type") not in CLAIM_TYPES:
        chk.warnings.append(f"{cid}: type {c.get('type')!r} not one of {', '.join(CLAIM_TYPES)}")


def _check_claims(chk: _Check, claims: list[dict]) -> None:
    """Each claim needs a unique id, a place in a body paragraph, no overlaps and valid cites."""
    seen = set()
    spans: dict[str, list[tuple[int, int, str]]] = {}
    for c in claims:
        cid = c.get("id")
        if not cid or cid in seen:
            chk.errors.append(f"claims.json: missing or duplicate id {cid!r}")
            continue
        seen.add(cid)
        place = _claim_place(chk, c, cid)
        if not place:
            continue
        para, span = place
        _check_claim_whitespace(chk, c, cid, para["text"][span[0] : span[1]])
        for s, e, other in spans.get(para["id"], []):
            if span[0] < e and s < span[1]:
                chk.errors.append(f"{cid} overlaps {other} in {para['id']}")
        spans.setdefault(para["id"], []).append((span[0], span[1], cid))
        _check_claim_cites(chk, c, cid)


def _check_notes(chk: _Check, notes: list[dict]) -> None:
    """A note must point at a known paragraph, and its quote must be found there."""
    paras = chk.paras
    for n in notes:
        if n.get("para") not in paras:
            chk.errors.append(f"notes.json: unknown paragraph {n.get('para')!r}")
        elif n.get("quote") and not pl_text.find_span(paras[n["para"]]["text"], n["quote"]):
            chk.errors.append(f"notes.json {n.get('id')}: quote not found in {n['para']}")


def _check_suggestion_text(chk: _Check, sg: dict, sid: str, para: dict) -> None:
    """The original text must be in the paragraph exactly once and differ from the suggestion."""
    orig = sg.get("original", "")
    if not orig or para["text"].count(orig) != 1:
        chk.errors.append(
            f"{sid}: original must appear exactly once in {para['id']}: {orig[:70]!r}"
        )
    if not sg.get("suggested") and sg.get("suggested") != "":
        chk.errors.append(f"{sid}: suggested text missing")
    if sg.get("suggested") == orig:
        chk.errors.append(f"{sid}: suggested text is identical to the original")


def _check_suggestion_links(chk: _Check, sg: dict, sid: str, claim_ids: set) -> None:
    """The claim and the references that a suggestion names must exist."""
    if sg.get("claim_id") and sg["claim_id"] not in claim_ids:
        chk.errors.append(f"{sid}: unknown claim {sg['claim_id']!r}")
    for r in sg.get("refs", []) or []:
        if r not in chk.ref_ids:
            chk.errors.append(f"{sid}: unknown reference {r!r}")


def _check_suggestions(chk: _Check, suggestions: list[dict], claim_ids: set) -> None:
    """Each suggestion needs a unique id, a known kind, paragraph, claim and references."""
    sids = set()
    for sg in suggestions:
        sid = sg.get("id")
        if not sid or sid in sids:
            chk.errors.append(f"suggestions.json: missing or duplicate id {sid!r}")
            continue
        sids.add(sid)
        if sg.get("kind") not in ("rewrite", "citation", "reference"):
            chk.errors.append(f"{sid}: kind must be rewrite, citation or reference")
        para = chk.paras.get(sg.get("para"))
        if not para:
            chk.errors.append(f"{sid}: unknown paragraph {sg.get('para')!r}")
            continue
        _check_suggestion_text(chk, sg, sid, para)
        _check_suggestion_links(chk, sg, sid, claim_ids)


def validate(work: Work, fix: bool = False):
    """Return (errors, warnings) for the workspace; with ``fix`` also repair claim whitespace."""
    paper = work.paper()
    chk = _Check(paras={p["id"]: p for p in paper["paragraphs"]}, fix=fix)

    _check_refs(chk, work.refs())

    claims = work.claims()
    _check_claims(chk, claims)
    if fix and chk.changed:
        jsave(work.p("claims.json"), claims)

    _check_notes(chk, work.notes())

    cov = pl_cites.coverage(work)
    if cov["uncovered_markers"]:
        chk.warnings.append(
            f"{cov['uncovered_markers']} of {cov['citation_markers']} citation markers are outside"
            " every claim; run `pl.py coverage` and extract them"
        )

    claim_ids = {c.get("id") for c in claims}
    _check_suggestions(chk, work.suggestions(), claim_ids)
    return chk.errors, chk.warnings
