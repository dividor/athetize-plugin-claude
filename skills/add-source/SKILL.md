---
name: add-source
description: This skill should be used when the user wants to "add a source", "provide the full text for reference 3", "here is the PDF for that citation", "upload a source", "use this link for the cited paper", or needs to supply a cited work that Athetize could not download itself.
metadata:
  version: "0.1.0"
---

# Add or replace a cited source

Attach a user-supplied file or URL to one reference in an existing check workspace (`<paper>-check/`). User-provided sources always take priority over downloaded ones.

## Steps

1. Locate the workspace. Use the folder the user names, otherwise the most recent `*-check/` directory that contains `refs.json`. If none exists, say the paper has not been checked yet and offer the `check-paper` skill.
2. Run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/pl.py" status --work W` (fallback `<skill base dir>/../../scripts/pl.py`) and show the `needs_source` list: reference id, short citation, and how many claims depend on it.
3. Map each file or URL the user gave to a reference id. Match by the id they state; otherwise by title or author in the filename. When a match is uncertain, ask. Never guess between two references.
4. For each pair run `pl.py add-source --work W REF FILE_OR_URL`. Accepted types: pdf, txt, md, html, htm, docx, xml. A URL is downloaded once and stored as `sources/<REF>.<ext>`.
5. Read the printed result. If the file produced little or no text (a scanned PDF, for instance), say so and ask for a text version or a different file. An image-only PDF cannot be verified.
6. The user can also drop files straight into `W/sources/` named `<refid>.pdf` and so on; the next run picks them up. For several files with arbitrary names in one folder (such as Downloads), use `pl.py import-sources FOLDER --work W` instead, which matches each file to its reference by title and author (see the `request-sources` skill). Mention this once.
7. When all files are added, offer the `recheck` skill. Run it immediately when the user already asked for an updated report.

## Rules

- Copy the file into `W/sources/`; never modify the original.
- Do not upload sources anywhere. Only metadata lookups use the network, and only URLs the user gave are fetched.
- If a URL is paywalled or blocked, report that and ask for the file instead, or offer the `fetch-sources` skill to open it in the user's own browser.
