"""Offline tests for the Athetize helper scripts.

Run from the repository root:  pytest
The network is replaced by a stub that always fails, so the pipeline is exercised with
user-provided sources only. Verdicts normally written by claim-verifier subagents are
simulated from fixtures/sample/simulated_verdicts.json.
"""

import argparse
import contextlib
import io
import json
import os
import re
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path

import pl
import pl_core
import pl_docx
import pl_http
import pl_identify
import pl_ingest
import pl_resolve
import pl_text
from pl_core import Work, jload, jsave

FIX = Path(__file__).resolve().parent / "fixtures" / "sample"


def _offline(*a, **k):
    raise pl_http.HttpError("network error for offline.test: stubbed")


class TextTests(unittest.TestCase):
    SRC = (
        "Results. Mean sleep fell by 18 minutes per night.\n\n"
        "In children under 12 we found no association between spindle density and retention.\f"
        "Page two has the follow-up result: recall was 12% lower."
    )

    def test_exact_quote(self):
        loc = pl_text.locate_quote(self.SRC, "Mean sleep fell by 18 minutes per night.")
        self.assertEqual(loc["mode"], "exact")

    def test_whitespace_and_page_break_tolerated(self):
        loc = pl_text.locate_quote(self.SRC, "retention. Page two has the follow-up result")
        self.assertIsNotNone(loc)

    def test_changed_number_rejected(self):
        self.assertIsNone(
            pl_text.locate_quote(self.SRC, "Mean sleep fell by 20 minutes per night.")
        )

    def test_negation_flip_rejected(self):
        self.assertIsNone(
            pl_text.locate_quote(
                self.SRC,
                (
                    "In children under 12 we found an association between spindle "
                    "density and retention."
                ),
            )
        )

    def test_invented_quote_rejected(self):
        self.assertIsNone(
            pl_text.locate_quote(
                self.SRC, "Participants recalled 30% fewer words in a field study."
            )
        )

    def test_page_numbers(self):
        start = self.SRC.index("Page two")
        self.assertEqual(pl_text.page_at(self.SRC, start), 2)
        self.assertEqual(pl_text.page_at(self.SRC, 3), 1)

    def test_bm25_prefers_relevant_passage(self):
        text = (
            "Cats are small.\n\n" * 5
            + "Spindle density showed r = 0.21 with retention in adults.\n\n"
            + "Dogs bark.\n\n" * 5
        )
        top = pl_text.bm25_rank(text, "spindle density retention 0.21", k=1)
        self.assertIn("Spindle density", top[0]["text"])

    def test_title_similarity(self):
        self.assertGreater(
            pl_text.title_similarity(
                "Sleep spindles and retention", "Sleep Spindles and Retention."
            ),
            0.95,
        )
        self.assertLess(pl_text.title_similarity("Sleep spindles", "School start times"), 0.5)


