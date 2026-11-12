# Changelog

All notable changes to Athetize will be documented in this file.

## [Unreleased]

### Changed
- The README screenshots are sharp: PNG at twice the pixel density, where they were 800-pixel JPEGs that GitHub stretched
- The social preview image shows two result labels, "Claim supported" and "Claim not supported", in place of a card with text
- The privacy policy says that a missing Python package is installed from PyPI only after the person agrees, and its summary no longer says "Nothing is kept" just before saying where results are written

## [0.8.6] - 2026-11-11

### Fixed
- Choosing a folder for missing sources asked the person to type a path even where a folder chooser was available. The skill named the desktop app's folder tool loosely, so Claude could miss it. It now gives the real tool names, `mcp__ccd_directory__request_directory` for the Code tab and `mcp__cowork__request_cowork_directory` for Cowork, and searches for them before anything else

### Changed
- Before asking how to add missing sources, the check works out what the session can do, and offers only answers it can honour. A chat cannot reach the person's folders, so it offers Attach files and no folder answer

## [0.8.5] - 2026-11-11

### Added
- When a Python package Athetize needs is missing, the check asks once at the start and installs it with `pl.py install-packages`. That installs only the missing ones, into the Python that runs the scripts: the active virtual environment, or else the person's user folder. It checks each one afterwards in a fresh process, and says what will not work if any is still missing

### Changed
- The README no longer has an install step for Python packages. The `pip` line remains in Troubleshooting

## [0.8.4] - 2026-11-11

### Added
- A social preview image for the repository, `docs/images/social-preview.png`
- Bug reports ask where the plugin was run: the terminal, the desktop app's Code tab or Cowork, or chat

### Changed
- Dependency updates from Dependabot: lxml 6.1.3 or newer for the plugin; pytest-cov, pre-commit, pypdf, flake8 and radon for development
- PyMuPDF 2.1 or newer on Python 3.11 and newer. It needs Python 3.11, so older Pythons keep 1.24.3 or newer, and the plugin still supports Python 3.9
- The plugin imports PyMuPDF as `pymupdf`. The old `fitz` name is deprecated in PyMuPDF 2 and will be removed
- CI checks that the plugin's requirements install on every Python it supports, and the development requirements on Python 3.11. CI installs its test packages without reading those files, so a minimum version that no longer installs could pass unnoticed
- The issue forms no longer point to Discussions, which are not enabled, and their paths match the plugin's folder
- The README screenshots show the current report, and a new one shows the Suggested Edits view

## [0.8.3] - 2026-11-11

### Changed
- The document's name no longer sits in a band at the top of the page. The paper's own title, when the manuscript marks one, opens the document, on its own line and aligned as in the paper
- The view switch reads "Highlight Checks" and "Suggested Edits". It has a row of its own under the title, or under the opening heading, ruled off from the paper below. Title and switch are left-aligned
- The reference list is shown at the end of the paper. An entry with a suggested fix is marked and opens its card, and the Suggested Edits view shows the fix as tracked changes, with the stepper moving through reference fixes too
- Download Report sits at the right end of the tab row, so it shows on both pages
- The information panel's diagram shows the paper indexes: Crossref and OpenAlex, Unpaywall as an option, and retracted sources flagged. Its text says so too

## [0.8.2] - 2026-11-11

### Changed
- The plugin is shown as "Athetize Fact Checker" in the app and in the marketplace. Its id stays `athetize`, so install commands and existing installs are unchanged

## [0.8.1] - 2026-11-11

Found by running the plugin in a cloud session linked to a computer.

### Changed
- A check no longer sends a list file of the missing sources. The report's Sources tab already lists them; the list is written only when the person asks which papers to download
- Choosing a folder now has a route for each kind of session. In a cloud session linked to the person's computer, the connected folder is listed and the folders that hold papers are offered as answers, so nothing is typed
- In a cloud or sandboxed session the person is no longer pointed at Add sources in the report, which cannot take files there
- Opened as a plain file, the report's Add sources says in two lines that it cannot take files and how to hand them over. The request to copy and paste is gone

## [0.8.0] - 2026-11-10

Prepared for submission to the Claude plugin directory.

### Changed
- The plugin now lives in its own `athetize/` folder, which is all that is installed. Tests, examples, docs and development tooling sit beside it and are no longer shipped to users. The marketplace entry points at that folder, so the install commands are unchanged
- The README says everything the plugin runs, sends, fetches and writes, gives example prompts and has a troubleshooting table, as the directory's checklist asks

