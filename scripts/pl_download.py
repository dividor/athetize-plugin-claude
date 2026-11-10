"""Open-access full text for one reference: gather legal links, fetch them, check the result.

Looks in arXiv, OpenAlex, Unpaywall, Semantic Scholar, ERIC, Europe PMC and any URL printed
in the reference, over plain HTTP. Nothing here drives a browser: a page that needs one is
left for the person, or for Claude's own browser tools through the fetch-sources skill.
"""

from __future__ import annotations

import re
import urllib.parse
from dataclasses import dataclass
from pathlib import Path

import pl_http
import pl_text
from pl_refmatch import eric_id, normalize_doi, strip_tags, title_in_text

PREVIEW_MARKERS = re.compile(
    r"preview of subscription content|access this article|log in via an institution|"
    r"buy article pdf|"
    r"purchase (?:this )?article|rent this article|get full access|"
    r"subscribe to (?:this )?journal|"
    r"you do not (?:currently )?have access|check access|institutional login|"
    r"sign in to (?:access|view)",
    re.I,
)

# Link kinds whose web page may itself be the full text.
_HTML_ROUTES = ("reference-url", "unpaywall-page", "openalex-oa", "semanticscholar")


@dataclass
class _Job:
    """What one download attempt needs: the reference, where to save and where to log."""

    ref: dict
    dest_dir: Path
    title: str | None
    email: str | None
    errors: list[str]


def _eric_query(ref: dict, eid: str | None) -> str | None:
    """ERIC search string: by document number when known, else by exact title."""
    if eid:
        return f"id:{eid}"
    if ref.get("title"):
        return 'title:"' + re.sub(r'["\\]', " ", ref["title"])[:180] + '"'
    return None


def _eric_doc_matches(d: dict, eid: str | None, ref: dict) -> bool:
    """True when an ERIC search hit has permitted full text and is the reference asked for."""
    if not d.get("e_fulltextauth"):
        return False
    if eid:
        return d.get("id") == eid
    return pl_text.title_similarity(ref.get("title") or "", strip_tags(d.get("title", ""))) >= 0.9


def eric_fulltext_urls(ref: dict, email: str | None, errors: list[str]) -> list[str]:
    """ERIC hosts author-permitted full text for many education reports and articles."""
    ids = []
    eid = eric_id(ref)
    try:
        q = _eric_query(ref, eid)
        if q is None:
            return []
        data = pl_http.http_json(
            "https://api.ies.ed.gov/eric/?"
            + urllib.parse.urlencode(
                {"search": q, "format": "json", "fields": "id,title,e_fulltextauth", "rows": 3}
            ),
            email,
        )
        for d in data.get("response", {}).get("docs", []):
            if _eric_doc_matches(d, eid, ref):
                ids.append(d["id"])
    except (pl_http.HttpError, KeyError, ValueError) as e:
        errors.append(f"eric: {e}")
    return [f"https://files.eric.ed.gov/fulltext/{i}.pdf" for i in ids]


def semantic_scholar_pdf(doi: str | None, arxiv: str | None, errors: list[str]) -> list[str]:
    """Open-access PDF link from Semantic Scholar, as a list of zero or one URL."""
    key = f"DOI:{doi}" if doi else (f"ARXIV:{arxiv}" if arxiv else None)
    if not key:
        return []
    try:
        data = pl_http.http_json(
            "https://api.semanticscholar.org/graph/v1/paper/"
            f"{urllib.parse.quote(key, safe=':/')}?fields=openAccessPdf"
        )
        u = (data.get("openAccessPdf") or {}).get("url")
        return [u] if u else []
    except pl_http.HttpError as e:
        errors.append(f"semanticscholar: {e}")
    return []