class ResolveHelperTests(unittest.TestCase):
    def test_title_in_text(self):
        t = (
            "Generative AI without guardrails can harm learning: Evidence "
            "from high school mathematics"
        )
        self.assertTrue(pl_resolve.title_in_text(t, "PNAS\n" + t + "\nAbstract ..."))
        self.assertFalse(
            pl_resolve.title_in_text(t, "Needs and quality of life in ecological economics. " * 50)
        )

    def test_author_mismatch(self):
        ref = {
            "raw": "Fanon, F. (2008). Black skin, white masks (R. Philcox, Trans.). Grove Press."
        }
        self.assertTrue(pl_resolve.author_mismatch(ref, {"authors": ["Webber"]}))
        self.assertFalse(pl_resolve.author_mismatch(ref, {"authors": ["Fanon"]}))
        corp = {"raw": "NICHD Early Child Care Research Network. (2005). Pathways to reading."}
        self.assertFalse(pl_resolve.author_mismatch(corp, {"authors": ["Smith"]}))
        hy = {"raw": "Immordino-Yang, M. H., & Damasio, A. (2007). We feel."}
        self.assertFalse(
            pl_resolve.author_mismatch(hy, {"authors": ["Immordino\u2010Yang", "Damasio"]})
        )

    def test_context_window(self):
        t = "First sentence here. Second one says the key thing. Third sentence follows. Fourth."
        st = t.index("says")
        en = t.index("thing") + 5
        cw = pl_text.context_window(t, st, en, before=40, after=40)
        self.assertEqual(cw["match"], "says the key thing")
        self.assertTrue(cw["after"].startswith("."))

    def test_eric_id(self):
        self.assertEqual(
            pl_resolve.eric_id({"url": "https://eric.ed.gov/?id=ED677055"}), "ED677055"
        )
        self.assertEqual(
            pl_resolve.eric_id({"raw": "... https://eric.ed.gov/?id=EJ1135993 [x]"}), "EJ1135993"
        )
        self.assertIsNone(pl_resolve.eric_id({"url": "https://doi.org/10.1/x"}))

    def test_untrusted_match_skips_record_links(self):
        calls = []
        orig = pl_http.http_get

        def fake(url, *a, **k):
            calls.append(url)
            raise pl_http.HttpError("stub")

        pl_http.http_get = fake
        try:
            with tempfile.TemporaryDirectory() as d:
                ref = {"id": "r1", "title": "A title", "url": "https://example.org/r1.pdf"}
                cand = {
                    "doi": "10.9/wrong",
                    "pdf_urls": ["https://wrong.example/paper.pdf"],
                    "oa_url": "https://wrong.example/oa",
                }
                pl_resolve.try_download_fulltext(ref, cand, Path(d), None, [], trusted=False)
        finally:
            pl_http.http_get = orig
        self.assertTrue(any("example.org/r1.pdf" in c for c in calls))
        self.assertFalse(any("wrong.example" in c for c in calls))


class DocxExportTests(unittest.TestCase):
    DOC = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body>"
        '<w:p><w:r><w:t xml:space="preserve">Short sleep is </w:t></w:r>'
        "<w:r><w:rPr><w:i/></w:rPr><w:t>always</w:t></w:r>"
        '<w:r><w:t xml:space="preserve"> linked to poor recall (Lee, 2020).</w:t></w:r></w:p>'
        "<w:p><w:r><w:t>Orphan fragment.</w:t></w:r></w:p>"
        "</w:body></w:document>"
    )

    def test_tracked_changes(self):
        with tempfile.TemporaryDirectory() as d:
            src, out = Path(d) / "in.docx", Path(d) / "out.docx"
            with zipfile.ZipFile(src, "w") as z:
                z.writestr("word/document.xml", self.DOC)
                z.writestr(
                    "[Content_Types].xml",
                    '<?xml version="1.0"?>'
                    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>',
                )
                z.writestr(
                    "word/_rels/document.xml.rels",
                    '<?xml version="1.0"?><Relationships '
                    'xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>',
                )
            paper = {
                "paragraphs": [
                    {
                        "id": "p1",
                        "text": "Short sleep is always linked to poor recall (Lee, 2020).",
                    },
                    {"id": "p2", "text": "Orphan fragment."},
                ]
            }
            sugs = [
                {
                    "id": "s1",
                    "para": "p1",
                    "original": "Short sleep is always linked to poor recall (Lee, 2020).",
                    "suggested": (
                        "Short sleep was linked to poorer recall in one lab study (Lee, " "2020)."
                    ),
                },
                {"id": "s2", "para": "p2", "original": "Orphan fragment.", "suggested": ""},
            ]
            res = pl_docx.apply(
                src,
                out,
                paper,
                sugs,
                author="Test",
                notes={"s1": ["Athetize s1", "Why: the source tested one lab study.\x0c"]},
            )
            self.assertTrue(all(r["applied"] for r in res), res)
            z = zipfile.ZipFile(out)
            xml = z.read("word/document.xml").decode()
            comments = z.read("word/comments.xml").decode()
            self.assertIn("Why: the source tested one lab study.", comments)
            self.assertIn("commentReference", xml)
            self.assertIn("comments.xml", z.read("word/_rels/document.xml.rels").decode())
            self.assertIn("/word/comments.xml", z.read("[Content_Types].xml").decode())
            self.assertIn("<w:delText", xml)
            self.assertIn("<w:ins ", xml)
            self.assertIn('w:author="Test"', xml)
            # paragraph mark of the deleted orphan is marked deleted
            self.assertRegex(xml, r"<w:pPr><w:rPr><w:del ")
            # accepting changes gives the suggested text
            p1 = xml.split("</w:p>")[0]
            p1 = re.sub(r"<w:del\b.*?</w:del>", "", p1, flags=re.S)
            self.assertEqual(
                "".join(re.findall(r"<w:t[^>]*>([^<]*)</w:t>", p1)),
                "Short sleep was linked to poorer recall in one lab study (Lee, 2020).",
            )

    def test_word_diff_groups_phrases(self):
        ops = pl_core.word_diff(
            "systems hear, score, and represent children", "systems transcribe speech of children"
        )
        self.assertEqual([o[0] for o in ops], ["=", "-", "+", "="])


