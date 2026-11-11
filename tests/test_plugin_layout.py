"""Checks that the repository is a valid, listable Claude Code plugin.

These mirror the Claude Code plugin documentation and the plugin directory's pre-submission
checklist: manifest fields, skill and agent frontmatter, no binaries or oversized files in the
plugin folder, and a README that names every network host the scripts contact. The plugin is
the athetize/ folder; tests, examples and docs sit beside it and are not shipped. The one
binary kept besides pictures is a small sample paper under examples/.
"""

import json
import re
import subprocess
import unittest
import zipfile
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
# The plugin itself: the folder that is installed, and that the directory checks.
PLUGIN_DIR = ROOT / "athetize"
PLUGIN = json.loads((PLUGIN_DIR / ".claude-plugin" / "plugin.json").read_text("utf-8"))
MARKETPLACE = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text("utf-8"))

MAX_FILES = 512
MAX_NON_IMAGE_BYTES = 256 * 1024
MAX_PLUGIN_FILE_BYTES = 5 * 1024 * 1024
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
# The one kind of binary file kept besides pictures: a sample paper to try the plugin on.
# It lives under examples/, outside what the plugin itself ships.
SAMPLE_DOCUMENT_EXTS = {".docx"}
SAMPLE_DOCUMENT_DIR = "examples"
MAX_SAMPLE_DOCUMENT_BYTES = 512 * 1024
FORBIDDEN_NAMES = {".DS_Store", "Thumbs.db", "desktop.ini"}
RESERVED_MARKETPLACE_NAMES = {
    "claude-plugins-official",
    "claude-community",
    "claude-plugins-community",
    "claude-code-plugins",
}
EXAMPLE_HOST = re.compile(r"(^|\.)example\.(org|com|edu|net)$")
# XML namespace identifiers in the Word-export code. They name a format; nothing is fetched.
XML_NAMESPACE_HOSTS = {"schemas.openxmlformats.org", "schemas.microsoft.com", "www.w3.org"}


def is_sample_document(path: Path) -> bool:
    """True for a Word file kept under examples/ as a sample paper."""
    inside = path.resolve().relative_to(ROOT).parts
    return path.suffix.lower() in SAMPLE_DOCUMENT_EXTS and inside[0] == SAMPLE_DOCUMENT_DIR


def repo_files() -> list:
    """Files git tracks or would track (so local caches and check workspaces are left out)."""
    res = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return [ROOT / line for line in res.stdout.splitlines() if (ROOT / line).exists()]


def frontmatter(path: Path) -> dict:
    """Top-level keys of a Markdown file's YAML frontmatter, values as raw strings."""
    text = path.read_text("utf-8")
    if not text.startswith("---\n"):
        return {}
    block = text[4 : text.index("\n---", 4)]
    keys = {}
    for line in block.splitlines():
        m = re.match(r"([A-Za-z_-]+):\s*(.*)$", line)
        if m:
            keys[m.group(1)] = m.group(2).strip()
    return keys


class ManifestTests(unittest.TestCase):
    def test_plugin_manifest_has_the_listing_fields(self):
        self.assertRegex(PLUGIN["name"], r"^[a-z0-9]+(-[a-z0-9]+)*$")
        self.assertLessEqual(len(PLUGIN["name"]), 64)
        self.assertRegex(PLUGIN["version"], r"^\d+\.\d+\.\d+$")
        self.assertTrue(PLUGIN["description"].strip())
        self.assertTrue(PLUGIN["author"]["name"].strip())
        self.assertEqual(PLUGIN["license"], "MIT")
        homepage = urlsplit(PLUGIN["homepage"])
        self.assertEqual(homepage.scheme, "https")
        self.assertTrue(homepage.netloc)

    def test_marketplace_serves_the_plugin_from_its_own_folder(self):
        self.assertNotIn(MARKETPLACE["name"], RESERVED_MARKETPLACE_NAMES)
        self.assertTrue(MARKETPLACE["owner"]["name"].strip())
        entries = MARKETPLACE["plugins"]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["name"], PLUGIN["name"])
        self.assertEqual(entries[0]["source"], "./athetize")
        self.assertTrue((ROOT / entries[0]["source"] / ".claude-plugin" / "plugin.json").is_file())

    def test_only_manifests_live_in_the_claude_plugin_folder(self):
        marketplace = sorted(p.name for p in (ROOT / ".claude-plugin").iterdir())
        self.assertEqual(marketplace, ["marketplace.json"])
        plugin = sorted(p.name for p in (PLUGIN_DIR / ".claude-plugin").iterdir())
        self.assertEqual(plugin, ["plugin.json"])

    def test_changelog_has_an_entry_for_the_current_version(self):
        changelog = (ROOT / "CHANGELOG.md").read_text("utf-8")
        self.assertIn(f"## [{PLUGIN['version']}]", changelog)


