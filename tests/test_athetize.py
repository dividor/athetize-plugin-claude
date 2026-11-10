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


class RecordMatchTests(unittest.TestCase):
    """Judging a database record against the reference as the paper prints it."""

    REPORT = {
        "id": "5",
        "title": (
            "Origins, Worlds, and Life: A Decadal Strategy for Planetary Science and "
            "Astrobiology 2023\u20132032"
        ),
        "authors": ["National Academies of Sciences, Engineering, and Medicine"],
        "year": 2023,
        "doi": "10.17226/26522",
    }
    ARTICLE = {
        "id": "34",
        "title": "Hydrogen and methane in Titan's atmosphere: chemistry, diffusion and escape",
        "authors": ["Strobel, D. F."],
        "year": 2012,
        "doi": "10.1139/p11-131",
    }

    def test_markup_is_removed_from_a_record_title(self):
        clean = pl_resolve.clean_title
        self.assertEqual(clean("The CH<sub>4</sub> structure"), "The CH4 structure")
        self.assertEqual(clean("Ly\n   <i>\u03b1</i>\n   Emissions"), "Ly \u03b1 Emissions")
        self.assertEqual(clean("Sleep &amp; memory<br/>revisited"), "Sleep & memory revisited")
        self.assertEqual(clean(None), "")

    def test_record_with_only_the_main_title_is_accepted(self):
        record = {"title": "Origins, Worlds, and Life", "authors": [], "year": 2023}
        self.assertGreaterEqual(pl_resolve.score_candidate(self.REPORT, record), 0.85)

    def test_record_title_with_a_footnote_after_it_is_accepted(self):
        record = {
            "title": self.ARTICLE["title"] + " 1This article is part of a Special Issue.",
            "authors": ["Strobel"],
            "year": 2012,
        }
        self.assertGreaterEqual(pl_resolve.score_candidate(self.ARTICLE, record), 0.85)
        shown = pl_resolve.citation_string(self.ARTICLE, record)
        self.assertIn(self.ARTICLE["title"], shown)
        self.assertNotIn("Special Issue", shown)

    def test_a_short_shared_opening_is_not_enough(self):
        ref = {"title": "Sleep and memory in adolescents: a cohort study", "year": 2020}
        record = {"title": "Sleep and", "year": 2020}
        self.assertLess(pl_resolve.score_candidate(ref, record), 0.6)

    def test_differences_from_an_accepted_record_are_listed(self):
        ref = {"title": "Pathways to Discovery in Astronomy", "year": 2021, "doi": "10.17226/26141"}
        record = {
            "title": "Pathways to Discovery in Astronomy",
            "year": 2023,
            "doi": "10.17226/26141",
        }
        self.assertEqual(
            pl_resolve.discrepancies(ref, record),
            ["year: the reference says 2021, the record says 2023"],
        )
        ref = {"title": "Lyman-alpha models for LRO LAMP based on MESSENGER data", "year": 2013}
        record = {
            "title": "Lyman-\u03b1 Models for LRO LAMP from MESSENGER and SOHO Data",
            "year": 2014,
            "doi": "10.1007/978-1-4614-6384-9_5",
        }
        got = pl_resolve.discrepancies(ref, record)
        self.assertEqual(len(got), 2)
        self.assertTrue(got[0].startswith("title: the record reads"))
        self.assertEqual(
            got[1], "DOI: the reference prints none, the record has 10.1007/978-1-4614-6384-9_5"
        )
        self.assertEqual(pl_resolve.discrepancies(self.ARTICLE, dict(self.ARTICLE)), [])
        self.assertEqual(pl_resolve.discrepancies(self.ARTICLE, None), [])

    def test_a_stored_mismatch_is_judged_again_with_the_current_rules(self):
        with tempfile.TemporaryDirectory() as d:
            prior = {
                "exists": "mismatch",
                "match_score": 0.404,
                "matched": {
                    "title": "Origins, Worlds, and <i>Life</i>",
                    "authors": [],
                    "year": 2023,
                    "doi": "10.17226/26522",
                },
                "doi": "10.17226/26522",
                "record_title": "Origins, Worlds, and <i>Life</i>",
                "metadata_done": True,
                "retracted": False,
                "retraction_notes": [],
            }
            entry = pl_resolve.resolve_one(
                Path(d), self.REPORT, prior, None, refresh=False, download=False
            )
        self.assertEqual(entry["exists"], "verified")
        self.assertEqual(entry["matched"]["title"], "Origins, Worlds, and Life")
        self.assertNotIn("record_title", entry)
        self.assertEqual(entry["discrepancies"], [])
        self.assertIn("A Decadal Strategy", entry["citation"])


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