def europe_pmc_lookup(doi: str | None, pmcid: str | None, email: str | None, errors: list[str]):
    """Return (pmcid, abstract) from Europe PMC if available."""
    try:
        query = f"DOI:{doi}" if doi else f"PMCID:{pmcid}"
        data = pl_http.http_json(
            "https://www.ebi.ac.uk/europepmc/webservices/rest/search?"
            + urllib.parse.urlencode(
                {"query": query, "format": "json", "resultType": "core", "pageSize": 1}
            ),
            email,
        )
        res = (data.get("resultList", {}).get("result") or [None])[0]
        if res:
            ok = res.get("isOpenAccess") == "Y"
            return (res.get("pmcid") if ok else None), strip_tags(res.get("abstractText", ""))
    except (pl_http.HttpError, KeyError) as e:
        errors.append(f"europepmc: {e}")
    return None, ""


def _accept_html(text: str, title: str | None, errors: list[str], how: str):
    """Cleaned page text when it is long enough, on topic and not a paywall page, else None."""
    text = pl_text.clean_extracted(text)
    if PREVIEW_MARKERS.search(text[:6000]) or PREVIEW_MARKERS.search(text[-4000:]):
        errors.append(f"{how}: page is a paywall or preview page, not full text")
        return None
    if len(text) < 12000:
        errors.append(f"{how}: page had too little text for full text ({len(text)} chars)")
        return None
    if not title_in_text(title, text):
        errors.append(f"{how}: page text does not match the reference title")
        return None
    return text


def _unpaywall_urls(doi: str | None, email: str | None, errors: list[str]) -> list[tuple[str, str]]:
    """Unpaywall links for a DOI: PDF links first, then landing pages. Needs a contact email."""
    urls: list[tuple[str, str]] = []
    if email and doi:
        try:
            up = pl_http.http_json(
                f"https://api.unpaywall.org/v2/{urllib.parse.quote(doi, safe='/')}"
                f"?email={urllib.parse.quote(email)}",
                email,
            )
            for loc in [up.get("best_oa_location")] + list(up.get("oa_locations") or []):
                if loc and loc.get("url_for_pdf"):
                    urls.append((loc["url_for_pdf"], "unpaywall"))
            for loc in [up.get("best_oa_location")] + list(up.get("oa_locations") or []):
                if loc and loc.get("url") and not loc.get("url_for_pdf"):
                    urls.append((loc["url"], "unpaywall-page"))
        except pl_http.HttpError as e:
            errors.append(f"unpaywall: {e}")
    elif doi and not email:
        errors.append(
            "unpaywall skipped: no contact email configured "
            "(run: pl.py config set-email you@example.org)"
        )
    return urls


def _gather_urls(
    job: _Job, cand: dict, doi: str | None, arxiv: str | None, trusted: bool
) -> list[tuple[str, str]]:
    """Every link worth trying, in the order they are tried, each with the route it came from."""
    ref = job.ref
    urls: list[tuple[str, str]] = []
    if arxiv:
        urls.append((f"https://arxiv.org/pdf/{arxiv}", "arxiv"))
    if trusted:
        for u in cand.get("pdf_urls") or []:
            urls.append((u, "openalex"))
    urls.extend(_unpaywall_urls(doi, job.email, job.errors))
    for u in semantic_scholar_pdf(doi, arxiv, job.errors):
        urls.append((u, "semanticscholar"))
    for u in eric_fulltext_urls(ref, job.email, job.errors):
        urls.append((u, "eric"))
    ref_url = ref.get("url")
    if ref_url and ref_url.startswith(("http://", "https://")) and "/buy/" not in ref_url:
        urls.append((ref_url, "reference-url"))
    if trusted and cand.get("oa_url"):
        urls.append((cand["oa_url"], "openalex-oa"))
    return urls


def _europe_pmc_fulltext(job: _Job, cand: dict, doi: str | None, trusted: bool):
    """Europe PMC full-text XML (open access subset). Returns (text, how, file) or None."""
    pmcid = cand.get("pmcid") if trusted else None
    if not pmcid and doi:
        pmcid, _ = europe_pmc_lookup(doi, None, job.email, job.errors)
    if pmcid:
        try:
            data, _, _ = pl_http.http_get(
                f"https://www.ebi.ac.uk/europepmc/webservices/rest/{pmcid}/fullTextXML",
                job.email,
                accept="application/xml",
            )
            text = pl_text.clean_extracted(pl_text.jats_to_text(data))
            if len(text) > 3000:
                f = job.dest_dir / f"{job.ref['id']}.xml"
                f.write_bytes(data)
                return text, "europepmc", f
        except Exception as e:  # xml parse errors too
            job.errors.append(f"europepmc fulltext: {e}")
    return None


