"""Passages for athetize: context around a quote, sentence-snapped chunks and BM25 ranking.

Standard library only.
"""

from __future__ import annotations

import math
import re

from pl_quote import tokenize


def page_at(text: str, offset: int):
    """1-based page number for a character offset, or None if no page breaks."""
    if "\f" not in text:
        return None
    return text.count("\f", 0, offset) + 1


# --------------------------------------------------------------------------
# Passage retrieval (BM25)
# --------------------------------------------------------------------------
_STOP = set(
    "a an and are as at be been but by for from has have in into is it its of on or that the "
    "their these this those to was were which with we our not than then also can may such using "
    "used use".split()
)


_SENT_END = re.compile(r"(?<=[.!?])[\"\u201d\u2019)\]]*\s+(?=[A-Z\u201c\"(\[0-9])")


def _trim_before(pre: str) -> str:
    """Cut the text before a quote so it starts at a sentence start (or mark it as cut)."""
    cuts = [m.end() for m in _SENT_END.finditer(pre)]
    if cuts:
        return pre[cuts[0] :]
    sp = pre.find(" ")
    return ("\u2026" + pre[sp + 1 :]) if sp >= 0 else pre


def _trim_after(post: str) -> str:
    """Cut the text after a quote so it ends at a sentence end (or mark it as cut)."""
    cuts = [m.start() for m in _SENT_END.finditer(post)]
    if cuts:
        return post[: cuts[-1] + 1]
    sp = post.rfind(" ")
    return (post[:sp] + "\u2026") if sp > 0 else post


def _one_line(x: str) -> str:
    """Collapse page breaks and runs of whitespace into single spaces."""
    return re.sub(r"\s+", " ", x.replace("\f", " ")).strip()


def context_window(text: str, start: int, end: int, before: int = 420, after: int = 420) -> dict:
    """Expand a located quote to whole sentences around it, within the given character budgets.

    Returns {"before", "match", "after"} with whitespace normalised. Everything comes from the
    source text itself, so the extra context needs no separate verification.
    """
    lo = max(0, start - before)
    hi = min(len(text), end + after)
    pre = text[lo:start]
    post = text[end:hi]
    if lo > 0:
        pre = _trim_before(pre)
    if hi < len(text):
        post = _trim_after(post)
    return {"before": _one_line(pre), "match": _one_line(text[start:end]), "after": _one_line(post)}


def _snap_start(text: str, s: int) -> int:
    """Move a passage start back to a sentence start (within 160 chars), if there is one."""
    back = text.rfind(". ", max(0, s - 160), s)
    back_nl = text.rfind("\n", max(0, s - 160), s)
    snap = max(back + 2 if back >= 0 else -1, back_nl + 1 if back_nl >= 0 else -1)
    return snap if snap >= 0 else s


def _snap_end(text: str, e: int) -> int:
    """Move a passage end forward to a sentence end (within 160 chars), if there is one."""
    fwd = text.find(". ", e, e + 160)
    return fwd + 1 if fwd >= 0 else e


def chunk_passages(text: str, window: int = 110, stride: int = 55):
    """Split text into overlapping passages snapped to sentence boundaries."""
    toks = tokenize(text)
    out: list[dict] = []
    if not toks:
        return out
    n = len(toks)
    i = 0
    while True:
        j = min(n, i + window)
        s = _snap_start(text, toks[i][1])
        e = _snap_end(text, toks[j - 1][2])
        out.append({"start": s, "end": e})
        if j >= n:
            break
        i += stride
    return out


def _passage_terms(text: str, passages: list[dict]) -> list[list[str]]:
    """Fill in each passage's text and return its search terms (stop words removed)."""
    docs = []
    for p in passages:
        p["text"] = text[p["start"] : p["end"]].replace("\f", " ").strip()
        docs.append([t for t, _, _ in tokenize(p["text"]) if t not in _STOP])
    return docs


def _doc_frequencies(docs: list[list[str]]) -> dict[str, int]:
    """Count how many passages contain each term."""
    df: dict[str, int] = {}
    for d in docs:
        for t in set(d):
            df[t] = df.get(t, 0) + 1
    return df


def _bm25_score(q_terms: list[str], d: list[str], df: dict[str, int], n_docs: int, avgdl: float):
    """BM25 score of one passage's terms `d` against the query terms."""
    k1, b = 1.5, 0.75
    tf: dict[str, int] = {}
    for t in d:
        tf[t] = tf.get(t, 0) + 1
    score = 0.0
    for t in set(q_terms):
        if t not in tf:
            continue
        idf = math.log(1 + (n_docs - df[t] + 0.5) / (df[t] + 0.5))
        score += idf * tf[t] * (k1 + 1) / (tf[t] + k1 * (1 - b + b * len(d) / max(1.0, avgdl)))
    return score


def _score_passages(passages: list[dict], docs: list[list[str]], query: str) -> list[tuple]:
    """Score every passage against the query; shared numbers add a bonus. Best first."""
    q_terms = [t for t, _, _ in tokenize(query) if t not in _STOP]
    nums = {t for t in q_terms if any(c.isdigit() for c in t)}
    n_docs = len(docs)
    avgdl = sum(len(d) for d in docs) / max(1, n_docs)
    df = _doc_frequencies(docs)
    scored = []
    for p, d in zip(passages, docs):
        score = _bm25_score(q_terms, d, df, n_docs, avgdl)
        score += 0.75 * len(nums & set(d))
        scored.append((score, p))
    scored.sort(key=lambda x: -x[0])
    return scored


def _overlaps_chosen(p: dict, chosen: list[dict]) -> bool:
    """True when more than half of passage `p` is already covered by a chosen passage."""
    return any(
        min(p["end"], c["end"]) - max(p["start"], c["start"]) > 0.5 * (p["end"] - p["start"])
        for c in chosen
    )


def bm25_rank(text: str, query: str, k: int = 5, max_chars: int = 1400):
    passages = chunk_passages(text)
    if not passages:
        return []
    docs = _passage_terms(text, passages)
    scored = _score_passages(passages, docs, query)
    chosen: list[dict] = []
    for score, p in scored:
        if score <= 0 and chosen:
            break
        if _overlaps_chosen(p, chosen):
            continue
        item = dict(p)
        if len(item["text"]) > max_chars:
            item["text"] = item["text"][:max_chars].rsplit(" ", 1)[0] + " …"
        item["score"] = round(score, 3)
        item["page"] = page_at(text, p["start"])
        chosen.append(item)
        if len(chosen) >= k:
            break
    for rank, c in enumerate(chosen, 1):
        c["rank"] = rank
    return chosen
