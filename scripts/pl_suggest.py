"""Suggested edits for flagged claims: batches for the suggestion writers, merging what they
write, and the Word-comment text that explains each suggestion."""

from __future__ import annotations

import html
import re

from pl_report import KIND_LABEL, VERDICT_COLOR, VERDICT_LABEL, build_data
from pl_workspace import Work, jload, jsave

FLAGGED = ("partially_supported", "overstated", "unsupported", "contradicted")
_HEAD = "1F4E79"
_MUTED = "6B7280"


# --------------------------------------------------------------------------
# Batches for the suggestion-writer subagents
# --------------------------------------------------------------------------
def _excerpt_line(e: dict) -> str:
    """One evidence excerpt as a line of text, with the matched part inside [[ ]]."""
    return " ".join(
        x
        for x in (
            e.get("before"),
            "[[" + e.get("match", "") + "]]",
            e.get("after"),
        )
        if x
    )


def _suggest_task(c: dict, paras: dict) -> dict:
    """What a suggestion writer needs for one flagged claim: its text, verdicts and evidence."""
    return {
        "claim_id": c["id"],
        "para": c["para"],
        "claim_text": c["text"],
        "paragraph_text": paras[c["para"]]["text"],
        "verdict": c["verdict"],
        "sources": [
            {
                "ref_id": i["ref_id"],
                "citation": i["citation"],
                "verdict": i["verdict"],
                "analysis": i["analysis"],
                "evidence_level": i["evidence_level"],
                "excerpts": [_excerpt_line(e) for e in i.get("excerpts", [])],
            }
            for i in c["items"]
        ],
    }


def _write_suggest_batches(work: Work, todo: list[dict], batch_size: int) -> int:
    """Replace suggest_batches/*.json; output file names skip any already used in suggest_parts."""
    bdir = work.p("suggest_batches")
    bdir.mkdir(exist_ok=True)
    for f in bdir.glob("*.json"):
        f.unlink()
    pdir = work.p("suggest_parts")
    used = {f.stem for f in pdir.glob("suggest-*.json")} if pdir.is_dir() else set()
    n, k_out = 0, 0
    for k in range(0, len(todo), batch_size):
        n += 1
        k_out += 1
        while f"suggest-{k_out:03d}" in used:
            k_out += 1
        jsave(
            bdir / f"suggest-{n:03d}.json",
            {
                "claims": todo[k : k + batch_size],
                "output_file": f"suggest_parts/suggest-{k_out:03d}.json",
            },
        )
    return n


def suggest_prepare(work: Work, batch_size: int = 8) -> dict:
    """Write suggest_batches/*.json: flagged claims with verdicts and evidence, for suggestion
    writers."""
    data = build_data(work, with_suggestions=False)
    paras = {p["id"]: p for p in work.paper()["paragraphs"]}
    existing = {sg.get("claim_id") for sg in work.suggestions() if sg.get("claim_id")}
    todo = []
    for c in data["claims"]:
        if c["verdict"] not in FLAGGED or c["id"] in existing:
            continue
        todo.append(_suggest_task(c, paras))
    n = _write_suggest_batches(work, todo, batch_size)
    return {"flagged_claims": len(todo), "batches": n, "already_have_suggestions": len(existing)}


def merge_suggestions(work: Work, replace: bool = False) -> int:
    """Add the suggestions written to suggest_parts/*.json to suggestions.json, without repeats.

    With ``replace`` the list is rebuilt from the part files alone, so a suggestion that was
    edited or deleted in its part file does not linger from an earlier merge.
    """
    parts = (
        sorted(work.p("suggest_parts").glob("*.json")) if work.p("suggest_parts").is_dir() else []
    )
    cur = [] if replace else work.suggestions()
    have = {(sg.get("para"), sg.get("original")) for sg in cur}
    added = 0
    for f in parts:
        for sg in jload(f, []) or []:
            key = (sg.get("para"), sg.get("original"))
            if key in have:
                continue
            have.add(key)
            cur.append(sg)
            added += 1
    for i, sg in enumerate(cur, 1):
        sg["id"] = f"s{i}"
        sg.setdefault("refs", [])
        sg.setdefault("kind", "rewrite")
    jsave(work.p("suggestions.json"), cur)
    return added


# --------------------------------------------------------------------------
# Word-comment text for each suggestion
# --------------------------------------------------------------------------
def _clip(text: str, n: int) -> str:
    """Collapse white space and cut the text to about ``n`` characters at a word break."""
    text = re.sub(r"\s+", " ", text or "").strip()
    return text if len(text) <= n else text[: n - 1].rsplit(" ", 1)[0] + "\u2026"


def _title(text: str) -> list:
    """A section title paragraph."""
    return [{"t": text, "b": True, "color": _HEAD}]


def _verdict_run(v: str, extra: str = "") -> dict:
    """A verdict label in its colour."""
    return {
        "t": VERDICT_LABEL.get(v, v) + extra,
        "b": True,
        "color": VERDICT_COLOR.get(v, _MUTED),
    }