def _ocr_if_thin(job: _Job, f: Path, text: str, how: str) -> tuple[str, str]:
    """Run OCR when a PDF gave little text. Returns the text to use and how it was obtained."""
    if len(text) < 2500 and pl_text.ocr_available():
        try:
            ocr = pl_text.clean_extracted(pl_text.ocr_pdf_text(f))
            if len(ocr) >= 2500:
                text, how = ocr, f"{how}+ocr"
        except Exception as e:
            job.errors.append(f"{how}: OCR failed: {e}"[:200])
    return text, how


def _save_pdf(job: _Job, data: bytes, how: str):
    """Save a PDF and read it. Returns (text, how, file), or None after deleting a bad file."""
    f = job.dest_dir / f"{job.ref['id']}.pdf"
    f.write_bytes(data)
    try:
        text = pl_text.clean_extracted(pl_text.read_pdf_text(f))
    except RuntimeError as e:
        job.errors.append(str(e))
        f.unlink(missing_ok=True)
        return None
    text, how = _ocr_if_thin(job, f, text, how)
    if len(text) < 2500:
        job.errors.append(f"{how}: PDF had too little extractable text (scanned, no OCR available)")
        f.unlink(missing_ok=True)
        return None
    if job.title and not title_in_text(job.title, text, window=6000):
        job.errors.append(f"{how}: PDF does not match the reference title")
        f.unlink(missing_ok=True)
        return None
    return text, how, f


def _fetch_url(job: _Job, url: str, how: str):
    """Fetch one link over HTTP. Returns (text, how, file), or None when it gave no full text."""
    try:
        data, _, _ = pl_http.http_get(
            url, job.email, accept="application/pdf,text/html;q=0.9,*/*;q=0.8"
        )
    except pl_http.HttpError as e:
        job.errors.append(f"{how} download: {e}")
        return None
    if data[:5] == b"%PDF-":
        return _save_pdf(job, data, how)
    if how in _HTML_ROUTES and b"<html" in data[:4000].lower():
        text = _accept_html(
            pl_text.html_to_text(data.decode("utf-8", "replace")), job.title, job.errors, how
        )
        if text:
            f = job.dest_dir / f"{job.ref['id']}.html"
            f.write_bytes(data)
            return text, f"{how}-html", f
    job.errors.append(f"{how}: link did not return a PDF")
    return None


def _fetch_direct(job: _Job, urls: list[tuple[str, str]]):
    """Try up to eight distinct links over HTTP. Returns (text, how, file) or None."""
    seen = set()
    tried = 0
    for url, how in urls:
        if url in seen or tried >= 8:
            continue
        seen.add(url)
        tried += 1
        got = _fetch_url(job, url, how)
        if got:
            return got
    return None


def try_download_fulltext(
    ref: dict,
    cand: dict,
    dest_dir: Path,
    email: str | None,
    errors: list[str],
    trusted: bool = True,
):
    """Try legal open-access routes. Returns (text, how, original_file) or (None, None, None).

    trusted=False means the metadata match is doubtful: links that came from the
    matched record are skipped, and only links printed in the reference are used.
    """
    doi = cand.get("doi") if trusted else normalize_doi(ref.get("doi"))
    arxiv = ref.get("arxiv")
    title = ref.get("title") or (cand.get("title") if trusted else None)
    job = _Job(ref, dest_dir, title, email, errors)
    urls = _gather_urls(job, cand, doi, arxiv, trusted)
    got = _europe_pmc_fulltext(job, cand, doi, trusted)
    if got:
        return got
    return _fetch_direct(job, urls) or (None, None, None)
