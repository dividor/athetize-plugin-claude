"""Batches for the claim-verifier subagents, and checking the verdicts they send back."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import pl_text
from pl_workspace import Work, jload, jsave

VERDICTS = (
    "supported",
    "partially_supported",
    "overstated",
    "unsupported",
    "contradicted",
    "unverifiable",
)
NEEDS_QUOTE = {"supported", "partially_supported", "overstated", "contradicted"}


def _evidence_text(work: Work, entry: dict) -> str:
    """Text of the evidence file named by a manifest entry, or "" when there is none."""
    f = entry.get("evidence_file")
    if not f:
        return ""
    path = work.p(f)
    return path.read_text("utf-8") if path.exists() else ""


# --------------------------------------------------------------------------
# Checking verdicts from the subagents
# --------------------------------------------------------------------------
def _batch_index(batch: dict) -> dict:
    """(claim id, reference id) -> (claim, reference) for everything a batch asked about."""
    index = {}
    for c in batch["claims"]:
        for r in c["refs"]:
            index[(c["claim_id"], r["ref_id"])] = (c, r)
    return index


def _merge_raw(work: Work) -> int:
    """Merge subagent verdict files into verdicts.json (with quote verification)."""
    vdir = work.p("verdicts")
    bdir = work.p("batches")
    store = work.verdicts()
    items = {(i["claim_id"], i["ref_id"]): i for i in store.get("items", [])}
    manifest = work.manifest().get("refs", {})
    merged = 0
    for vf in sorted(vdir.glob("batch-*.json")):
        bf = bdir / vf.name
        batch = jload(bf)
        if not batch:
            continue
        index = _batch_index(batch)
        raw = jload(vf, [])
        if isinstance(raw, dict):
            raw = raw.get("verdicts", [])
        for v in raw:
            key = (v.get("claim_id"), v.get("ref_id"))
            if key not in index:
                continue
            claim, ref = index[key]
            entry = manifest.get(ref["ref_id"], {})
            items[key] = verify_verdict(work, v, claim, ref, entry)
            merged += 1
    store["items"] = list(items.values())
    store["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    jsave(work.p("verdicts.json"), store)
    return merged


def _confidence(v: dict) -> float:
    """The verifier's confidence as a number from 0 to 1; 0.5 when it is missing or unusable."""
    try:
        return max(0.0, min(1.0, float(v.get("confidence", 0.5))))
    except (TypeError, ValueError):
        return 0.5


def _limit_by_evidence_level(
    verdict: str, level: str, analysis: str, flags: list[str]
) -> tuple[str, str]:
    """Hold a verdict to what the available source text can show; return (verdict, analysis)."""
    if verdict not in VERDICTS:
        flags.append("invalid_verdict")
        verdict = "unverifiable"
    if level == "none":
        verdict = "unverifiable"
        flags.append("no_source")
    if level == "abstract":
        flags.append("abstract_only")
        if verdict == "unsupported":
            verdict = "unverifiable"
            flags.append("abstract_cannot_refute")
            analysis = (
                analysis + " The abstract alone cannot show that the full paper lacks support."
            ).strip()
    return verdict, analysis


def _quote_list(v: dict, quote: str) -> list[str]:
    """Up to four distinct quotes offered as evidence, the main one first."""
    quotes: list[str] = []
    raw_quotes = v.get("evidence_quotes")
    if isinstance(raw_quotes, list):
        quotes = [str(q).strip() for q in raw_quotes if str(q).strip()]
    if quote and quote not in quotes:
        quotes.insert(0, quote)
    return quotes[:4]


def _find_quotes(text: str, quotes: list[str]) -> tuple[list[dict], int]:
    """Locate each quote in the source text; return (places found, number not found)."""
    evidences: list[dict] = []
    missing = 0
    for q in quotes:
        loc = pl_text.locate_quote(text, q) if text else None
        if loc is None:
            missing += 1
            continue
        evidences.append(
            {
                "start": loc["start"],
                "end": loc["end"],
                "match": loc["mode"],
                "score": loc["score"],
                "page": pl_text.page_at(text, loc["start"]),
                "text": text[loc["start"] : loc["end"]]
                .replace("\f", " ")
                .replace("\n", " ")
                .strip(),
            }
        )
    return evidences, missing


def verify_verdict(work: Work, v: dict, claim: dict, ref: dict, entry: dict) -> dict:
    """Check one verifier verdict against the source text and return the stored verdict record.

    A verdict that needs a quote is downgraded to "unverifiable" when none of its quotes can be
    found in the source.
    """
    verdict = str(v.get("verdict", "")).strip().lower().replace(" ", "_")
    flags: list[str] = []
    analysis = str(v.get("analysis", "")).strip()
    quote = str(v.get("evidence_quote", "")).strip()
    level = ref.get("evidence_level", "none")
    conf = _confidence(v)
    original = verdict
    evidence = None

    verdict, analysis = _limit_by_evidence_level(verdict, level, analysis, flags)

    quotes = _quote_list(v, quote)
    evidences: list[dict] = []
    if verdict in NEEDS_QUOTE:
        evidences, missing = _find_quotes(_evidence_text(work, entry), quotes)
        if not evidences:
            flags.append("quote_not_found")
            analysis = (
                f"{analysis} [Downgraded from '{original}': the quoted evidence could not be"
                " found in the source text.]"
            ).strip()
            verdict = "unverifiable"
        elif missing:
            flags.append("some_quotes_not_found")
        evidences.sort(key=lambda e: e["start"])
        evidence = evidences[0] if evidences else None
    return {
        "claim_id": claim["claim_id"],
        "ref_id": ref["ref_id"],
        "verdict": verdict,
        "original_verdict": original,
        "confidence": conf,
        "analysis": analysis,
        "evidence_quote": quote,
        "evidence_quotes": quotes,
        "evidence": evidence,
        "evidences": evidences,
        "evidence_level": level,
        "evidence_hash": ref.get("evidence_hash"),
        "claim_hash": claim.get("claim_hash"),
        "flags": flags,
    }


