"""Build the plugin zip that people upload in Claude: Customize, Plugins, Add, Upload plugin.

The archive holds the files git tracks in the plugin folder, under that folder's name as its
single top-level entry, so `.claude-plugin/plugin.json` sits where the upload looks for it.
Entries are sorted and carry a fixed date, so the same commit always gives the same bytes.

    python tools/release/build_plugin_zip.py --out athetize.zip
    python tools/release/build_plugin_zip.py --out athetize.zip --expect-version 0.8.8 \
        --notes-out notes.md
"""

from __future__ import annotations

import argparse
import json
import re
import stat
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLUGIN_FOLDER = "athetize"
MANIFEST = ".claude-plugin/plugin.json"
FIXED_DATE = (1980, 1, 1, 0, 0, 0)
UPLOAD_LIMIT_BYTES = 200 * 1024 * 1024
UPLOAD_LIMIT_FILES = 5000


class ZipCheckError(Exception):
    """The zip would not be accepted by Upload plugin, or does not match the release."""


def tracked_files(root: Path, folder: str) -> list[str]:
    """Paths, relative to `root`, of the files git tracks under `folder`, sorted."""
    # A fixed git command with an argument list: no shell is involved.
    out = subprocess.run(
        ["git", "ls-files", "-z", "--", folder],
        cwd=root,
        check=True,
        capture_output=True,
    ).stdout.decode("utf-8")
    files = sorted(p for p in out.split("\0") if p)
    if not files:
        raise ZipCheckError(f"git tracks no files under {folder}/")
    return files


def plugin_version(root: Path, folder: str) -> str:
    """The version in the plugin's manifest."""
    manifest = json.loads((root / folder / MANIFEST).read_text("utf-8"))
    return str(manifest["version"])


def changelog_section(changelog: str, version: str) -> str:
    """The body of the changelog's `## [version]` section, without its heading."""
    heading = re.compile(r"^## \[" + re.escape(version) + r"\][^\n]*\n", re.M)
    found = heading.search(changelog)
    if not found:
        raise ZipCheckError(f"CHANGELOG.md has no section for {version}")
    rest = changelog[found.end() :]
    following = re.search(r"^## \[", rest, re.M)
    body = rest[: following.start()] if following else rest
    return body.strip() + "\n"


def _write_entry(archive: zipfile.ZipFile, root: Path, path: str) -> None:
    source = root / path
    info = zipfile.ZipInfo(path, date_time=FIXED_DATE)
    info.compress_type = zipfile.ZIP_DEFLATED
    executable = source.stat().st_mode & stat.S_IXUSR
    info.external_attr = (0o100755 if executable else 0o100644) << 16
    archive.writestr(info, source.read_bytes())


def check_zip(path: Path, folder: str) -> list[str]:
    """Check the archive against the upload's rules and return its entry names."""
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
    tops = {name.split("/", 1)[0] for name in names}
    if tops != {folder}:
        raise ZipCheckError(f"the zip must hold only {folder}/ at its root, found {sorted(tops)}")
    if f"{folder}/{MANIFEST}" not in names:
        raise ZipCheckError(f"the zip has no {folder}/{MANIFEST}")
    if len(names) > UPLOAD_LIMIT_FILES:
        raise ZipCheckError(f"the zip has {len(names)} files; the upload limit is 5000")
    if path.stat().st_size > UPLOAD_LIMIT_BYTES:
        raise ZipCheckError("the zip is over the 200 MB upload limit")
    return names


def build_zip(root: Path, folder: str, out: Path, expect_version: str | None = None) -> dict:
    """Write the plugin zip to `out`, check it, and describe what was written."""
    version = plugin_version(root, folder)
    if expect_version is not None and expect_version != version:
        raise ZipCheckError(
            f"the release is {expect_version} but {folder}/{MANIFEST} says {version}"
        )
    files = tracked_files(root, folder)
    with zipfile.ZipFile(out, "w") as archive:
        for path in files:
            _write_entry(archive, root, path)
    names = check_zip(out, folder)
    return {"path": str(out), "version": version, "files": len(names), "bytes": out.stat().st_size}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", required=True, type=Path, help="where to write the zip")
    parser.add_argument("--expect-version", help="fail unless plugin.json has this version")
    parser.add_argument("--notes-out", type=Path, help="write this version's changelog here")
    args = parser.parse_args(argv)
    try:
        result = build_zip(ROOT, PLUGIN_FOLDER, args.out, args.expect_version)
        if args.notes_out:
            changelog = (ROOT / "CHANGELOG.md").read_text("utf-8")
            args.notes_out.write_text(changelog_section(changelog, result["version"]), "utf-8")
    except ZipCheckError as err:
        print(f"error: {err}", file=sys.stderr)
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
