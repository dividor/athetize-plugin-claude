"""Athetize commands that find, list, request and import the cited sources."""

from __future__ import annotations

import shutil
from pathlib import Path

import pl_core
import pl_http
import pl_resolve
import pl_text
from pl_cmd_setup import out
from pl_core import Work, jsave
from pl_identify import _match_file_to_ref, with_known_doi


def _remember_email(email, cfg: dict) -> None:
    """Save a contact email given on the command line when it differs from the stored one."""
    if email and email != cfg.get("email"):
        stored = pl_core.jload(pl_core.config_path(), {}) or {}
        stored["email"] = email
        pl_core.save_config(stored)


def cmd_resolve(args):
    work = Work(args.work)
    refs = work.refs()
    if not refs:
        raise SystemExit(
            "refs.json is empty or missing. Write it from the reference paragraphs first."
        )
    cfg = pl_core.load_config()
    email = args.email or cfg.get("email")
    _remember_email(args.email, cfg)
    manifest = work.manifest()
    if args.only:
        refs = [r for r in refs if r["id"] in set(args.only.split(","))]
    print(
        f"Resolving {len(refs)} references"
        + ("" if email else " (no contact email: Unpaywall will be skipped)")
        + "..."
    )
    if args.only:
        keep = manifest.get("refs", {})
    else:
        keep = None
    manifest = pl_resolve.resolve_all(
        work.root,
        refs,
        manifest,
        email,
        refresh=args.refresh,
        download=not args.no_download,
    )
    if keep is not None:
        keep.update(manifest["refs"])
        manifest["refs"] = keep
    jsave(work.p("manifest.json"), manifest)
    cmd_status(args)


def _status_data(work: Work):
    manifest = work.manifest().get("refs", {})
    refs = work.refs()
    claims = work.claims()
    cited_by: dict[str, list[str]] = {}
    for c in claims:
        for r in c.get("cites", []):
            cited_by.setdefault(r, []).append(c["id"])
    rows = []
    for r in refs:
        e = manifest.get(r["id"], {})
        rows.append(
            {
                "id": r["id"],
                "status": e.get("status", "unresolved"),
                "exists": e.get("exists", "unchecked"),
                "retracted": e.get("retracted", False),
                "discrepancies": e.get("discrepancies", []),
                "citation": (e.get("citation") or r.get("raw") or r["id"])[:140],
                "claims_affected": len(cited_by.get(r["id"], [])),
                "errors": e.get("errors", [])[:3],
            }
        )
    return rows


def cmd_status(args):
    work = Work(args.work)
    rows = _status_data(work)
    summary: dict[str, int] = {}
    for r in rows:
        summary[r["status"]] = summary.get(r["status"], 0) + 1
    needs = [r for r in rows if r["status"] in ("missing", "abstract_only")]
    retracted = [r for r in rows if r["retracted"]]
    bad_exist = [r for r in rows if r["exists"] in ("mismatch", "not_found", "partial_match")]
    result = {
        "references_by_status": summary,
        "needs_source": [
            {k: r[k] for k in ("id", "status", "citation", "claims_affected")}
            | ({"why": r["errors"]} if r["errors"] else {})
            for r in needs
        ],
        "retracted": [{"id": r["id"], "citation": r["citation"]} for r in retracted],
        "metadata_problems": [
            {"id": r["id"], "exists": r["exists"], "citation": r["citation"]} for r in bad_exist
        ],
        "reference_discrepancies": [
            {"id": r["id"], "citation": r["citation"], "differs": r["discrepancies"]}
            for r in rows
            if r["discrepancies"]
        ],
    }
    verdicts = work.verdicts().get("items", [])
    result["verdicts_recorded"] = len(verdicts)
    out(result)


def _browser_links(ref: dict, e: dict) -> list[str]:
    """Web addresses worth opening in a browser for one reference, without repeats."""
    links = []
    if e.get("doi") and e.get("exists") == "verified":
        links.append(f"https://doi.org/{e['doi']}")
    elif ref.get("doi"):
        links.append(f"https://doi.org/{ref['doi']}")
    if ref.get("url"):
        links.append(ref["url"])
    if e.get("exists") == "verified":
        links += list(e.get("oa_urls") or [])
    return list(dict.fromkeys(links))


def cmd_browser_queue(args):
    """List references still lacking full text, with links to open in the person's browser."""
    work = Work(args.work)
    manifest = work.manifest().get("refs", {})
    refs = {r["id"]: r for r in work.refs()}
    rows = [
        r
        for r in _status_data(work)
        if r["status"] in ("missing", "abstract_only") and r["claims_affected"] >= args.min_claims
    ]
    rows.sort(key=lambda r: -r["claims_affected"])
    queue = []
    for r in rows:
        ref = refs[r["id"]]
        links = _browser_links(ref, manifest.get(r["id"], {}))
        queue.append(
            {
                "id": r["id"],
                "status": r["status"],
                "exists": r["exists"],
                "claims_affected": r["claims_affected"],
                "citation": r["citation"],
                "title": ref.get("title"),
                "links": links,
                "kind": "book" if not links else "article",
                "save_as": f"sources/{r['id']}.txt",
            }
        )
    jsave(work.p("browser_queue.json"), queue)
    out({"queue_file": str(work.p("browser_queue.json")), "count": len(queue), "items": queue})