### Added
- Listing details in the manifest: a display name, an icon, and links to the documentation, support and privacy policy
- A privacy policy, `docs/privacy.md`
- A copy of the licence and a README of its own inside the plugin folder, so a zip of the plugin stands alone. A test keeps that README's account of what the plugin runs and sends identical to the main one
- The one-step install line, `/plugin install athetize --marketplace dividor/athetize-plugin-claude`
- CI validates the plugin with the directory profile
- Tests that hold the plugin folder to the directory's file rules: text and pictures only, size and count limits

## [0.7.26] - 2026-11-10

### Changed
- README: install steps for the Claude desktop app and claude.ai beside the Claude Code ones; the requirements no longer say both that nothing needs installing and to install it; "What you get" is now "Features" and lists what the plugin does

## [0.7.25] - 2026-11-10

### Changed
- The README is rewritten to be short and to follow the usual layout of a plugin repository: what it is, install, use, what you get, privacy, limitations. The long material moved to `docs/reference.md` and `docs/privacy.md`

## [0.7.24] - 2026-11-10

### Added
- The sample paper's Word file, `examples/bananas/banana_sample.docx`, is in the repository. A test keeps it in step with its text. The repository's rule against binary files now allows small Word files under `examples/`, which the plugin itself does not ship

## [0.7.23] - 2026-11-10

### Added
- A sample paper about bananas under `examples/`, with figures and a builder that makes its Word file with a SAMPLE watermark. Its claims were written to produce every kind of result
- Screenshots of a check of that sample in the README
- The information panel has a small diagram of what Athetize does, and its wording is more formal
- The check-paper skill looks on the web for references that are reports or web documents and have no record in the citation databases

### Fixed
- Full text from Europe PMC was never fetched when the PubMed Central id came from OpenAlex, which gives it without its "PMC" prefix. The id is now put in the form Europe PMC expects, so more open-access papers are downloaded
- A paragraph centred by its Word style, and not only by its own setting, is centred in the report

### Changed
- The help under the contact email in Settings is one short line

## [0.7.22] - 2026-11-10

### Changed
- In Settings the contact email is marked "(Optional)"

## [0.7.21] - 2026-11-10

### Added
- A cog at the top opens Settings, with one setting: the contact email that Unpaywall asks for. It is saved or removed through the helper, checked to be a plain address, and shown back only with the name masked. A source that was not looked up on Unpaywall for want of an email has an "Add email" link that opens it
- On the Sources page, "Found in Crossref" and "Found in OpenAlex" are links to the record there

### Changed
- On the Sources page the label "Cited for" reads "Citations", and Upload document sits at the right of that same line
- The command `config set-email` uses the same strict check of the address

## [0.7.20] - 2026-11-10

### Changed
- Download Report is a plain link on the document's own line, its text level with the document's name, where it was a button
- Pressing Add sources opens the chooser and nothing else. The box of text that appeared on the Sources page while the chooser was open is gone; what was added is still shown afterwards

### Fixed
- The golden test file had grown past the repository's 256 KB limit for text files, because it kept a full copy of the report template. The test now checks that a built report is the template around its data and keeps only that data

## [0.7.19] - 2026-11-10

### Changed
- Every link that leaves the report opens in a new tab. Each link already asked for one; the page now enforces it for any link, so none can be missed

## [0.7.18] - 2026-11-10

### Added
- The information panel links to Athetize on GitHub. The address comes from the plugin's manifest

## [0.7.17] - 2026-11-10

### Changed
- The Highlight and Suggested Edits switch sits inside the paper, at its top right beside the first heading. It is a joined pair with a ruled edge, so it cannot be mistaken for the grey result filters
- Download Report and Add sources are softer: a light blue tint with a ruled edge and medium-weight text, where they were solid blocks

## [0.7.16] - 2026-11-10

### Changed
- The Word download button reads "Download Report"
- The switch between the two views of the paper reads "Highlight" and "Suggested Edits" and sits in the toolbar line, beside the hint and the stepper, so it no longer takes a row of its own. The hint follows the view

## [0.7.15] - 2026-11-10

### Added
- A switch above the paper shows it either with the results highlighted or with the suggested rewordings marked in the text, deletions struck through and additions underlined, as in the Word document. In that view the stepper moves from edit to edit, and a suggestion left out of the Word document is left out here too

