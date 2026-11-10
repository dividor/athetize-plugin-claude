"""Reference resolution and open-access source download for athetize.

Uses Crossref, OpenAlex, Europe PMC, arXiv, ERIC, Semantic Scholar and (with a
contact email) Unpaywall, plus any URL printed in the reference itself. An optional
headless browser (pl_browser, Playwright) renders pages that need JavaScript; it
stops at bot checks instead of trying to pass them. Only legal open-access copies
are downloaded. Network failures are recorded, never
retried in loops.

The work is split across modules: pl_refmatch (matching helpers), pl_metadata
(Crossref and OpenAlex lookup) and pl_download (open-access download). Their public
names are re-exported here, so `pl_resolve.<name>` keeps working.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pl_text
from pl_download import (
    PREVIEW_MARKERS,
    _accept_html,
    eric_fulltext_urls,
    europe_pmc_lookup,
    semantic_scholar_pdf,
    try_download_fulltext,
)
from pl_metadata import candidate_from_crossref, candidate_from_openalex, resolve_metadata
from pl_refmatch import (
    _EN_STOP,
    _author_segment,
    _fold,
    abstract_from_inverted,
    abstract_matches,
    author_mismatch,
    citation_string,
    display_fields,
    doi_slug,
    eric_id,
    first_surname,
    looks_english,
    normalize_doi,
    score_candidate,
    strip_tags,
    surname,
    title_in_text,
)

__all__ = [
    "PREVIEW_MARKERS",
    "USER_FILE_EXTS",
    "_EN_STOP",
    "_accept_html",
    "_author_segment",
    "_fold",
    "abstract_from_inverted",
    "abstract_matches",
    "author_mismatch",
    "candidate_from_crossref",
    "candidate_from_openalex",
    "citation_string",
    "display_fields",
    "doi_slug",
    "eric_fulltext_urls",
    "eric_id",
    "europe_pmc_lookup",
    "find_user_file",
    "first_surname",
    "looks_english",
    "normalize_doi",
    "resolve_all",
    "resolve_metadata",
    "resolve_one",
    "score_candidate",
    "semantic_scholar_pdf",
    "strip_tags",
    "surname",
    "title_in_text",
    "try_download_fulltext",
]

USER_FILE_EXTS = (".pdf", ".txt", ".md", ".html", ".htm", ".docx", ".xml")

# Manifest fields carried over from an earlier run, by stage.
_METADATA_KEYS = (
    "exists",
    "match_score",
    "matched",
    "retracted",
    "retraction_notes",
    "doi",
    "link",
    "citation",
    "metadata_done",
    "oa_urls",
    "pmcid",
    "abstract_cached",
)
_ABSTRACT_KEYS = (
    "status",
    "evidence_level",
    "evidence_file",
    "local_file",
    "evidence_hash",
    "evidence_chars",
)
_FULL_TEXT_KEYS = _ABSTRACT_KEYS + ("how",)


# --------------------------------------------------------------------------
# User-provided files
# --------------------------------------------------------------------------
def find_user_file(work: Path, ref: dict, doi: str | None):
    """File in sources/ named after the reference id or its DOI, or None."""
    src = work / "sources"
    if not src.is_dir():
        return None
    stems = {ref["id"].casefold()}
    if doi:
        stems.add(doi_slug(doi).casefold())
    for f in sorted(src.iterdir()):
        if f.is_file() and f.suffix.lower() in USER_FILE_EXTS and f.stem.casefold() in stems:
            return f
    return None


# --------------------------------------------------------------------------
# Stages of resolving one reference
# --------------------------------------------------------------------------
def _copy_keys(entry: dict, prior: dict, keys: tuple[str, ...]) -> None:
    """Copy the named fields that the earlier manifest entry has into the new entry."""
    for k in keys:
        if k in prior:
            entry[k] = prior[k]


def _note_record_title(entry: dict) -> None:
    """For a doubtful match, keep the matched record's title so the report can show it."""
    if entry.get("exists") in ("partial_match", "mismatch") and (entry.get("matched") or {}).get(
        "title"
    ):
        entry["record_title"] = entry["matched"]["title"]


def _reuse_metadata(ref: dict, prior: dict, entry: dict, errors: list[str]) -> dict:
    """Metadata stage from the earlier manifest entry. Returns the candidate for download."""
    _copy_keys(entry, prior, _METADATA_KEYS)
    if (
        entry.get("exists") == "verified"
        and entry.get("matched")
        and author_mismatch(ref, entry["matched"])
        and normalize_doi(ref.get("doi")) != entry.get("doi")
    ):
        entry["exists"] = "partial_match"
        errors.append(
            "first author of the matched record differs from the reference; match downgraded"
        )
    # Recompute what is shown, so older manifests stop displaying a doubtful match.
    entry["citation"], entry["link"], shown_doi = display_fields(
        ref, entry.get("exists"), entry.get("matched"), entry.get("doi")
    )
    if entry.get("exists") != "verified":
        entry["doi"] = shown_doi
        _note_record_title(entry)
    cand = dict(prior.get("matched") or {})
    cand["pdf_urls"] = prior.get("oa_urls") or []
    cand["pmcid"] = prior.get("pmcid")
    cand["doi"] = prior.get("doi")
    return cand


