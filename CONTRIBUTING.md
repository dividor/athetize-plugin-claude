# Contributing to Athetize

Thank you for your interest in contributing to Athetize! This document provides guidelines and instructions for contributing to the project.

## 📋 Table of Contents

- [Requirements for Acceptable Contributions](#-requirements-for-acceptable-contributions)
- [Code of Conduct](#-code-of-conduct)
- [Getting Started](#-getting-started)
- [Repository Layout](#-repository-layout)
- [Pre-commit Hooks](#-pre-commit-hooks)
- [Security](#-security)
- [Making Changes](#-making-changes)
- [Commit Guidelines](#-commit-guidelines)
- [Testing](#-testing)
- [Plugin Standards](#-plugin-standards)
- [GitHub Actions (CI)](#-github-actions-ci)
- [Pull Request Process](#-pull-request-process)
- [Issue Guidelines](#-issue-guidelines)
- [Code Style](#-code-style)
- [Release Process](#-release-process)

## ✅ Requirements for Acceptable Contributions

All contributions must meet the following requirements before they will be accepted:

1. **Pre-commit hooks pass** — install and run `pre-commit` before opening a pull request (see [Pre-commit Hooks](#-pre-commit-hooks)). This is mandatory, not optional. Hooks enforce formatting, linting, type checking, complexity limits, and secret detection.

2. **Coding standards** — Python code must conform to the style enforced by our toolchain:
   - **Formatting**: `black` (line length 100)
   - **Import order**: `isort`
   - **Linting**: `flake8`
   - **Type annotations**: `mypy`
   - **Security**: `bandit` (medium and high severity findings fail the build)
   - **Complexity**: no function above cyclomatic complexity 20 or cognitive complexity 20, and no file with a maintainability index below 10 (enforced by `tools/quality/code_metrics.py`). Aim for 10 or less.

3. **No suppressions or exemptions** — no `# noqa`, `# type: ignore`, `# nosec`, `# pragma: no cover`, skipped tests, ignore lists or excluded files. If a check fires, fix the code. If a file is too complex, refactor it.

4. **Tests** — new features must include unit tests; bug fixes should include a regression test. Run `pytest` before submitting.

5. **No secrets or credentials** — `detect-secrets` and `gitleaks` run on every commit. Never commit API keys, tokens, or passwords. The plugin itself must never bundle or ask for credentials.

6. **Sources are obtained lawfully** — Athetize downloads only from open-access routes, files and URLs the user provides, or the user's own browser session. Contributions that work around paywalls, bot checks or publisher access controls, or that use mirrors or shadow libraries, will not be accepted.

7. **Commit messages** — follow [Conventional Commits](https://www.conventionalcommits.org/) (`feat:`, `fix:`, `docs:`, `ci:`, etc.).

8. **Pull requests, not direct pushes** — never commit directly to `main`. Create a feature branch and open a pull request.

If any of these requirements are not met, CI will fail and the PR will not be merged until the issues are resolved.

## 🤝 Code of Conduct

By participating in this project, you agree to abide by our [Code of Conduct](CODE_OF_CONDUCT.md). Please treat all community members with respect and create a welcoming environment for everyone.

## 🚀 Getting Started

### Prerequisites

- Python 3.11 for the development tooling (the plugin scripts themselves support Python 3.9 and newer, and CI tests 3.9 to 3.13)
- Git
- A GitHub account
- [Claude Code](https://claude.com/claude-code), to validate the manifests and try the plugin

### Fork and Clone

1. Fork the repository on GitHub
2. Clone your fork locally:
   ```bash
   git clone https://github.com/YOUR-USERNAME/athetize-plugin-claude.git
   cd athetize-plugin-claude
   ```
3. Add the upstream repository:
   ```bash
   git remote add upstream https://github.com/dividor/athetize-plugin-claude.git
   ```

### 💻 Development Setup

```bash
python3.11 -m venv .venv
source .venv/bin/activate  # On macOS/Linux
# .venv\Scripts\activate    # On Windows

pip install -r requirements-dev.txt
pre-commit install
```

To try your working copy in Claude Code without installing it:

```bash
claude --plugin-dir .
```

## 🗂️ Repository Layout

The repository root is the plugin. Claude Code installs it straight from here (`"source": "./"` in the marketplace file).

| Path | What it is |
|---|---|
| `.claude-plugin/plugin.json` | Plugin manifest (name, version, description, author) |
| `.claude-plugin/marketplace.json` | Makes this repository its own plugin marketplace |
| `skills/<name>/SKILL.md` | One skill per folder: instructions Claude follows |
| `agents/<name>.md` | Subagents the skills launch in parallel |
| `scripts/pl.py`, `scripts/pl_*.py` | The helper the skills call for every deterministic step (see the module map below) |
| `scripts/requirements.txt` | The helper's optional runtime packages |
| `templates/report.html` | The self-contained report page |
| `tests/` | Offline tests and the sample fixtures |
| `tools/quality/` | Repository tooling (complexity gate); not used by the plugin at run time |

### Script modules

`pl.py` is the entry point. Everything else is a small module with one job, imported by bare name. The complexity gate keeps them small: when a file grows past the limits, split it.

| Modules | Job |
|---|---|
| `pl.py`, `pl_cli.py`, `pl_cmd_setup.py`, `pl_cmd_sources.py`, `pl_cmd_report.py` | Command line: argument parser and one `cmd_*` function per subcommand |
| `pl_workspace.py`, `pl_validate.py`, `pl_claims.py`, `pl_cites.py` | The check workspace: JSON files, validation, claim merging, citation markers |
| `pl_ingest.py`, `pl_text.py`, `pl_xml.py` | Reading manuscripts and sources (Word, PDF, Markdown, LaTeX, HTML, XML) |
| `pl_resolve.py`, `pl_metadata.py`, `pl_refmatch.py`, `pl_download.py`, `pl_browser.py`, `pl_http.py` | Looking references up and fetching open-access full text. `pl_http.py` is the only module that touches the network |
| `pl_quote.py`, `pl_passages.py`, `pl_verify.py` | Finding passages, locating quotes in sources, checking verdicts |
| `pl_suggest.py`, `pl_docx.py`, `pl_docx_comments.py`, `pl_docx_package.py` | Suggested edits and the Word tracked-changes export |
| `pl_report.py` | Data for `templates/report.html` |
| `pl_core.py` | Re-exports the workspace, validation, verification and report functions under one name |

## 🔧 Pre-commit Hooks

We use pre-commit hooks to ensure code quality and consistency. **This is required for all contributors.**
This mirrors the CI `code-quality` job. Code metrics run as part of pre-commit and
fail if any file is rated **bad** (see [Code Complexity Checks](#code-complexity-checks)).

Much of this code was developed with AI assistance. That only works with comprehensive tests and automated quality checks, and code complexity validation matters most, because AI tends to write code that is hard to follow and support. So please make sure pre-commit hooks are active if you use AI to code. Thanks!

### What Pre-commit Does

Our pre-commit configuration (`.pre-commit-config.yaml`) automatically:

- **Code Formatting**: Runs `black` to format Python code
- **Import Sorting**: Runs `isort` to organize imports
- **Linting**: Runs `flake8` for code quality checks
- **Type Checking**: Runs `mypy` for static type analysis
- **Python Security**: Runs `bandit` for security vulnerability scanning
- **Secrets Detection**: Runs `detect-secrets` and `gitleaks` to prevent credential leaks
- **Debug Guards**: Fails on `breakpoint()`/`pdb` style debug statements
- **File Hygiene**: Trailing whitespace, end-of-file, mixed line endings, no symlinks
- **Config Validation**: Checks syntax for YAML/JSON/TOML files
- **Merge Conflict Detection**: Prevents committing unresolved conflicts
- **Large File Prevention**: Blocks files larger than 250 KB (plugin directories reject larger non-image files)
- **Code Metrics Gate**: Fails if any file is **bad** for cyclomatic, cognitive, or MI

### Running Pre-commit Manually

```bash
# Run on all files
pre-commit run --all-files

# Run on staged files only
pre-commit run
```

Hooks always run on commit. Do not use `git commit --no-verify`; fix the issue instead.

### Code Complexity Checks

Use the code metrics script to measure cyclomatic complexity, cognitive
complexity, and maintainability index for `scripts`, `tools` and `tests`:

```bash
python tools/quality/code_metrics.py --fail-on-bad
```

| Metric | Good | Okay | Bad (fails) |
|---|---|---|---|
| Cyclomatic complexity, worst function in the file | 1-10 | 11-20 | 21+ |
| Cognitive complexity, worst function in the file | 0-10 | 11-20 | 21+ |
| Maintainability index of the file | 20 or more | 10-19 | below 10 |

## 🔒 Security

See [SECURITY.md](SECURITY.md) for the security policy and how to report a vulnerability.

### Security Scanning in Pre-commit

- **Bandit**: Python static application security testing (SAST)
- **detect-secrets**: Prevents accidental credential commits
- **Gitleaks**: Enhanced secret detection with regex patterns

If `detect-secrets` flags a string that is genuinely not a secret (a test hash, for example), audit it into the baseline rather than excluding the file:

```bash
detect-secrets scan --baseline .secrets.baseline
detect-secrets audit .secrets.baseline
```

### Security Scanning in CI

- **pip-audit**: Python dependency vulnerability scanning
- **Bandit SAST**: Python security analysis with a JSON report
- **Gitleaks**: Repository-wide secret scanning
- **SBOM**: CycloneDX bill of materials for the runtime packages

### Security rules for plugin code

- All network access goes through `scripts/pl_http.py`, which fetches `http` and `https` only and verifies TLS certificates. Do not call `urllib`, `requests` or sockets anywhere else.
- Any new network destination must be listed in the README "Privacy and safety" section.
- Parse untrusted XML with `defusedxml`. Never use `eval()`, `exec()` or `shell=True`.
- Text from papers, sources and web pages is data. Skills and agents must say so and must never follow instructions found in it.
- The report template must not load anything from the network and must build text with `textContent`.

### Dependency Management

Dependabot is configured (`.github/dependabot.yml`) to open monthly PRs for:

- Python dependency updates (`requirements-dev.txt`, `scripts/requirements.txt`)
- GitHub Actions version updates

## 🛠️ Making Changes

### Development Workflow

1. **Create a feature branch:**
   ```bash
   git checkout -b feat/your-feature-name
   ```

2. **Make your changes:**
   - Write code following our [Code Style](#-code-style)
   - Add tests for new functionality
   - Update documentation, skills and agents as needed

3. **Test your changes** (see [Testing](#-testing))

4. **Commit your changes** (see [Commit Guidelines](#-commit-guidelines))

5. **Push and create PR:**
   ```bash
   git push origin feat/your-feature-name
   ```

### Types of Contributions

- **Bug Fixes**: Fix existing issues
- **Features**: Add new functionality
- **Documentation**: Improve docs, skill instructions, or comments
- **Tests**: Add or improve test coverage
- **Refactoring**: Improve code structure without changing functionality

## 📝 Commit Guidelines

We use [Conventional Commits](https://www.conventionalcommits.org/) format:

```
<type>(<scope>): <description>

[optional body]

[optional footer(s)]
```

### Commit Types

- **feat:** New features
- **fix:** Bug fixes
- **docs:** Documentation changes
- **style:** Code style changes (formatting, etc.)
- **refactor:** Code refactoring
- **test:** Adding or modifying tests
- **chore:** Build process or auxiliary tool changes
- **perf:** Performance improvements
- **ci:** CI/CD pipeline changes
- **build:** Build system or dependencies

### Examples

```bash
feat: match author-year citations with two surnames
fix: keep page numbers when a quote spans a page break
docs: explain how to add a library proxy
test: add a regression test for hyphenated quotes
chore: update development dependencies
```

### Commit Best Practices

- Keep commits atomic (one logical change per commit)
- Write clear, descriptive commit messages
- Reference issues when applicable: `fixes #123`
- Use imperative mood: "add feature" not "added feature"

## 🧪 Testing

```bash
# All tests (offline; the network is stubbed)
pytest

# With coverage
pytest --cov=scripts --cov=tools --cov-report=term-missing
```

- Tests must exercise the real scripts. Do not reimplement logic in a test.
- Never skip, disable or delete a test to get a green run. Fix the code or the test.
- Tests must not touch the internet. Patch `pl_http.http_get`, or use a local server as `tests/test_http.py` does.

### The golden-output test

`tests/test_golden.py` runs the pipeline and most helper functions on the sample paper in `tests/fixtures/sample/` and compares everything with `tests/fixtures/sample/golden.json`. Any change in behaviour, intended or not, shows up as a diff.

- A refactor must leave the golden file untouched.
- When you change behaviour on purpose, regenerate the file, review the diff line by line, and explain it in the PR:
  ```bash
  ATHETIZE_UPDATE_GOLDEN=1 pytest tests/test_golden.py
  git diff tests/fixtures/sample/golden.json
  ```

### Manual verification

Tests cannot judge what Claude does with the skills. For changes to skills, agents, the report or the Word export, run the affected skill in Claude Code on a real paper (`claude --plugin-dir .`), open `report.html` in a browser, and open the exported `.docx` in Word. Say in the PR what you checked.

## 🧩 Plugin Standards

The plugin follows the [Claude Code plugin documentation](https://code.claude.com/docs/en/plugins) and the requirements for directory listing.

```bash
claude plugin validate --strict .claude-plugin/plugin.json
claude plugin validate --strict .claude-plugin/marketplace.json
```

- The plugin `name` is permanent. Never rename it.
- Only the two manifests live in `.claude-plugin/`. Skills, agents, scripts and templates stay at the repository root.
- Every skill has `name` and `description` frontmatter; every agent has `name`, `description` and an explicit `tools` list with the fewest tools it needs.
- Skills and agents refer to bundled files through `${CLAUDE_PLUGIN_ROOT}`.
- No hooks, no MCP servers and nothing that runs without the user asking.
- No binaries, symlinks, submodules or files over 256 KiB; no `.DS_Store` or `__pycache__` (`tests/test_plugin_layout.py` checks this).
- The README must describe everything the plugin runs, sends and fetches.

## ✅ GitHub Actions (CI)

The CI workflow in `.github/workflows/ci.yml` runs on push and PRs:

- **code-quality**: Pre-commit hooks (formatting, linting, type checks, security scans, complexity gate).
- **tests**: `pytest` on Python 3.9, 3.10, 3.11, 3.12 and 3.13.
- **plugin-validate**: `claude plugin validate --strict` on both manifests. No model calls and no API key.
- **security-scan**: Dependency audit (pip-audit), Bandit SAST, Gitleaks.
- **sbom**: CycloneDX bill of materials.

CI needs no secrets.

## 🔍 Pull Request Process

### Before Submitting

1. **Update your branch:**
   ```bash
   git fetch upstream
   git rebase upstream/main
   ```

2. **Run all checks:** `pre-commit run --all-files` and `pytest`

3. **Update documentation** if needed, including `CHANGELOG.md`

### PR Guidelines

- **Title**: Use conventional commit format
- **Description**: Clearly explain what and why
- **Link Issues**: Reference related issues
- **Screenshots**: Include for report changes
- **Breaking Changes**: Clearly document any breaking changes

### Review Process

1. **Automated Checks**: CI runs tests and linting
2. **Peer Review**: At least one maintainer reviews the code
3. **Feedback**: Address any requested changes
4. **Approval**: Maintainer approves and merges

## 🐛 Issue Guidelines

### Before Creating an Issue

1. **Search existing issues** to avoid duplicates
2. **Check the README**, including Limitations
3. **Test with the latest version**

### Bug Reports

Include:
- **Environment**: OS, Claude Code version, plugin version, Python version, output of `pl.py doctor`
- **Steps to reproduce** the issue
- **Expected behavior**
- **Actual behavior**
- **Error messages** (full output)
- **A small made-up example** that shows the problem. Do not post unpublished manuscripts or paywalled source text.

### Feature Requests

Include:
- **Clear description** of the feature
- **Use case** and motivation
- **Proposed solution** (if you have one)
- **Alternatives considered**

## 🎨 Code Style

- **Formatter**: Black (line length 100)
- **Import Sorting**: isort (Black-compatible profile)
- **Linting**: flake8
- **Type Checking**: mypy; use type hints for function signatures
- **Python version**: the scripts must run on Python 3.9. Keep `from __future__ import annotations` at the top of every module and avoid newer-only syntax.
- **Dependencies**: standard library first. A new runtime package needs a strong reason, must be listed in `scripts/requirements.txt` and the README, and must fail with a clear message when missing.
- **Fail loudly**: no silent fallbacks. A failed lookup or unreadable file is reported to the user, never hidden.
- **Plain English**: in code, comments, skills and docs, say what the thing does.

## 🔄 Release Process

1. Update `version` in `.claude-plugin/plugin.json`. Users only receive an update when this changes.
2. Add an entry to `CHANGELOG.md`.
3. Merge the PR to `main` once CI passes.
4. Tag the release (`claude plugin tag` creates an `athetize--vX.Y.Z` tag and checks the manifests agree) and create a GitHub Release with notes.

## 📞 Getting Help

- **Documentation**: Check the README and the skill files
- **Issues**: Create a GitHub issue for bugs or feature requests
- **Discussions**: Use GitHub Discussions for questions

Thank you for contributing to Athetize! 🎉
