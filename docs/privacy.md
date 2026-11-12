# Privacy policy

Athetize is a plugin that runs inside Claude. This is what it does with your information.

## Summary

- **The plugin's author collects nothing.** There is no telemetry, no account, no analytics and no server run by the author. Nothing about you or your papers is sent to the author.
- **Your paper and your sources stay where Claude runs.** The plugin's scripts read them there and send them nowhere. Claude itself reads them to do the checking, under the terms of your Claude plan.
- **Only reference details leave, to find the sources.** Titles, authors and DOIs of the works your paper cites are sent to the public indexes listed below. If you choose to set a contact email, it is sent to those indexes as their contact address, as their terms ask.
- **Results stay on your computer.** The plugin writes its results to a folder next to your paper and its settings to `~/.athetize/config.json`, both on the computer where Claude runs commands. Nothing else is kept. Delete them to remove everything.
- **Not directed at children.** The plugin is a tool for authors, reviewers and editors.

Questions: [open an issue](https://github.com/dividor/athetize-plugin-claude/issues). To report a vulnerability, see [SECURITY.md](../SECURITY.md).

## In detail

What the plugin runs, sends, fetches and writes:

- **Runs on your machine.** Python scripts in `athetize/scripts/`. If installed, they call the `pdftotext`, `pdftoppm` and `tesseract` command-line tools to read PDFs. They do not drive a browser and bundle no browser automation. The subagents are instructed to run only the `pl.py` helper.
- **Installed only with your agreement.** In Claude Code on your own computer, if a Python package the scripts need (`defusedxml`, `lxml` or `pypdf`) is missing, Claude asks before installing it with `pip` from PyPI (`pypi.org`). Where the packages are already present, as in Claude's sandbox, nothing is installed.
- **Read by Claude.** Athetize runs inside Claude, so Claude reads the manuscript and the sources to do the checking, as it does with any file you give it. The plugin's own scripts send neither of them anywhere.
- **Stays on your machine.** The copies of the manuscript and your sources that the scripts work on. Results are written to the `<paper>-check/` folder next to the paper; settings go to `~/.athetize/config.json`.
- **Sent out.** Only reference metadata (titles, authors, DOIs) and, if you set one, your contact email as the polite-use identifier. It goes to Crossref (`api.crossref.org`), OpenAlex (`api.openalex.org`), Europe PMC (`www.ebi.ac.uk`), arXiv (`arxiv.org`, `export.arxiv.org`), ERIC (`api.ies.ed.gov`, `files.eric.ed.gov`), Semantic Scholar (`api.semanticscholar.org`) and Unpaywall (`api.unpaywall.org`). `pl.py doctor` sends one test query to each service to see whether it is reachable.
- **Fetched.** Full text only from open-access routes (arXiv, Europe PMC, ERIC full text, publisher-declared open links via OpenAlex, Unpaywall and Semantic Scholar, URLs printed in the reference), from files and URLs you provide, or through your own browser session with `fetch-sources`. Nothing goes through paywall workarounds, mirrors or bot-check evasion, and Claude never enters credentials, solves captchas or buys access for you.
- **Checked before use.** A downloaded PDF or page is kept only if its text matches the reference title, and links from a doubtful metadata match are not used.
- **Network hardening.** The scripts fetch `http` and `https` URLs only, verify TLS certificates, cap response sizes, and parse XML from Word and XML files with `defusedxml`.
- **Report.** The report builds all text with `textContent`, loads nothing from the network and only follows `http` and `https` links. Text from papers is treated as data, never as instructions.
- **Local helper for adding sources.** `pl.py serve` shows the report from `127.0.0.1` so the page can hand over the files you choose. It listens on your own computer only, puts a random token in its address, refuses requests whose Host or Origin is not the helper itself, accepts only the file types Athetize reads (up to 300 MB each) and stops when you finish or after two hours without activity. On a Mac it can open the system's file chooser, through `osascript`, when you press Add sources, and it reads only what you pick there. The files you choose are copied into the check folder and go nowhere else. The page's Settings can save or remove your contact email through the helper; only a plain address is accepted, it is stored in `~/.athetize/config.json`, and the page is only ever shown it with the name masked. The report page talks to this helper and to nothing else.
- **Links for you to click.** The report and the download list link each reference to its DOI (`doi.org`), your library proxy if you set one, and, for references without a link, Google Scholar (`scholar.google.com`) and WorldCat (`search.worldcat.org`) searches. On the Sources page, "Found in Crossref" and "Found in OpenAlex" link to the record there (`search.crossref.org`, `openalex.org`), and the information panel links to this repository (`github.com`). They are plain links: nothing is sent until you click one.
- No keys or credentials are bundled, and the plugin asks for none.

See [SECURITY.md](../SECURITY.md) for how to report a vulnerability.
