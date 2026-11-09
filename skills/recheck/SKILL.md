---
name: recheck
description: This skill should be used when the user asks to "recheck the paper", "run it on the new draft", "check the latest version", "update the report", "re-run the check after adding sources", "refresh the athetize report", "verify again", or has edited the paper text, claims or sources in an existing check workspace and wants a fresh report.
metadata:
  version: "0.1.0"
---

# Recheck after changes

Rebuild the report for an existing workspace, redoing only the claim-and-source pairs whose claim text or source text changed. Verdicts are cached by hash, so unchanged pairs are kept.

## Steps

Helper: `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/pl.py"` (fallback `<skill base dir>/../../scripts/pl.py`), written as `PL`. Workspace as `W`.

1. Find the workspace as in the `add-source` skill. Stop if `refs.json` is missing and offer `check-paper`.
2. If the paper file itself was edited, re-run `PL ingest PAPER --work W`. Paragraph ids may shift, so then re-extract claims (workflow step 4 of `check-paper`) before continuing. Existing verdicts for unchanged claim text are still reused because they are keyed by claim id and text hash; after re-extraction, run `PL validate --work W --fix`.
3. Run `PL resolve --work W` (with `--browser` when a headless browser is installed). It keeps existing metadata and downloads, notices new user files in `W/sources/`, and retries references that were missing. Add `--refresh` only when the user asks to re-query the services.
4. Run `PL coverage --work W`; if citation markers are uncovered, extract them (see check-paper step 5) before preparing. Then run `PL prepare --work W`. The output says how many claims need new verdicts. When it is zero, skip to step 6.
5. Launch one `claim-verifier` subagent per batch file in a single message, giving each the batch path, the work directory and the full path of the helper script. Then run `PL verify --work W`.
6. For newly flagged claims, run `PL suggest-prepare --work W`. When it reports flagged claims, follow the `suggest-edits` skill's steps 2 to 5 for them.
7. Run `PL build --work W`. If the paper is a .docx, also run `PL export-docx --work W` so the Word copy matches the report. Send `report.html`, plus the Word file when there is one, with SendUserFile.
8. Tell the user what changed since the last report in two or three sentences, using only figures the scripts printed, and list the references that still need a source. If any remain, offer a download list (the `request-sources` skill); if a browser is connected, also offer the `fetch-sources` skill. When the person says they saved files into a folder, run `request-sources` step 3 to import them.

## A new draft of a paper that was already checked

When the user gives a revised version of the manuscript (a new file, not edits inside the workspace), start a new workspace and carry the earlier work forward instead of starting over:

1. `PL ingest NEW_PAPER --work W_NEW`.
2. Write `W_NEW/refs.json` from the new reference list (check-paper step 3). Reuse the earlier reference ids for works that are still cited, so their sources carry over; give new works new ids.
3. `PL carry-over --work W_NEW --from W_OLD`. It copies sources and manifest entries, and brings across the claims whose text is unchanged. It also brings their verdicts when claim and source text are unchanged, and the suggestions whose original text still appears. Report its counts, including suggestions the authors already applied.
4. `PL resolve --work W_NEW` (with `--browser` when available) for the new references.
5. Run claim-extractor agents on the paragraphs that changed or are new, writing `claims_parts/add-NN.json`. Then run `PL merge-claims --work W_NEW --append 'add-*.json'` and `PL coverage --work W_NEW` until no citation is uncovered.
6. Continue from step 4 above: prepare, verify, suggest, build and export. Tell the user what changed between the drafts: new flags, resolved flags and reference-list fixes made.

## Rules

- Never delete `verdicts.json` unless the user asks for a full re-run; if they do, delete it and run steps 4 to 6.
- Do not alter earlier verdicts by hand.
- Keep the same screening-aid framing as `check-paper`.
