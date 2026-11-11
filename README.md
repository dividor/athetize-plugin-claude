# Athetize

[![CI](https://github.com/dividor/athetize-plugin-claude/actions/workflows/ci.yml/badge.svg)](https://github.com/dividor/athetize-plugin-claude/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

*To athetize: to mark a passage as spurious or unsupported.*

A Claude plugin that checks whether the sources a paper cites support what the paper says. It finds each claim, gets the cited source, compares the two, and shows the result on your paper with the evidence quoted. It is a screening aid: every flag still needs a human look.

![The report: a paper with each claim highlighted by its result](docs/images/report-highlights.jpg)

## Install

In Claude Code:

```
/plugin marketplace add dividor/athetize-plugin-claude
/plugin install athetize@athetize-plugin
```

**Requirements.** Python 3.9 or newer. Athetize uses the same libraries as Claude's own Word and PDF skills, so where those work there is nothing to install. On your own computer, install them once:

```bash
pip install defusedxml lxml pypdf
```

## Use

Ask Claude:

```text
Check the citations in draft.docx
```

Athetize reads the paper (Word, PDF, Markdown, LaTeX or text), downloads open-access copies of the sources, and asks you for the ones it could not get. You can point it at a folder of papers, pick files, or skip. Results go in a folder next to the paper:

- `report.html`: your paper with every claim highlighted, the evidence behind each result, and a Sources tab.
- A Word copy with each suggested edit as a tracked change and a comment that says why, for Word manuscripts.

Other things to say:

| Say | What happens |
| --- | --- |
| "Add the sources in ~/Papers" | Each file is read and matched to its reference. No renaming. |
| "Open the report so I can add sources" | Add files from the report's Sources tab. |
| "Update the report" | Rechecks only what changed. |
| "Suggest fixes" | Drafts rewordings and reference-list fixes. |

To try it without a paper of your own, check the sample in [examples](examples/README.md).

## What you get

| Result | Meaning |
| --- | --- |
| Supported | A passage in the source backs the claim. |
| Partial | Partly backed, or the claim goes further than the source. |
| Not supported | The source does not back the claim, or contradicts it. |
| Can't verify | The source could not be read. Never guessed. |
| Uncited | A specific claim with no citation. |

A result that rests on an abstract only is marked provisional. Retracted sources and reference-list errors are flagged.

Click a claim for the analysis, the quoted passage and a suggested rewording:

![A claim's card: the result, the analysis and the evidence quoted from the source](docs/images/report-claim-card.jpg)

The Sources tab shows where each source's text came from and lets you add the missing ones:

![The Sources tab: each reference, where its text came from and the claims that cite it](docs/images/report-sources.jpg)

The Word copy, shown here in LibreOffice:

![The Word copy: tracked changes with a comment explaining each one](docs/images/word-export.png)

## Privacy

- Claude reads your paper and the sources. The plugin's scripts send neither anywhere.
- Only reference details (titles, authors, DOIs) are sent out, to find the sources: Crossref (`api.crossref.org`), OpenAlex (`api.openalex.org`), Europe PMC (`www.ebi.ac.uk`), arXiv (`arxiv.org`, `export.arxiv.org`), ERIC (`api.ies.ed.gov`, `files.eric.ed.gov`), Semantic Scholar (`api.semanticscholar.org`) and, if you set a contact email, Unpaywall (`api.unpaywall.org`).
- Full text is downloaded only from open-access copies, from addresses printed in a reference, and from files you provide.
- The report links to `doi.org`, `scholar.google.com`, `search.worldcat.org`, `search.crossref.org`, `openalex.org` and `github.com`. Nothing is sent until you click.
- No hooks, no MCP servers, no bundled keys.

The full account is in [docs/privacy.md](docs/privacy.md). To report a vulnerability, see [SECURITY.md](SECURITY.md).

## Limitations

- The checking is done by AI and can be wrong. Read the quoted evidence before you change your paper.
- Without a source's text there is no result beyond "can't verify".
- Figures, tables and equations in sources are not interpreted, and scanned PDFs need OCR tools.

## More

- [docs/reference.md](docs/reference.md): every skill and agent, the files a check writes, and how results are kept honest.
- [CHANGELOG.md](CHANGELOG.md) and [CONTRIBUTING.md](CONTRIBUTING.md).

Development: `pip install -r requirements-dev.txt`, then `pytest`.

## License

[MIT](LICENSE)
