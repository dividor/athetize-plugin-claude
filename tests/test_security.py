"""Tests for the security hardening: safe XML parsing and certificate checks in the browser."""

import argparse
import contextlib
import importlib.machinery
import io
import json
import sys
import tempfile
import types
import unittest
import zipfile
from pathlib import Path
from unittest import mock

import defusedxml
from lxml import etree

import pl
import pl_browser
import pl_docx_package
import pl_http
import pl_text
import pl_xml

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
        self.assertTrue(any("pip install defusedxml" in note for note in info["notes"]))


class _FakePage:
    url = "https://example.org/article"

    def goto(self, url, timeout, wait_until):
        return types.SimpleNamespace(headers={"content-type": "text/html"}, status=200)

    def wait_for_timeout(self, ms):
        return None

    def inner_text(self, selector):
        return "Full article text. " * 400

    def evaluate(self, script):
        return None


class _FakeChromium:
    """Stands in for Playwright's Chromium and records how it was started."""

    def __init__(self, calls):
        self.calls = calls

    def launch(self, **kwargs):
        self.calls["launch"] = kwargs
        return self

    def new_context(self, **kwargs):
        self.calls["new_context"] = kwargs
        return types.SimpleNamespace(new_page=_FakePage)

    def close(self):
        self.calls["closed"] = True


class BrowserTlsTests(unittest.TestCase):
    def _fake_playwright(self, calls) -> dict:
        package = types.ModuleType("playwright")
        package.__spec__ = importlib.machinery.ModuleSpec("playwright", None)
        sync_api = types.ModuleType("playwright.sync_api")

        @contextlib.contextmanager
        def sync_playwright():
            yield types.SimpleNamespace(chromium=_FakeChromium(calls))

        setattr(sync_api, "sync_playwright", sync_playwright)
        return {"playwright": package, "playwright.sync_api": sync_api}

    def test_browser_is_started_with_certificate_checks_on(self):
        calls: dict = {}
        with mock.patch.dict(sys.modules, self._fake_playwright(calls)):
            self.assertTrue(pl_browser.available())
            result = pl_browser.fetch("https://example.org/article")
        self.assertEqual(result["kind"], "html")
        self.assertEqual(calls["launch"], {})
        self.assertEqual(calls["new_context"], {"accept_downloads": True})
        self.assertTrue(calls["closed"])

    def test_fetch_reports_a_missing_browser(self):
        with mock.patch.dict(sys.modules, {"playwright": None}):
            self.assertFalse(pl_browser.available())
            self.assertEqual(pl_browser.fetch("https://example.org")["kind"], "error")


if __name__ == "__main__":
    unittest.main()