def _docx_with(path, body_xml):
    """Write a minimal Word file whose body is the given paragraphs."""
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(
            "word/document.xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            f"<w:body>{body_xml}</w:body></w:document>",
        )
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


def _accepted_text(docx):
    """The document's text with every tracked change accepted."""
    xml = zipfile.ZipFile(docx).read("word/document.xml").decode()
    xml = re.sub(r"<w:del .*?</w:del>", "", xml, flags=re.S)
    return "".join(re.findall(r"<w:t(?: [^>]*)?>([^<]*)</w:t>", xml))


class DocxNormalisedTextTests(unittest.TestCase):
    """Edits still land when Word's characters differ from the text stored at ingest."""

    # an angstrom sign (U+212B) and an fi ligature (U+FB01), as Word files often hold them
    RAW = "The exobase sits near 4000 \u212b and the \ufb01t is good (Lee, 2020)."

    def _export(self, suggestions):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        src, out = Path(d) / "in.docx", Path(d) / "out.docx"
        _docx_with(src, f"<w:p><w:r><w:t>{self.RAW}</w:t></w:r></w:p>")
        paper = pl_ingest.ingest(src)
        stored = paper["paragraphs"][0]["text"]
        self.assertEqual(
            stored, "The exobase sits near 4000 \u00c5 and the fit is good (Lee, 2020)."
        )
        for s in suggestions:
            s["para"] = "p1"
        return pl_docx.apply(src, out, paper, suggestions, author="Test"), out

    def test_paragraph_is_found_and_the_untouched_characters_are_kept(self):
        res, out = self._export(
            [
                {
                    "id": "s1",
                    "original": "sits near 4000 \u00c5",
                    "suggested": "sits at about 4000 \u00c5",
                }
            ]
        )
        self.assertEqual(res, [{"id": "s1", "applied": True}])
        self.assertEqual(
            _accepted_text(out),
            "The exobase sits at about 4000 \u212b and the \ufb01t is good (Lee, 2020).",
        )

    def test_an_edit_inside_a_ligature_replaces_the_whole_word(self):
        res, out = self._export(
            [{"id": "s1", "original": "the fit is good", "suggested": "the match is good"}]
        )
        self.assertEqual(res, [{"id": "s1", "applied": True}])
        self.assertEqual(
            _accepted_text(out),
            "The exobase sits near 4000 \u212b and the match is good (Lee, 2020).",
        )

    def test_map_gives_each_stored_character_its_raw_span(self):
        norm, spans = pl_docx._norm_with_map("a \u00a0b\u00adc \ufb01 A\u030a")
        self.assertEqual(norm, "a bc fi \u00c5")
        self.assertEqual(len(norm), len(spans))
        self.assertEqual(spans[5], spans[6])  # f and i come from the one ligature
        self.assertEqual(spans[-1], (9, 11))  # A with its combining ring
        self.assertEqual(pl_docx._raw_range(spans, norm, 6, 7, "X"), (7, 8, "fX"))