def _remove_user_files(src: Path, ref_id: str) -> None:
    """Delete the files a person supplied earlier for this reference."""
    for old in src.glob(f"{ref_id}.*"):
        if old.is_file() and old.suffix.lower() in pl_resolve.USER_FILE_EXTS:
            old.unlink()


def _download_source(url: str, src: Path, ref_id: str) -> Path:
    """Fetch a source from a web address into the sources folder and return where it was saved."""
    cfg = pl_core.load_config()
    data, ctype, final = pl_http.http_get(url, cfg.get("email"))
    if data[:5] == b"%PDF-":
        ext = ".pdf"
    elif b"<html" in data[:2000].lower() or "html" in ctype.lower():
        ext = ".html"
    else:
        ext = ".txt"
    dest = src / f"{ref_id}{ext}"
    dest.write_bytes(data)
    return dest


def _copy_source_file(target: str, src: Path, ref_id: str) -> Path:
    """Copy a file the person supplied into the sources folder and return where it was saved."""
    f = Path(target).expanduser()
    if not f.exists():
        raise SystemExit(f"File not found: {f}")
    if f.suffix.lower() not in pl_resolve.USER_FILE_EXTS:
        raise SystemExit(
            f"Unsupported type {f.suffix}. Use one of: {', '.join(pl_resolve.USER_FILE_EXTS)}"
        )
    dest = src / f"{ref_id}{f.suffix.lower()}"
    if f.resolve() == dest.resolve():
        return dest  # already in the sources folder under this name: nothing to copy or replace
    _remove_user_files(src, ref_id)
    shutil.copy2(f, dest)
    return dest


def _resolve_into_manifest(work: Work, manifest: dict, ref_id: str, ref: dict, email) -> dict:
    """Read one reference's source files again and store the result in the manifest."""
    prior = manifest.get("refs", {}).get(ref_id)
    entry = pl_resolve.resolve_one(work.root, ref, prior, email, refresh=False, download=False)
    manifest.setdefault("refs", {})[ref_id] = entry
    return entry


def cmd_add_source(args):
    work = Work(args.work)
    refs = {r["id"]: r for r in work.refs()}
    if args.ref not in refs:
        raise SystemExit(
            f"Unknown reference id {args.ref!r}. Known ids: {', '.join(list(refs)[:20])}"
        )
    src = work.p("sources")
    src.mkdir(exist_ok=True)
    target = args.source
    if target.startswith(("http://", "https://")):
        dest = _download_source(target, src, args.ref)
    else:
        dest = _copy_source_file(target, src, args.ref)
    manifest = work.manifest()
    cfg = pl_core.load_config()
    entry = _resolve_into_manifest(work, manifest, args.ref, refs[args.ref], cfg.get("email"))
    jsave(work.p("manifest.json"), manifest)
    print(
        f"Added {dest.name} for {args.ref}: status={entry.get('status')}, "
        f"{entry.get('evidence_chars')} characters of text."
    )
    if entry.get("status") != "user_provided":
        print("Warning:", "; ".join(entry.get("errors", [])) or "file could not be read")
    print("Run the recheck step to re-verify the claims that cite it.")


def _needs_rows(work: Work, include_abstract_only: bool = True) -> list[dict]:
    wanted = ("missing", "abstract_only") if include_abstract_only else ("missing",)
    rows = [r for r in _status_data(work) if r["status"] in wanted]
    rows.sort(key=lambda r: (-r["claims_affected"], r["id"]))
    return rows


def _candidate_files(folders: list, recursive: bool) -> list[Path]:
    """Files to consider for import: named files, plus supported visible files in named folders."""
    files = []
    for d in folders:
        d = Path(d).expanduser()
        if d.is_file():
            files.append(d)
        elif d.is_dir():
            files += [
                p
                for p in sorted(d.rglob("*") if recursive else d.iterdir())
                if p.is_file()
                and p.suffix.lower() in pl_resolve.USER_FILE_EXTS
                and not p.name.startswith(".")
            ]
    return files


def _place_file(
    f: Path, text, refs: dict[str, dict], wanted: set, taken: dict[str, str], found: dict
) -> None:
    """Match one readable file to a reference and sort it.

    It ends up matched, ambiguous, unmatched, or a duplicate: a second copy of a work
    already taken from this batch, or a work that already has its full text.
    """
    rid, how, amb = _match_file_to_ref(f, text or "", refs)
    if rid is None:
        if how == "ambiguous":
            found["ambiguous"].append({"file": f.name, "could_be": amb})
        elif len((text or "").strip()) < 500:
            why = "has almost no text (a scan, or a saved sign-in page?)"
            found["unmatched"].append({"file": f.name, "why": why})
        else:
            found["unmatched"].append({"file": f.name, "why": how})
    elif rid in taken:
        why = f"another copy of reference {rid}; {taken[rid]} is used"
        found["duplicates"].append({"file": f.name, "ref": rid, "why": why})
    elif rid not in wanted:
        why = f"reference {rid} already has its full text"
        found["duplicates"].append({"file": f.name, "ref": rid, "why": why})
    elif len((text or "").strip()) < 500:
        why = f"matched {rid} but has almost no text (scanned or a preview page?)"
        found["unmatched"].append({"file": f.name, "why": why})
    else:
        taken[rid] = f.name
        found["matched"].append({"file": f.name, "ref": rid, "how": how, "path": str(f)})


