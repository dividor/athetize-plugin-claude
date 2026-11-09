"""Pure helpers for matching a reference to a metadata record: DOI, author and title checks.

Nothing here touches the network or the file system.
"""

from __future__ import annotations

import html
import re

import pl_text


def normalize_doi(doi: str | None) -> str | None:
    """Bare lower-case DOI from a DOI, a doi.org link or a 'doi:' string, or None."""
    if not doi:
        return None
    doi = doi.strip()
    doi = re.sub(r"^(https?://(dx\.)?doi\.org/|doi:\s*)", "", doi, flags=re.I)
    m = re.search(r"10\.\d{4,9}/\S+", doi)
    return m.group(0).rstrip(".,;)").lower() if m else None


def doi_slug(doi: str) -> str:
    """DOI made safe for use as a file name."""
    return re.sub(r"[^A-Za-z0-9._-]+", "_", doi)


def surname(author: str) -> str:
    """Case-folded surname from 'Surname, I.' or 'Given Surname'."""
    author = author.strip()
    if "," in author:
        return author.split(",")[0].strip().casefold()
    return author.split()[-1].strip().casefold() if author.split() else ""


def abstract_from_inverted(inv: dict | None) -> str:
    """Rebuild an abstract from OpenAlex's word-to-positions index."""
    if not inv:
        return ""
    pos = {}
    for word, idxs in inv.items():
        for i in idxs:
            pos[i] = word
    return " ".join(pos[i] for i in sorted(pos))


def strip_tags(s: str) -> str:
    """Remove markup tags and collapse whitespace."""
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s or "")).strip()


def title_in_text(title: str | None, text: str, window: int = 8000) -> bool:
    """True when most distinctive words of the title appear near the start of the text."""
    words = [w for w in re.findall(r"[a-z]{4,}", (title or "").casefold())]
    if not words:
        return True
    head = text[:window].casefold()
    hits = sum(1 for w in words if w in head)
    return hits / len(words) >= 0.75


def eric_id(ref: dict) -> str | None:
    """ERIC document number (ED... or EJ...) printed in the reference, or None."""
    blob = " ".join(str(ref.get(k) or "") for k in ("url", "raw"))
    m = re.search(r"\b(E[DJ]\d{5,7})\b", blob)
    return m.group(1) if m else None


def _fold(x: str) -> str:
    """Case-fold and turn every kind of hyphen or dash into a plain hyphen."""
    return re.sub(r"[‐-―−]", "-", x).casefold()


def _author_segment(ref: dict) -> str:
    """The part of the raw reference before the year, where the authors are listed."""
    raw = ref.get("raw") or ""
    m = re.match(r"(.*?)\(\s*(?:\d{4}|n\.d\.)", raw)
    return _fold(m.group(1) if m else raw[:120])


def first_surname(ref: dict) -> str:
    """First author's surname from refs.json authors, or the raw text when it reads 'Surname, I.'.

    Corporate authors (no 'Surname, Initials' pattern) return '' so they are never penalised.
    """
    if ref.get("authors"):
        return _fold(surname(ref["authors"][0]))
    m = re.match(r"\s*([^,(.]+?),\s*(?:[A-Z]\.|[A-Z][a-z]+\s)", ref.get("raw") or "")
    return _fold(m.group(1).strip()) if m else ""


def author_mismatch(ref: dict, cand: dict) -> bool:
    """True when the record's authors do not include the reference's first author."""
    want = first_surname(ref)
    have = [_fold(a) for a in (cand.get("authors") or []) if a]
    if not want or not have:
        return False
    names = set(have) | {_fold(surname(a)) for a in have}
    if want in names or any(want in n or n in want for n in names if len(n) > 2):
        return False
    seg = _author_segment(ref)
    overlap = sum(1 for n in names if len(n) > 2 and re.search(r"\b" + re.escape(n) + r"\b", seg))
    return overlap < 2