def _exists_from_score(score: float | None, doi: str | None) -> str:
    """How well the found record matches the reference, as a manifest 'exists' value."""
    if score is None:
        return "verified"  # DOI resolved, no title given to compare
    if score >= 0.85:
        return "verified"
    if score >= 0.6:
        return "partial_match"
    return "mismatch" if doi else "not_found"


def _record_no_match(ref: dict, doi: str | None, entry: dict, errors: list[str]) -> dict:
    """Fill the entry when no record was found. Returns a bare candidate for download."""
    entry["exists"] = "unchecked" if any("network error" in e for e in errors) else "not_found"
    entry["match_score"] = None
    entry["matched"] = None
    entry["retracted"] = False
    entry["retraction_notes"] = []
    entry["doi"] = doi
    entry["citation"] = citation_string(ref, None)
    entry["link"] = f"https://doi.org/{doi}" if doi else (ref.get("url") or None)
    return {"doi": doi, "pdf_urls": []}


def _record_match(ref: dict, doi: str | None, cand: dict, score: float | None, entry: dict) -> None:
    """Fill the entry from the record that was found."""
    exists = _exists_from_score(score, doi)
    entry["exists"] = exists
    entry["match_score"] = None if score is None else round(score, 3)
    entry["matched"] = {
        k: cand.get(k) for k in ("title", "authors", "year", "venue", "doi", "source")
    }
    notes = list(cand.get("retraction_notes") or [])
    if cand.get("is_retracted") and not notes:
        notes.append("retracted (OpenAlex)")
    entry["retracted"] = bool(notes)
    entry["retraction_notes"] = notes
    entry["doi"] = cand.get("doi") or doi
    entry["citation"], entry["link"], shown_doi = display_fields(
        ref, exists, entry["matched"], entry["doi"]
    )
    if exists != "verified":
        _note_record_title(entry)
        entry["doi"] = shown_doi
    entry["oa_urls"] = cand.get("pdf_urls") or []
    entry["pmcid"] = cand.get("pmcid")


def _fresh_metadata(
    ref: dict, doi: str | None, email: str | None, entry: dict, errors: list[str]
) -> dict:
    """Metadata stage from the network. Returns the candidate for download."""
    cand, score = resolve_metadata(ref, email, errors)
    if cand is None:
        cand = _record_no_match(ref, doi, entry, errors)
    else:
        _record_match(ref, doi, cand, score, entry)
    entry["metadata_done"] = entry["exists"] != "unchecked"
    return cand


def _use_user_file(work: Path, rid: str, user_file: Path, entry: dict, errors: list[str]) -> bool:
    """Read a file the user supplied as the evidence. False when it could not be read."""
    try:
        text = pl_text.extract_file_text(user_file)
        (work / "sources" / "_text" / f"{rid}.txt").write_text(text, "utf-8")
        entry.update(
            status="user_provided",
            evidence_level="full_text",
            evidence_file=f"sources/_text/{rid}.txt",
            local_file=str(user_file.relative_to(work)),
            evidence_hash=pl_text.short_hash(text),
            evidence_chars=len(text),
        )
        entry["errors"] = errors
        return True
    except Exception as e:
        errors.append(f"could not read {user_file.name}: {e}")
    return False


def _reuse_prior_evidence(
    work: Path, prior: dict, entry: dict, refresh: bool, download: bool
) -> bool:
    """Keep evidence fetched in an earlier run when it is still on disk. True when kept."""
    prior_ok = (
        prior
        and prior.get("status") == "full_text"
        and (work / prior.get("evidence_file", "")).exists()
    )
    if prior_ok and not refresh:
        _copy_keys(entry, prior, _FULL_TEXT_KEYS)
        entry["errors"] = prior.get("errors", [])
        return True

    # Without downloads, keep a previously fetched abstract rather than losing it
    prior_abs = (
        prior
        and prior.get("status") == "abstract_only"
        and prior.get("evidence_file")
        and (work / prior["evidence_file"]).exists()
    )
    if prior_abs and not download and not refresh and entry.get("exists") == "verified":
        _copy_keys(entry, prior, _ABSTRACT_KEYS)
        entry["errors"] = prior.get("errors", [])
        return True
    return False


def _download_evidence(
    work: Path,
    ref: dict,
    cand: dict,
    entry: dict,
    email: str | None,
    errors: list[str],
    browser: bool,
) -> bool:
    """Try to download open-access full text. True when the entry now has full text."""
    rid = ref["id"]
    auto_dir = work / "sources" / "_auto"
    ref_doi = normalize_doi(ref.get("doi"))
    trusted = entry.get("exists") == "verified" or (
        entry.get("exists") == "partial_match" and ref_doi and ref_doi == entry.get("doi")
    )
    if not trusted:
        errors.append(
            f"metadata match is {entry.get('exists')}: "
            "links from the matched record were not used"
        )
    text, how, original = try_download_fulltext(
        ref, cand, auto_dir, email, errors, trusted=bool(trusted), browser=browser
    )
    if not text:
        return False
    (auto_dir / f"{rid}.txt").write_text(text, "utf-8")
    entry.update(
        status="full_text",
        evidence_level="full_text",
        evidence_file=f"sources/_auto/{rid}.txt",
        local_file=str(original.relative_to(work)),
        evidence_hash=pl_text.short_hash(text),
        evidence_chars=len(text),
        how=how,
    )
    entry["errors"] = errors
    return True