def _sort_files(
    files: list[Path], refs: dict[str, dict], wanted: set, have_hashes: set, since
) -> tuple[dict, dict[str, str]]:
    """Sort files into matched, ambiguous, unmatched, duplicates and already imported.

    A file is compared with every reference, so a paper that is already covered is named
    as such instead of being reported as unknown. Also returns which reference each
    matched file was taken for.
    """
    found: dict[str, list] = {
        "matched": [],
        "unmatched": [],
        "ambiguous": [],
        "skipped": [],
        "duplicates": [],
    }
    taken: dict[str, str] = {}
    seen: dict[str, str] = {}
    for f in files:
        if since and f.stat().st_mtime < since:
            continue
        digest = pl_text.sha256_file(f)
        if digest in have_hashes:
            found["skipped"].append(f.name)
            continue
        if digest in seen:
            found["duplicates"].append({"file": f.name, "why": f"same content as {seen[digest]}"})
            continue
        seen[digest] = f.name
        try:
            text = pl_text.extract_file_text(f)
        except Exception as exc:  # unreadable file
            found["unmatched"].append({"file": f.name, "why": f"could not read: {exc}"[:120]})
            continue
        _place_file(f, text, refs, wanted, taken, found)
    return found, taken


def _import_matched(work: Work, refs: dict[str, dict], src: Path, matched: list[dict]) -> None:
    """Copy each matched file into the sources folder and record what could be read from it."""
    cfg = pl_core.load_config()
    manifest = work.manifest()
    for m in matched:
        f = Path(m["path"])
        _remove_user_files(src, m["ref"])
        dest = src / f"{m['ref']}{f.suffix.lower()}"
        shutil.copy2(f, dest)
        entry = _resolve_into_manifest(work, manifest, m["ref"], refs[m["ref"]], cfg.get("email"))
        m["status"] = entry.get("status")
        m["characters"] = entry.get("evidence_chars")
    jsave(work.p("manifest.json"), manifest)


def source_hashes(work: Work) -> set:
    """Content hashes of every file already in the check's sources folder."""
    src = work.p("sources")
    return (
        {pl_text.sha256_file(p) for p in src.rglob("*") if p.is_file()} if src.is_dir() else set()
    )


def import_paths(
    work: Work,
    paths: list,
    every: bool = False,
    recursive: bool = False,
    since=None,
    dry_run: bool = False,
) -> dict:
    """Match the given files, and the files in the given folders, to references and add them.

    Each file is read and identified by what it says; see ``pl_identify``. Files with the
    same content, second copies of one work and works that already have full text are
    reported as duplicates and not added. ``every`` lets a file replace existing full text.
    """
    refs = {r["id"]: r for r in work.refs()}
    wanted = {r["id"] for r in _needs_rows(work)} if not every else set(refs)
    known = work.manifest().get("refs", {})
    candidates = {rid: with_known_doi(ref, known.get(rid, {})) for rid, ref in refs.items()}
    src = work.p("sources")
    src.mkdir(exist_ok=True)
    files = _candidate_files(paths, recursive)
    found, taken = _sort_files(files, candidates, wanted, source_hashes(work), since)
    matched = found["matched"]
    if not dry_run:
        _import_matched(work, refs, src, matched)
    still = [r["id"] for r in _needs_rows(work) if r["id"] not in taken]
    for m in matched:
        m.pop("path", None)
    return {
        "files_seen": len(files),
        "matched": matched,
        "ambiguous": found["ambiguous"],
        "unmatched": found["unmatched"],
        "duplicates": found["duplicates"],
        "already_imported": found["skipped"],
        "still_need_source": still,
        "dry_run": bool(dry_run),
    }


def cmd_import_sources(args):
    """Match downloaded files in a folder to references still lacking full text, and add them."""
    out(
        import_paths(
            Work(args.work), args.folders, args.all, args.recursive, args.since, args.dry_run
        )
    )


def cmd_passages(args):
    work = Work(args.work)
    entry = work.manifest().get("refs", {}).get(args.ref)
    if not entry or not entry.get("evidence_file"):
        out({"ref": args.ref, "evidence_level": "none", "passages": []})
        return
    text = (work.p(entry["evidence_file"])).read_text("utf-8")
    passages = pl_text.bm25_rank(text, args.query, k=args.k, max_chars=2000)
    out(
        {
            "ref": args.ref,
            "evidence_level": entry.get("evidence_level"),
            "passages": [
                {"rank": p["rank"], "page": p["page"], "text": p["text"]} for p in passages
            ],
        }
    )