### Changed
- The line about missing sources sits above the result filters
- The filter chips share the whole width of their row, so every row of the page ends at the same edge

## [0.7.14] - 2026-11-10

### Changed
- The top of the report is simpler. The Word download sits beside the document's name and reads "Updated Document"
- The result filters are plain square chips on a soft fill, with no outlines, in one toolbar under the tabs. The hint and the previous and next stepper share the line below them, and the stepper has no boxes

## [0.7.13] - 2026-11-10

### Changed
- The result filters are one ruled strip of square toggles, with no rounded corners and no "Filter" label. A filter that is on takes the look of the highlight it picks out in the paper: a soft tint over a coloured rule

## [0.7.12] - 2026-11-10

### Changed
- The coloured bar above the results is removed. The results (Supported, Partial and so on) are now filter chips: outlined and rounded, with a "Filter" label, and they fill in when switched on
- The box about missing sources is one short line: how many sources have no full text and how many claims cannot be fully checked yet
- The information panel says plainly to read the quoted evidence before changing the paper

## [0.7.11] - 2026-11-10

### Added
- On the Sources page, each reference with no full text, or only an abstract, has an **Upload document** button. The file is added to that reference as it is, without matching. A file with almost no text is refused, and a file whose text reads like another reference is added with a note saying so
- An information button at the top says briefly what Athetize does, that the checking is done by AI and can be wrong, and what is sent where
- Links that leave the report carry a small arrow, since they open in a new tab

## [0.7.10] - 2026-11-10

### Changed
- The Word button reads "Word document with suggested edits"
- A rule under the Athetize Fact Checker row sets it apart from the document. The document's name is smaller and lighter, under a small "Document checked" label, so it no longer competes with the product name

## [0.7.9] - 2026-11-10

### Added
- The report shows a Word paper the way it was written. Headings are headings, at their depth, including ones the author only set in bold. Bold, italic, subscripts and superscripts are kept. Tables are drawn as tables, with merged cells and ruled lines where the paper has them. The paper's pictures stand where they are, with their captions. Centred lines stay centred
- Add sources has a plus icon

### Changed
- The Results and Sources tabs no longer look like buttons. They are folder tabs: the page you are on is white with a coloured top edge and opens into the page, the other is a grey tab behind it. Only actions are filled buttons

## [0.7.8] - 2026-11-10

### Changed
- Statuses are quiet text with a small square in their colour, not boxes, so they do not read as buttons. A source with full text says how it got it: "Full text, downloaded" or "Full text, provided by you"
- The line under the All and Need full text tabs starts at the edge of the cards
- The box about missing sources on the main page is calmer: a soft border, one sentence and its button beside it
- The Word button reads "Word copy with suggested edits"

## [0.7.7] - 2026-11-10

### Changed
- The report is headed "Athetize Fact Checker"; the "Citation check" tag is gone
- The tabs sit on a rule that starts where they start, in line with the title
- The Word report button is filled, so it stands out
- The date of the check, the file name and the version sit in a small line at the foot of the page
- The stepper never shows a dash. It reads 1 before anything is picked, and the first press goes to the first claim
- On the Sources page, the claims a source is cited for are chips in the legend's colours, by what that source did for the claim. Notes about the reference itself are a chip too, such as s1, and open in a card. The inline "Differs from the database record" line and the inline fix are gone
- The paragraph of instructions under Add sources is removed

## [0.7.6] - 2026-11-10

### Changed
- The Results and Sources tabs are boxes on a rule, with the page you are on filled in
- The line under the title (file name, date, counts) is removed

## [0.7.5] - 2026-11-10

### Changed
- The top of the report is simpler. The bar holds only the name and the light and dark switch; Results and Sources are tabs under the title; the Word report button sits with previous and next on the Results page. The "Back to document" bar is gone, since the Results tab does that
- Source labels say what they mean. "Located" is now "Found in Crossref" or "Found in OpenAlex", with a note on hover. "No source" is now "No full text found", followed by why: no open-access copy listed, a site that refused the download, Unpaywall not tried for want of a contact email, and so on

### Fixed
- Running `resolve` again with downloads on no longer turns "abstract only" into "nothing" when the download fails. The abstract saved earlier is kept
- A run with `--no-download` keeps the reasons the last download attempt failed

