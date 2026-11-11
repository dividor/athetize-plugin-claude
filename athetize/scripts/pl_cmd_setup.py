"""Athetize commands that set up a check: doctor, config, ingest, paragraphs, validate, merge."""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from pathlib import Path

import pl_core
import pl_http
import pl_ingest
import pl_packages
import pl_text
import pl_workspace
from pl_core import Work, jsave

# Services that `doctor` tries to reach, in the order they are tried and reported.
_DOCTOR_SERVICES = {
    "crossref": "https://api.crossref.org/works?rows=0",
    "openalex": "https://api.openalex.org/works?per-page=1",
    "europepmc": (
        "https://www.ebi.ac.uk/europepmc/webservices/rest/search" "?query=a&format=json&pageSize=1"
    ),
    "arxiv": "https://export.arxiv.org/api/query?search_query=all:test&max_results=1",
    "eric": "https://api.ies.ed.gov/eric/?search=reading&rows=1&format=json",
    "semanticscholar": (
        "https://api.semanticscholar.org/graph/v1/paper/DOI:10.1145/3628516.3655809" "?fields=title"
    ),
}


def out(obj):
    """Print a value as indented JSON."""
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def _check_services(email) -> dict[str, str]:
    """Try each lookup service once and record "ok" or why it could not be reached."""
    network = {}
    for name, url in _DOCTOR_SERVICES.items():
        try:
            pl_http.http_get(url, email, timeout=12)
            network[name] = "ok"
        except pl_http.HttpError as e:
            network[name] = f"blocked or down: {e}"
    return network


def _doctor_notes(info: dict) -> list[str]:
    """Advice for the person, based on what the environment check found."""
    notes = []
    if info["missing_packages"]:
        notes.append(
            "Missing Python packages: "
            + ", ".join(info["missing_packages"])
            + ". Ask the person, then run `pl.py install-packages`."
        )
    if not info["contact_email"]:
        notes.append(
            "No contact email set. Unpaywall needs one for open-access lookups: "
            "pl.py config set-email you@example.org"
        )
    if any(v != "ok" for v in info["network"].values()):
        notes.append(
            "Some services are unreachable from this environment. Sources from those hosts "
            "will be marked missing; ask the user to supply them."
        )
    return notes


def cmd_doctor(args):
    cfg = pl_core.load_config()
    info = {
        "python": sys.version.split()[0],
        "pdf_extractor": None,
        "xml_parser": "defusedxml" if importlib.util.find_spec("defusedxml") else None,
        "contact_email": cfg.get("email") or None,
        "network": {},
    }
    info["pdf_extractor"] = pl_text.pdf_extractor()
    info["missing_packages"] = pl_packages.missing_packages()
    info["network"] = _check_services(cfg.get("email"))
    info["notes"] = _doctor_notes(info)
    out(info)


def cmd_config(args):
    cfg = pl_core.load_config()
    if args.action == "set-email":
        try:
            pl_workspace.set_email(pl_workspace.clean_email(args.value or ""))
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
        print(f"Saved contact email to {pl_core.config_path()}")
    elif args.action in ("set-folder", "set-proxy"):
        key = "download_folder" if args.action == "set-folder" else "library_doi_proxy"
        stored = pl_core.jload(pl_core.config_path(), {}) or {}
        if args.value:
            stored[key] = args.value
        else:
            stored.pop(key, None)
        pl_core.save_config(stored)
        print(f"Saved {key} = {args.value!r} to {pl_core.config_path()}")
    else:
        shown = dict(cfg)
        if shown.get("email"):
            shown["email"] = pl_workspace.masked_email(shown["email"])
        out(shown)


def cmd_ingest(args):
    paper_path = Path(args.paper).expanduser().resolve()
    if not paper_path.exists():
        raise SystemExit(f"File not found: {paper_path}")
    work = Work(args.work or paper_path.parent / f"{paper_path.stem}-check")
    work.root.mkdir(parents=True, exist_ok=True)
    for d in ("sources", "claims_parts", "batches", "verdicts"):
        work.p(d).mkdir(exist_ok=True)
    paper = pl_ingest.ingest(paper_path)
    jsave(work.p("paper.json"), paper)
    if paper_path.suffix.lower() == ".docx":
        # a private copy, so the Word export never depends on (or touches) the user's original
        shutil.copy2(paper_path, work.p("manuscript.docx"))
    kinds = {}
    for p in paper["paragraphs"]:
        kinds[p["kind"]] = kinds.get(p["kind"], 0) + 1
    ref_paras = [p["id"] for p in paper["paragraphs"] if p["kind"] == "reference"]
    out(
        {
            "work_dir": str(work.root),
            "title": paper["title"],
            "paragraphs": len(paper["paragraphs"]),
            "by_kind": kinds,
            "reference_paragraphs": f"{ref_paras[0]}..{ref_paras[-1]}" if ref_paras else None,
            "warning": (
                None
                if ref_paras
                else "No reference section detected. Check paper.json; the bibliography may "
                "use an unusual heading."
            ),
        }
    )


def cmd_paragraphs(args):
    work = Work(args.work)
    paper = work.paper()
    ids = [p["id"] for p in paper["paragraphs"]]
    lo = ids.index(args.from_) if args.from_ in ids else 0
    hi = ids.index(args.to) if args.to in ids else len(ids) - 1
    for p in paper["paragraphs"][lo : hi + 1]:
        if args.kind and p["kind"] != args.kind:
            continue
        print(f"[{p['id']}] ({p['kind']}) {p['text']}\n")


def cmd_validate(args):
    work = Work(args.work)
    errors, warnings = pl_core.validate(work, fix=args.fix)
    for w in warnings:
        print("warning:", w)
    for e in errors:
        print("ERROR:", e)
    if errors:
        print(f"\n{len(errors)} error(s). Fix them in the JSON files and run validate again.")
        sys.exit(1)
    print("OK: refs.json, claims.json and notes.json are valid.")


def cmd_merge(args):
    if args.append:
        out(pl_core.append_claim_parts(Work(args.work), args.append))
        return
    n = pl_core.merge_claim_parts(Work(args.work))
    print(f"Merged claims_parts/*.json into claims.json ({n} claims, renumbered c1..).")
