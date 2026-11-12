"""Tests for the security hardening: safe XML parsing and no browser automation."""

import argparse
import contextlib
import io
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

import defusedxml
from lxml import etree

import pl
import pl_cli
import pl_docx_package
import pl_http
import pl_text
import pl_workspace
import pl_xml

ROOT = Path(__file__).resolve().parent.parent
PLUGIN_DIR = ROOT / "athetize"
INTERNAL_ENTITY = b'<?xml version="1.0"?><!DOCTYPE d [<!ENTITY e "expanded">]><d>x &e; y</d>'
W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _external_entity(path: Path) -> bytes:
    return (
        f'<?xml version="1.0"?><!DOCTYPE d [<!ENTITY e SYSTEM "{path.as_uri()}">]><d>&e;</d>'
    ).encode()


class SafeXmlTests(unittest.TestCase):
    def test_ordinary_xml_is_parsed(self):
        root = pl_xml.parse_xml(b"<a><b>text</b></a>")
        self.assertEqual(root.find("b").text, "text")

    def test_entity_declarations_are_refused(self):
        with self.assertRaises(defusedxml.EntitiesForbidden):
            pl_xml.parse_xml(INTERNAL_ENTITY)

    def test_external_entities_are_refused(self):
        with tempfile.TemporaryDirectory() as d:
            secret = Path(d) / "secret.txt"
            secret.write_text("TOP-SECRET", "utf-8")
            with self.assertRaises(defusedxml.EntitiesForbidden):
                pl_xml.parse_xml(_external_entity(secret))

    def test_word_file_with_entity_declarations_is_refused(self):
        document = (
            '<?xml version="1.0"?><!DOCTYPE d [<!ENTITY e "expanded">]>'
            f'<w:document xmlns:w="{W_NS}"><w:body><w:p><w:r><w:t>&e;</w:t></w:r></w:p>'
            "</w:body></w:document>"
        )
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "paper.docx"
            with zipfile.ZipFile(path, "w") as z:
                z.writestr("word/document.xml", document)
            with self.assertRaises(defusedxml.EntitiesForbidden):
                pl_text.read_docx_paragraphs(path)

    def test_missing_defusedxml_fails_with_install_advice(self):
        with mock.patch.dict(sys.modules, {"defusedxml": None}):
            with self.assertRaises(RuntimeError) as ctx:
                pl_xml.parse_xml(b"<a/>")
        self.assertIn("pip install defusedxml", str(ctx.exception))


class WordExportXmlTests(unittest.TestCase):
    def test_internal_entities_are_not_expanded(self):
        root = pl_docx_package.parse_xml(INTERNAL_ENTITY, etree)
        self.assertNotIn("expanded", "".join(root.itertext()))

    def test_external_entities_are_not_read(self):
        with tempfile.TemporaryDirectory() as d:
            secret = Path(d) / "secret.txt"
            secret.write_text("TOP-SECRET", "utf-8")
            root = pl_docx_package.parse_xml(_external_entity(secret), etree)
            self.assertNotIn("TOP-SECRET", etree.tostring(root).decode())


class DoctorTests(unittest.TestCase):
    def _doctor(self) -> dict:
        def offline(*args, **kwargs):
            raise pl_http.HttpError("network error for offline.test: stubbed")

        buf = io.StringIO()
        with mock.patch.object(pl_http, "http_get", offline), contextlib.redirect_stdout(buf):
            pl.cmd_doctor(argparse.Namespace())
        return json.loads(buf.getvalue())

    def test_doctor_reports_the_xml_parser(self):
        self.assertEqual(self._doctor()["xml_parser"], "defusedxml")

    def test_doctor_says_how_to_install_a_missing_xml_parser(self):
        with mock.patch.dict(sys.modules, {"defusedxml": None}):
            info = self._doctor()
        self.assertIsNone(info["xml_parser"])
        self.assertIn("defusedxml", info["missing_packages"])
        self.assertTrue(any("pl.py install-packages" in note for note in info["notes"]))


class ContactEmailSourceTests(unittest.TestCase):
    """The contact email is sent to the scholarly indexes, so it comes only from Settings."""

    def test_an_email_in_the_environment_is_not_used(self):
        with tempfile.TemporaryDirectory() as d:
            env = {"ATHETIZE_HOME": d, "ATHETIZE_EMAIL": "from-env@example.org"}
            with mock.patch.dict("os.environ", env):
                self.assertNotIn("email", pl_workspace.load_config())
                pl_workspace.set_email("saved@example.org")
                self.assertEqual(pl_workspace.load_config()["email"], "saved@example.org")

    def test_no_script_reads_an_email_from_the_environment(self):
        for path in sorted((PLUGIN_DIR / "scripts").glob("*.py")):
            self.assertNotIn("ATHETIZE_EMAIL", path.read_text("utf-8"), path.name)


class NoBrowserAutomationTests(unittest.TestCase):
    """The scripts never drive a browser; pages that need one go to Claude's own tools."""

    DRIVERS = ("playwright", "selenium", "pyppeteer", "puppeteer", "webdriver")

    def test_no_script_or_requirement_names_a_browser_driver(self):
        files = sorted((PLUGIN_DIR / "scripts").glob("*.py")) + [
            PLUGIN_DIR / "scripts" / "requirements.txt"
        ]
        self.assertGreater(len(files), 20)
        for path in files:
            text = path.read_text("utf-8").casefold()
            for driver in self.DRIVERS:
                self.assertNotIn(driver, text, f"{path.name} mentions {driver}")

    def test_resolve_has_no_browser_option(self):
        parser = pl_cli.build_parser("test")
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            parser.parse_args(["resolve", "--work", "x", "--browser"])
        self.assertFalse(hasattr(parser.parse_args(["resolve", "--work", "x"]), "browser"))

    def test_doctor_does_not_report_or_suggest_a_headless_browser(self):
        with mock.patch.object(pl_http, "http_get", return_value=(b"{}", "application/json", "")):
            with contextlib.redirect_stdout(io.StringIO()) as buf:
                pl.cmd_doctor(argparse.Namespace())
        report = json.loads(buf.getvalue())
        self.assertNotIn("headless_browser", report)
        self.assertNotIn("browser", " ".join(report["notes"]).casefold())


if __name__ == "__main__":
    unittest.main()
