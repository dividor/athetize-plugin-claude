"""Finding the Python packages Athetize needs, and installing the missing ones."""

import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pl_packages


def _only_installed(*names):
    """Pretend only these modules are installed."""
    return mock.patch.object(
        pl_packages.importlib.util,
        "find_spec",
        side_effect=lambda name: object() if name in names else None,
    )


class MissingPackagesTests(unittest.TestCase):
    def test_word_and_export_packages_are_reported_when_absent(self):
        with (
            _only_installed(),
            mock.patch.object(pl_packages.pl_text, "pdf_extractor", return_value="pdftotext"),
        ):
            self.assertEqual(pl_packages.missing_packages(), ["defusedxml", "lxml"])

    def test_pypdf_counts_only_when_nothing_can_read_a_pdf(self):
        with (
            _only_installed("defusedxml", "lxml"),
            mock.patch.object(pl_packages.pl_text, "pdf_extractor", return_value=None),
        ):
            self.assertEqual(pl_packages.missing_packages(), ["pypdf"])
        with (
            _only_installed("defusedxml", "lxml"),
            mock.patch.object(pl_packages.pl_text, "pdf_extractor", return_value="pymupdf"),
        ):
            self.assertEqual(pl_packages.missing_packages(), [])

    def test_every_package_says_what_it_is_for(self):
        self.assertEqual(sorted(pl_packages.NEEDED_FOR), ["defusedxml", "lxml", "pypdf"])


class PipCommandTests(unittest.TestCase):
    def _command(self, in_venv, managed):
        with tempfile.TemporaryDirectory() as stdlib:
            if managed:
                (Path(stdlib) / "EXTERNALLY-MANAGED").write_text("[externally-managed]\n")
            prefix = "/venv" if in_venv else "/usr"
            with (
                mock.patch.object(sys, "prefix", prefix),
                mock.patch.object(sys, "base_prefix", "/usr"),
                mock.patch.object(pl_packages.sysconfig, "get_path", return_value=stdlib),
            ):
                return pl_packages.pip_install_command(["lxml"])

    def test_it_installs_into_the_python_that_runs_the_scripts(self):
        command = self._command(in_venv=True, managed=False)
        self.assertEqual(command[:3], [sys.executable, "-m", "pip"])
        self.assertEqual(command[-1], "lxml")

    def test_a_virtual_environment_gets_the_packages_itself(self):
        command = self._command(in_venv=True, managed=True)
        self.assertNotIn("--user", command)
        self.assertNotIn("--break-system-packages", command)

    def test_otherwise_they_go_into_the_user_folder(self):
        command = self._command(in_venv=False, managed=False)
        self.assertIn("--user", command)
        self.assertNotIn("--break-system-packages", command)

    def test_a_system_managed_python_is_told_the_user_folder_is_wanted(self):
        command = self._command(in_venv=False, managed=True)
        self.assertIn("--user", command)
        self.assertIn("--break-system-packages", command)


class InstallTests(unittest.TestCase):
    def _run(self, missing, still):
        calls = []

        def fake_run(command, **kwargs):
            calls.append(command)
            if command[1] == "-c":  # the fresh process that checks what is still missing
                return subprocess.CompletedProcess(command, 0, json.dumps(still), "")
            return subprocess.CompletedProcess(command, 1 if still else 0, "", "ERROR: no network")

        with (
            mock.patch.object(pl_packages, "missing_packages", return_value=missing),
            mock.patch.object(pl_packages.subprocess, "run", side_effect=fake_run),
        ):
            return pl_packages.install_missing(), calls

    def test_nothing_is_run_when_nothing_is_missing(self):
        report, calls = self._run([], [])
        self.assertEqual(report, {"installed": [], "still_missing": [], "command": None})
        self.assertEqual(calls, [])

    def test_missing_packages_are_installed_and_checked_in_a_fresh_process(self):
        report, calls = self._run(["defusedxml", "lxml"], [])
        self.assertEqual(report["installed"], ["defusedxml", "lxml"])
        self.assertEqual(report["still_missing"], [])
        self.assertEqual(calls[0][-2:], ["defusedxml", "lxml"])
        self.assertEqual(calls[1][:2], [sys.executable, "-c"])
        self.assertIn("pip install", report["command"])

    def test_a_failed_install_says_what_will_not_work_and_what_pip_said(self):
        report, _ = self._run(["lxml"], ["lxml"])
        self.assertEqual(report["installed"], [])
        self.assertEqual(report["not_available"], ["the Word copy with suggested edits"])
        self.assertEqual(report["pip_said"], ["ERROR: no network"])

    def test_the_command_fails_loudly_when_a_package_is_still_missing(self):
        with (
            mock.patch.object(
                pl_packages,
                "install_missing",
                return_value={"installed": [], "still_missing": ["lxml"], "command": "pip"},
            ),
            contextlib.redirect_stdout(io.StringIO()) as out,
        ):
            with self.assertRaises(SystemExit) as stop:
                pl_packages.cmd_install_packages(None)
        self.assertEqual(stop.exception.code, 1)
        self.assertEqual(json.loads(out.getvalue())["still_missing"], ["lxml"])

    def test_the_still_missing_check_runs_a_real_python(self):
        self.assertEqual(
            pl_packages._still_missing(["json", "no_such_module_x"]), ["no_such_module_x"]
        )


if __name__ == "__main__":
    unittest.main()
