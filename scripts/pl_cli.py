"""Command-line argument parser for pl.py: one subcommand per Athetize step."""

from __future__ import annotations

import argparse

import pl_cmd_report
import pl_cmd_setup
import pl_cmd_sources
import pl_serve


def _add_setup_commands(sub) -> None:
    """Add the subcommands that set up a check (doctor to merge-claims)."""
    sub.add_parser("doctor").set_defaults(fn=pl_cmd_setup.cmd_doctor)
    c = sub.add_parser("config")
    c.add_argument("action", choices=["set-email", "set-folder", "set-proxy", "show"])
    c.add_argument("value", nargs="?")
    c.set_defaults(fn=pl_cmd_setup.cmd_config)

    c = sub.add_parser("ingest")
    c.add_argument("paper")
    c.add_argument("--work")
    c.set_defaults(fn=pl_cmd_setup.cmd_ingest)

    c = sub.add_parser("paragraphs")
    c.add_argument("--work", required=True)
    c.add_argument("--from", dest="from_")
    c.add_argument("--to")
    c.add_argument("--kind")
    c.set_defaults(fn=pl_cmd_setup.cmd_paragraphs)

    c = sub.add_parser("validate")
    c.add_argument("--work", required=True)
    c.add_argument("--fix", action="store_true")
    c.set_defaults(fn=pl_cmd_setup.cmd_validate)

    c = sub.add_parser("merge-claims")
    c.add_argument("--work", required=True)
    c.add_argument(
        "--append",
        metavar="GLOB",
        help=(
            "append claims_parts/GLOB to existing claims, keeping ids and verdicts "
            "(e.g. 'add-*.json')"
        ),
    )
    c.set_defaults(fn=pl_cmd_setup.cmd_merge)


def _add_lookup_commands(sub) -> None:
    """Add the subcommands that look sources up and list what is missing."""
    c = sub.add_parser("resolve")
    c.add_argument("--work", required=True)
    c.add_argument("--email")
    c.add_argument("--refresh", action="store_true")
    c.add_argument("--no-download", action="store_true")
    c.add_argument(
        "--browser",
        action="store_true",
        help="headless-browser fallback for open-access pages (needs Playwright)",
    )
    c.add_argument("--only", help="comma-separated reference ids to resolve")
    c.set_defaults(fn=pl_cmd_sources.cmd_resolve)

    c = sub.add_parser("browser-queue")
    c.add_argument("--work", required=True)
    c.add_argument("--min-claims", type=int, default=1)
    c.set_defaults(fn=pl_cmd_sources.cmd_browser_queue)

    c = sub.add_parser("status")
    c.add_argument("--work", required=True)
    c.set_defaults(fn=pl_cmd_sources.cmd_status)


def _add_supply_commands(sub) -> None:
    """Add the subcommands that bring in sources from the person and search their text."""
    c = sub.add_parser("add-source")
    c.add_argument("--work", required=True)
    c.add_argument("ref")
    c.add_argument("source")
    c.set_defaults(fn=pl_cmd_sources.cmd_add_source)

    c = sub.add_parser(
        "request-sources", help="Write a download request for references lacking full text"
    )
    c.add_argument("--work", required=True)
    c.add_argument(
        "--folder",
        help=(
            "Folder the person should save files into "
            "(default: config download_folder or ~/Downloads)"
        ),
    )
    c.add_argument(
        "--proxy",
        help="Library DOI proxy prefix, e.g. https://doi-org.ezproxy.example.edu (default: config)",
    )
    c.add_argument("--min-claims", type=int, default=0)
    c.add_argument(
        "--missing-only",
        action="store_true",
        help="Leave out references where an abstract was found",
    )
    c.set_defaults(fn=pl_cmd_report.cmd_request_sources)

    c = sub.add_parser(
        "import-sources",
        help="Match files in a folder to references lacking full text and add them",
    )
    c.add_argument("folders", nargs="+")
    c.add_argument("--work", required=True)
    c.add_argument("--dry-run", action="store_true")
    c.add_argument("--recursive", action="store_true")
    c.add_argument(
        "--all",
        action="store_true",
        help="Also consider references that already have full text (replaces them)",
    )
    c.add_argument("--since", type=float, help="Only files modified after this Unix time")
    c.set_defaults(fn=pl_cmd_sources.cmd_import_sources)

    c = sub.add_parser(
        "serve",
        help="Show the report from this computer so its Add sources button can take files",
    )
    c.add_argument("--work", required=True)
    c.add_argument("--port", type=int, default=0, help="Port on 127.0.0.1 (default: any free)")
    c.add_argument(
        "--idle-minutes",
        type=float,
        default=120,
        help="Stop when the page has not asked for anything for this long",
    )
    c.set_defaults(fn=pl_serve.cmd_serve)

    c = sub.add_parser("passages")
    c.add_argument("--work", required=True)
    c.add_argument("--ref", required=True)
    c.add_argument("--query", required=True)
    c.add_argument("-k", type=int, default=8)
    c.set_defaults(fn=pl_cmd_sources.cmd_passages)


