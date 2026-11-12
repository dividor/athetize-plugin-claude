"""The Python packages Athetize needs, and installing the missing ones.

Claude Code runs the plugin's scripts with the Python on the person's own computer, and a
plugin cannot ask for packages to be installed with it. So a check starts by finding what is
missing; the person is asked, and only then are the packages installed, into the Python that
runs these scripts.
"""

from __future__ import annotations

import importlib.util
import json
import shlex
import subprocess
import sys
import sysconfig
from pathlib import Path

import pl_text

# What each package is for, in the words a person is told.
NEEDED_FOR = {
    "defusedxml": "reading Word (.docx) papers and Word or XML sources",
    "lxml": "the Word copy with suggested edits",
    "pypdf": "reading PDF papers and sources",
}
# Installing can take a while on a slow connection, but not for ever.
_PIP_WAIT = 600


def missing_packages() -> list[str]:
    """The packages that are not installed. pypdf counts only when nothing else reads PDFs."""
    missing = [name for name in ("defusedxml", "lxml") if importlib.util.find_spec(name) is None]
    if pl_text.pdf_extractor() is None:
        missing.append("pypdf")
    return missing


def _in_virtual_environment() -> bool:
    return sys.prefix != getattr(sys, "base_prefix", sys.prefix)


def _externally_managed() -> bool:
    """True for a Python whose packages the system manages (PEP 668), as Homebrew's is."""
    return Path(sysconfig.get_path("stdlib"), "EXTERNALLY-MANAGED").is_file()


def pip_install_command(packages: list[str]) -> list[str]:
    """The pip command that installs the packages into the Python running these scripts.

    Inside a virtual environment they go into it. Otherwise they go into the person's own
    user folder, never the system's; a system-managed Python needs pip told that this is
    what is wanted.
    """
    command = [sys.executable, "-m", "pip", "install", "--disable-pip-version-check"]
    if not _in_virtual_environment():
        command.append("--user")
        if _externally_managed():
            command.append("--break-system-packages")
    return command + list(packages)


_STILL_MISSING = (
    "import importlib.util, json, sys; "
    "print(json.dumps([n for n in sys.argv[1:] if importlib.util.find_spec(n) is None]))"
)


def _still_missing(packages: list[str]) -> list[str]:
    """Which of the packages a fresh Python process still cannot find.

    A fresh process is asked because a user folder created by the install is not on this
    process's search path.
    """
    # A fixed command with an argument list: no shell is involved.
    res = subprocess.run(
        [sys.executable, "-c", _STILL_MISSING, *packages],
        capture_output=True,
        text=True,
        timeout=60,
    )
    return json.loads(res.stdout or "[]")


def install_missing() -> dict:
    """Install the missing packages and say what happened."""
    missing = missing_packages()
    if not missing:
        return {"installed": [], "still_missing": [], "command": None}
    command = pip_install_command(missing)
    # A fixed command with an argument list: no shell is involved.
    res = subprocess.run(command, capture_output=True, text=True, timeout=_PIP_WAIT)
    still = _still_missing(missing)
    report = {
        "installed": [name for name in missing if name not in still],
        "still_missing": still,
        "command": shlex.join(command),
    }
    if still:
        report["not_available"] = [NEEDED_FOR[name] for name in still]
        report["pip_said"] = (res.stderr or res.stdout).strip().splitlines()[-6:]
    return report


def cmd_install_packages(args):
    """Install the Python packages Athetize needs and that are missing; fail if any still are."""
    report = install_missing()
    print(json.dumps(report, indent=2, ensure_ascii=False))
    if report["still_missing"]:
        raise SystemExit(1)