def _pick_abstract(
    ref: dict, cand: dict, entry: dict, email: str | None, errors: list[str], download: bool
) -> str:
    """Abstract to fall back on: from the matched record, else Europe PMC; '' when unusable."""
    abstract = ""
    if entry.get("exists") == "verified" and (entry.get("matched") or cand.get("abstract")):
        abstract = (cand.get("abstract") or "").strip()
    if not abstract and entry.get("doi") and download:
        _, abstract = europe_pmc_lookup(entry["doi"], None, email, errors)
    if abstract and not abstract_matches(
        ref.get("title") or (entry.get("matched") or {}).get("title"), abstract
    ):
        errors.append(
            "abstract from the matched record does not fit this reference "
            "(language or topic); not used"
        )
        abstract = ""
    return abstract


def _record_abstract(work: Path, ref: dict, entry: dict, abstract: str) -> None:
    """Save a long enough abstract as the evidence, or mark the evidence as missing."""
    rid = ref["id"]
    if len(abstract) >= 200:
        path = work / "sources" / "_auto" / f"{rid}.abstract.txt"
        title = (entry.get("matched") or {}).get("title") or ref.get("title") or ""
        content = (title + "\n\n" + abstract).strip()
        path.write_text(content, "utf-8")
        entry.update(
            status="abstract_only",
            evidence_level="abstract",
            evidence_file=f"sources/_auto/{rid}.abstract.txt",
            local_file=None,
            evidence_hash=pl_text.short_hash(content),
            evidence_chars=len(content),
        )
    else:
        entry.update(
            status="missing",
            evidence_level="none",
            evidence_file=None,
            local_file=None,
            evidence_hash="none",
            evidence_chars=0,
        )


# --------------------------------------------------------------------------
# Main entry
# --------------------------------------------------------------------------
def resolve_one(
    work: Path,
    ref: dict,
    prior: dict | None,
    email: str | None,
    refresh: bool,
    download: bool,
    browser: bool = False,
) -> dict:
    """Resolve one reference: check it exists, then find the best evidence text for it."""
    rid = ref["id"]
    (work / "sources" / "_auto").mkdir(parents=True, exist_ok=True)
    (work / "sources" / "_text").mkdir(parents=True, exist_ok=True)
    errors: list[str] = []
    entry: dict = {"id": rid, "attempted_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    doi = normalize_doi(ref.get("doi"))
    earlier: dict = prior or {}

    # 1. Metadata (reuse previous if present and not refreshing)
    if not refresh and earlier.get("metadata_done"):
        cand = _reuse_metadata(ref, earlier, entry, errors)
    else:
        cand = _fresh_metadata(ref, doi, email, entry, errors)

    # 2. Evidence: user-provided file wins
    user_file = find_user_file(work, ref, entry.get("doi"))
    if user_file is not None and _use_user_file(work, rid, user_file, entry, errors):
        return entry

    # 3. Previously downloaded full text (or, without downloads, a previous abstract)
    if _reuse_prior_evidence(work, earlier, entry, refresh, download):
        return entry

    # 4. Download
    if (
        download
        and entry.get("exists") != "unchecked"
        and _download_evidence(work, ref, cand, entry, email, errors, browser)
    ):
        return entry

    # 5. Abstract fallback
    abstract = _pick_abstract(ref, cand, entry, email, errors, download)
    _record_abstract(work, ref, entry, abstract)
    entry["errors"] = errors
    return entry


def resolve_all(
    work: Path,
    refs: list[dict],
    manifest: dict,
    email: str | None,
    refresh: bool = False,
    download: bool = True,
    workers: int = 4,
    browser: bool = False,
):
    """Resolve every reference in parallel and store the results in the manifest."""
    prior_entries = manifest.get("refs", {})
    results = {}

    def job(ref):
        try:
            return ref["id"], resolve_one(
                work, ref, prior_entries.get(ref["id"]), email, refresh, download, browser
            )
        except Exception as e:  # never let one reference abort the run
            return ref["id"], {
                "id": ref["id"],
                "status": "missing",
                "evidence_level": "none",
                "evidence_file": None,
                "evidence_hash": "none",
                "exists": "unchecked",
                "errors": [f"unexpected error: {e}"],
                "citation": ref.get("raw") or ref["id"],
                "retracted": False,
                "retraction_notes": [],
            }

    with ThreadPoolExecutor(max_workers=workers) as ex:
        for rid, entry in ex.map(job, refs):
            results[rid] = entry
            print(
                f"  {rid}: {entry.get('status')} / {entry.get('exists')}"
                + (" / RETRACTED" if entry.get("retracted") else ""),
                flush=True,
            )
    manifest["refs"] = results
    manifest["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    return manifest