class WordReportLinkTests(unittest.TestCase):
    """The report offers the Word copy once it has been written beside it."""

    def test_export_refreshes_the_report_so_it_offers_the_word_copy(self):
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / "My paper.docx"
            with zipfile.ZipFile(src, "w") as z:
                z.writestr("word/document.xml", DocxExportTests.DOC)
                z.writestr(
                    "[Content_Types].xml",
                    '<?xml version="1.0"?>'
                    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>',
                )
                z.writestr(
                    "word/_rels/document.xml.rels",
                    '<?xml version="1.0"?><Relationships '
                    'xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>',
                )
            w = Work(Path(d) / "My paper-check")
            w.root.mkdir()
            jsave(w.p("paper.json"), pl_ingest.ingest(src))
            shutil.copy(src, w.p("manuscript.docx"))
            for name in ("refs.json", "claims.json"):
                jsave(w.p(name), [])
            self.assertEqual(
                pl_core.word_export_name(w.paper()), "My paper_athetize_suggestions.docx"
            )
            self.assertIsNone(pl_core.build_data(w)["meta"]["word_export"])
            pl_core.build_report(w)
            args = argparse.Namespace(
                work=str(w.root),
                paper=None,
                out=None,
                only=None,
                exclude=None,
                author="Test",
                no_notes=True,
            )
            with contextlib.redirect_stdout(io.StringIO()) as buf:
                pl.cmd_export_docx(args)
            self.assertIn("Updated report.html", buf.getvalue())
            self.assertTrue(w.p("My paper_athetize_suggestions.docx").exists())
            self.assertIn(
                '"word_export": "My paper_athetize_suggestions.docx"',
                w.p("report.html").read_text("utf-8"),
            )


class CoverageTests(unittest.TestCase):
    def test_citation_markers_detected(self):
        t = (
            "Zhu et al. (2026) distinguish this. Bias exists (Johnson et "
            "al., 2023; Hofmann et al., 2024). "
            "Fanon (1952, 2008) wrote. Bishop's (1990) metaphor. Results "
            "agree [3, 4]. (G. Liu et al., 2026) and (Xu, Wang, et al., 2021)."
        )
        found = [m.group(0) for rx in pl_core._CITE_PATTERNS for m in rx.finditer(t)]
        for want in [
            "Zhu et al. (2026)",
            "(Johnson et al., 2023; Hofmann et al., 2024)",
            "Fanon (1952, 2008)",
            "Bishop's (1990)",
            "[3, 4]",
            "(G. Liu et al., 2026)",
            "(Xu, Wang, et al., 2021)",
        ]:
            self.assertTrue(any(want in f or f in want for f in found), want)


