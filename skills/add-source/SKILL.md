---
name: add-source
description: This skill should be used when the user wants to "add a source", "provide the full text for reference 3", "here is the PDF for that citation", "upload a source", "use this link for the cited paper", "add these sources", "add the sources in this folder", "add the attached files as sources", "open the report so I can add sources", or needs to supply cited works that Athetize could not download itself.
metadata:
  version: "0.1.0"
---

# Add or replace a cited source

Add files, a folder of files or a URL the user supplies to an existing check workspace (`<paper>-check/`). The person never has to name, number or sort the files: the helper reads each one and works out which reference it is. User-provided sources always take priority over downloaded ones.

## Steps

1. Locate the workspace. Use the folder the user names, or the `<paper name>-check/` folder beside the paper they name, otherwise the most recent `*-check/` directory that contains `refs.json`. If none exists, say the paper has not been checked yet and offer the `check-paper` skill.
2. Run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/pl.py" status --work W` (fallback `<skill base dir>/../../scripts/pl.py`) and show the `needs_source` list: reference id, short citation, and how many claims depend on it.
3. Work out which reference each file belongs to.
   - The person said which reference it is (an id, or "this is the Strobel paper"): use that.
   - Otherwise run `pl.py import-sources PATH... --work W --dry-run`, giving the files, the folder, or both. It reads each file and identifies its reference from the DOI or arXiv number printed on its first pages, or from the reference title together with the first author's surname. The file name is looked at only when the text does not settle it. Add `--all` when the person is replacing a source that already has full text, and `--recursive` for a folder with subfolders.
   - Show anything listed under `ambiguous` or `unmatched` and ask which reference it is. Never guess between two references, and never ask the person to rename a file.
   - Files listed under `duplicates` are not added: they repeat another file's content, are a second copy of a work already taken, or belong to a reference that already has its full text. Say so in one line, with the reason given for each.
4. Add them. Run the same `import-sources` command without `--dry-run` for the files it matched. For a file whose reference the person stated, and for a URL, run `pl.py add-source --work W REF FILE_OR_URL`. Accepted types: pdf, txt, md, html, htm, docx, xml. A URL is downloaded once.
5. Read the printed result. If the file produced little or no text (a scanned PDF, for instance), say so and ask for a text version or a different file. An image-only PDF cannot be verified.
6. When all files are added, offer the `recheck` skill. Run it immediately when the user already asked for an updated report.

## Adding from the report page

The report's Sources page has an **Add sources** button. It lets the person choose files or a whole folder, or drop them on the page, and skips duplicates, when the report is shown by the plugin's helper instead of opened as a file. Use this when the person asks to add sources from the report, or says "open the report so I can add sources".

1. Run `pl.py serve --work W` as a background command, never in the foreground: it waits for the person. Its first line of output is JSON with a `url` on `127.0.0.1`.
2. Give the person that address and say: open it, go to Sources, press Add sources. Open it for them when a browser tool is available.
3. The helper reads each file they choose, matches it to its reference and adds it. A reference that still lacks its full text also has its own **Upload document** button on its row: a file given there is added to that reference as it is, with no matching, because the person has said which one it is. It stops when they press "Finish and recheck", or after two hours without activity, and prints `added`, `not_recognised` and `next`.
4. When it has stopped and `email_changed` is true: the person saved a contact email in the page's Settings. Run `pl.py resolve --work W` so open-access copies are looked up with it, then continue.
5. When it has stopped: for each file under `not_recognised` (kept in `not_recognised_folder`), ask which reference it is and add it with `pl.py add-source --work W REF FILE`. If anything was added, run the `recheck` skill and tell the person the report is ready. Start the helper again if they want to add more.

This needs the session's commands to run on the person's own computer. In a cloud or sandboxed session their browser cannot reach the helper: ask for attached files or a folder instead (the steps above).

## Rules

- Copy the file into `W/sources/`; never modify the original.
- Do not upload sources anywhere. Only metadata lookups use the network, and only URLs the user gave are fetched.
- If a URL is paywalled or blocked, report that and ask for the file instead, or offer the `fetch-sources` skill to open it in the user's own browser.
