"""Tests for the complexity gate in tools/quality/code_metrics.py.

The tool is a standalone script outside the test path, so it is loaded from its file.
Synthetic Python files are written to a temporary directory and measured for real.

Run from the plugin root:  python3 -m unittest discover -s tests -v
"""

import contextlib
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

TOOL_PATH = Path(__file__).resolve().parent.parent / "tools" / "quality" / "code_metrics.py"

SIMPLE_SOURCE = "def add(a, b):\n    return a + b\n"

BRANCHY_SOURCE = (
    "def add(a, b):\n"
    "    return a + b\n"
    "\n"
    "\n"
    "def sign(value):\n"
    "    if value < 0:\n"
    "        return -1\n"
    "    return 1\n"
)

BRANCH_COUNT = 25


def _load_tool():
    spec = importlib.util.spec_from_file_location("code_metrics", TOOL_PATH)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load {TOOL_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


cm = _load_tool()


def _complex_source() -> str:
    """A function with one early return per branch: far above the limit of 20."""
    lines = ["def pick(value):"]
    for number in range(BRANCH_COUNT):
        lines.append(f"    if value == {number}:")
        lines.append(f"        return {number}")
    lines.append("    return -1")
    return "\n".join(lines) + "\n"


def _write(directory: Path, name: str, source: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(source, encoding="utf-8")
    return path


def _call(entry, *args: str):
    """Call main() or run() with the given command line; returns (exit code, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    argv = ["code_metrics.py", *args]
    with mock.patch.object(sys, "argv", argv):
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = entry()
    return code, out.getvalue(), err.getvalue()


class TempDirTestCase(unittest.TestCase):
    """Gives each test an empty temporary directory as self.tmp."""

    def setUp(self):
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        self.tmp = Path(holder.name).resolve()


class RatingTests(unittest.TestCase):
    def test_complexity_ratings_at_boundaries(self):
        cases = [(0, "good"), (10, "good"), (11, "okay"), (20, "okay"), (21, "bad")]
        for value, expected in cases:
            with self.subTest(value=value):
                self.assertEqual(cm.rate_cc(value), expected)
                self.assertEqual(cm.rate_cog(value), expected)

    def test_maintainability_rating_at_boundaries(self):
        cases = [
            (100.0, "good"),
            (20.0, "good"),
            (19.99, "okay"),
            (10.0, "okay"),
            (9.99, "bad"),
            (0.0, "bad"),
            (None, "n/a"),
        ]
        for value, expected in cases:
            with self.subTest(value=value):
                self.assertEqual(cm.rate_mi(value), expected)


class DiscoverFilesTests(TempDirTestCase):
    def test_finds_python_files_sorted(self):
        second = _write(self.tmp / "pkg" / "sub", "b.py", SIMPLE_SOURCE)
        first = _write(self.tmp / "pkg", "a.py", SIMPLE_SOURCE)
        _write(self.tmp / "pkg", "notes.txt", "not python")
        self.assertEqual(cm.discover_files([str(self.tmp)], set()), [first, second])

    def test_leaves_out_excluded_directory(self):
        kept = _write(self.tmp / "pkg", "a.py", SIMPLE_SOURCE)
        _write(self.tmp / "pkg" / "skipme", "b.py", SIMPLE_SOURCE)
        self.assertEqual(cm.discover_files([str(self.tmp)], {"skipme"}), [kept])

    def test_accepts_a_single_file(self):
        path = _write(self.tmp, "a.py", SIMPLE_SOURCE)
        text = _write(self.tmp, "notes.txt", "not python")
        self.assertEqual(cm.discover_files([str(path), str(text)], set()), [path])

    def test_fails_for_missing_path(self):
        missing = self.tmp / "nowhere"
        with self.assertRaisesRegex(FileNotFoundError, "Path does not exist"):
            cm.discover_files([str(missing)], set())


class FileMetricsTests(TempDirTestCase):
    def test_small_file(self):
        path = _write(self.tmp, "small.py", BRANCHY_SOURCE)
        metrics = cm.build_file_metrics(path)
        self.assertEqual(metrics.path, str(path))
        self.assertEqual(metrics.nloc, 6)
        self.assertEqual(metrics.function_count, 2)
        self.assertEqual(metrics.cyclomatic_max, 2)
        self.assertAlmostEqual(metrics.cyclomatic_avg, 1.5)
        self.assertEqual(metrics.cognitive_max, 1)
        self.assertAlmostEqual(metrics.cognitive_avg, 0.5)
        self.assertGreater(metrics.mi, 20)
        self.assertEqual(metrics.mi_rank, "A")

    def test_file_without_functions(self):
        metrics = cm.build_file_metrics(_write(self.tmp, "constants.py", "VALUE = 1\n"))
        self.assertEqual(metrics.function_count, 0)
        self.assertEqual(metrics.cyclomatic_max, 0)
        self.assertEqual(metrics.cognitive_max, 0)

    def test_rejects_invalid_python(self):
        path = _write(self.tmp, "broken.py", "def broken(:\n")
        with self.assertRaisesRegex(RuntimeError, "Failed to parse Python file"):
            cm.build_file_metrics(path)

    def test_collect_bad_files_lists_only_bad_files_with_reasons(self):
        simple = cm.build_file_metrics(_write(self.tmp, "simple.py", SIMPLE_SOURCE))
        complex_ = cm.build_file_metrics(_write(self.tmp, "complex.py", _complex_source()))
        bad_files = cm.collect_bad_files([simple, complex_])
        self.assertEqual([item["path"] for item in bad_files], [complex_.path])
        self.assertEqual(bad_files[0]["reasons"], ["cyclomatic", "cognitive"])
        self.assertEqual(bad_files[0]["cyclomatic_max"], BRANCH_COUNT + 1)
        self.assertEqual(bad_files[0]["cognitive_max"], BRANCH_COUNT)

    def test_collect_bad_files_reports_low_maintainability(self):
        metrics = cm.FileMetrics(
            path="big.py",
            nloc=5000,
            function_count=1,
            cyclomatic_avg=1.0,
            cyclomatic_max=1,
            cognitive_avg=0.0,
            cognitive_max=0,
            mi=9.5,
            mi_rank="B",
        )
        self.assertEqual(cm.collect_bad_files([metrics])[0]["reasons"], ["maintainability"])

    def test_summarize_groups_files_by_path(self):
        used = self.tmp / "used"
        empty = self.tmp / "empty"
        metrics = [
            cm.build_file_metrics(_write(used, "simple.py", SIMPLE_SOURCE)),
            cm.build_file_metrics(_write(used, "complex.py", _complex_source())),
            cm.build_file_metrics(_write(self.tmp / "elsewhere", "small.py", BRANCHY_SOURCE)),
        ]
        summary = cm.summarize(metrics, [str(used), str(empty)])
        self.assertEqual(summary[str(used)]["files"], 2)
        self.assertEqual(summary[str(used)]["cyclomatic_max"], BRANCH_COUNT + 1)
        self.assertEqual(summary[str(used)]["cognitive_max"], BRANCH_COUNT)
        self.assertEqual(summary[str(empty)]["files"], 0)
        self.assertIsNone(summary[str(empty)]["mi_avg"])
        self.assertEqual(summary["other"]["files"], 1)

    def test_format_table_pads_columns(self):
        table = cm.format_table([["File", "MI"], ["a.py", "9.50"]])
        self.assertEqual(table.splitlines(), ["File  MI  ", "a.py  9.50"])


class MainTests(TempDirTestCase):
    def test_text_fails_for_over_complex_function(self):
        path = _write(self.tmp, "complex.py", _complex_source())
        code, out, err = _call(cm.main, "--paths", str(self.tmp), "--fail-on-bad")
        self.assertEqual(code, 1)
        self.assertTrue(out.startswith("Summary\n"), out)
        self.assertIn("Top 20 files by cyclomatic complexity", out)
        self.assertIn("Top 20 files by cognitive complexity", out)
        self.assertIn("Lowest 20 MI", out)
        self.assertIn("Guidelines", out)
        bad_section = out.split("Bad files (must fix)\n")[1]
        self.assertIn(str(path), bad_section)
        self.assertIn("cyclomatic, cognitive", bad_section)
        self.assertIn("Found 1 file(s) with bad metrics.", err)

    def test_text_passes_for_simple_function(self):
        _write(self.tmp, "simple.py", SIMPLE_SOURCE)
        code, out, err = _call(cm.main, "--paths", str(self.tmp), "--fail-on-bad", "--top", "5")
        self.assertEqual(code, 0)
        self.assertIn("Top 5 files by cyclomatic complexity", out)
        self.assertNotIn("Bad files (must fix)", out)
        self.assertEqual(err, "")

    def test_json_fails_for_over_complex_function(self):
        path = _write(self.tmp, "complex.py", _complex_source())
        args = ["--paths", str(self.tmp), "--format", "json", "--fail-on-bad"]
        code, out, err = _call(cm.main, *args)
        self.assertEqual(code, 1)
        payload = json.loads(out)
        self.assertIs(payload["has_bad_files"], True)
        self.assertEqual([item["path"] for item in payload["bad_files"]], [str(path)])
        self.assertEqual(payload["files"][0]["cyclomatic_max"], BRANCH_COUNT + 1)
        self.assertEqual(payload["summary"][str(self.tmp)]["files"], 1)
        self.assertIn("Found 1 file(s) with bad metrics.", err)

    def test_json_passes_for_simple_function(self):
        _write(self.tmp, "simple.py", SIMPLE_SOURCE)
        args = ["--paths", str(self.tmp), "--format", "json", "--fail-on-bad"]
        code, out, err = _call(cm.main, *args)
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertIs(payload["has_bad_files"], False)
        self.assertEqual(payload["bad_files"], [])
        self.assertEqual(err, "")

    def test_reports_bad_files_without_failing_unless_asked(self):
        _write(self.tmp, "complex.py", _complex_source())
        code, out, err = _call(cm.main, "--paths", str(self.tmp))
        self.assertEqual(code, 0)
        self.assertIn("Bad files (must fix)", out)
        self.assertEqual(err, "")

    def test_writes_json_to_output_file(self):
        source_dir = self.tmp / "src"
        _write(source_dir, "simple.py", SIMPLE_SOURCE)
        output = self.tmp / "metrics.json"
        code, out, _ = _call(cm.main, "--paths", str(source_dir), "--output", str(output))
        self.assertEqual(code, 0)
        self.assertEqual(out, "")
        self.assertIs(json.loads(output.read_text(encoding="utf-8"))["has_bad_files"], False)

    def test_leaves_out_excluded_directory(self):
        _write(self.tmp, "simple.py", SIMPLE_SOURCE)
        _write(self.tmp / "generated", "complex.py", _complex_source())
        args = ["--paths", str(self.tmp), "--exclude-dir", "generated", "--fail-on-bad"]
        code, out, _ = _call(cm.main, *args)
        self.assertEqual(code, 0)
        self.assertNotIn("complex.py", out)

    def test_fails_for_missing_path(self):
        with self.assertRaisesRegex(FileNotFoundError, "Path does not exist"):
            _call(cm.main, "--paths", str(self.tmp / "nowhere"))

    def test_default_paths_are_this_repository_python_directories(self):
        with mock.patch.object(sys, "argv", ["code_metrics.py"]):
            self.assertEqual(cm.parse_args().paths, ["scripts", "tools", "tests"])


class RunTests(TempDirTestCase):
    def test_reports_unparsable_file_and_returns_one(self):
        path = _write(self.tmp, "broken.py", "def broken(:\n")
        code, _, err = _call(cm.run, "--paths", str(self.tmp))
        self.assertEqual(code, 1)
        self.assertIn(f"Failed to parse Python file: {path}", err)

    def test_names_file_that_radon_cannot_measure(self):
        # radon splits lines on U+2028, so a literal one inside a string defeats it
        path = _write(self.tmp, "separator.py", 'TEXT = "a\u2028b"\n')
        code, _, err = _call(cm.run, "--paths", str(self.tmp))
        self.assertEqual(code, 1)
        self.assertIn(f"Failed to compute maintainability index for {path}", err)


if __name__ == "__main__":
    unittest.main()
