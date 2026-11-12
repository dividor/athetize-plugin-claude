# Athetize Fact Checker

A Claude plugin that checks whether the sources a paper cites support what the paper says. It finds each claim, gets the cited source, compares the two, and shows the result on your paper with the evidence quoted. It is a screening aid: every flag still needs a human look.

Screenshots, a sample paper to try it on and the full documentation are in the [repository](https://github.com/dividor/athetize-plugin-claude).

## Install

In Claude Code:

```
/plugin install athetize --marketplace dividor/athetize-plugin-claude
```

In the Claude desktop app or claude.ai, open Customize, Plugins, Add. Then choose Add marketplace and enter `dividor/athetize-plugin-claude`, or choose Upload plugin and pick [`athetize.zip`](https://github.com/dividor/athetize-plugin-claude/releases/latest/download/athetize.zip) from the latest release.

## Use

Ask Claude to check a paper (Word, PDF, Markdown, LaTeX or text). Athetize downloads open-access copies of the sources and asks you for the ones it could not get. Results go in a folder next to the paper: an HTML report and, for Word manuscripts, a Word copy with each suggested edit as a tracked change.

## Example prompts

- "Check the citations in draft.docx"
- "Add the sources in ~/Papers and update the report"
- "Open the report so I can add sources"
- "Suggest fixes and give me a Word version with tracked changes"
- "Which papers do I still need to download?"

To try it without a paper of your own, check the sample in [examples](examples/README.md).

## Features

- **Claim-by-claim checking.** Every cited claim is compared with the text of its source. Each result quotes the passage it rests on, and a script confirms that the quote is really in the source.
- **Finds the sources.** Downloads open-access copies, follows addresses printed in a reference, and looks on the web for reports that have no record in the academic indexes.
- **Uses your files.** Point it at a folder or pick files. Each one is read and matched to its reference by its content, whatever it is called.
- **Interactive report.** One HTML file: your paper with its figures and tables, claims highlighted by result, filters, a card for each claim, and a Sources tab where missing sources can be added.
- **Suggested edits.** Rewordings bounded by the evidence, and fixes to the reference list. View them in the report, or download a Word copy with tracked changes and comments.
- **Reference checks.** Flags retracted sources and references whose year, title or DOI disagrees with the database record.
- **Fast rechecks.** After new sources or a new draft, only what changed is checked again.

## Requirements

Python 3.9 or newer. Athetize also uses three small Python packages, `defusedxml`, `lxml` and `pypdf`, the ones Claude's own Word and PDF skills use. If any is missing, Claude offers to install it when a check starts.

## What it runs, sends and fetches

- **Runs.** Python scripts in `athetize/scripts`, where Claude runs commands. With your agreement, `pip` to install any of the three Python packages that are missing, from PyPI (`pypi.org`). If they are installed, the `pdftotext`, `pdftoppm` and `tesseract` tools to read PDFs. While you add sources from the report, a small web server on `127.0.0.1` that only the report page can reach; on a Mac it opens the system file chooser through `osascript`. It stops when you finish, or after two hours.
- **Sends.** Reference details only (titles, authors, DOIs), to find the sources: Crossref (`api.crossref.org`), OpenAlex (`api.openalex.org`), Europe PMC (`www.ebi.ac.uk`), arXiv (`arxiv.org`, `export.arxiv.org`), ERIC (`api.ies.ed.gov`, `files.eric.ed.gov`), Semantic Scholar (`api.semanticscholar.org`) and, if you set a contact email, Unpaywall (`api.unpaywall.org`). The email, when set, goes to these services as their contact address.
- **Fetches.** Full text from open-access copies, from addresses printed in a reference, and from files or addresses you provide.
- **Writes.** A `<paper>-check` folder next to your paper, and `~/.athetize/config.json` for settings.
- **Links.** The report links to `doi.org`, `scholar.google.com`, `search.worldcat.org`, `search.crossref.org`, `openalex.org` and `github.com`. Nothing is sent until you click.
- **Does not.** No hooks, no MCP servers, no telemetry, no bundled keys. Claude reads your paper and the sources; the scripts send neither anywhere.

See the [privacy policy](https://github.com/dividor/athetize-plugin-claude/blob/main/docs/privacy.md). To report a vulnerability, see [SECURITY.md](https://github.com/dividor/athetize-plugin-claude/blob/main/SECURITY.md).

## Help

- [Documentation](https://github.com/dividor/athetize-plugin-claude/blob/main/docs/reference.md)
- [Report a problem](https://github.com/dividor/athetize-plugin-claude/issues)

## License

[MIT](LICENSE)
