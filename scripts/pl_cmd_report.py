"""Athetize commands that check claims and write things for the person to read.

These are the download request, the report, the suggestions and the Word export.
"""

from __future__ import annotations

from pathlib import Path

import pl_core
import pl_docx
import pl_text
from pl_cmd_setup import out
from pl_cmd_sources import _needs_rows, _ref_title, _ref_year
from pl_core import Work, jsave


def _request_links(ref: dict, e: dict, proxy: str) -> list[tuple[str, str]]:
    """Labelled links for getting hold of one reference, most direct first."""
    doi = (e.get("doi") if e.get("exists") == "verified" else None) or ref.get("doi")
    links = []
    if doi:
        links.append(("DOI", f"https://doi.org/{doi}"))
        if proxy:
            links.append(("Library", proxy.rstrip("/") + "/" + doi))
    if ref.get("url") and not (doi and doi.lower() in ref["url"].lower()):
        links.append(("Link", ref["url"]))
    for u in (e.get("oa_urls") or [])[:2] if e.get("exists") == "verified" else []:
        links.append(("Open access", u))
    su = pl_core._search_urls(ref, e)
    links += [("Google Scholar", su["scholar"]), ("WorldCat", su["worldcat"])]
    return links


def _request_item(n: int, row: dict, ref: dict, e: dict, proxy: str) -> dict:
    """One entry of the download request: what to get, where to look and how to name it."""
    links = _request_links(ref, e, proxy)
    return {
        "n": n,
        "id": row["id"],
        "status": row["status"],
        "claims_affected": row["claims_affected"],
        "citation": pl_core._clean_raw(ref.get("raw") or row["citation"]),
        "title": _ref_title(ref),
        "year": _ref_year(ref),
        "save_as": f"{row['id']}.pdf",
        "links": [{"label": a, "url": b} for a, b in dict.fromkeys(links)],
    }


def _request_markdown(items: list[dict], folder: str, proxy: str) -> str:
    """The download request as a Markdown page for the person to read."""
    lines = [
        f"# Sources needed: {len(items)} references",
        "",
        f"Please download the full text of the works below and save them in **{folder}**.",
        "",
        "- Naming the file `<id>.pdf` (shown for each item) makes matching certain, but any "
        "name works: Athetize matches files to references by title and author.",
        "- PDF is best. A saved web page (.html), Word file or text file also works. "
        "Book chapters: the pages that are cited are enough.",
        "- Use your library sign-in for paywalled items"
        + (" (the Library links go through your library proxy)." if proxy else "."),
        '- Skip anything you cannot get; those claims stay marked "can\'t verify".',
        "- When you are done, say so and the check will pick the files up and re-verify the "
        "affected claims.",
        "",
    ]
    for it in items:
        what = "only the abstract was found" if it["status"] == "abstract_only" else "no text found"
        lines.append(f"## {it['n']}. {it['citation']}")
        lines.append("")
        lines.append(
            f"- Save as: `{it['save_as']}` · claims affected: {it['claims_affected']} · {what}"
        )
        lines.append("- " + " · ".join(f"[{link['label']}]({link['url']})" for link in it["links"]))
        lines.append("")
    return "\n".join(lines)


def cmd_request_sources(args):
    """Write a download request.

    It lists every reference still lacking full text, with links and where to save it.
    """
    work = Work(args.work)
    cfg = pl_core.load_config()
    folder = args.folder or cfg.get("download_folder") or "~/Downloads"
    proxy = args.proxy if args.proxy is not None else (cfg.get("library_doi_proxy") or "")
    manifest = work.manifest().get("refs", {})
    refs = {r["id"]: r for r in work.refs()}
    rows = [
        r
        for r in _needs_rows(work, not args.missing_only)
        if r["claims_affected"] >= args.min_claims
    ]
    items = [
        _request_item(n, r, refs[r["id"]], manifest.get(r["id"], {}), proxy)
        for n, r in enumerate(rows, 1)
    ]
    md = _request_markdown(items, folder, proxy)
    work.p("source_request.md").write_text(md, encoding="utf-8")
    jsave(work.p("source_request.json"), {"folder": folder, "items": items})
    out(
        {
            "request_file": str(work.p("source_request.md")),
            "folder": folder,
            "count": len(items),
            "items": [
                {k: it[k] for k in ("n", "id", "save_as", "claims_affected", "status")}
                for it in items
            ],
        }
    )


