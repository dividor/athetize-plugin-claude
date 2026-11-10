# Athetize

[![CI](https://github.com/dividor/athetize-plugin-claude/actions/workflows/ci.yml/badge.svg)](https://github.com/dividor/athetize-plugin-claude/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

*To athetize: to mark a passage as spurious or unsupported.*

Athetize is a plugin for [Claude Code](https://claude.com/claude-code) that checks whether the sources a paper cites actually back up what the paper says. Give it a manuscript. It splits the text into claims, finds and downloads every cited source it legally can, lets you supply the rest, checks each claim against its source, and produces one self-contained `report.html`. In that report, claims are overlaid in green, amber or red on the paper's own text. Hover a claim (or tap on a phone) to see the analysis, the exact evidence from the source, and a link to it.

It is a screening aid for authors, reviewers and editors, not a judge. Every flag still needs a human look.

## What you get

- Green: a passage in the source supports the claim. The quoted evidence was found in the source text by a programmatic check.
- Amber: partly supported, or the claim is stronger than the source (correlation stated as causation, "all" from "some").
- Red: unsupported, or contradicted by the source.
- Grey: could not be verified, usually because no full text was available.
- Blue: a specific claim with no citation, or a note about an inconsistency inside the paper.
- For Word manuscripts, a copy of the paper with every suggested edit as a tracked change. Each change has a comment explaining it: the verdict, why the edit is needed, the source's analysis, evidence excerpts with page numbers, and the link. Your original file is never modified.
- Source list with status per reference: full text, abstract only, provided by you, or missing. Retracted works and references whose metadata does not match are flagged.

## Install

In Claude Code, add this repository as a plugin marketplace and install the plugin:

```
/plugin marketplace add dividor/athetize-plugin-claude
/plugin install athetize@athetize-plugin
```

The same from a terminal:

```bash
claude plugin marketplace add dividor/athetize-plugin-claude
```

```bash
claude plugin install athetize@athetize-plugin
```

To try a local clone without installing it, start Claude Code with `claude --plugin-dir /path/to/athetize-plugin-claude`.

### Requirements

Python 3.9 or newer. The helper scripts use the standard library plus a few small packages, depending on what you check:

| You want to | Needs | Install |
|---|---|---|
| Check a Markdown, LaTeX or plain-text paper | nothing extra | |
| Check a Word (.docx) paper, or use Word and XML sources | `defusedxml` | `pip install defusedxml` |
| Check a PDF paper, or use PDF sources | any one of `pymupdf` (fastest), poppler's `pdftotext`, or `pypdf` | `pip install pymupdf` |
| Export suggested edits as Word tracked changes | `lxml` | `pip install lxml` |
| Read scanned PDFs | the `tesseract` and `pdftoppm` command-line tools | your package manager |

Athetize uses the same libraries as Claude's own document skills: `defusedxml` and `lxml` for Word files and, when nothing faster is present, `pypdf` for PDFs. Where those skills already work, such as a session that runs commands in Claude's sandbox, there is nothing to install.

Everything at once: `pip install -r scripts/requirements.txt` from the plugin folder. On systems that protect the system Python, use a virtual environment or add `--break-system-packages`. Claude runs `pl.py doctor` at the start of a check and tells you what is missing.

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
| Scripts | `scripts/pl.py` and `pl_*.py` modules | Ingest, reference lookup, downloads, passage retrieval, quote verification, report build |
| Template | `templates/report.html` | The report: one file, no external requests |

The plugin has no hooks and no MCP servers. Nothing runs until you ask Claude to check a paper. The one background process is the local helper described under Privacy and safety, which runs only while you add sources from the report.

## Setup

1. Install the plugin and the Python packages you need (see Requirements).
2. Optional but recommended: a contact email for open-access lookups through Unpaywall. Tell Claude when asked, and it runs `pl.py config set-email you@example.org` for you. The address is stored in `~/.athetize/config.json` (readable only by you) and sent only to the lookup services as their polite-use identifier. Set `ATHETIZE_EMAIL` instead to avoid storing it. `ATHETIZE_HOME` moves the config folder.
3. Nothing to set up for pages that need a real browser. The scripts do not drive one. Those references go to `fetch-sources`, where Claude uses its own browser tools (Claude in Chrome, or the desktop app's browser) in your browser, with your sign-ins.

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
report.html     open in any browser; Results and Sources tabs; Word report downloads the copy with suggested edits
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

## Privacy and safety

What the plugin runs, sends, fetches and writes:

- **Runs on your machine.** Python scripts in `scripts/`. If installed, they call the `pdftotext`, `pdftoppm` and `tesseract` command-line tools to read PDFs. They do not drive a browser and bundle no browser automation. The subagents are instructed to run only the `pl.py` helper.
- **Stays on your machine.** The manuscript and your sources. Results are written to the `<paper>-check/` folder next to the paper; settings go to `~/.athetize/config.json`.
- **Sent out.** Only reference metadata (titles, authors, DOIs) and, if you set one, your contact email as the polite-use identifier. It goes to Crossref (`api.crossref.org`), OpenAlex (`api.openalex.org`), Europe PMC (`www.ebi.ac.uk`), arXiv (`arxiv.org`, `export.arxiv.org`), ERIC (`api.ies.ed.gov`, `files.eric.ed.gov`), Semantic Scholar (`api.semanticscholar.org`) and Unpaywall (`api.unpaywall.org`). `pl.py doctor` sends one test query to each service to see whether it is reachable.
- **Fetched.** Full text only from open-access routes (arXiv, Europe PMC, ERIC full text, publisher-declared open links via OpenAlex, Unpaywall and Semantic Scholar, URLs printed in the reference), from files and URLs you provide, or through your own browser session with `fetch-sources`. Nothing goes through paywall workarounds, mirrors or bot-check evasion, and Claude never enters credentials, solves captchas or buys access for you.
- **Checked before use.** A downloaded PDF or page is kept only if its text matches the reference title, and links from a doubtful metadata match are not used.
- **Network hardening.** The scripts fetch `http` and `https` URLs only, verify TLS certificates, cap response sizes, and parse XML from Word and XML files with `defusedxml`.
- **Report.** The report builds all text with `textContent`, loads nothing from the network and only follows `http` and `https` links. Text from papers is treated as data, never as instructions.
- **Local helper for adding sources.** `pl.py serve` shows the report from `127.0.0.1` so the page can hand over the files you choose. It listens on your own computer only, puts a random token in its address, refuses requests whose Host or Origin is not the helper itself, accepts only the file types Athetize reads (up to 300 MB each) and stops when you finish or after two hours without activity. On a Mac it can open the system's file chooser, through `osascript`, when you press Add sources, and it reads only what you pick there. The files you choose are copied into the check folder and go nowhere else. The report page talks to this helper and to nothing else.
- **Links for you to click.** The report and the download list link each reference to its DOI (`doi.org`), your library proxy if you set one, and, for references without a link, Google Scholar (`scholar.google.com`) and WorldCat (`search.worldcat.org`) searches. They are plain links: nothing is sent until you click one.
- No keys or credentials are bundled, and the plugin asks for none.

See [SECURITY.md](SECURITY.md) for how to report a vulnerability.

## Limitations

- Language-model judgment can be wrong. Treat the report as a prioritised reading list.
- Without full text there is no verdict beyond "can't verify". Many closed-access works need you to provide a copy.
- Scanned PDFs without a text layer need the OCR tools above; without them they cannot be read.
- Figures, tables and equations in sources are not interpreted; only extracted text is searched.
- Reference parsing and in-text citation mapping handle numbered and author-year styles, but unusual styles may need a look at `refs.json` and `claims.json`.
- Services can rate-limit. A failed lookup is reported, never silently skipped.

## Development

```bash
pip install -r requirements-dev.txt
```

```bash
pytest
```

The tests run offline with a stubbed network and simulated verdicts, including a fabricated quote that must be downgraded. A golden-output test pins the behaviour of the whole pipeline on a sample paper. Pre-commit hooks and CI enforce formatting, linting, type checks, security scans, a complexity gate and plugin manifest validation. See [CONTRIBUTING.md](CONTRIBUTING.md).

## Roadmap

- Suggested additional references for unsupported claims (search-backed, user-approved).
- DOCX-native highlights.

## License

[MIT](LICENSE)