class TrackedChangesInManuscriptTests(unittest.TestCase):
    """A manuscript that already holds another person's tracked insertion stays valid."""

    W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    # the middle of the sentence was inserted by someone else and is still tracked
    BODY = (
        '<w:p><w:r><w:t xml:space="preserve">Profiles </w:t></w:r>'
        '<w:ins w:id="7" w:author="Koskinen" w:date="2026-01-01T00:00:00Z">'
        '<w:r><w:t xml:space="preserve">often cannot be fitted by diffusive models</w:t></w:r>'
        "</w:ins>"
        '<w:r><w:t xml:space="preserve"> (Lee, 2020).</w:t></w:r></w:p>'
    )

    def test_new_insertion_is_not_placed_inside_the_other_persons_insertion(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        src, out = Path(d) / "in.docx", Path(d) / "out.docx"
        _docx_with(src, self.BODY)
        paper = pl_ingest.ingest(src)
        original = "Profiles often cannot be fitted by diffusive models (Lee, 2020)."
        self.assertEqual(paper["paragraphs"][0]["text"], original)
        sug = {
            "id": "s1",
            "para": "p1",
            "original": "often cannot be fitted by diffusive models",
            "suggested": "cannot both be fitted by a single diffusive model",
        }
        res = pl_docx.apply(src, out, paper, [sug], author="Athetize")
        self.assertEqual(res, [{"id": "s1", "applied": True}])
        self.assertEqual(
            _accepted_text(out),
            "Profiles cannot both be fitted by a single diffusive model (Lee, 2020).",
        )
        from lxml import etree

        root = etree.fromstring(zipfile.ZipFile(out).read("word/document.xml"))
        ins = "{%s}ins" % self.W_NS
        nested = [e for e in root.iter(ins) if any(a.tag == ins for a in e.iterancestors())]
        self.assertEqual(nested, [])
        authors = [e.get("{%s}author" % self.W_NS) for e in root.iter(ins)]
        self.assertIn("Athetize", authors)
        # their text is still theirs, now in more than one piece, each with its own id
        theirs = [e for e in root.iter(ins) if e.get("{%s}author" % self.W_NS) == "Koskinen"]
        self.assertGreater(len(theirs), 1)
        ids = [e.get("{%s}id" % self.W_NS) for e in root.iter(ins)]
        self.assertEqual(len(ids), len(set(ids)))


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


class IngestTests(unittest.TestCase):
    def test_fixture_structure(self):
        paper = pl_ingest.ingest(FIX / "paper.md")
        kinds = {p["id"]: p["kind"] for p in paper["paragraphs"]}
        self.assertEqual(paper["title"], "Sleep Duration and Memory Consolidation in Adolescents")
        self.assertEqual(kinds["p1"], "heading")
        self.assertEqual(kinds["p5"], "body")
        self.assertEqual(kinds["p9"], "reference")
        self.assertEqual(kinds["p12"], "reference")

    def _ingest_docx(self, body_xml, name="Titan_escape-plan.docx"):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        src = Path(d) / name
        _docx_with(src, body_xml)
        return pl_ingest.ingest(src)

    @staticmethod
    def _p(text):
        return f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>"

    def test_document_name_is_the_title_when_the_paper_marks_none(self):
        # an opening line, a numbered section and a heading far down are not a title
        paper = self._ingest_docx(
            self._p("Science/Technical/Management Plan")
            + self._p("1. Introduction")
            + self._p("We propose to characterize the escape of hydrogen from Titan.") * 5
            + self._p("TASKS / TIMELINE")
        )
        self.assertEqual(paper["title"], "Titan_escape-plan")
        self.assertEqual(paper["paragraphs"][1]["kind"], "heading")
        self.assertEqual(paper["paragraphs"][-1]["kind"], "heading")

    def test_a_title_marked_in_the_paper_is_used(self):
        styled = (
            '<w:p><w:pPr><w:pStyle w:val="Title"/></w:pPr>'
            "<w:r><w:t>Escape of hydrogen from Titan</w:t></w:r></w:p>"
        )
        paper = self._ingest_docx(
            self._p("Draft for comment") + styled + self._p("1. Introduction")
        )
        self.assertEqual(paper["title"], "Escape of hydrogen from Titan")
        self.assertEqual(
            pl_ingest.ingest(FIX / "paper.md")["title"],
            "Sleep Duration and Memory Consolidation in Adolescents",
        )

    def test_marks_without_words_are_kept_but_not_sent_for_checking(self):
        paper = self._ingest_docx(
            self._p("Plan of work for the escape study")
            + self._p("*")
            + self._p("12")
            + self._p("Fit the profile (PI, Student)")
            + self._p("References")
            + self._p("1.")
        )
        kinds = [p["kind"] for p in paper["paragraphs"]]
        self.assertEqual(kinds, ["body", "other", "other", "body", "heading", "reference"])
        self.assertEqual(
            [p["id"] for p in paper["paragraphs"]], ["p1", "p2", "p3", "p4", "p5", "p6"]
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


class FailingSourceTests(unittest.TestCase):
    """A source that does not back a claim stays visible when another source does better."""

    def test_claim_keeps_its_best_verdict_and_names_the_failing_source(self):
        with tempfile.TemporaryDirectory() as d:
            w = Work(d)
            jsave(w.p("paper.json"), pl_ingest.ingest(FIX / "paper.md"))
            for f in ("refs.json", "claims.json"):
                shutil.copy(FIX / f, w.p(f))
            # c6 cites references 1 and 2; c1 cites reference 1 only
            rows = [("c6", "1", "partially_supported"), ("c6", "2", "unsupported")]
            rows.append(("c1", "1", "supported"))
            jsave(
                w.p("verdicts.json"),
                {
                    "items": [
                        {
                            "claim_id": c,
                            "ref_id": r,
                            "verdict": v,
                            "analysis": "x",
                            "confidence": 0.9,
                            "evidence_level": "full_text",
                        }
                        for c, r, v in rows
                    ]
                },
            )
            by_id = {c["id"]: c for c in pl_core.build_data(w)["claims"]}
            self.assertEqual(by_id["c6"]["verdict"], "partially_supported")
            self.assertEqual(by_id["c6"]["failing_refs"], ["2"])
            self.assertEqual(by_id["c1"]["failing_refs"], [])
            html = pl_core.build_report(w).read_text("utf-8")
            self.assertIn('"failing_refs": ["2"]', html)
            self.assertIn("has-bad", html)


class MergeSuggestionsTests(unittest.TestCase):
    """Suggestions written in part files become one list."""

    def _args(self, work, replace=False):
        return argparse.Namespace(work=str(work.root), replace=replace)

    def _merge(self, work, replace=False):
        with contextlib.redirect_stdout(io.StringIO()) as buf:
            pl.cmd_merge_suggestions(self._args(work, replace))
        return buf.getvalue()

    def test_replace_drops_a_suggestion_removed_from_its_part_file(self):
        with tempfile.TemporaryDirectory() as d:
            w = Work(d)
            jsave(w.p("paper.json"), pl_ingest.ingest(FIX / "paper.md"))
            for f in ("refs.json", "claims.json"):
                shutil.copy(FIX / f, w.p(f))
            w.p("suggest_parts").mkdir()
            para = w.paper()["paragraphs"][4]
            first = {"para": para["id"], "original": para["text"][:30], "suggested": "A."}
            second = {"para": para["id"], "original": para["text"][40:70], "suggested": "B."}
            jsave(w.p("suggest_parts", "part-01.json"), [first, second])
            self.assertIn("Merged 2 new suggestions (2 total)", self._merge(w))
            jsave(w.p("suggest_parts", "part-01.json"), [dict(second, suggested="C.")])
            # a plain merge only adds, so the removed and the old wording both stay
            self.assertIn("Merged 0 new suggestions (2 total)", self._merge(w))
            self.assertIn("Rebuilt suggestions.json", self._merge(w, replace=True))
            self.assertEqual([(s["id"], s["suggested"]) for s in w.suggestions()], [("s1", "C.")])


class ProvisionalResultTests(unittest.TestCase):
    """A verdict reached from an abstract alone is marked provisional."""

    def _item(self, verdict, level):
        return {"verdict": verdict, "evidence_level": level}

    def test_basis_is_the_fullest_text_behind_the_claims_own_verdict(self):
        basis = pl_core.evidence_basis
        items = [self._item("supported", "abstract"), self._item("unverifiable", "none")]
        self.assertEqual(basis("supported", items), "abstract")
        items.append(self._item("supported", "full_text"))
        self.assertEqual(basis("supported", items), "full_text")
        # full text behind a different verdict does not vouch for this one
        mixed = [self._item("supported", "abstract"), self._item("unverifiable", "full_text")]
        self.assertEqual(basis("supported", mixed), "abstract")
        self.assertEqual(basis("unverifiable", [self._item("unverifiable", "none")]), "none")

    def test_report_marks_only_judged_abstract_results_as_provisional(self):
        with tempfile.TemporaryDirectory() as d:
            w = Work(d)
            jsave(w.p("paper.json"), pl_ingest.ingest(FIX / "paper.md"))
            for f in ("refs.json", "claims.json"):
                shutil.copy(FIX / f, w.p(f))
            rows = [
                ("c1", "1", "supported", "full_text"),
                ("c2", "2", "supported", "abstract"),
                ("c3", "3", "unverifiable", "abstract"),
            ]
            jsave(
                w.p("verdicts.json"),
                {
                    "items": [
                        {
                            "claim_id": c,
                            "ref_id": r,
                            "verdict": v,
                            "analysis": "x",
                            "confidence": 0.9,
                            "evidence_level": level,
                        }
                        for c, r, v, level in rows
                    ]
                },
            )
            by_id = {c["id"]: c for c in pl_core.build_data(w)["claims"]}
            self.assertEqual(
                [(by_id[c]["evidence_basis"], by_id[c]["provisional"]) for c in ("c1", "c2", "c3")],
                [("full_text", False), ("abstract", True), ("abstract", False)],
            )
            with contextlib.redirect_stdout(io.StringIO()) as buf:
                pl.cmd_build(argparse.Namespace(work=str(w.root), out=None))
            self.assertIn("Provisional: 1 result rests on an abstract only.", buf.getvalue())
            html = w.p("report.html").read_text("utf-8")
            self.assertIn('id="provLine"', html)
            self.assertIn("this result is provisional until the full text is added", html)


class OutdatedSuggestionTests(unittest.TestCase):
    """A rewording drafted for a claim that is no longer flagged is removed."""

    def test_suggestion_for_a_claim_now_supported_is_dropped_everywhere(self):
        with tempfile.TemporaryDirectory() as d:
            w = Work(d)
            jsave(w.p("paper.json"), pl_ingest.ingest(FIX / "paper.md"))
            for f in ("refs.json", "claims.json"):
                shutil.copy(FIX / f, w.p(f))
            para = w.paper()["paragraphs"][4]
            keep = {"claim_id": "c2", "para": para["id"], "original": "x", "suggested": "y"}
            gone = {"claim_id": "c1", "para": para["id"], "original": "a", "suggested": "b"}
            fix = {"kind": "reference", "para": "p9", "original": "r", "suggested": "s"}
            w.p("suggest_parts").mkdir()
            jsave(w.p("suggest_parts", "part-01.json"), [keep, gone])
            jsave(w.p("suggest_parts", "references.json"), [fix])
            pl_core.merge_suggestions(w)
            rows = [("c1", "1", "supported"), ("c2", "2", "overstated")]
            jsave(
                w.p("verdicts.json"),
                {
                    "items": [
                        {
                            "claim_id": c,
                            "ref_id": r,
                            "verdict": v,
                            "analysis": "x",
                            "confidence": 0.9,
                            "evidence_level": "full_text",
                        }
                        for c, r, v in rows
                    ]
                },
            )
            out = pl_core.suggest_prepare(w)
            self.assertEqual(
                [(x["claim_id"], x["now"]) for x in out["dropped_outdated"]], [("c1", "supported")]
            )
            left = [(s.get("claim_id"), s["kind"]) for s in w.suggestions()]
            self.assertEqual(left, [("c2", "rewrite"), (None, "reference")])
            # a later merge does not bring it back
            pl_core.merge_suggestions(w)
            self.assertEqual(len(w.suggestions()), 2)
            self.assertEqual(pl_core.suggest_prepare(w)["dropped_outdated"], [])


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
