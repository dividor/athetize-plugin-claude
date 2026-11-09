"""Metadata lookup for one reference: Crossref and OpenAlex records turned into candidates."""

from __future__ import annotations

import urllib.parse

import pl_http
from pl_refmatch import abstract_from_inverted, normalize_doi, score_candidate, strip_tags, surname

_RETRACTION_TYPES = ("retraction", "withdrawal", "removal", "expression_of_concern")
_OPENALEX_EXTRA_KEYS = ("is_retracted", "pdf_urls", "pmcid", "oa_url", "openalex_id")


def candidate_from_crossref(msg: dict) -> dict:
    """Candidate record built from one Crossref work."""
    title = (msg.get("title") or [""])[0]
    authors = [f"{a.get('family', '')}".strip() for a in msg.get("author", []) if a.get("family")]
    issued = (msg.get("issued", {}).get("date-parts") or [[None]])[0][0]
    venue = (msg.get("container-title") or [""])[0]
    retracted = []
    for upd in msg.get("updated-by", []) or []:
        if str(upd.get("type", "")).lower() in _RETRACTION_TYPES:
            retracted.append(str(upd.get("type")))
    return {
        "source": "crossref",
        "title": title,
        "authors": authors,
        "year": issued,
        "venue": venue,
        "doi": normalize_doi(msg.get("DOI")),
        "abstract": strip_tags(msg.get("abstract", "")),
        "retraction_notes": retracted,
    }


def _openalex_authors(w: dict) -> list[str]:
    """Last word of each author's display name in an OpenAlex work."""
    authors = []
    for a in w.get("authorships", []) or []:
        name = (a.get("author") or {}).get("display_name") or ""
        if name:
            authors.append(name.split()[-1])
    return authors


def _openalex_pdf_urls(w: dict) -> list[str]:
    """PDF links from every location of an OpenAlex work, best first, without repeats."""
    pdfs = []
    for location in [w.get("best_oa_location"), w.get("primary_location")] + list(
        w.get("locations") or []
    ):
        if location and location.get("pdf_url"):
            pdfs.append(location["pdf_url"])
    return list(dict.fromkeys(pdfs))


def candidate_from_openalex(w: dict) -> dict:
    """Candidate record built from one OpenAlex work."""
    authors = _openalex_authors(w)
    loc = w.get("primary_location") or {}
    venue = ((loc.get("source") or {}).get("display_name")) or ""
    pdf_urls = _openalex_pdf_urls(w)
    ids = w.get("ids") or {}
    return {
        "source": "openalex",
        "title": w.get("title") or w.get("display_name") or "",
        "authors": authors,
        "year": w.get("publication_year"),
        "venue": venue,
        "doi": normalize_doi(w.get("doi")),
        "abstract": abstract_from_inverted(w.get("abstract_inverted_index")),
        "is_retracted": bool(w.get("is_retracted")),
        "pdf_urls": pdf_urls,
        "pmcid": (ids.get("pmcid") or "").rsplit("/", 1)[-1] or None,
        "oa_url": (w.get("open_access") or {}).get("oa_url"),
        "openalex_id": w.get("id"),
    }


def _mailto(email: str | None, sep: str) -> str:
    """Query-string piece that tells the service who is asking, or '' without an email."""
    return f"{sep}mailto={urllib.parse.quote(email)}" if email else ""


def _merge_openalex(cand: dict, oa: dict) -> None:
    """Copy retraction and open-access fields (and a missing abstract) from OpenAlex."""
    cand.update({k: v for k, v in oa.items() if k in _OPENALEX_EXTRA_KEYS})
    if not cand.get("abstract"):
        cand["abstract"] = oa.get("abstract", "")


def _crossref_by_doi(doi: str, email: str | None, errors: list[str]) -> dict | None:
    """Crossref record for a DOI, or None when the lookup fails."""
    try:
        msg = pl_http.http_json(
            f"https://api.crossref.org/works/{urllib.parse.quote(doi, safe='/')}"
            + _mailto(email, "?"),
            email,
        )["message"]
        return candidate_from_crossref(msg)
    except pl_http.HttpError as e:
        errors.append(f"crossref doi lookup: {e}")
    return None


