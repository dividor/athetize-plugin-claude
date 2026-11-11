"""Tests for bringing in sources: reading PDFs, matching files to references, duplicates,
adding a source, and the counts used to ask for sources that could not be downloaded.
"""

import argparse
import contextlib
import io
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pl
import pl_core
import pl_http
import pl_identify
import pl_ingest
import pl_report
import pl_resolve
import pl_text
from pl_core import Work, jload, jsave

FIX = Path(__file__).resolve().parent / "fixtures" / "sample"


def _offline(*a, **k):
    raise pl_http.HttpError("network error for offline.test: stubbed")


def _tiny_pdf(pages):
    """A small, valid PDF with one line of text per page, built here so no binary is stored."""
    kids = " ".join(f"{4 + 2 * i} 0 R" for i in range(len(pages)))
    objs = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    for i, text in enumerate(pages):
        stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET"
        objs.append(
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 3 0 R >> >> /Contents {5 + 2 * i} 0 R >>"
        )
        objs.append(f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream")
    out, offsets = b"%PDF-1.4\n", []
    for number, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n{body}\nendobj\n".encode()
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets)
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return out


class PdfReadingTests(unittest.TestCase):
    """PDFs are read with the fastest extractor present; pypdf is the one always worth having."""

    def _present(self, modules=(), tools=()):
        """Pretend only these Python modules and command-line tools are installed."""
        find = mock.patch.object(
            pl_text.importlib.util,
            "find_spec",
            side_effect=lambda name: object() if name in modules else None,
        )
        which = mock.patch.object(
            pl_text.shutil, "which", side_effect=lambda tool: tool if tool in tools else None
        )
        return find, which

    def _choice(self, modules=(), tools=()):
        find, which = self._present(modules, tools)
        with find, which:
            return pl_text.pdf_extractor()

    def test_fastest_extractor_present_is_chosen(self):
        self.assertEqual(self._choice(("fitz", "pypdf"), ("pdftotext",)), "pymupdf")
        self.assertEqual(self._choice(("pypdf",), ("pdftotext",)), "pdftotext")
        self.assertEqual(self._choice(("pypdf",)), "pypdf")
        self.assertIsNone(self._choice())

    def test_pypdf_reads_each_page_when_it_is_the_only_extractor(self):
        with tempfile.TemporaryDirectory() as d:
            pdf = Path(d) / "paper.pdf"
            pdf.write_bytes(_tiny_pdf(["Methane escapes from Titan", "Second page of the paper"]))
            with mock.patch.object(pl_text, "pdf_extractor", return_value="pypdf"):
                text = pl_text.read_pdf_text(pdf)
                via_file = pl_text.extract_file_text(pdf)
        self.assertEqual(
            [page.strip() for page in text.split("\f")],
            ["Methane escapes from Titan", "Second page of the paper"],
        )
        self.assertIn("Methane escapes from Titan", via_file)
        self.assertIn("\f", via_file)

    def test_no_extractor_says_what_to_install(self):
        with mock.patch.object(pl_text, "pdf_extractor", return_value=None):
            with self.assertRaises(RuntimeError) as caught:
                pl_text.read_pdf_text(Path("paper.pdf"))
        self.assertIn("pip install pypdf", str(caught.exception))