def verify(work: Work) -> dict:
    """Merge finished verdict files and count the stored verdicts by outcome."""
    merged = _merge_raw(work)
    items = work.verdicts().get("items", [])
    counts: dict[str, int] = {}
    downgraded = 0
    for i in items:
        counts[i["verdict"]] = counts.get(i["verdict"], 0) + 1
        if i.get("original_verdict") != i["verdict"]:
            downgraded += 1
    return {"merged_now": merged, "by_verdict": counts, "downgraded_by_checks": downgraded}


# --------------------------------------------------------------------------
# Batches for the claim-verifier subagents
# --------------------------------------------------------------------------
@dataclass
class _Prep:
    """What ``prepare`` knows while it decides which claim and reference pairs still need work."""

    work: Work
    manifest: dict
    refs: dict
    done: dict
    k_passages: int
    texts: dict[str, str] = field(default_factory=dict)
    auto: int = 0


def _no_source_verdict(claim_id: str, rid: str, ehash: str, chash: str) -> dict:
    """The verdict stored without asking a verifier when a reference has no source text."""
    return {
        "claim_id": claim_id,
        "ref_id": rid,
        "verdict": "unverifiable",
        "original_verdict": "unverifiable",
        "confidence": 1.0,
        "analysis": (
            "No source text is available for this reference, so the claim could not be checked."
        ),
        "evidence_quote": "",
        "evidence": None,
        "evidence_level": "none",
        "evidence_hash": ehash,
        "claim_hash": chash,
        "flags": ["no_source"],
    }


def _ref_task(prep: _Prep, c: dict, chash: str, rid: str) -> dict | None:
    """What a verifier needs to check a claim against one reference; None if nothing to do."""
    entry = prep.manifest.get(rid)
    if entry is None:
        return None  # not resolved yet; resolve first
    ehash = entry.get("evidence_hash", "none")
    prev = prep.done.get((c["id"], rid))
    if prev and prev.get("evidence_hash") == ehash and prev.get("claim_hash") == chash:
        return None
    level = entry.get("evidence_level", "none")
    if level == "none":
        prep.done[(c["id"], rid)] = _no_source_verdict(c["id"], rid, ehash, chash)
        prep.auto += 1
        return None
    if rid not in prep.texts:
        prep.texts[rid] = _evidence_text(prep.work, entry)
    passages = pl_text.bm25_rank(prep.texts[rid], c["text"], k=prep.k_passages)
    return {
        "ref_id": rid,
        "citation": entry.get("citation") or prep.refs.get(rid, {}).get("raw", rid),
        "evidence_level": level,
        "evidence_hash": ehash,
        "source_chars": entry.get("evidence_chars"),
        "passages": [{"rank": p["rank"], "page": p["page"], "text": p["text"]} for p in passages],
    }


def _pending_claims(prep: _Prep, claims: list[dict], paras: dict) -> list[dict]:
    """Batch entries for the claims that still have at least one reference to check."""
    pending_claims = []
    for c in claims:
        if not c.get("checkworthy", True) or not c.get("cites"):
            continue
        chash = pl_text.short_hash(c["text"])
        todo_refs = []
        for rid in c["cites"]:
            task = _ref_task(prep, c, chash, rid)
            if task is not None:
                todo_refs.append(task)
        if todo_refs:
            pending_claims.append(
                {
                    "claim_id": c["id"],
                    "claim_text": c["text"],
                    "claim_hash": chash,
                    "type": c.get("type", "empirical"),
                    "paragraph_context": paras[c["para"]]["text"],
                    "refs": todo_refs,
                }
            )
    return pending_claims


def _write_batches(work: Work, pending_claims: list[dict], batch_size: int) -> int:
    """Replace the old batch and verdict files with fresh batch files; return how many."""
    bdir, vdir = work.p("batches"), work.p("verdicts")
    for d in (bdir, vdir):
        d.mkdir(exist_ok=True)
        for f in d.glob("batch-*.json"):
            f.unlink()
    n_batches = 0
    for i in range(0, len(pending_claims), batch_size):
        n_batches += 1
        name = f"batch-{n_batches:03d}.json"
        jsave(
            bdir / name,
            {"claims": pending_claims[i : i + batch_size], "output_file": f"verdicts/{name}"},
        )
    return n_batches


def prepare(work: Work, batch_size: int = 6, k_passages: int = 5):
    """Write batch files for claims that still need an LLM verdict."""
    _merge_raw(work)  # keep any finished work
    claims = work.claims()
    paper = work.paper()
    paras = {p["id"]: p for p in paper["paragraphs"]}
    manifest = work.manifest().get("refs", {})
    refs = {r["id"]: r for r in work.refs()}
    store = work.verdicts()
    done = {(i["claim_id"], i["ref_id"]): i for i in store.get("items", [])}
    prep = _Prep(work=work, manifest=manifest, refs=refs, done=done, k_passages=k_passages)

    pending_claims = _pending_claims(prep, claims, paras)

    store["items"] = list(done.values())
    jsave(work.p("verdicts.json"), store)

    n_batches = _write_batches(work, pending_claims, batch_size)
    return {
        "pending_claims": len(pending_claims),
        "batches": n_batches,
        "auto_unverifiable": prep.auto,
    }
