#!/usr/bin/env python3
"""Athetize command line helper.

The skills in this plugin call these subcommands for every deterministic step.
Python 3.9+, standard library only (PyMuPDF optional for PDF text).

  pl.py doctor
  pl.py config set-email you@example.org | show
  pl.py ingest PAPER [--work DIR]
  pl.py paragraphs --work DIR [--from p1 --to p40] [--kind body|heading|reference]
  pl.py validate --work DIR [--fix]
  pl.py merge-claims --work DIR [--append 'add-*.json']
  pl.py resolve --work DIR [--email E] [--refresh] [--no-download]
  pl.py status --work DIR
  pl.py browser-queue --work DIR [--min-claims 1]
  pl.py add-source --work DIR REF FILE_OR_URL
  pl.py passages --work DIR --ref REF --query TEXT [-k 8]
  pl.py prepare --work DIR [--batch-size 6]
  pl.py verify --work DIR
  pl.py build --work DIR [--out report.html]
  pl.py carry-over --work NEW --from OLD
  pl.py link-cites --work DIR [--dry-run]
  pl.py coverage --work DIR [--para p3,p4]
  pl.py suggest-prepare --work DIR
  pl.py merge-suggestions --work DIR
  pl.py export-docx --work DIR [--paper PAPER.docx] [--out FILE] [--only s1,s2] [--exclude s3] \
[--author NAME]
"""

from __future__ import annotations

import pl_cli
from pl_cmd_report import (
    cmd_build,
    cmd_carry_over,
    cmd_coverage,
    cmd_export_docx,
    cmd_link_cites,
    cmd_merge_suggestions,
    cmd_prepare,
    cmd_request_sources,
    cmd_suggest_prepare,
    cmd_verify,
)
from pl_cmd_setup import (
    cmd_config,
    cmd_doctor,
    cmd_ingest,
    cmd_merge,
    cmd_paragraphs,
    cmd_validate,
    out,
)
from pl_cmd_sources import (
    _needs_rows,
    _status_data,
    cmd_add_source,
    cmd_browser_queue,
    cmd_import_sources,
    cmd_passages,
    cmd_resolve,
    cmd_status,
    import_paths,
)
from pl_identify import _match_file_to_ref, _ref_title, _ref_year
from pl_serve import cmd_serve

__all__ = [
    "_match_file_to_ref",
    "_needs_rows",
    "_ref_title",
    "_ref_year",
    "_status_data",
    "cmd_add_source",
    "cmd_browser_queue",
    "cmd_build",
    "cmd_carry_over",
    "cmd_config",
    "cmd_coverage",
    "cmd_doctor",
    "cmd_export_docx",
    "cmd_import_sources",
    "cmd_ingest",
    "cmd_link_cites",
    "cmd_merge",
    "cmd_merge_suggestions",
    "cmd_paragraphs",
    "cmd_passages",
    "cmd_prepare",
    "cmd_request_sources",
    "cmd_resolve",
    "cmd_serve",
    "cmd_status",
    "import_paths",
    "cmd_suggest_prepare",
    "cmd_validate",
    "cmd_verify",
    "main",
    "out",
]


def main():
    """Parse the command line and run the chosen subcommand."""
    args = pl_cli.build_parser(__doc__).parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