class ImportSourcesTests(unittest.TestCase):
    def setUp(self):
        self.pl = pl
        self.targets = {
            "steele1997": {
                "id": "steele1997",
                "raw": (
                    "Steele, C. M. (1997). A threat in the air: How stereotypes shape "
                    "intellectual identity and performance. American Psychologist, "
                    "52(6), 613-629."
                ),
            },
            "hall2010": {
                "id": "hall2010",
                "raw": (
                    "Hall, L. A. (2010). The negative consequences of becoming a "
                    "good reader: Identity theory as a lens for understanding struggling "
                    "readers. Teachers College Record, 112(7)."
                ),
            },
        }

    def test_filename_id_wins(self):
        rid, how, _ = self.pl._match_file_to_ref(Path("hall2010.pdf"), "", self.targets)
        self.assertEqual((rid, how), ("hall2010", "filename"))

    def test_content_match_needs_title_and_author(self):
        text = (
            "American Psychologist 1997\n"
            "A Threat in the Air: How Stereotypes Shape Intellectual Identity "
            "and Performance\nClaude M. Steele\n"
        ) + "x " * 400
        rid, how, _ = self.pl._match_file_to_ref(Path("download (3).pdf"), text, self.targets)
        self.assertEqual((rid, how), ("steele1997", "content"))
        wrong = text.replace("Steele", "Someone")
        rid, how, _ = self.pl._match_file_to_ref(Path("download (3).pdf"), wrong, self.targets)
        self.assertIsNone(rid)

    def test_unrelated_file_unmatched(self):
        rid, how, _ = self.pl._match_file_to_ref(
            Path("notes.txt"), "grocery list: milk, eggs", self.targets
        )
        self.assertIsNone(rid)

    def test_title_fragment_trimmed(self):
        self.assertEqual(
            self.pl._ref_title({"title": "Black skin, white masks (R"}), "Black skin, white masks"
        )