class LinkCitesTests(unittest.TestCase):
    def test_link_cites(self):
        with tempfile.TemporaryDirectory() as d:
            w = Work(d)
            jsave(w.p("paper.json"), {"paragraphs": [{"id": "p1", "kind": "body", "text": "x"}]})
            jsave(
                w.p("refs.json"),
                [
                    {"id": "unicef2024", "raw": "UNICEF. (2024). Card deck."},
                    {
                        "id": "unicef2024b",
                        "raw": "UNICEF Innocenti \u2013 Global Office. (2024). Report.",
                    },
                    {"id": "liu2026", "raw": "Liu, G., Christian, B. (2026). AI assistance."},
                    {"id": "vaughn2020", "raw": "Vaughn, M., Jang, B. G. (2020). Review."},
                ],
            )
            jsave(
                w.p("claims.json"),
                [
                    {
                        "id": "c1",
                        "para": "p1",
                        "text": "Gains are greatest (UNICEF Innocenti, 2024).",
                        "cites": [],
                    },
                    {
                        "id": "c2",
                        "para": "p1",
                        "text": "Design guidance follows (UNICEF, 2024).",
                        "cites": [],
                    },
                    {
                        "id": "c3",
                        "para": "p1",
                        "text": (
                            "Skipping rose (G. Liu et al., 2026) and agency matters (Vaughn, "
                            "Jang, et al., 2020)."
                        ),
                        "cites": [],
                    },
                ],
            )
            res = pl_core.link_cites(w)
            cl = {c["id"]: c["cites"] for c in w.claims()}
            self.assertEqual(cl["c1"], ["unicef2024b"])
            self.assertEqual(cl["c2"], ["unicef2024"])
            self.assertEqual(sorted(cl["c3"]), ["liu2026", "vaughn2020"])
            self.assertEqual(res["added"], 4)


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

    def test_a_file_may_replace_full_text_when_every_reference_is_open(self):
        self.put("d.txt", self.ALVAREZ + "Sleep duration fell across cohorts. " * 40)
        res = pl.import_paths(self.work, [self.papers], every=True, dry_run=True)
        self.assertEqual([(m["file"], m["ref"]) for m in res["matched"]], [("d.txt", "1")])
        self.assertEqual(res["duplicates"], [])


class IngestTests(unittest.TestCase):
    def test_fixture_structure(self):
        paper = pl_ingest.ingest(FIX / "paper.md")
        kinds = {p["id"]: p["kind"] for p in paper["paragraphs"]}
        self.assertEqual(paper["title"], "Sleep Duration and Memory Consolidation in Adolescents")
        self.assertEqual(kinds["p1"], "heading")
        self.assertEqual(kinds["p5"], "body")
        self.assertEqual(kinds["p9"], "reference")
        self.assertEqual(kinds["p12"], "reference")


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


class PendingReportTests(unittest.TestCase):
    """A report can be built before any claim has been verified."""

    def test_report_builds_when_cited_claims_have_no_verdict_yet(self):
        with tempfile.TemporaryDirectory() as d:
            w = Work(d)
            jsave(w.p("paper.json"), pl_ingest.ingest(FIX / "paper.md"))
            for f in ("refs.json", "claims.json"):
                shutil.copy(FIX / f, w.p(f))
            data = pl_core.build_data(w)
            by_id = {c["id"]: c for c in data["claims"]}
            self.assertEqual(by_id["c1"]["verdict"], "pending")
            item = by_id["c1"]["items"][0]
            self.assertEqual(item["verdict"], "pending")
            self.assertEqual(item["quote"], "")
            self.assertEqual(item["analysis"], "Not checked yet.")
            self.assertEqual(by_id["c4"]["verdict"], "uncited")
            self.assertTrue(pl_core.build_report(w).exists())
            self.assertEqual(pl_core.suggest_prepare(w)["flagged_claims"], 0)


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

    def test_request_totals_follow_the_missing_only_filter(self):
        res = self._request(missing_only=True)
        self.assertEqual(res["summary"]["need_source"], 2)
        self.assertEqual(res["summary"]["abstract_only"], 0)
        self.assertEqual(res["summary"]["claims_relying"], 2)
        md = self.work.p("source_request.md").read_text("utf-8")
        self.assertIn("4 references: 2 have no text at all. 2 claims rely", md)
        self.assertNotIn("quickest wins", md)

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

    def test_report_has_a_sources_page_with_a_way_back_and_a_way_to_add(self):
        html = pl_core.build_report(self.work).read_text("utf-8")
        for needed in (
            'id="sourcesView"',
            'id="back"',
            'id="addSources"',
            'id="pickFiles"',
            'id="pickFolder"',
            'id="srcFilter"',
        ):
            self.assertIn(needed, html)
        self.assertIn("Back to document", html)
        self.assertIn("Review Missing Sources", html)
        self.assertIn('id="wordReport"', html)
        self.assertNotIn("Screening aid", html)
        self.assertNotIn("<kbd>", html)
        self.assertNotIn(".pdf' next to this report", html)


