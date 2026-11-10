"""Workspace data model, validation, batching, quote verification and report data.

The code lives in smaller modules; this module gathers their names in one place so that
``pl_core.<name>`` keeps working for the command-line tool and the tests:

- ``pl_workspace``: the workspace directory, JSON files and saved configuration
- ``pl_validate``: checks on refs, claims, notes and suggestions
- ``pl_claims``: merging and appending claims, carry-over from an earlier draft
- ``pl_cites``: author-year citation linking and citation-marker coverage
- ``pl_verify``: verifier batches and checking of the verdicts that come back
- ``pl_suggest``: suggestion batches, merging, and Word-comment notes
- ``pl_report``: report data, labels and search links
"""

from __future__ import annotations

from pl_cites import _CITE_PATTERNS, _SENT_SPLIT, _ref_keys, coverage, link_cites
from pl_claims import append_claim_parts, carry_over, merge_claim_parts
from pl_report import (
    KIND_LABEL,
    NEEDS_SOURCE,
    RANK,
    REQUEST_FILE,
    VERDICT_COLOR,
    VERDICT_LABEL,
    _clean_raw,
    _search_urls,
    aggregate,
    build_data,
    build_report,
    evidence_basis,
    safe_json_for_html,
    sources_needed,
    word_diff,
    word_export_name,
)
from pl_suggest import (
    _HEAD,
    _MUTED,
    FLAGGED,
    _clip,
    merge_suggestions,
    suggest_prepare,
    suggestion_notes,
)
from pl_validate import CLAIM_TYPES, validate
from pl_verify import (
    NEEDS_QUOTE,
    VERDICTS,
    _evidence_text,
    _merge_raw,
    prepare,
    verify,
    verify_verdict,
)
from pl_workspace import Work, config_path, jload, jsave, load_config, save_config

__all__ = [
    "CLAIM_TYPES",
    "FLAGGED",
    "KIND_LABEL",
    "NEEDS_QUOTE",
    "NEEDS_SOURCE",
    "RANK",
    "REQUEST_FILE",
    "VERDICTS",
    "VERDICT_COLOR",
    "VERDICT_LABEL",
    "Work",
    "_CITE_PATTERNS",
    "_HEAD",
    "_MUTED",
    "_SENT_SPLIT",
    "_clean_raw",
    "_clip",
    "_evidence_text",
    "_merge_raw",
    "_ref_keys",
    "_search_urls",
    "aggregate",
    "append_claim_parts",
    "build_data",
    "build_report",
    "evidence_basis",
    "carry_over",
    "config_path",
    "coverage",
    "jload",
    "jsave",
    "link_cites",
    "load_config",
    "merge_claim_parts",
    "merge_suggestions",
    "prepare",
    "safe_json_for_html",
    "save_config",
    "sources_needed",
    "suggest_prepare",
    "suggestion_notes",
    "validate",
    "word_export_name",
    "verify",
    "verify_verdict",
    "word_diff",
]