class IdentifyFileTests(unittest.TestCase):
    """A supplied file is identified from what it says, whatever it is called."""

    def setUp(self):
        self.refs = {
            "1": {
                "id": "1",
                "title": "Methane escape from Titan's atmosphere",
                "authors": ["Yelle, R. V."],
                "year": 2008,
                "doi": "10.1029/2007JE003031",
            },
            "9": {
                "id": "9",
                "title": "Titan's hydrodynamically escaping atmosphere",
                "authors": ["Strobel, D. F."],
                "year": 2008,
            },
            "10": {
                "id": "10",
                "title": (
                    "Titan's hydrodynamically escaping atmosphere: Escape rates and the "
                    "structure of the exobase region"
                ),
                "authors": ["Strobel, D. F."],
                "year": 2009,
            },
            "35": {
                "id": "35",
                "title": "Hybrid fluid/kinetic modeling of Pluto's escaping atmosphere",
                "authors": ["Erwin, J."],
                "year": 2013,
                "arxiv": "arXiv:1211.3994",
            },
        }
        # the opening of the paper that is reference 35, which discusses the other works
        self.erwin = (
            "Hybrid fluid/kinetic modeling of\nPluto\u2019s escaping atmosphere\n"
            "Justin Erwin, OJ Tucker, Robert E Johnson\n\nAbstract\n"
            "Methane escape from the atmosphere of Titan was treated by Yelle et al. (2008), "
            "and Strobel (2008, 2009) described a hydrodynamically escaping atmosphere and "
            "its escape rates near the exobase region for Titan's structure. "
        ) + "Further text of the paper. " * 60

    def match(self, name, text):
        return pl._match_file_to_ref(Path(name), text, self.refs)

    def test_title_page_decides_among_works_on_the_same_subject(self):
        self.assertEqual(self.match("download (6).pdf", self.erwin), ("35", "content", []))

    def test_what_the_file_says_beats_a_misleading_name(self):
        self.assertEqual(self.match("1.pdf", self.erwin), ("35", "content", []))

    def test_doi_on_the_first_page_identifies_the_file(self):
        text = "JOURNAL OF GEOPHYSICAL RESEARCH\ndoi:10.1029/2007JE003031, 2008\n" + "x " * 400
        self.assertEqual(self.match("jgre2380.pdf", text), ("1", "content", []))

    def test_arxiv_number_identifies_the_file(self):
        text = "arXiv:1211.3994v2 [astro-ph.EP] 7 Jun 2013\nPreprint\n" + "x " * 400
        self.assertEqual(self.match("1211.3994v2.pdf", text), ("35", "content", []))

    def test_a_longer_doi_does_not_count_as_this_one(self):
        text = "doi:10.1029/2007JE0030315 Some other article\n" + "x " * 400
        self.assertEqual(self.match("other.pdf", text)[0], None)

    def test_longer_title_wins_when_one_title_begins_another(self):
        text = (
            "Titan's hydrodynamically escaping atmosphere: Escape rates and the structure "
            "of the exobase region\nDarrell F. Strobel\nIcarus 2009\n"
        ) + "x " * 400
        self.assertEqual(self.match("main.pdf", text), ("10", "content", []))

    def test_publisher_file_name_is_not_read_as_a_reference_number(self):
        rid, how, _ = self.match("1-s2.0-S0019103508003692-main.pdf", "unrelated " * 200)
        self.assertEqual((rid, how), (None, "no match"))

    def test_file_named_after_the_reference_is_used_when_the_text_is_silent(self):
        self.assertEqual(self.match("9.pdf", "scanned pages " * 100), ("9", "filename", []))
        self.assertEqual(self.match("notes__10.txt", "x " * 100), ("10", "filename", []))

    def test_title_without_the_author_is_not_enough(self):
        text = self.erwin.replace("Erwin", "Someone")
        self.assertEqual(self.match("download.pdf", text)[0], None)

    def test_a_scanned_title_that_is_nearly_word_for_word_is_accepted(self):
        refs = {
            "38": {
                "id": "38",
                "title": (
                    "Solar Lyman-alpha latitude variation at solar maximum from "
                    "interplanetary Lyman-alpha observations"
                ),
                "authors": ["Pryor, W. R."],
                "year": 1992,
            },
            "4": {"id": "4", "title": "The Archean atmosphere", "authors": ["Catling, D."]},
        }
        scan = (
            "The Astrophysical Journal, 394:363-377, 1992 July 20\n"
            "SOLAR LYMAN-a LATITUDE VARIATION AT SOLAR MAXIMUM FROM\n"
            "INTERPLANETARY LYMAN-a OBSERVATIONS\nW. R. Pryor, J. M. Ajello, C. A. Barth\n"
        ) + "Interplanetary observations were made with two spectrometers. " * 30
        self.assertEqual(
            pl._match_file_to_ref(Path("1992ApJ.pdf"), scan, refs), ("38", "content", [])
        )
        # the same title without the author is not enough
        nameless = scan.replace("Pryor", "Someone")
        self.assertIsNone(pl._match_file_to_ref(Path("1992ApJ.pdf"), nameless, refs)[0])

    def test_confirmed_doi_is_used_when_the_paper_gave_none(self):
        entry = {"exists": "verified", "doi": "10.1016/j.icarus.2007.08.014"}
        self.assertEqual(pl_identify.with_known_doi(self.refs["9"], entry)["doi"], entry["doi"])
        self.assertNotIn("doi", pl_identify.with_known_doi(self.refs["9"], {"doi": "10.1/x"}))
        self.assertEqual(
            pl_identify.with_known_doi(self.refs["1"], entry)["doi"], "10.1029/2007JE003031"
        )