class PipelineTests(unittest.TestCase):
    """ingest -> validate -> resolve (offline) -> prepare -> verify -> build."""

    @classmethod
    def setUpClass(cls):
        cls._orig_get = pl_http.http_get
        pl_http.http_get = _offline
        cls.tmp = Path(tempfile.mkdtemp())
        cls.home_patch = os.environ.get("ATHETIZE_HOME")
        os.environ["ATHETIZE_HOME"] = str(cls.tmp / "home")
        os.environ.pop("ATHETIZE_EMAIL", None)
        cls.work = Work(cls.tmp / "sample-check")
        w = cls.work
        w.root.mkdir(parents=True)
        for d in ("sources", "claims_parts", "batches", "verdicts"):
            w.p(d).mkdir()
        jsave(w.p("paper.json"), pl_ingest.ingest(FIX / "paper.md"))
        for f in ("refs.json", "claims.json", "notes.json"):
            shutil.copy(FIX / f, w.p(f))
        for f in (FIX / "sources").iterdir():
            shutil.copy(f, w.p("sources", f.name))
        cls.manifest = pl_resolve.resolve_all(
            w.root, w.refs(), w.manifest(), None, refresh=False, download=True
        )
        jsave(w.p("manifest.json"), cls.manifest)
        cls.prep = pl_core.prepare(w)

    @classmethod
    def tearDownClass(cls):
        pl_http.http_get = cls._orig_get
        if cls.home_patch is None:
            os.environ.pop("ATHETIZE_HOME", None)
        else:
            os.environ["ATHETIZE_HOME"] = cls.home_patch
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _write_simulated(self):
        sim = jload(FIX / "simulated_verdicts.json")
        w = self.work
        for bf in sorted(w.p("batches").glob("batch-*.json")):
            batch = jload(bf)
            out = []
            for c in batch["claims"]:
                for r in c["refs"]:
                    v = sim.get(f"{c['claim_id']}|{r['ref_id']}")
                    if v:
                        out.append({"claim_id": c["claim_id"], "ref_id": r["ref_id"], **v})
            jsave(w.p(batch["output_file"]), out)

    def test_01_validate_clean(self):
        errors, warnings = pl_core.validate(self.work)
        self.assertEqual(errors, [])

    def test_02_validate_catches_bad_claim(self):
        w = self.work
        good = w.claims()
        bad = good + [
            {
                "id": "c99",
                "para": "p5",
                "text": "This sentence is not in the paper.",
                "cites": ["9"],
                "type": "empirical",
            }
        ]
        jsave(w.p("claims.json"), bad)
        try:
            errors, _ = pl_core.validate(w)
            self.assertTrue(any("c99" in e for e in errors))
        finally:
            jsave(w.p("claims.json"), good)

    def test_03_sources_resolved(self):
        refs = self.manifest["refs"]
        for rid in ("1", "2", "3"):
            self.assertEqual(refs[rid]["status"], "user_provided", rid)
            self.assertEqual(refs[rid]["evidence_level"], "full_text", rid)
        self.assertIn(refs["4"]["status"], ("missing", "unresolved"))
        self.assertEqual(refs["4"]["evidence_level"], "none")

    def test_04_prepare_makes_batches_and_auto_unverifiable(self):
        self.assertGreaterEqual(self.prep["batches"], 1)
        self.assertGreaterEqual(self.prep["auto_unverifiable"], 1)  # c5 -> ref 4 has no source
        batch = jload(self.work.p("batches", "batch-001.json"))
        claim_ids = {c["claim_id"] for c in batch["claims"]}
        self.assertNotIn("c4", claim_ids)  # uncited
        self.assertNotIn("c5", claim_ids)  # no source text

    def test_05_verify_downgrades_fabricated_quote(self):
        self._write_simulated()
        res = pl_core.verify(self.work)
        items = {(i["claim_id"], i["ref_id"]): i for i in self.work.verdicts()["items"]}
        fab = items[("c2", "2")]
        self.assertEqual(fab["original_verdict"], "supported")
        self.assertEqual(fab["verdict"], "unverifiable")
        self.assertIn("quote_not_found", fab["flags"])
        good = items[("c3", "3")]
        self.assertEqual(good["verdict"], "contradicted")
        self.assertIsNotNone(good["evidence"])
        self.assertEqual(items[("c5", "4")]["verdict"], "unverifiable")
        self.assertGreaterEqual(res["downgraded_by_checks"], 1)

    def test_06_aggregate_rules(self):
        self.assertEqual(pl_core.aggregate([]), "pending")
        self.assertEqual(pl_core.aggregate(["supported", "contradicted"]), "partially_supported")
        self.assertEqual(pl_core.aggregate(["supported", "unverifiable"]), "supported")
        self.assertEqual(pl_core.aggregate(["unverifiable", "unsupported"]), "unverifiable")
        self.assertEqual(pl_core.aggregate(["overstated", "contradicted"]), "overstated")

    def test_07_abstract_cannot_refute(self):
        w = self.work
        claim = {"claim_id": "cx", "claim_hash": "h"}
        ref = {"ref_id": "r", "evidence_level": "abstract", "evidence_hash": "e"}
        v = pl_core.verify_verdict(
            w,
            {"verdict": "unsupported", "confidence": 0.9, "analysis": "Nothing found."},
            claim,
            ref,
            {},
        )
        self.assertEqual(v["verdict"], "unverifiable")
        self.assertIn("abstract_cannot_refute", v["flags"])

    def test_08_build_data_and_report(self):
        self._write_simulated()
        pl_core.verify(self.work)
        data = pl_core.build_data(self.work)
        by_id = {c["id"]: c for c in data["claims"]}
        self.assertEqual(by_id["c3"]["verdict"], "contradicted")
        self.assertEqual(by_id["c4"]["verdict"], "uncited")
        self.assertEqual(by_id["c1"]["verdict"], "supported")
        self.assertEqual(by_id["c2"]["verdict"], "unverifiable")
        self.assertEqual(by_id["c5"]["verdict"], "unverifiable")
        self.assertEqual(
            by_id["c6"]["verdict"], "supported"
        )  # best of supported, partially_supported... see aggregate
        for c in data["claims"]:
            para = next(p for p in data["paragraphs"] if p["id"] == c["para"])
            self.assertEqual(para["text"][c["start"] : c["end"]], c["text"])
        self.assertFalse(any(p["kind"] == "reference" for p in data["paragraphs"]))
        out = pl_core.build_report(self.work)
        html = out.read_text("utf-8")
        self.assertNotIn("/*__ATHETIZE_DATA__*/null", html)
        self.assertNotIn("<script src=", html)
        self.assertNotIn("http://", html.replace("http://www.w3.org", ""))

    def test_09_cache_skips_unchanged_pairs(self):
        self._write_simulated()
        pl_core.verify(self.work)
        again = pl_core.prepare(self.work)
        # Every pair already has a verdict keyed by claim and evidence hash, including the
        # downgraded one, so a rerun has nothing left to verify.
        self.assertEqual(again["pending_claims"], 0)
        self.assertEqual(again["batches"], 0)

    def test_09b_changed_source_invalidates_cache(self):
        w = self.work
        src = w.p("sources", "2.txt")
        original = src.read_text("utf-8")
        try:
            src.write_text(
                original + "\n\nAddendum. A replication found a similar effect.", "utf-8"
            )
            m = pl_resolve.resolve_all(
                w.root, w.refs(), w.manifest(), None, refresh=False, download=True
            )
            jsave(w.p("manifest.json"), m)
            again = pl_core.prepare(w)
            self.assertGreaterEqual(again["pending_claims"], 1)
        finally:
            src.write_text(original, "utf-8")
            m = pl_resolve.resolve_all(
                w.root, w.refs(), w.manifest(), None, refresh=False, download=True
            )
            jsave(w.p("manifest.json"), m)

    def test_10_html_escaping(self):
        s = pl_core.safe_json_for_html({"x": "</script><b>&"})
        self.assertNotIn("</script>", s)
        self.assertNotIn("<", s)
        self.assertEqual(json.loads(s)["x"], "</script><b>&")


if __name__ == "__main__":
    unittest.main()
