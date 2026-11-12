"""Tests for tools/release/build_plugin_zip.py, which builds the zip people upload in Claude.

The tool is a standalone script outside the test path, so it is loaded from its file. It is
run on this repository's own plugin folder and on small git repositories made for a test.
"""

import contextlib
import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOL_PATH = ROOT / "tools" / "release" / "build_plugin_zip.py"


def _load_tool():
    spec = importlib.util.spec_from_file_location("build_plugin_zip", TOOL_PATH)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load {TOOL_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


tool = _load_tool()

CHANGELOG = """# Changelog

## [Unreleased]

### Changed
- Something not yet released

## [1.2.0] - 2026-11-12

### Fixed
- The fix in 1.2.0

## [1.1.0] - 2026-11-01

### Added
- The feature in 1.1.0
"""


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _make_repo(d: Path, version: str = "1.2.0") -> Path:
    """A git repository with a small plugin folder, one executable and one untracked file."""
    repo = d / "repo"
    manifest = repo / "plug" / ".claude-plugin" / "plugin.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({"name": "plug", "version": version}), "utf-8")
    script = repo / "plug" / "scripts" / "run.py"
    script.parent.mkdir()
    script.write_text("print('hi')\n", "utf-8")
    script.chmod(0o755)
    (repo / "plug" / "README.md").write_text("Plug.\n", "utf-8")
    (repo / "outside.txt").write_text("not part of the plugin\n", "utf-8")
    _git(repo, "init", "-q")
    _git(repo, "add", ".")
    (repo / "plug" / "scripts" / "untracked.pyc").write_bytes(b"\0compiled")
    return repo


class RepositoryZipTests(unittest.TestCase):
    """The zip built from this repository's own plugin folder."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.out = Path(cls.tmp.name) / "athetize.zip"
        cls.result = tool.build_zip(ROOT, "athetize", cls.out)
        with zipfile.ZipFile(cls.out) as archive:
            cls.names = archive.namelist()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_holds_the_plugin_folder_as_its_only_top_level_entry(self):
        self.assertEqual({n.split("/", 1)[0] for n in self.names}, {"athetize"})
        self.assertIn("athetize/.claude-plugin/plugin.json", self.names)

    def test_holds_exactly_the_tracked_files(self):
        self.assertEqual(self.names, tool.tracked_files(ROOT, "athetize"))
        self.assertFalse([n for n in self.names if "__pycache__" in n or n.endswith(".pyc")])

    def test_reports_the_manifest_version(self):
        manifest = json.loads((ROOT / "athetize/.claude-plugin/plugin.json").read_text("utf-8"))
        self.assertEqual(self.result["version"], manifest["version"])
        self.assertEqual(self.result["files"], len(self.names))

    def test_the_same_commit_gives_the_same_bytes(self):
        again = Path(self.tmp.name) / "again.zip"
        tool.build_zip(ROOT, "athetize", again)
        self.assertEqual(again.read_bytes(), self.out.read_bytes())

    def test_the_changelog_has_notes_for_the_current_version(self):
        changelog = (ROOT / "CHANGELOG.md").read_text("utf-8")
        self.assertTrue(tool.changelog_section(changelog, self.result["version"]).strip())


class BuildZipTests(unittest.TestCase):
    """Building from a small repository made for the test."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = _make_repo(Path(self.tmp.name))
        self.out = Path(self.tmp.name) / "plug.zip"

    def tearDown(self):
        self.tmp.cleanup()

    def test_leaves_out_untracked_files_and_files_outside_the_folder(self):
        tool.build_zip(self.repo, "plug", self.out)
        with zipfile.ZipFile(self.out) as archive:
            names = archive.namelist()
        self.assertEqual(
            names, ["plug/.claude-plugin/plugin.json", "plug/README.md", "plug/scripts/run.py"]
        )

    def test_keeps_executable_scripts_executable(self):
        tool.build_zip(self.repo, "plug", self.out)
        with zipfile.ZipFile(self.out) as archive:
            modes = {i.filename: (i.external_attr >> 16) & 0o777 for i in archive.infolist()}
        self.assertEqual(modes["plug/scripts/run.py"], 0o755)
        self.assertEqual(modes["plug/README.md"], 0o644)

    def test_a_matching_release_version_is_accepted(self):
        self.assertEqual(tool.build_zip(self.repo, "plug", self.out, "1.2.0")["version"], "1.2.0")

    def test_a_release_version_that_differs_from_the_manifest_is_refused(self):
        with self.assertRaisesRegex(tool.ZipCheckError, "release is 1.3.0 but .* says 1.2.0"):
            tool.build_zip(self.repo, "plug", self.out, "1.3.0")
        self.assertFalse(self.out.exists())

    def test_a_folder_git_does_not_track_is_refused(self):
        with self.assertRaisesRegex(tool.ZipCheckError, "tracks no files"):
            tool.tracked_files(self.repo, "missing")


class CheckZipTests(unittest.TestCase):
    """The checks against what Upload plugin accepts."""

    def _zip(self, names):
        path = Path(self.tmp.name) / "test.zip"
        with zipfile.ZipFile(path, "w") as archive:
            for name in names:
                archive.writestr(name, "{}")
        return path

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def test_accepts_a_plugin_folder_with_its_manifest(self):
        path = self._zip(["plug/.claude-plugin/plugin.json", "plug/README.md"])
        self.assertEqual(len(tool.check_zip(path, "plug")), 2)

    def test_refuses_a_second_top_level_entry(self):
        path = self._zip(["plug/.claude-plugin/plugin.json", "notes.txt"])
        with self.assertRaisesRegex(tool.ZipCheckError, "only plug/ at its root"):
            tool.check_zip(path, "plug")

    def test_refuses_a_zip_without_the_manifest(self):
        path = self._zip(["plug/README.md"])
        with self.assertRaisesRegex(tool.ZipCheckError, "no plug/.claude-plugin/plugin.json"):
            tool.check_zip(path, "plug")


class ChangelogSectionTests(unittest.TestCase):
    def test_returns_only_that_version_s_section(self):
        self.assertEqual(
            tool.changelog_section(CHANGELOG, "1.2.0"), "### Fixed\n- The fix in 1.2.0\n"
        )

    def test_returns_the_last_section_to_the_end(self):
        self.assertEqual(
            tool.changelog_section(CHANGELOG, "1.1.0"), "### Added\n- The feature in 1.1.0\n"
        )

    def test_a_version_without_a_section_is_an_error(self):
        with self.assertRaisesRegex(tool.ZipCheckError, "no section for 9.9.9"):
            tool.changelog_section(CHANGELOG, "9.9.9")

    def test_does_not_match_a_version_that_only_starts_the_same(self):
        with self.assertRaises(tool.ZipCheckError):
            tool.changelog_section(CHANGELOG, "1.2")


class MainTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = tool.main(list(args))
        return code, out.getvalue(), err.getvalue()

    def test_writes_the_zip_and_the_release_notes(self):
        version = tool.plugin_version(ROOT, "athetize")
        zip_path, notes = self.dir / "athetize.zip", self.dir / "notes.md"
        code, out, _ = self._run(
            "--out", str(zip_path), "--expect-version", version, "--notes-out", str(notes)
        )
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["version"], version)
        self.assertTrue(zip_path.is_file())
        changelog = (ROOT / "CHANGELOG.md").read_text("utf-8")
        self.assertEqual(notes.read_text("utf-8"), tool.changelog_section(changelog, version))

    def test_a_wrong_release_version_fails_with_a_message(self):
        code, out, err = self._run("--out", str(self.dir / "x.zip"), "--expect-version", "0.0.0")
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertIn("error: the release is 0.0.0", err)


if __name__ == "__main__":
    unittest.main()