class DuplicateSourcesTests(unittest.TestCase):
    """A folder of papers is scanned for repeats before anything is added."""

    BRANDT = (
        "Sleep spindles and overnight retention across the lifespan\n"
        "Karin Brandt and Rafael Silva\nJournal of Sleep Studies, 2021\n\n"
    )
    ALVAREZ = (
        "Secular trends in adolescent sleep duration\nMaria Alvarez and Li Chen\n"
        "Journal of Sleep Research, 2019\n\n"
    )

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.work = Work(self.tmp / "sample-check")
        self.work.root.mkdir()
        jsave(self.work.p("paper.json"), pl_ingest.ingest(FIX / "paper.md"))
        for f in ("refs.json", "claims.json"):
            shutil.copy(FIX / f, self.work.p(f))
        statuses = {"1": "full_text", "2": "abstract_only", "3": "missing", "4": "missing"}
        jsave(
            self.work.p("manifest.json"),
            {"refs": {k: {"status": v} for k, v in statuses.items()}},
        )
        self.papers = self.tmp / "papers"
        self.papers.mkdir()

    def put(self, name, text):
        (self.papers / name).write_text(text, "utf-8")

    def test_repeats_are_named_and_only_one_copy_is_taken(self):
        self.put("a.txt", self.BRANDT + "Published version. " * 40)
        self.put("b.txt", self.BRANDT + "Published version. " * 40)
        self.put("c.txt", self.BRANDT + "Preprint with different wording. " * 40)
        self.put("d.txt", self.ALVAREZ + "Sleep duration fell across cohorts. " * 40)
        res = pl.import_paths(self.work, [self.papers], dry_run=True)
        self.assertEqual(
            [(m["file"], m["ref"], m["how"]) for m in res["matched"]], [("a.txt", "3", "content")]
        )
        self.assertEqual(
            res["duplicates"],
            [
                {"file": "b.txt", "why": "same content as a.txt"},
                {
                    "file": "c.txt",
                    "ref": "3",
                    "why": "another copy of reference 3; a.txt is used",
                },
                {"file": "d.txt", "ref": "1", "why": "reference 1 already has its full text"},
            ],
        )
        self.assertEqual((res["unmatched"], res["ambiguous"]), ([], []))

    def test_a_file_with_almost_no_text_says_so(self):
        self.put("saved page.txt", "Enable JavaScript and cookies to continue")
        res = pl.import_paths(self.work, [self.papers], dry_run=True)
        self.assertEqual(
            res["unmatched"],
            [
                {
                    "file": "saved page.txt",
                    "why": "has almost no text (a scan, or a saved sign-in page?)",
                }
            ],
        )

    def test_a_file_may_replace_full_text_when_every_reference_is_open(self):
        self.put("d.txt", self.ALVAREZ + "Sleep duration fell across cohorts. " * 40)
        res = pl.import_paths(self.work, [self.papers], every=True, dry_run=True)
        self.assertEqual([(m["file"], m["ref"]) for m in res["matched"]], [("d.txt", "1")])
        self.assertEqual(res["duplicates"], [])


class AddSourceTests(unittest.TestCase):
    def _workspace(self, d):
        w = Work(d)
        jsave(w.p("paper.json"), pl_ingest.ingest(FIX / "paper.md"))
        for f in ("refs.json", "claims.json"):
            shutil.copy(FIX / f, w.p(f))
        w.p("sources").mkdir()
        return w

    def _add(self, w, ref, source):
        orig = pl_http.http_get
        pl_http.http_get = _offline
        try:
            with contextlib.redirect_stdout(io.StringIO()) as buf:
                pl.cmd_add_source(argparse.Namespace(work=str(w.root), ref=ref, source=str(source)))
        finally:
            pl_http.http_get = orig
        return buf.getvalue()

    def test_adding_a_file_already_in_the_sources_folder_keeps_it(self):
        with tempfile.TemporaryDirectory() as d:
            w = self._workspace(d)
            placed = w.p("sources", "1.txt")
            shutil.copy(FIX / "sources" / "1.txt", placed)
            printed = self._add(w, "1", placed)
            self.assertTrue(placed.exists())
            self.assertIn("status=user_provided", printed)
            self.assertEqual(w.manifest()["refs"]["1"]["status"], "user_provided")

    def test_adding_a_file_replaces_an_older_one_for_the_same_reference(self):
        with tempfile.TemporaryDirectory() as d:
            w = self._workspace(d)
            w.p("sources", "1.md").write_text("old copy", "utf-8")
            self._add(w, "1", FIX / "sources" / "1.txt")
            self.assertFalse(w.p("sources", "1.md").exists())
            self.assertEqual(
                w.p("sources", "1.txt").read_text("utf-8"),
                (FIX / "sources" / "1.txt").read_text("utf-8"),
            )


