# Athetize reference

The details behind the [README](../README.md): every skill and agent, what to say, where results go and how verdicts are kept honest.

## Requirements in detail

Python 3.9 or newer. The helper scripts use the standard library plus a few small packages, depending on what you check:

| You want to | Needs | Install |
|---|---|---|
| Check a Markdown, LaTeX or plain-text paper | nothing extra | |
| Check a Word (.docx) paper, or use Word and XML sources | `defusedxml` | `pip install defusedxml` |
| Check a PDF paper, or use PDF sources | any one of `pymupdf` (fastest), poppler's `pdftotext`, or `pypdf` | `pip install pymupdf` |
| Export suggested edits as Word tracked changes | `lxml` | `pip install lxml` |
| Read scanned PDFs | the `tesseract` and `pdftoppm` command-line tools | your package manager |

Athetize uses the same libraries as Claude's own document skills: `defusedxml` and `lxml` for Word files and, when nothing faster is present, `pypdf` for PDFs. Where those skills already work, such as a session that runs commands in Claude's sandbox, there is nothing to install.

Everything at once: `pip install -r athetize/scripts/requirements.txt` from the plugin folder. On systems that protect the system Python, use a virtual environment or add `--break-system-packages`. Claude runs `pl.py doctor` at the start of a check and tells you what is missing.

To try a local clone without installing it, start Claude Code with `claude --plugin-dir /path/to/athetize-plugin-claude`.

## Components

| Type | Name | Purpose |
|---|---|---|
| Skill | `check-paper` | Full pipeline from manuscript to report |
| Skill | `add-source` | Add files, a folder of files or a URL as sources; each file is read and matched to its reference |
| Skill | `recheck` | Re-verify only what changed and rebuild the report |
| Skill | `suggest-edits` | Draft minimal rewordings for flagged claims and reference-list fixes, show them in the report, export a tracked-changes .docx |
| Skill | `request-sources` | Write a download list (links, library proxy) for references still lacking full text; import whatever you save into a folder, under any file names |
| Skill | `fetch-sources` | Open still-missing references in your own browser (your sign-ins), save the full text, then recheck |
| Agent | `claim-extractor` | Finds claims in a range of paragraphs (parallel, for long papers) |
| Agent | `claim-verifier` | Compares claims with source passages for one batch (parallel) |
| Agent | `suggestion-writer` | Drafts evidence-bounded rewordings for one batch of flagged claims (parallel) |
| Scripts | `athetize/scripts/pl.py` and `pl_*.py` modules | Ingest, reference lookup, downloads, passage retrieval, quote verification, report build |
| Template | `athetize/templates/report.html` | The report: one file, no external requests |

The plugin has no hooks and no MCP servers. Nothing runs until you ask Claude to check a paper. The one background process is the local helper described under Privacy and safety, which runs only while you add sources from the report.

## Use

Say, for example:

- "Check the citations in `draft.docx`." Claude runs `check-paper`.
- "Here is the PDF for reference 4." Claude runs `add-source`, then offers `recheck`.
- "Add the sources in `~/Papers`." or "Add the attached files as sources." Claude runs `add-source`. You do not name or number the files: each one is read and matched to its reference.
- "Open the report so I can add sources." Claude runs `pl.py serve` and gives you an address on your own computer. On the report's Sources page, **Add sources** then lets you choose files or a whole folder (one system chooser on a Mac); you can also drop them on the page. Each file is matched to its reference straight away, duplicates are skipped and named, and **Finish and recheck** hands the list back to Claude. On that page a claim link such as `c5` opens the claim's analysis in place. This works where Claude's commands run on your own computer.
- "Update the report." Claude runs `recheck`.
- "Run it on the new draft." Claude starts a new workspace and runs `pl.py carry-over` from the old one. Unchanged claims keep their sources and verdicts, so only new or edited text is checked again.
- "Get the missing sources through my browser." Claude runs `fetch-sources`.
- When sources are missing, Claude asks how you want to add them: choose a folder that holds the papers, pick the files, fetch them through your browser, or skip. You never rename or sort anything.
- "Which papers do I need to download?" Claude runs `pl.py request-sources`, which writes `source_request.md`: one short table with a link for each missing reference (add `pl.py config set-proxy https://doi-org.<library>.idm.oclc.org` for library links). "I saved them" then runs `pl.py import-sources FOLDER`, which reads each file, identifies its reference from the DOI or arXiv number on its first pages or from the title and first author, and skips anything already imported. File names do not matter.
- "Suggest fixes and give me a Word version with tracked changes." Claude runs `suggest-edits`.

Results live in a folder next to the paper called `<paper>-check/`:

```
paper.json  refs.json  claims.json  notes.json  manifest.json  verdicts.json  suggestions.json
manuscript.docx                      private copy of a Word manuscript (used for the export)
<paper>_athetize_suggestions.docx    tracked changes plus an explanatory comment on each
sources/        the sources you supplied, filed by reference
sources/_auto/  downloaded full text and abstracts
report.html     open in any browser; Results and Sources tabs; Download Report gives the Word copy with suggested edits
```

"Open local copy" links, and "Open at p. N" links on each evidence excerpt (which jump to that page of the PDF), appear only when `report.html` is opened from the check folder on your own computer (they point into `sources/`). Browsers do not let a web page open files on your computer. A hosted copy of the report never includes the source PDFs; its citation links go to the publisher or DOI instead.

A source you provide always wins over a downloaded one.

## How the checks stay honest

- Every green, amber or red verdict must quote the source. The script searches the source text for that quote; if it is not there, or if numbers or negations differ, the verdict becomes "can't verify".
- A reference with no text is never marked unsupported. An abstract alone can confirm a claim but cannot show a paper lacks support.
- Verdicts are cached against hashes of the claim and the source, so a recheck only redoes what changed.
- The report shows page numbers when the source is a PDF.

## Suggested edits

`suggest-edits` adds, for each partly supported, overstated or contradicted claim, the smallest rewording that stays within the quoted evidence, plus factual reference-list fixes confirmed by Crossref or the source PDF (years, DOIs, missing volume and pages, leftover working notes, orphan fragments). Cards show each suggestion as a phrase-level diff with a copy button and an include/exclude toggle. `pl.py export-docx` writes a new copy of the Word manuscript with the included suggestions as tracked changes (needs `lxml`); the original file and its existing tracked changes are left as they are.

## Contact email

Unpaywall asks for a contact email with each lookup. Set one in the report's Settings, or tell Claude and it runs `pl.py config set-email you@example.org`. It is stored in `~/.athetize/config.json`, readable only by you, and sent only to the lookup services. Without it, Unpaywall is skipped and everything else still works.