def _openalex_by_doi(
    doi: str, email: str | None, errors: list[str], cand: dict | None
) -> dict | None:
    """OpenAlex record for a DOI: used alone, or merged into the Crossref candidate."""
    try:
        w = pl_http.http_json(
            f"https://api.openalex.org/works/doi:{urllib.parse.quote(doi, safe='/')}"
            + _mailto(email, "?"),
            email,
        )
        oa = candidate_from_openalex(w)
        if cand is None:
            cand = oa
        else:
            _merge_openalex(cand, oa)
    except pl_http.HttpError as e:
        errors.append(f"openalex doi lookup: {e}")
    return cand


def _metadata_by_doi(ref: dict, doi: str, email: str | None, errors: list[str]):
    """Look the DOI up in Crossref, then OpenAlex. Returns (candidate, similarity)."""
    cand = _crossref_by_doi(doi, email, errors)
    cand = _openalex_by_doi(doi, email, errors, cand)
    if cand is not None:
        cand["doi"] = doi
        return cand, (score_candidate(ref, cand) if ref.get("title") else None)
    return None, None


def _crossref_search(
    ref: dict, title: str, first_author: str, email: str | None, errors: list[str]
) -> tuple[dict | None, float]:
    """Best-scoring Crossref search result for the title and first author."""
    best: dict | None = None
    best_score = 0.0
    try:
        q = urllib.parse.urlencode(
            {
                "query.bibliographic": f"{title} {first_author}".strip(),
                "rows": 4,
                "select": "DOI,title,author,issued,container-title,abstract,updated-by",
            }
        )
        items = pl_http.http_json(
            "https://api.crossref.org/works?" + q + _mailto(email, "&"),
            email,
        )["message"]["items"]
        for it in items:
            c = candidate_from_crossref(it)
            s = score_candidate(ref, c)
            if s > best_score:
                best, best_score = c, s
    except (pl_http.HttpError, KeyError) as e:
        errors.append(f"crossref search: {e}")
    return best, best_score


def _openalex_search(
    ref: dict,
    title: str,
    email: str | None,
    errors: list[str],
    best: dict | None,
    best_score: float,
) -> tuple[dict | None, float]:
    """Replace the best candidate so far with any better-scoring OpenAlex search result."""
    try:
        q = urllib.parse.urlencode({"search": title, "per-page": 4})
        data = pl_http.http_json(
            "https://api.openalex.org/works?" + q + _mailto(email, "&"),
            email,
        )
        for w in data.get("results", []):
            c = candidate_from_openalex(w)
            s = score_candidate(ref, c)
            if s > best_score:
                best, best_score = c, s
    except (pl_http.HttpError, KeyError) as e:
        errors.append(f"openalex search: {e}")
    return best, best_score


def _openalex_enrich(best: dict, email: str | None, errors: list[str]) -> None:
    """Add OpenAlex data (retraction flag, open-access links) to a good Crossref match."""
    if not best.get("doi"):
        return
    try:
        w = pl_http.http_json(
            f"https://api.openalex.org/works/doi:{urllib.parse.quote(best['doi'], safe='/')}",
            email,
        )
        _merge_openalex(best, candidate_from_openalex(w))
    except pl_http.HttpError as e:
        errors.append(f"openalex enrich: {e}")


def _metadata_by_title(ref: dict, title: str, email: str | None, errors: list[str]):
    """Search Crossref, then OpenAlex, by title. Returns (candidate, similarity)."""
    first_author = surname(ref["authors"][0]) if ref.get("authors") else ""
    best, best_score = _crossref_search(ref, title, first_author, email, errors)
    if best_score < 0.85:
        best, best_score = _openalex_search(ref, title, email, errors, best, best_score)
    elif best is not None:
        _openalex_enrich(best, email, errors)
    return (best, best_score) if best is not None else (None, None)


def resolve_metadata(ref: dict, email: str | None, errors: list[str]):
    """Return (best_candidate, similarity) using the DOI first, then title search."""
    doi = normalize_doi(ref.get("doi"))
    if doi:
        return _metadata_by_doi(ref, doi, email, errors)
    title = ref.get("title") or ""
    if not title and ref.get("raw"):
        title = ref["raw"][:200]
    if not title:
        return None, None
    return _metadata_by_title(ref, title, email, errors)
