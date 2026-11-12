"""Workspace directory model, JSON file helpers and the saved user configuration."""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path


# --------------------------------------------------------------------------
# JSON helpers
# --------------------------------------------------------------------------
def jload(path: Path, default=None):
    """Read a JSON file, or return ``default`` when the file does not exist."""
    path = Path(path)
    if not path.exists():
        return default
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def jsave(path: Path, obj) -> None:
    """Write ``obj`` as JSON by way of a temporary file, so a crash never leaves half a file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


class Work:
    """A per-paper workspace directory."""

    def __init__(self, root):
        self.root = Path(root).resolve()

    def p(self, *parts) -> Path:
        return self.root.joinpath(*parts)

    def require(self, name: str) -> Path:
        path = self.p(name)
        if not path.exists():
            raise SystemExit(
                f"Missing {name} in {self.root}. Run the earlier workflow steps first."
            )
        return path

    def paper(self):
        return jload(self.require("paper.json"))

    def refs(self):
        return jload(self.p("refs.json"), [])

    def claims(self):
        return jload(self.p("claims.json"), [])

    def notes(self):
        return jload(self.p("notes.json"), [])

    def manifest(self):
        return jload(self.p("manifest.json"), {"refs": {}})

    def verdicts(self):
        return jload(self.p("verdicts.json"), {"items": []})

    def suggestions(self):
        return jload(self.p("suggestions.json"), [])


# --------------------------------------------------------------------------
# Config (contact email etc.)
# --------------------------------------------------------------------------
def config_path() -> Path:
    """Location of the saved configuration file (ATHETIZE_HOME overrides ~/.athetize)."""
    base = os.environ.get("ATHETIZE_HOME") or str(Path.home() / ".athetize")
    return Path(base) / "config.json"


def load_config() -> dict:
    """Saved configuration. The contact email comes only from here, never the environment."""
    return jload(config_path(), {}) or {}


# An ordinary address and nothing else: it is sent in a request header, so spaces, line
# breaks and brackets must never get through.
_EMAIL = re.compile(
    r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+"
)
_EMAIL_MAX = 254


def clean_email(value: str) -> str:
    """The contact email as it will be stored; ValueError when it is not a plain address."""
    email = value.strip()
    if len(email) > _EMAIL_MAX or not _EMAIL.fullmatch(email):
        raise ValueError("Give a valid email address, such as name@university.edu.")
    return email


def masked_email(email: str) -> str:
    """An email with all but the first letter of its name hidden, for showing on screen."""
    user, _, domain = email.partition("@")
    return user[:1] + "***@" + domain


def set_email(value: str) -> None:
    """Store the contact email in the configuration file; an empty value removes it."""
    stored = jload(config_path(), {}) or {}
    if value.strip():
        stored["email"] = clean_email(value)
    else:
        stored.pop("email", None)
    save_config(stored)


def save_config(cfg: dict) -> None:
    """Write the configuration file and make it readable by its owner only where possible."""
    path = config_path()
    jsave(path, cfg)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
