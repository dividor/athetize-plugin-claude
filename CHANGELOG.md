# Changelog

All notable changes to Athetize will be documented in this file.

## [0.6.0] - 2026-11-09

First release from the public repository. The plugin's features are unchanged from 0.5.0; this release hardens it, restructures the code and sets it up for distribution as a Claude Code plugin.

### Security
- Network access now goes through one module that fetches `http` and `https` only; file, ftp and data URLs and redirects to them are refused
- The optional headless browser no longer ignores TLS certificate errors
- XML from Word and XML files is parsed with `defusedxml`; the Word export parses with entity resolution and network access switched off
- The `suggestion-writer` agent is limited to the Read and Write tools

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
