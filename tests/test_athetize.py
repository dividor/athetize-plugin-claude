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