def cmd_prepare(args):
    res = pl_core.prepare(Work(args.work), batch_size=args.batch_size, k_passages=args.passages)
    out(res)


def cmd_verify(args):
    out(pl_core.verify(Work(args.work)))


def cmd_build(args):
    work = Work(args.work)
    path = pl_core.build_report(work, Path(args.out) if args.out else None)
    data = pl_core.build_data(work)
    print(f"Wrote {path} ({path.stat().st_size // 1024} KB)")
    for p in data["problems"]:
        print("warning:", p)


def cmd_carry_over(args):
    out(pl_core.carry_over(Work(args.work), Work(args.from_)))


def cmd_link_cites(args):
    out(pl_core.link_cites(Work(args.work), write=not args.dry_run))


def cmd_coverage(args):
    res = pl_core.coverage(Work(args.work))
    if args.para:
        res["items"] = [i for i in res["items"] if i["para"] in set(args.para.split(","))]
    out(res)


def cmd_suggest_prepare(args):
    out(pl_core.suggest_prepare(Work(args.work), batch_size=args.batch_size))


def cmd_merge_suggestions(args):
    work = Work(args.work)
    n = pl_core.merge_suggestions(work)
    errors, warnings = pl_core.validate(work)
    errs = [e for e in errors if e.startswith(("suggestions.json", "s"))]
    print(f"Merged {n} new suggestions ({len(work.suggestions())} total).")
    for e in errs:
        print("ERROR", e)


def _find_manuscript(work: Work, paper: dict, given) -> Path:
    """Pick the Word file to edit: the one given, else the workspace copy, else the original."""
    if given:
        src = Path(given).expanduser()
    elif work.p("manuscript.docx").exists():
        src = work.p("manuscript.docx")
    else:
        src = work.root.parent / (paper.get("source_file") or "")
    if not src.exists() or src.suffix.lower() != ".docx":
        raise SystemExit(
            "No Word manuscript found. The Word export needs the paper as .docx: "
            "pass --paper FILE.docx."
        )
    return src


def _chosen_suggestions(work: Work, only, exclude) -> list:
    """The saved suggestions, limited to the `only` ids and without the `exclude` ids."""
    sugs = work.suggestions()
    if only:
        keep = set(only.split(","))
        sugs = [x for x in sugs if x["id"] in keep]
    if exclude:
        drop = set(exclude.split(","))
        sugs = [x for x in sugs if x["id"] not in drop]
    return sugs


def cmd_export_docx(args):
    work = Work(args.work)
    paper = work.paper()
    src = _find_manuscript(work, paper, args.paper)
    if pl_text.sha256_file(src) != paper.get("sha256"):
        print(
            "Warning: this .docx differs from the one that was checked; "
            "unmatched paragraphs will be skipped."
        )
    sugs = _chosen_suggestions(work, args.only, args.exclude)
    stem = Path(paper.get("source_file") or src.name).stem
    outp = Path(args.out) if args.out else work.p(stem + "_athetize_suggestions.docx")
    notes = {} if args.no_notes else pl_core.suggestion_notes(work)
    res = pl_docx.apply(src, outp, paper, sugs, author=args.author, notes=notes)
    ok = [r for r in res if r["applied"]]
    print(
        f"Wrote {outp}: {len(ok)} of {len(res)} suggestions applied as tracked changes"
        + ("" if args.no_notes else ", each with a comment explaining why")
        + f" (author: {args.author})."
    )
    for r in res:
        if not r["applied"]:
            print(f"  skipped {r['id']}: {r['reason']}")