## [0.7.4] - 2026-11-10

### Fixed
- The Word copy was invalid when an edit added text inside an insertion that another person had already tracked in the manuscript: the new insertion was nested inside theirs. Their insertion is now cut in two around the new text. On the test paper the Word copy went from failing the docx validator to passing it, with all 24 edits applied
- After a recheck, a rewording drafted for a claim that is no longer flagged is removed by `suggest-prepare`, from the list and from its part file, and named under `dropped_outdated`
- `build` always prints the `Provisional:` line, saying "none" when no result rests on an abstract only
- The recheck skill writes the list of missing sources again, so it does not name references that now have full text

## [0.7.3] - 2026-11-10

### Changed
- On a Mac, Add sources opens one system chooser that takes files and folders together, in place of the menu with two choices. A web page can only offer one kind of dialog at a time, so elsewhere the menu stays. Files are read where they are and are never moved or renamed
- The three buttons at the top are right-aligned and the same width

### Fixed
- A scanned paper whose title is not word for word the reference's, such as "LYMAN-a" for "Lyman-alpha", is now identified when the first author is there too. On 31 real papers the right reference scored 0.98 or more on this comparison and a wrong one 0.86 or less
- A file with almost no text, such as a saved sign-in page, is reported as that and not as "no match"

## [0.7.2] - 2026-11-10

### Changed
- A result reached from an abstract alone is shown as provisional everywhere. In the report those claims are striped in the text, the summary bar splits each group into "read in full" and "from the abstract only", an "Abstract only" filter lists them, and each claim's card says the full text is what settles it. `build` prints how many there are, and the Word comments say so too
- The top of the report has Results, Sources and the Word report in that order, with the page you are on highlighted
- The report's title is the title marked in the paper when there is one, and otherwise the document's name. It is no longer guessed from the opening line
- The wording that called abstract-only references "the quickest wins" is removed from the list of missing sources and from the skill. The skill now says never to describe abstract-only checking as a win or as the claim being partly checked

## [0.7.1] - 2026-11-10

### Changed
- When the person chooses to give a folder of sources, Claude opens their own folder chooser, loading the session's folder tool first when it is not loaded yet. It asks for a typed path only when the session has no such tool

## [0.7.0] - 2026-11-10

### Changed
- Asking for sources that could not be downloaded is now one question: choose a folder that holds the papers, pick the files, fetch them through the browser, or skip. Claude opens the folder chooser when the session has one, and the report's Add sources page for picking files. After the browser route it asks again for whatever is still missing, in place of carrying on silently. An unattended run opens its final summary with the request
- The list of missing sources is one short table, a row per reference with a direct link, in place of a block for every reference. It no longer names a folder to save into
- `pl.py request-sources` prints and saves a `summary`: how many of the paper's references are listed, how many have only an abstract or no text, and how many claims rely on at least one of them. The download list opens with the same figures
- `report.html` shows a banner above the paper when sources lack full text, with those figures, a link to the download list and a button that opens the Sources page filtered to them
- Supplied files are identified from what they say, not from their names: the DOI or arXiv number printed on the first pages, or the reference title as one run of words together with the first author's surname. The file name is looked at only when the text does not settle it. The download list, the report and the skills no longer suggest file names
- `add-source` takes several files or a folder and works out which reference each one is

### Added
- References whose accepted record disagrees with the paper on the year, the title or the DOI are listed by `pl.py status` as `reference_discrepancies` and shown on the report's Sources page
- `pl.py merge-suggestions --replace` rebuilds the list from the part files, so a changed or deleted suggestion does not linger
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
- A suggested edit was left out of the Word copy when its paragraph held a character that is normalised at ingest, such as an angstrom sign or a ligature. The export now compares and locates text the way it is stored
- A claim with one cited source that does not support it, and another that does better, no longer hides that finding. It is counted and filtered under "Not supported" too, underlined in red in the text, and named in the claim's card
- A database record whose title is only the start of the reference's title, or carries a footnote after it, is no longer called a mismatch. Stored records are judged again with this rule
- Markup such as `<sub>` and `<i>` is removed from record titles, and a record's capitals or footnote no longer replace the paper's own title in the citation shown
- The paper title is no longer taken from a numbered section heading or a heading far down the paper. A paper with no marked title uses the document's name
- Table marks and other paragraphs without words are kept in the report but no longer sent to claim extraction
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