class ComponentTests(unittest.TestCase):
    def test_every_skill_has_name_and_description(self):
        skills = sorted((PLUGIN_DIR / "skills").glob("*/SKILL.md"))
        self.assertGreaterEqual(len(skills), 1)
        for path in skills:
            with self.subTest(skill=path.parent.name):
                meta = frontmatter(path)
                self.assertEqual(meta.get("name"), path.parent.name)
                self.assertTrue(meta.get("description"))
                self.assertLessEqual(len(meta["description"]), 1536)

    def test_every_agent_has_name_description_and_an_explicit_tool_list(self):
        agents = sorted((PLUGIN_DIR / "agents").glob("*.md"))
        self.assertGreaterEqual(len(agents), 1)
        for path in agents:
            with self.subTest(agent=path.stem):
                meta = frontmatter(path)
                self.assertEqual(meta.get("name"), path.stem)
                self.assertTrue(meta.get("description"))
                self.assertIn("tools", meta)

    def test_components_use_the_plugin_root_variable_not_a_placeholder(self):
        for path in [*PLUGIN_DIR.glob("skills/**/*.md"), *PLUGIN_DIR.glob("agents/*.md")]:
            with self.subTest(file=str(path.relative_to(ROOT))):
                self.assertNotIn("<plugin root>", path.read_text("utf-8"))

    def test_folder_question_names_the_real_folder_tools(self):
        # a loose name made Claude miss the tool and ask for a typed path instead
        text = (PLUGIN_DIR / "skills" / "check-paper" / "SKILL.md").read_text("utf-8")
        for tool in (
            "mcp__ccd_directory__request_directory",
            "mcp__cowork__request_cowork_directory",
        ):
            self.assertIn(f"`{tool}`", text)
        self.assertIn("Never offer an answer the session cannot honour", text)

    def test_skills_that_call_the_helper_reference_it_through_the_plugin_root(self):
        for path in sorted((PLUGIN_DIR / "skills").glob("*/SKILL.md")):
            text = path.read_text("utf-8")
            if "pl.py" in text:
                with self.subTest(skill=path.parent.name):
                    self.assertIn("${CLAUDE_PLUGIN_ROOT}/scripts/pl.py", text)

    def test_no_hooks_or_mcp_servers_are_bundled(self):
        for name in ("hooks", ".mcp.json", "hooks.json"):
            self.assertFalse((ROOT / name).exists(), name)
        for key in ("hooks", "mcpServers"):
            self.assertNotIn(key, PLUGIN)