def _link_para(link: str) -> list:
    """A "Link: <address>" paragraph."""
    return [
        {"t": "Link: ", "b": True, "color": _HEAD},
        {"t": link, "color": "1D4ED8"},
    ]


def _excerpt_runs(ex: dict, excerpt_chars: int) -> list:
    """One evidence excerpt as runs: a little context, the matched text in bold, more context."""
    before = ex.get("before", "")
    if len(before) > 220:
        before = before[-220:]
        before = before[before.find(" ") + 1 :] if " " in before else before
    before = _clip(before, 240)
    after = _clip(ex.get("after", "")[:220], 240)
    match = _clip(ex.get("match", ""), excerpt_chars)
    runs = []
    if before:
        runs.append(
            {
                "t": "\u2026" + before.lstrip("\u2026") + " ",
                "i": True,
                "color": _MUTED,
            }
        )
    runs.append({"t": "\u201c" + match + "\u201d", "i": True, "b": True})
    if after:
        runs.append(
            {
                "t": " " + after.rstrip("\u2026") + "\u2026",
                "i": True,
                "color": _MUTED,
            }
        )
    return runs


def _excerpt_paras(it: dict, blank: list, max_excerpts: int, excerpt_chars: int) -> list:
    """Titled paragraphs for the first few evidence excerpts of one source."""
    paras: list = []
    exs = (it.get("excerpts") or [])[:max_excerpts]
    for k, ex in enumerate(exs):
        label = (
            "Evidence"
            + (f" {k + 1} of {len(exs)}" if len(exs) > 1 else "")
            + (f" (p. {ex['page']})" if ex.get("page") else "")
        )
        paras += [blank, _title(label), _excerpt_runs(ex, excerpt_chars)]
    return paras


def _source_paras(it: dict, blank: list, max_excerpts: int, excerpt_chars: int) -> list:
    """Paragraphs for one cited source: citation, verdict, analysis, evidence and link."""
    no_src = "no_source" in (it.get("flags") or [])
    conf = (
        f" \u00b7 {round(it['confidence'] * 100)}% confidence"
        if it.get("confidence") is not None and not no_src
        else ""
    )
    level_notes: dict[str | None, str] = {
        "abstract": " \u00b7 abstract only, so provisional until the full text is read",
        "none": " \u00b7 no source text",
    }
    level = level_notes.get(it.get("evidence_level"), "")
    paras = [
        blank,
        _title("Source"),
        [{"t": html.unescape(it["citation"]), "b": True}],
        [_verdict_run(it["verdict"]), {"t": conf + level, "color": _MUTED}],
    ]
    if it.get("analysis"):
        paras += [blank, _title("Analysis"), [{"t": _clip(it["analysis"], 900)}]]
    paras += _excerpt_paras(it, blank, max_excerpts, excerpt_chars)
    if it.get("link"):
        paras += [blank, _link_para(it["link"])]
    return paras


def _reference_paras(sg: dict, refs: dict, blank: list) -> list:
    """Paragraphs for a suggestion with no claim: each of its references as written, with links."""
    paras: list = []
    for rid in sg.get("refs", []):
        r = refs.get(rid)
        if not r:
            continue
        raw = r.get("raw") or r["citation"]
        paras += [blank, _title("Reference as written"), [{"t": raw}]]
        if r.get("link") and r["link"] not in raw:
            paras += [blank, _link_para(r["link"])]
    return paras


def suggestion_notes(work: Work, max_excerpts: int = 2, excerpt_chars: int = 700) -> dict:
    """Word-comment content for each suggestion, mirroring the report card.

    Returns {sid: [paragraph, ...]} where a paragraph is a list of runs (see pl_docx._comment_el):
    a title line, then titled sections separated by blank lines, with verdicts in colour.
    """
    data = build_data(work)
    claims = {c["id"]: c for c in data["claims"]}
    refs = {r["id"]: r for r in data["refs"]}
    blank: list = []

    out = {}
    for sg in data.get("suggestions", []):
        c = claims.get(sg.get("claim_id"))
        paras: list = []
        head = [
            {
                "t": f"Athetize \u00b7 {KIND_LABEL.get(sg['kind'], sg['kind'])}",
                "b": True,
                "size": 22,
            },
            {"t": f"  ({sg['id']})", "color": _MUTED},
        ]
        paras.append(head)
        if c:
            paras.append([{"t": "Claim verdict: ", "color": _MUTED}, _verdict_run(c["verdict"])])
        if sg.get("rationale"):
            paras += [blank, _title("Why this edit"), [{"t": sg["rationale"]}]]
        if c:
            for it in c["items"]:
                paras += _source_paras(it, blank, max_excerpts, excerpt_chars)
        else:
            paras += _reference_paras(sg, refs, blank)
        paras += [
            blank,
            [
                {
                    "t": "Screening aid: check the source before accepting this change.",
                    "i": True,
                    "color": _MUTED,
                }
            ],
        ]
        out[sg["id"]] = paras
    return out