def _add_check_commands(sub) -> None:
    """Add the subcommands that check claims against their sources."""
    c = sub.add_parser("prepare")
    c.add_argument("--work", required=True)
    c.add_argument("--batch-size", type=int, default=6)
    c.add_argument("--passages", type=int, default=5)
    c.set_defaults(fn=pl_cmd_report.cmd_prepare)

    c = sub.add_parser("verify")
    c.add_argument("--work", required=True)
    c.set_defaults(fn=pl_cmd_report.cmd_verify)

    c = sub.add_parser("carry-over")
    c.add_argument("--work", required=True)
    c.add_argument("--from", dest="from_", required=True, help="workspace of the earlier draft")
    c.set_defaults(fn=pl_cmd_report.cmd_carry_over)

    c = sub.add_parser("link-cites")
    c.add_argument("--work", required=True)
    c.add_argument("--dry-run", action="store_true")
    c.set_defaults(fn=pl_cmd_report.cmd_link_cites)

    c = sub.add_parser("coverage")
    c.add_argument("--work", required=True)
    c.add_argument("--para", help="comma-separated paragraph ids to report")
    c.set_defaults(fn=pl_cmd_report.cmd_coverage)


def _add_output_commands(sub) -> None:
    """Add the subcommands that produce suggestions, the Word export and the report."""
    c = sub.add_parser("suggest-prepare")
    c.add_argument("--work", required=True)
    c.add_argument("--batch-size", type=int, default=8)
    c.set_defaults(fn=pl_cmd_report.cmd_suggest_prepare)

    c = sub.add_parser("merge-suggestions")
    c.add_argument("--work", required=True)
    c.set_defaults(fn=pl_cmd_report.cmd_merge_suggestions)

    c = sub.add_parser("export-docx")
    c.add_argument("--work", required=True)
    c.add_argument("--paper")
    c.add_argument("--out")
    c.add_argument("--only")
    c.add_argument("--exclude")
    c.add_argument("--author", default="Athetize")
    c.add_argument(
        "--no-notes", action="store_true", help="skip the explanatory comment on each edit"
    )
    c.set_defaults(fn=pl_cmd_report.cmd_export_docx)

    c = sub.add_parser("build")
    c.add_argument("--work", required=True)
    c.add_argument("--out")
    c.set_defaults(fn=pl_cmd_report.cmd_build)


def build_parser(description) -> argparse.ArgumentParser:
    """Build the pl.py parser; subcommands are added in the order the help text lists them."""
    ap = argparse.ArgumentParser(
        prog="pl.py", description=description, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    _add_setup_commands(sub)
    _add_lookup_commands(sub)
    _add_supply_commands(sub)
    _add_check_commands(sub)
    _add_output_commands(sub)
    return ap