class RecordLinkTests(unittest.TestCase):
    """The label that says where a reference was found leads to its record there."""

    def test_record_page_for_each_database(self):
        doi = "10.1029/2007je003031"
        self.assertEqual(
            pl_report.record_url({"source": "crossref", "doi": doi}),
            "https://search.crossref.org/search/works?from_ui=yes&q=10.1029/2007je003031",
        )
        self.assertEqual(
            pl_report.record_url({"source": "openalex", "doi": doi}),
            "https://openalex.org/works?filter=doi:10.1029/2007je003031",
        )
        self.assertIn("%3C", pl_report.record_url({"source": "crossref", "doi": "10.1/a<b"}))

    def test_no_link_without_a_doi_or_a_known_database(self):
        self.assertIsNone(pl_report.record_url(None))
        self.assertIsNone(pl_report.record_url({"source": "crossref"}))
        self.assertIsNone(pl_report.record_url({"source": "elsewhere", "doi": "10.1/x"}))


class SourcesNeededTests(unittest.TestCase):
    """The counts used to ask the person for sources that could not be downloaded."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        home = os.environ.get("ATHETIZE_HOME")
        os.environ["ATHETIZE_HOME"] = str(self.tmp / "home")
        self.addCleanup(self._restore_home, home)
        self.work = Work(self.tmp / "sample-check")
        w = self.work
        w.root.mkdir(parents=True)
        jsave(w.p("paper.json"), pl_ingest.ingest(FIX / "paper.md"))
        for f in ("refs.json", "claims.json"):
            shutil.copy(FIX / f, w.p(f))
        # Reference 1 has full text; 2 has an abstract; 3 and 4 have nothing.
        # Claims: c1 cites 1, c2 cites 2, c3 cites 3, c4 cites nothing, c5 cites 4,
        # c6 cites 1 and 2.
        statuses = {"1": "full_text", "2": "abstract_only", "3": "missing", "4": "missing"}
        jsave(w.p("manifest.json"), {"refs": {k: {"status": v} for k, v in statuses.items()}})

    @staticmethod
    def _restore_home(value):
        if value is None:
            os.environ.pop("ATHETIZE_HOME", None)
        else:
            os.environ["ATHETIZE_HOME"] = value

    def _request(self, missing_only=False):
        with contextlib.redirect_stdout(io.StringIO()) as buf:
            pl.cmd_request_sources(
                argparse.Namespace(
                    work=str(self.work.root),
                    folder="~/Downloads/Test",
                    proxy="",
                    min_claims=0,
                    missing_only=missing_only,
                )
            )
        return json.loads(buf.getvalue())

    def test_a_claim_citing_two_lacking_references_counts_once(self):
        claims = [{"id": "c1", "cites": ["a", "b"]}, {"id": "c2", "cites": ["c"]}, {"id": "c3"}]
        got = pl_core.sources_needed(5, {"a": "abstract_only", "b": "missing"}, claims)
        self.assertEqual(
            got,
            {
                "references": 5,
                "need_source": 2,
                "abstract_only": 1,
                "no_text": 1,
                "claims_relying": 1,
            },
        )

    def test_request_prints_and_saves_the_totals(self):
        res = self._request()
        want = {
            "references": 4,
            "need_source": 3,
            "abstract_only": 1,
            "no_text": 2,
            "claims_relying": 4,
        }
        self.assertEqual(res["summary"], want)
        self.assertEqual(res["count"], 3)
        # Most-cited first, each with the citation needed to name it to the person.
        self.assertEqual([i["id"] for i in res["items"]], ["2", "3", "4"])
        self.assertEqual(res["items"][0]["claims_affected"], 2)
        self.assertEqual(res["items"][0]["status"], "abstract_only")
        self.assertTrue(res["items"][0]["citation"].startswith("Okafor"))
        self.assertEqual(jload(self.work.p("source_request.json"))["summary"], want)
        md = self.work.p("source_request.md").read_text("utf-8")
        self.assertIn(
            "This list covers 3 of the paper's 4 references: 1 has only an abstract and "
            "2 have no text at all. 4 claims rely on at least one of them",
            md,
        )
        self.assertIn("has only a provisional result: the full text is what settles it", md)
        self.assertNotIn("quickest", md)
        self.assertNotIn("partly checked", md)

    def test_request_totals_follow_the_missing_only_filter(self):
        res = self._request(missing_only=True)
        self.assertEqual(res["summary"]["need_source"], 2)
        self.assertEqual(res["summary"]["abstract_only"], 0)
        self.assertEqual(res["summary"]["claims_relying"], 2)
        md = self.work.p("source_request.md").read_text("utf-8")
        self.assertIn("4 references: 2 have no text at all. 2 claims rely", md)
        self.assertNotIn("provisional", md)

    def test_request_says_so_when_nothing_is_lacking(self):
        statuses = {k: {"status": "full_text"} for k in ("1", "2", "3", "4")}
        jsave(self.work.p("manifest.json"), {"refs": statuses})
        res = self._request()
        self.assertEqual(res["count"], 0)
        self.assertEqual(res["summary"]["claims_relying"], 0)
        md = self.work.p("source_request.md").read_text("utf-8")
        self.assertIn("already has its full text", md)

    def test_report_data_carries_the_totals_and_the_download_list(self):
        before = pl_core.build_data(self.work)["sources_needed"]
        self.assertEqual(before["need_source"], 3)
        self.assertEqual(before["claims_relying"], 4)
        self.assertIsNone(before["request_file"])
        self.assertIsNone(before["folder"])
        self._request()
        after = pl_core.build_data(self.work)["sources_needed"]
        self.assertEqual(after["request_file"], "source_request.md")
        self.assertEqual(after["folder"], "~/Downloads/Test")
        html = pl_core.build_report(self.work).read_text("utf-8")
        self.assertIn('id="needSrc"', html)
        self.assertIn('"sources_needed": {"references": 4, "need_source": 3', html)

    def test_the_list_is_one_short_table_with_a_direct_link_per_work(self):
        self._request()
        md = self.work.p("source_request.md").read_text("utf-8")
        rows = [line for line in md.splitlines() if line.startswith("| [")]
        self.assertEqual(len(rows), 3)
        self.assertTrue(rows[0].startswith("| [2] Okafor"), rows[0])
        self.assertIn("| 2 | abstract |", rows[0])
        self.assertIn("| nothing |", rows[1])
        self.assertNotIn("##", md)
        self.assertLess(len(md.splitlines()), 15)

    def test_report_says_why_a_source_has_no_full_text(self):
        reasons = pl_core.missing_reasons
        self.assertEqual(
            reasons({"errors": [], "oa_urls": []}), ["no open-access copy is listed for it"]
        )
        entry = {
            "oa_urls": ["https://example.org/a.pdf"],
            "errors": [
                "unpaywall skipped: no contact email configured (run: pl.py config set-email)",
                "openalex download: HTTP 403 for www.sciencedirect.com",
                "openalex download: HTTP 403 for www.sciencedirect.com",
                "openalex: link did not return a PDF",
            ],
        }
        self.assertEqual(
            reasons(entry),
            [
                "open-access copies were not looked up on Unpaywall (no contact email set)",
                "www.sciencedirect.com refused an automatic download",
                "an open link did not lead to the full text",
            ],
        )
        refs = {r["id"]: r for r in pl_core.build_data(self.work)["refs"]}
        self.assertEqual(refs["1"]["why_missing"], [])  # it has full text
        self.assertEqual(refs["3"]["why_missing"], ["no open-access copy is listed for it"])

    def test_reasons_are_kept_when_a_later_run_tries_no_download(self):
        ref = {"id": "3", "title": "Sleep spindles", "authors": ["Brandt, K."], "year": 2021}
        prior = {
            "exists": "not_found",
            "metadata_done": True,
            "matched": None,
            "status": "missing",
            "errors": ["openalex download: HTTP 403 for example.org"],
        }
        with tempfile.TemporaryDirectory() as d:
            entry = pl_resolve.resolve_one(Path(d), ref, prior, None, refresh=False, download=False)
        self.assertEqual(entry["errors"], ["openalex download: HTTP 403 for example.org"])

    def test_a_failed_download_on_a_later_run_keeps_the_abstract(self):
        ref = {"id": "3", "title": "Sleep spindles", "authors": ["Brandt, K."], "year": 2021}
        with tempfile.TemporaryDirectory() as d:
            work = Path(d)
            (work / "sources" / "_auto").mkdir(parents=True)
            (work / "sources" / "_auto" / "3.abstract.txt").write_text(
                "An abstract. " * 30, "utf-8"
            )
            prior = {
                "exists": "verified",
                "metadata_done": True,
                "matched": {"title": "Sleep spindles", "authors": ["Brandt"], "year": 2021},
                "status": "abstract_only",
                "evidence_level": "abstract",
                "evidence_file": "sources/_auto/3.abstract.txt",
                "evidence_hash": "abc",
                "evidence_chars": 390,
            }
            real_get = pl_http.http_get
            pl_http.http_get = _offline
            try:
                entry = pl_resolve.resolve_one(work, ref, prior, None, refresh=False, download=True)
            finally:
                pl_http.http_get = real_get
        self.assertEqual(entry["status"], "abstract_only")
        self.assertEqual(entry["evidence_file"], "sources/_auto/3.abstract.txt")
        self.assertEqual(entry["evidence_hash"], "abc")

    def test_nothing_tells_the_person_how_to_name_a_file(self):
        res = self._request()
        md = self.work.p("source_request.md").read_text("utf-8")
        self.assertIn("Keep whatever file names you get", md)
        self.assertNotIn("Save as", md)
        saved = jload(self.work.p("source_request.json"))
        for item in res["items"] + saved["items"]:
            self.assertNotIn("save_as", item)
        with contextlib.redirect_stdout(io.StringIO()) as buf:
            pl.cmd_status(argparse.Namespace(work=str(self.work.root)))
        for row in json.loads(buf.getvalue())["needs_source"]:
            self.assertNotIn("provide_as", row)
        for ref in pl_core.build_data(self.work)["refs"]:
            self.assertNotIn("provide_as", ref)

    def test_report_has_results_and_sources_tabs_and_a_way_to_add(self):
        html = pl_core.build_report(self.work).read_text("utf-8")
        for needed in (
            'id="sourcesView"',
            'class="pagetabs"',
            'id="openResults"',
            'id="openSources"',
            'id="addSources"',
            'id="pickFiles"',
            'id="pickFolder"',
            'id="srcFilter"',
        ):
            self.assertIn(needed, html)
        self.assertNotIn("Back to document", html)
        self.assertIn('Athetize <span class="brand-sub">Fact Checker</span>', html)
        self.assertNotIn("Citation check", html)
        self.assertNotIn("drop them on this page", html)
        self.assertIn('id="foot"', html)
        self.assertIn('<a class="dl" id="wordReport"', html)
        self.assertIn('id="settingsBtn"', html)
        self.assertIn("'Citations '", html)
        self.assertNotIn("Cited for", html)
        self.assertIn("Download Report</a>", html)
        self.assertNotIn("in the window that has opened", html)
        self.assertIn("Review Missing Sources", html)
        self.assertNotIn("'No source'", html)
        self.assertNotIn("'Located'", html)
        self.assertIn('id="wordReport"', html)
        self.assertNotIn("Screening aid", html)
        self.assertNotIn("<kbd>", html)
        self.assertNotIn(".pdf' next to this report", html)


if __name__ == "__main__":
    unittest.main()