class FileTests(unittest.TestCase):
    """Rules for every file in the repository, and stricter ones for the plugin folder."""

    @classmethod
    def setUpClass(cls):
        cls.files = repo_files()
        cls.plugin_files = [p for p in cls.files if PLUGIN_DIR in p.parents]

    def test_no_operating_system_junk_bytecode_or_symlinks(self):
        for path in self.files:
            with self.subTest(file=str(path.relative_to(ROOT))):
                self.assertNotIn(path.name, FORBIDDEN_NAMES)
                self.assertNotIn("__MACOSX", path.parts)
                self.assertNotIn("__pycache__", path.parts)
                self.assertFalse(path.is_symlink())

    def test_plugin_folder_is_within_the_listing_limits(self):
        self.assertGreater(len(self.plugin_files), 0)
        self.assertLessEqual(len(self.plugin_files), MAX_FILES)
        for path in self.plugin_files:
            with self.subTest(file=str(path.relative_to(ROOT))):
                self.assertLess(path.stat().st_size, MAX_PLUGIN_FILE_BYTES)

    def test_plugin_folder_holds_only_text_and_pictures(self):
        # the directory holds a submission for review over any other kind of binary file
        for path in self.plugin_files:
            if path.suffix.lower() in IMAGE_EXTS:
                continue
            with self.subTest(file=str(path.relative_to(ROOT))):
                data = path.read_bytes()
                self.assertLess(len(data), MAX_NON_IMAGE_BYTES)
                data.decode("utf-8")  # raises on a binary file

    def test_the_rest_of_the_repository_is_text_pictures_and_sample_documents(self):
        for path in self.files:
            if path.suffix.lower() in IMAGE_EXTS or is_sample_document(path):
                continue
            with self.subTest(file=str(path.relative_to(ROOT))):
                data = path.read_bytes()
                self.assertLess(len(data), MAX_NON_IMAGE_BYTES)
                data.decode("utf-8")

    def test_sample_documents_are_small_word_files_kept_under_examples(self):
        samples = [p for p in self.files if p.suffix.lower() in SAMPLE_DOCUMENT_EXTS]
        self.assertTrue(samples, "the sample paper's Word file is missing")
        for path in samples:
            with self.subTest(file=str(path.relative_to(ROOT))):
                self.assertTrue(is_sample_document(path), "Word files belong under examples/")
                self.assertLess(path.stat().st_size, MAX_SAMPLE_DOCUMENT_BYTES)
                with zipfile.ZipFile(path) as z:
                    self.assertIn("word/document.xml", z.namelist())
                    self.assertIsNone(z.testzip())

    def test_plugin_folder_ships_nothing_but_the_plugin(self):
        tops = {p.relative_to(PLUGIN_DIR).parts[0] for p in self.plugin_files}
        self.assertEqual(
            tops,
            {
                ".claude-plugin",
                "LICENSE",
                "README.md",
                "agents",
                "logo.png",
                "scripts",
                "skills",
                "templates",
            },
        )

    def test_readme_and_license_are_present(self):
        # the directory reads the plugin folder's README; the one at the top is for GitHub
        for readme in (PLUGIN_DIR / "README.md", ROOT / "README.md"):
            prose = re.sub(r"```.*?```", "", readme.read_text("utf-8"), flags=re.S)
            self.assertGreaterEqual(len(prose.split()), 40, readme)
        self.assertIn("MIT License", (ROOT / "LICENSE").read_text("utf-8"))
        self.assertEqual(
            (PLUGIN_DIR / "LICENSE").read_text("utf-8"), (ROOT / "LICENSE").read_text("utf-8")
        )

    def test_both_readmes_give_the_same_account_of_what_the_plugin_does(self):
        def bullets(path, heading):
            text = path.read_text("utf-8").split(f"## {heading}\n", 1)[1].split("\n## ", 1)[0]
            return [line for line in text.splitlines() if line.startswith("- ")]

        for heading in ("What it runs, sends and fetches", "Example prompts"):
            with self.subTest(section=heading):
                top = bullets(ROOT / "README.md", heading)
                self.assertTrue(top)
                self.assertEqual(bullets(PLUGIN_DIR / "README.md", heading), top)

    def test_shown_name_is_the_same_everywhere(self):
        # the id stays "athetize" so install commands keep working; this is the name people see
        self.assertEqual(PLUGIN["name"], "athetize")
        self.assertEqual(PLUGIN["displayName"], "Athetize Fact Checker")
        self.assertEqual(MARKETPLACE["plugins"][0]["displayName"], PLUGIN["displayName"])
        for readme in (ROOT / "README.md", PLUGIN_DIR / "README.md"):
            first = readme.read_text("utf-8").splitlines()[0]
            self.assertEqual(first, "# " + PLUGIN["displayName"], readme)

    def test_listing_fields_point_at_real_things(self):
        self.assertTrue((PLUGIN_DIR / PLUGIN["icon"]).is_file())
        for key in ("documentationUrl", "supportUrl", "privacyPolicyUrl", "homepage"):
            self.assertTrue(PLUGIN[key].startswith("https://"), key)
        for key in ("documentationUrl", "privacyPolicyUrl"):
            path = PLUGIN[key].split("/blob/main/", 1)[1]
            self.assertTrue((ROOT / path).is_file(), path)

    def test_readme_gives_at_least_three_example_prompts(self):
        for path in (PLUGIN_DIR / "README.md", ROOT / "README.md"):
            section = path.read_text("utf-8").split("## Example prompts", 1)[1].split("\n## ", 1)[0]
            self.assertGreaterEqual(len(re.findall(r"(?m)^- ", section)), 3, path)


class DisclosureTests(unittest.TestCase):
    def test_readme_names_every_host_the_scripts_contact_or_link_to(self):
        readme = (PLUGIN_DIR / "README.md").read_text("utf-8")
        hosts = set()
        for path in (PLUGIN_DIR / "scripts").glob("*.py"):
            hosts.update(
                re.findall(r"https?://([a-z0-9][a-z0-9.-]*\.[a-z]{2,})", path.read_text("utf-8"))
            )
        hosts = {h for h in hosts if not EXAMPLE_HOST.search(h)} - XML_NAMESPACE_HOSTS
        self.assertGreaterEqual(len(hosts), 5)
        for host in sorted(hosts):
            with self.subTest(host=host):
                self.assertTrue(host in readme, f"README.md does not mention {host}")


if __name__ == "__main__":
    unittest.main()
