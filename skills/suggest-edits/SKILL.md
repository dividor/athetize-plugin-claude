---
name: suggest-edits
description: This skill should be used when the user asks to "suggest edits", "fix the flagged claims", "rewrite the overstated sentences", "propose rewordings", "export suggestions to Word", "make a tracked-changes version", or wants suggested fixes for claims and references that an Athetize check flagged, shown in the report and exported as a .docx with tracked changes.
metadata:
  version: "0.3.0"
---

# Suggest edits for flagged claims

Turn the check's findings into concrete edits. Each flagged claim gets a minimal rewording grounded in the quoted evidence, and the reference list gets factual fixes. All edits appear in the report as diffs, and can be exported to the original Word manuscript as tracked changes.

Helper: `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/pl.py"` (fallback `<skill base dir>/../../scripts/pl.py`), written `PL`. Workspace `W`. Read `../check-paper/references/schemas.md` (suggestions.json) first.

## Steps

1. **Prepare.** Run `PL suggest-prepare --work W`. It writes `W/suggest_batches/suggest-NNN.json` for claims judged partly supported, overstated, unsupported or contradicted that have no suggestion yet.
2. **Draft rewordings.** Launch one `suggestion-writer` agent per batch, all in one message. Each writes `W/suggest_parts/suggest-NNN.json`.
3. **Reference fixes (Claude, not the writers).** Write `W/suggest_parts/references.json` with `kind: "reference"` entries for problems the check found in the reference list. Use the whole reference paragraph as `original`.
   - Years that differ from the in-text citation, decided by the issue year in Crossref or the article PDF.
   - Wrong or mismatched DOIs and links.
   - Missing volume, issue or pages that Crossref or the PDF confirms.
   - Internal working notes left in entries, such as "[detail unverified …]".
   - Orphan fragments of deleted entries: set `suggested` to `""` to delete the paragraph.

   Only change what a lookup confirms; state the source of each fix in `rationale`. Never invent an entry for a work that is cited but missing from the list: list those for the user instead.
4. **Merge and validate.** Run `PL merge-suggestions --work W`, then `PL validate --work W`. Fix every error in the part files and merge again.
5. **Review before showing.** Read each suggestion against its verdict analysis. Drop or fix any that adds a claim not in the evidence, changes the authors' argument rather than its strength, or removes a citation without saying why.
6. **Build.** Run `PL build --work W`. Cards show the suggestions with a word diff, a copy button and an include/exclude toggle for the Word export. The Sources page shows reference fixes, and a summary line sits under the paper.
7. **Export to Word.** When the manuscript is a .docx, run `PL export-docx --work W --author "<name>"`. It uses the copy ingest kept at `W/manuscript.docx`; pass `--paper FILE.docx` to use another file. Each tracked change gets a Word comment with the card's content: verdict, why, source analyses, evidence excerpts and links (`--no-notes` turns this off). Add `--exclude s3,s7` (or `--only`) when the user excluded some; the report's "Copy exclusion list" gives them the ids. Existing tracked changes in the manuscript are kept. Check the result:
   - Open it and confirm the changes show as tracked changes.
   - With the docx skill available, run its `validate.py` with `--original` and `--author`.
   - Report any suggestions the script skipped, and why.
8. **Deliver.** Send the report and the .docx. Summarise in a few lines: how many rewordings and how many reference fixes, the most consequential ones, and anything cited but missing from the reference list.

## Rules

- Suggestions are proposals for the authors. Never present them as required, and never edit the original manuscript in place; export writes a new file.
- Every rewording must stay within what the quoted evidence shows. No new sources, numbers or findings.
- Keep the authors' voice. The smallest edit that fixes the problem wins.
