# Changelog

All notable changes to Athetize will be documented in this file.

## [Unreleased]

### Changed
- Asking for sources that could not be downloaded is now one question with three answers (download them, fetch through the browser, continue without), asked after the download list has been sent. Work continues on the claims that already have source text, and an unattended run opens its final summary with the request
- `pl.py request-sources` prints and saves a `summary`: how many of the paper's references are listed, how many have only an abstract or no text, and how many claims rely on at least one of them. The download list opens with the same figures
- `report.html` shows a banner above the paper when sources lack full text, with those figures, a link to the download list and a button that opens the Sources page filtered to them
- Supplied files are identified from what they say, not from their names: the DOI or arXiv number printed on the first pages, or the reference title as one run of words together with the first author's surname. The file name is looked at only when the text does not settle it. The download list, the report and the skills no longer suggest file names
- `add-source` takes several files or a folder and works out which reference each one is

### Added
- PDFs can be read with `pypdf`, the library Claude's own PDF skill uses, when neither PyMuPDF nor `pdftotext` is present. Nothing changes where one of those is installed. With this, Athetize needs no package beyond the ones Claude's own document skills use
- `pl.py serve --work W`: a helper that shows the report from `127.0.0.1` and takes the files chosen on its Sources page. It listens on this computer only, puts a random token in its address, refuses requests from other pages, and stops on "Finish and recheck" or after two hours without activity, printing what was added

### Report
- Sources is its own page, opened from a Sources button in the top bar, with a Back to document button that returns to where you were reading. The browser's Back button does the same
- The Sources page has one Add sources button. When Claude shows the report through the new local helper, the button offers Files or A folder, each opening the standard chooser, and files or a whole folder can be dropped on the page. Each file is matched to its reference at once and the list updates. Opened as a plain file, the button gives the request that gets Claude to open the report that way
- On the Sources page a claim link such as c5 opens the claim's analysis there, with a link to show it in the document
- Added files are scanned for duplicates before anything is sent or added: the same content twice, a second copy of one work, a work that already has its full text, and files added before. Each is named with the reason
- The filter on the Sources page is a pair of tabs, All and Need full text, in place of the toggle button
- The "Screening aid" box and the note about the j and k keys are gone
- The legend sits under the colour bar it explains and wraps, so no entry is hidden. Previous and next have the sticky row to themselves
- A Word report button in the top bar downloads the Word copy with the suggested edits. `export-docx` refreshes the report so the button appears
- The summary of suggested edits sits under the paper

### Fixed
- A file from the same field as several references could be matched to the wrong one, or reported as ambiguous, because any text that used the words of a title counted as carrying that title
- A file with a publisher's name such as `1-s2.0-S0019103508.pdf` is no longer taken for reference 1
- On the Sources page a "Closest database record" note no longer squeezes the citation into a narrow column

### Removed
- The optional headless browser (Playwright) and `pl.py resolve --browser`. The scripts no longer drive a browser at all. Pages that plain downloads cannot read go to the `fetch-sources` skill, which uses Claude's own browser tools in the person's browser
- `provide_as` in the manifest, `pl.py status` and the report data, and `save_as` in the download list

## [0.6.0] - 2026-11-09

First release from the public repository. The plugin's features are unchanged from 0.5.0; this release hardens it, restructures the code and sets it up for distribution as a Claude Code plugin.

### Security
- Network access now goes through one module that fetches `http` and `https` only; file, ftp and data URLs and redirects to them are refused
- The optional headless browser no longer ignores TLS certificate errors
- XML from Word and XML files is parsed with `defusedxml`; the Word export parses with entity resolution and network access switched off
- The `suggestion-writer` agent is limited to the Read and Write tools

### Report
- New look for `report.html`: a clean brand row, the paper title, a sticky row of result filters and one aligned column for the paper and its sources
- Sans-serif type, square corners and thin borders; result colours pass WCAG AA contrast in light and dark themes
- Highlights use colour plus a line style, with no glyph badges in the text
- New icon: a document with a check mark
- A heading with no text after it (the reference list heading) is no longer shown; the Sources section replaces it

### Fixed
- Building a report no longer crashes when a cited claim has no verdict yet; it shows as "Not checked"
- `add-source` no longer deletes a file that is already in the workspace `sources/` folder under the reference's name

### Changed
- **New required package for Word and XML files:** `defusedxml` (`pip install defusedxml`). `pl.py doctor` reports when it is missing
- The helper scripts are split into smaller modules; behaviour is pinned by a golden-output test
- One version number: the report and the HTTP user agent now read it from the plugin manifest
- Agents refer to the helper script and reference files through `${CLAUDE_PLUGIN_ROOT}`, and the skills pass them the helper's full path

### Added
- Marketplace manifest, so the repository installs with `/plugin marketplace add dividor/athetize-plugin-claude`
- Pre-commit hooks and CI: black, isort, flake8, mypy, bandit, detect-secrets, gitleaks, a complexity gate, pip-audit, SBOM, tests on Python 3.9 to 3.13 and `claude plugin validate`
- Tests for the network module, the security hardening, the complexity gate and the plugin layout
- CONTRIBUTING, SECURITY, CODE_OF_CONDUCT, issue and pull request templates

## [0.5.0]

- Version developed before the public repository: check-paper, add-source, recheck, suggest-edits, request-sources and fetch-sources skills; claim-extractor, claim-verifier and suggestion-writer agents; HTML report; Word tracked-changes export
