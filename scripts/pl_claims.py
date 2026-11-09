"""Building claims.json: merging extractor output, appending extra claims, and carrying work
forward from a check of an earlier draft."""

from __future__ import annotations

import re
import shutil

import pl_text
from pl_workspace import Work, jload, jsave


# --------------------------------------------------------------------------
# Claims written by the extractor subagents
# --------------------------------------------------------------------------
def _existing_spans(claims: list[dict], paras: dict) -> dict[str, list[tuple[int, int]]]:
    """Character span of each existing claim inside its paragraph, grouped by paragraph id."""
    spans: dict[str, list[tuple[int, int]]] = {}
    for c in claims:
        sp = (
            pl_text.find_span(paras.get(c["para"], {}).get("text", ""), c.get("text", ""))
            if c.get("para") in paras
            else None
        )
        if sp:
            spans.setdefault(c["para"], []).append(sp)
    return spans


def _next_claim_number(claims: list[dict]) -> int:
    """One more than the highest number used in an existing "c<number>" claim id."""
    nums = [
        int(m.group(1))
        for c in claims
        for m in [re.fullmatch(r"c(\d+)", str(c.get("id", "")))]
        if m
    ]
    return (max(nums) if nums else 0) + 1


def _new_claim_span(c: dict, paras: dict, spans: dict) -> tuple[tuple[int, int] | None, str]:
    """Span of a claim to append, or (None, reason) when it has to be skipped."""
    para = paras.get(c.get("para"))
    sp = pl_text.find_span(para["text"], c.get("text", "")) if para else None
    if not sp:
        return None, "not found in paragraph"
    if any(a < sp[1] and sp[0] < b for a, b in spans.get(c["para"], [])):
        return None, "overlaps an existing claim"
    return sp, ""


def append_claim_parts(work: Work, pattern: str = "add-*.json") -> dict:
    """Append claims from claims_parts/<pattern> to claims.json with new ids, keeping existing
    ids and verdicts.

    New claims that overlap an existing span in the same paragraph are skipped and reported.
    """
    paper = work.paper()
    paras = {p["id"]: p for p in paper["paragraphs"]}
    claims = work.claims()
    spans = _existing_spans(claims, paras)
    nxt = _next_claim_number(claims)
    added, skipped = 0, []
    for f in sorted(work.p("claims_parts").glob(pattern)):
        for c in jload(f, []) or []:
            sp, reason = _new_claim_span(c, paras, spans)
            if not sp:
                skipped.append({"file": f.name, "text": c.get("text", "")[:80], "reason": reason})
                continue
            c["id"] = f"c{nxt}"
            nxt += 1
            c.setdefault("cites", [])
            c.setdefault("checkworthy", True)
            claims.append(c)
            spans.setdefault(c["para"], []).append(sp)
            added += 1
    jsave(work.p("claims.json"), claims)
    return {"added": added, "skipped": skipped, "total": len(claims)}


def merge_claim_parts(work: Work) -> int:
    """Combine every claims_parts/*.json file into claims.json, in paper order, with fresh ids."""
    paper = work.paper()
    order = {p["id"]: i for i, p in enumerate(paper["paragraphs"])}
    paras = {p["id"]: p for p in paper["paragraphs"]}
    parts = sorted(work.p("claims_parts").glob("*.json"))
    merged = []
    for f in parts:
        merged.extend(jload(f, []))

    def key(c):
        para = paras.get(c.get("para"))
        span = pl_text.find_span(para["text"], c.get("text", "")) if para else None
        return (order.get(c.get("para"), 10**6), span[0] if span else 0)

    merged.sort(key=key)
    for i, c in enumerate(merged, 1):
        c["id"] = f"c{i}"
        c.setdefault("cites", [])
        c.setdefault("checkworthy", True)
    jsave(work.p("claims.json"), merged)
    return len(merged)


# --------------------------------------------------------------------------
# Carry-over from an earlier draft
# --------------------------------------------------------------------------
def _locate_once(text: str, paras: list[dict]) -> dict | None:
    """The one paragraph that holds ``text`` exactly once, or None."""
    hits = [p for p in paras if text and p["text"].count(text) == 1]
    return hits[0] if len(hits) == 1 else None


def _copy_missing_sources(src_old, src_new) -> int:
    """Copy source files that the new workspace does not have yet; return how many were copied."""
    copied = 0
    if src_old.is_dir():
        for f in src_old.rglob("*"):
            if f.is_file():
                dst = src_new / f.relative_to(src_old)
                if not dst.exists():
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(f, dst)
                    copied += 1
    return copied


def _carry_sources(work: Work, prev: Work, ref_ids: set) -> dict:
    """Bring forward source files and the manifest entries of references that both drafts cite."""
    copied = _copy_missing_sources(prev.p("sources"), work.p("sources"))
    old_man = prev.manifest().get("refs", {})
    man = work.manifest()
    man.setdefault("refs", {})
    kept_refs = 0
    for rid, e in old_man.items():
        if rid in ref_ids and rid not in man["refs"]:
            man["refs"][rid] = e
            kept_refs += 1
    jsave(work.p("manifest.json"), man)
    return {"source_files_copied": copied, "references_with_prior_sources": kept_refs}