def score_candidate(ref: dict, cand: dict) -> float:
    """Title similarity between reference and record, lowered for author or year disagreement."""
    sim = pl_text.title_similarity(ref.get("title") or "", cand.get("title") or "")
    if author_mismatch(ref, cand):
        # A different first author usually means a review, chapter or commentary about the work.
        sim = min(sim - 0.08, 0.7)
    if ref.get("year") and cand.get("year"):
        try:
            if abs(int(ref["year"]) - int(cand["year"])) > 1:
                sim -= 0.08
        except (TypeError, ValueError):
            pass
    return max(0.0, sim)


def _who_from_raw(raw: str) -> str:
    """Author wording for a short citation, read from the start of the raw reference text."""
    m = re.match(r"\s*(.+?)\s*\(\d{4}|\s*(.+?)\s*\(n\.d\.\)", raw)
    if not m:
        return ""
    names = (m.group(1) or m.group(2) or "").strip().rstrip(".,")
    parts = [x.strip() for x in re.split(r",\s*(?:&\s*)?|\s&\s", names) if x.strip()]
    surnames = [x for x in parts if not re.fullmatch(r"(?:[A-Z]\.?\s*-?)+", x)]
    if len(surnames) == 2:
        return f"{surnames[0]} & {surnames[1]}"
    if surnames:
        return surnames[0] + (" et al." if len(surnames) > 2 else "")
    if names and not re.search(r",", names):
        return names  # corporate author, e.g. CAST
    return ""


def _who(ref: dict, base: dict) -> str:
    """Author wording for a short citation: the listed authors, else the raw reference text."""
    authors = base.get("authors") or ref.get("authors") or []
    first = authors[0] if authors else ""
    who = (first + (" et al." if len(authors) > 1 else "")).strip()
    if not who and ref.get("raw"):
        who = _who_from_raw(ref["raw"])
    return who


def citation_string(ref: dict, matched: dict | None) -> str:
    """Short citation.

    Uses the matched record only when the caller trusts it; otherwise the paper's own wording.
    """
    base = matched or {}
    who = _who(ref, base)
    year = base.get("year") or ref.get("year") or ""
    title = base.get("title") or ref.get("title") or ""
    venue = base.get("venue") or ref.get("venue") or ""
    title, venue = html.unescape(title or ""), html.unescape(venue or "")
    head = (who + (f" ({year})" if year else "")).strip()
    parts = [p for p in [head, title.rstrip("."), venue] if p]
    return ". ".join(parts) if parts else (ref.get("raw") or ref["id"])


def display_fields(ref: dict, exists: str | None, matched: dict | None, doi: str | None):
    """Citation, link and DOI to show. A doubtful match never replaces the paper's own reference."""
    ref_doi = normalize_doi(ref.get("doi"))
    if exists == "verified" and matched:
        d = matched.get("doi") or ref_doi or doi
        return citation_string(ref, matched), (f"https://doi.org/{d}" if d else ref.get("url")), d
    link = f"https://doi.org/{ref_doi}" if ref_doi else ref.get("url")
    return citation_string(ref, None), link, ref_doi


_EN_STOP = {
    "the",
    "of",
    "and",
    "to",
    "in",
    "a",
    "is",
    "that",
    "for",
    "with",
    "on",
    "as",
    "are",
    "this",
    "by",
}


def looks_english(text: str) -> bool:
    """True when enough common English words appear (short texts always pass)."""
    words = re.findall(r"[a-z]+", text.casefold())
    if len(words) < 20:
        return True
    return sum(1 for w in words if w in _EN_STOP) / len(words) >= 0.05


def abstract_matches(title: str | None, abstract: str) -> bool:
    """Reject abstracts attached to the wrong record.

    That is: another language, or none of the title's words at all.
    """
    if not looks_english(abstract):
        return False
    words = [w for w in re.findall(r"[a-z]{5,}", (title or "").casefold())]
    if not words:
        return True
    body = abstract.casefold()
    return any(re.search(r"\b" + re.escape(w), body) for w in words)
