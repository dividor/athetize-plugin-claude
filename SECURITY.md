# Security Policy

Athetize is a Claude Code plugin: skills, subagents and local Python scripts. It runs on the user's machine, with the user's permissions, on documents that may come from other people. This page says how to report a problem and what the project does to keep the plugin safe.

## Reporting Security Vulnerabilities

If you discover a security vulnerability in this project, please report it responsibly:

1. **Do NOT** create a public GitHub issue for security vulnerabilities
2. Report it privately through GitHub: the repository's **Security** tab, then **Report a vulnerability** ([direct link](https://github.com/dividor/athetize-plugin-claude/security/advisories/new))
3. Include steps to reproduce the issue
4. Allow reasonable time for the issue to be addressed before public disclosure

We aim to acknowledge security reports within 48 hours and provide a fix timeline within 7 days.

## Supported Versions

Only the latest released version receives security fixes.

## What the Plugin Does and Does Not Do

- **No credentials.** The plugin bundles no keys, asks for none, and never enters passwords, solves captchas or buys access. The only setting it stores is an optional contact email in `~/.athetize/config.json`, readable only by the user.
- **No background activity.** There are no hooks, MCP servers or scheduled tasks. Scripts run only when a skill calls them.
- **Limited network use.** Only reference metadata (titles, authors, DOIs) is sent, to the scholarly lookup services listed in the README. Manuscripts and source texts never leave the machine.
- **Lawful sources only.** Full text comes from open-access routes, from files and URLs the user provides, or from the user's own browser session. The plugin does not work around paywalls or bot checks.

## Protections in the Code

| Risk | Protection |
|---|---|
| A reference or redirect pointing at a local file or another protocol | `scripts/pl_http.py` is the only network path. It fetches `http` and `https` only and has no file, ftp or data handlers. |
| Interception of downloads | TLS certificates are verified, including in the optional headless browser. |
| Oversized downloads | Responses are capped at 60 MB. |
| Malicious XML in a Word or XML file (entity expansion, external entities) | Parsed with `defusedxml`; the Word export parses with entity resolution and network access switched off. |
| Shell injection through file names | External tools (`pdftotext`, `pdftoppm`, `tesseract`) are called with argument lists, never through a shell. |
| Script injection into the report from paper or source text | The report builds all text with `textContent`, escapes the embedded data, loads nothing from the network, and follows only `http` and `https` links. |
| Prompt injection from paper, source or web text | Every skill and agent is told to treat that text as data and ignore instructions in it. Subagents get the fewest tools they need. Verdicts must quote the source, and a script checks each quote is really there. |
| A wrong paper being downloaded | A downloaded file is kept only if its text matches the reference title; links from a doubtful metadata match are not used. |

Prompt-injection defences reduce risk but cannot remove it. Review what Claude proposes to run, especially when checking documents from people you do not know.

## Automated Security Scanning

Every commit and pull request runs:

- **Bandit** (static analysis; medium and high findings fail the build, with no skipped checks)
- **detect-secrets** and **Gitleaks** (secret scanning)
- **pip-audit** (known vulnerabilities in dependencies)
- **CycloneDX** (software bill of materials)
- **Dependabot** (monthly dependency and GitHub Actions updates)

See [CONTRIBUTING.md](CONTRIBUTING.md#-security) for the rules contributors follow.