def _still_present_claims(old_claims: list[dict], body: list[dict], ref_ids: set) -> list[dict]:
    """Copies of the old claims whose text still appears once in the body, without overlaps."""
    new_claims: list[dict] = []
    spans: dict[str, list[tuple[int, int]]] = {}
    for c in old_claims:
        p = _locate_once(c.get("text", ""), body)
        if not p:
            continue
        st = p["text"].find(c["text"])
        en = st + len(c["text"])
        if any(a < en and st < b for a, b in spans.get(p["id"], [])):
            continue
        spans.setdefault(p["id"], []).append((st, en))
        nc = dict(c)
        nc["para"] = p["id"]
        nc["cites"] = [r for r in c.get("cites", []) if r in ref_ids]
        new_claims.append(nc)
    return new_claims


def _carry_claims(
    work: Work, prev: Work, body: list[dict], allp: list[dict], ref_ids: set
) -> tuple[dict, dict]:
    """Write claims.json from the old claims still in the text; return (counts, old-to-new ids)."""
    old_claims = prev.claims()
    new_claims = _still_present_claims(old_claims, body, ref_ids)
    order = {p["id"]: i for i, p in enumerate(allp)}
    new_claims.sort(
        key=lambda c: (
            order[c["para"]],
            next(p for p in body if p["id"] == c["para"])["text"].find(c["text"]),
        )
    )
    id_map = {}
    for i, c in enumerate(new_claims, 1):
        id_map[c["id"]] = f"c{i}"
        c["id"] = f"c{i}"
    jsave(work.p("claims.json"), new_claims)
    counts = {
        "claims_carried": len(new_claims),
        "claims_not_found": len(old_claims) - len(new_claims),
    }
    return counts, id_map


def _carry_verdicts(work: Work, prev: Work, id_map: dict, ref_ids: set) -> dict:
    """Keep old verdicts whose claim was carried and whose source text is unchanged."""
    man_refs = work.manifest().get("refs", {})
    items = []
    for v in prev.verdicts().get("items", []):
        nid = id_map.get(v["claim_id"])
        if not nid or v["ref_id"] not in ref_ids:
            continue
        if v.get("evidence_hash") != (man_refs.get(v["ref_id"]) or {}).get("evidence_hash"):
            continue
        nv = dict(v)
        nv["claim_id"] = nid
        items.append(nv)
    jsave(work.p("verdicts.json"), {"items": items})
    return {"verdicts_carried": len(items)}


def _carry_suggestions(
    work: Work, prev: Work, allp: list[dict], id_map: dict, ref_ids: set
) -> dict:
    """Keep old suggestions whose original text still appears once; count those already applied."""
    carried, applied = [], 0
    for sg in prev.suggestions():
        sug = sg.get("suggested") or ""
        p = _locate_once(sg.get("original", ""), allp)
        if not p:
            if sug and any(sug in q["text"] for q in allp):
                applied += 1
            continue
        nsg = dict(sg)
        nsg["para"] = p["id"]
        nsg["claim_id"] = id_map.get(sg.get("claim_id")) if sg.get("claim_id") else None
        nsg["refs"] = [r for r in sg.get("refs", []) if r in ref_ids]
        carried.append(nsg)
    for i, sg in enumerate(carried, 1):
        sg["id"] = f"s{i}"
    jsave(work.p("suggestions.json"), carried)
    return {
        "suggestions_carried": len(carried),
        "suggestions_already_applied": applied,
        "suggestions_obsolete": len(prev.suggestions()) - len(carried) - applied,
    }


def carry_over(work: Work, prev: Work) -> dict:
    """Bring forward work from a check of an earlier draft of the same paper.

    - sources/ and manifest entries for reference ids present in both refs.json files;
    - claims whose exact text still appears once in a body paragraph (cites limited to current
      ref ids);
    - verdicts for those claims when the claim text and source text are unchanged (same hashes);
    - suggestions whose original text still appears once; a suggestion whose new text is already
      in the draft is counted as applied and dropped.
    Run after ingest and after writing refs.json; claims.json must not exist yet.
    """
    if work.p("claims.json").exists() and work.claims():
        raise SystemExit("claims.json already has claims; carry-over runs on a fresh workspace.")
    paper = work.paper()
    body = [p for p in paper["paragraphs"] if p["kind"] != "reference"]
    allp = paper["paragraphs"]
    ref_ids = {r["id"] for r in work.refs()}
    res = _carry_sources(work, prev, ref_ids)
    claim_counts, id_map = _carry_claims(work, prev, body, allp, ref_ids)
    res.update(claim_counts)
    # verdicts (only when claim text and evidence are unchanged)
    res.update(_carry_verdicts(work, prev, id_map, ref_ids))
    res.update(_carry_suggestions(work, prev, allp, id_map, ref_ids))
    return res
