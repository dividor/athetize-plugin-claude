"""Golden-output regression test for the Athetize helper scripts.

Runs the offline pipeline and most helper functions on the sample fixtures and compares
every result with ``tests/fixtures/sample/golden.json``. Timestamps and temporary paths are
scrubbed before comparison. Any behaviour change, intended or not, shows up as a diff.

To accept an intended change, regenerate the file and review the diff in git:

    ATHETIZE_UPDATE_GOLDEN=1 pytest tests/test_golden.py
"""

import argparse
import contextlib
import io
import json
import os
import re
import shutil
import tempfile
import types
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

ROOT = Path(__file__).resolve().parent.parent
FIX = ROOT / "tests" / "fixtures" / "sample"
GOLDEN = FIX / "golden.json"

VOLATILE_KEYS = {
    "generated",
    "updated_at",
    "attempted_at",
    "work_dir",
    "request_file",
    "queue_file",
    "version",
}

DIGEST = re.compile(r"[0-9a-f]{16}|[0-9a-f]{64}")

TEX = r"""
\documentclass{article}
\title{A \textbf{TeX} paper}
\begin{document}
\maketitle
\section{Intro}
Sleep matters~\cite{alvarez2019, okafor2020}. % a comment
It was shown\citep[p.~3]{brandt2021} that ``spindles'' help \emph{retention}.
\begin{figure}
ignored figure body
\end{figure}
\section*{References}
\bibitem{alvarez2019} Alvarez, M. (2019). Secular trends.
\bibitem{okafor2020} Okafor, T. (2020). Restricted sleep.
\end{document}
"""

MD = """# Title here

Intro paragraph with a [link](http://example.org) and **bold** text
continued on a second line.

- item one [1]
- item two

| a | b |
|---|---|

---

![figure](img.png)

## References

[1] Someone (2020). A thing.
"""

DOCX_XML = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
    "<w:body>"
    '<w:p><w:pPr><w:pStyle w:val="Title"/></w:pPr><w:r><w:t>Docx title</w:t></w:r></w:p>'
    '<w:p><w:r><w:t xml:space="preserve">Short sleep is </w:t></w:r>'
    "<w:r><w:rPr><w:i/></w:rPr><w:t>always</w:t></w:r>"
    '<w:r><w:t xml:space="preserve"> linked to poor recall (Lee, 2020).</w:t></w:r></w:p>'
    "<w:p><w:r><w:t>Second</w:t><w:tab/><w:t>paragraph with tab.</w:t></w:r></w:p>"
    "<w:p><w:r><w:t>Orphan fragment.</w:t></w:r></w:p>"
    "</w:body></w:document>"
)

JATS = (
    '<?xml version="1.0"?><article xmlns:xlink="http://www.w3.org/1999/xlink"><front>'
    "<article-meta><title-group><article-title>School start times and attendance: a district "
    "trial</article-title></title-group><abstract><p>We tested later start times.</p></abstract>"
    "</article-meta></front><body><sec><title>Methods</title>"
    + "".join(
        f"<p>Paragraph {i} describes the district trial of later start times and attendance "
        "outcomes in detail, with numbers such as 9% and 12 schools.</p>"
        for i in range(40)
    )
    + "<fig><caption>A caption.</caption></fig></sec></body></article>"
)


def _html_page(title: str, words: int = 2600) -> str:
    body = " ".join(f"word{i % 37} attendance trial" for i in range(words))
    return (
        f"<html><head><title>{title}</title><script>var x=1;</script></head><body>"
        f"<nav>menu</nav><h1>{title}</h1><p>{body}</p><p>Discussion of the results.</p>"
        "<footer>foot</footer></body></html>"
    )


def _scrub(obj, tmp: str):
    """Drop volatile keys and replace temporary paths, recursively."""
    if isinstance(obj, dict):
        return {k: _scrub(v, tmp) for k, v in obj.items() if k not in VOLATILE_KEYS}
    if isinstance(obj, list):
        return [_scrub(v, tmp) for v in obj]
    if isinstance(obj, str):
        if DIGEST.fullmatch(obj):
            return "digest:" + obj  # labelled, so secret scanners do not take it for a key
        return obj.replace(tmp, "<tmp>")
    return obj


class FakeNetwork:
    """Canned responses keyed by URL fragment, in the order they are checked."""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def __call__(self, url, email=None, accept="*/*", timeout=25, max_bytes=0):
        self.calls.append(url)
        for frag, resp in self.routes:
            if frag in url:
                if isinstance(resp, Exception):
                    raise resp
                data, ctype = resp
                return data, ctype, url
        raise pl_http.HttpError(f"network error for {url}: stubbed")


def _offline(*a, **k):
    raise pl_http.HttpError("network error for offline.test: stubbed")


class GoldenTest(unittest.TestCase):
    maxDiff = None

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls.env = {k: os.environ.get(k) for k in ("ATHETIZE_HOME", "ATHETIZE_EMAIL")}
        os.environ["ATHETIZE_HOME"] = str(cls.tmp / "home")
        os.environ.pop("ATHETIZE_EMAIL", None)
        cls._orig_get = pl_http.http_get
        cls.actual = {}
        try:
            cls._collect()
        finally:
            pl_http.http_get = cls._orig_get

    @classmethod
    def tearDownClass(cls):
        for k, v in cls.env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(cls.tmp, ignore_errors=True)

    @classmethod
    def _collect(cls):
        out = cls.actual
        s = types.SimpleNamespace(tmp=str(cls.tmp))
        pl_http.http_get = _offline
        cls._collect_ingest(out, s)
        cls._collect_pipeline(out, s)
        cls._collect_suggestions(out, s)
        cls._collect_carry_over(out, s)
        cls._collect_link_cites(out, s)
        cls._collect_text(out, s)
        cls._collect_resolve_helpers(out, s)
        cls._collect_downloads(out, s)
        cls._collect_metadata(out, s)
        cls._collect_docx(out, s)
        cls._collect_cli(out, s)
        cls.actual = _scrub(out, s.tmp)

    @classmethod
    def _collect_ingest(cls, out, s):
        # --- ingest -------------------------------------------------------------
        out["ingest_md"] = pl_ingest.ingest(FIX / "paper.md")
        out["parse_markdown"] = pl_ingest.parse_markdown(MD)
        out["parse_tex"] = pl_ingest.parse_tex(TEX)
        out["parse_plain"] = pl_ingest.parse_plain("One para.\n\n\nTwo\nlines.\n")
        out["looks_like_heading"] = {
            s: pl_ingest.looks_like_heading(s)
            for s in (
                "References",
                "1. Introduction",
                "RESULTS",
                "A sentence ends.",
                "2.1 Data:",
                "",
            )
        }
        tex_path = cls.tmp / "paper.tex"
        tex_path.write_text(TEX, "utf-8")
        out["ingest_tex"] = pl_ingest.ingest(tex_path)
        s.docx_path = cls.tmp / "paper.docx"
        with zipfile.ZipFile(s.docx_path, "w") as z:
            z.writestr("word/document.xml", DOCX_XML)
        out["ingest_docx"] = pl_ingest.ingest(s.docx_path)
        out["ingest_docx"].pop("sha256")  # zip members carry the current time

    @classmethod
    def _collect_pipeline(cls, out, s):
        # --- pipeline on the fixture ---------------------------------------------
        s.w = Work(cls.tmp / "sample-check")
        s.w.root.mkdir(parents=True)
        for d in ("sources", "claims_parts", "batches", "verdicts"):
            s.w.p(d).mkdir()
        jsave(s.w.p("paper.json"), pl_ingest.ingest(FIX / "paper.md"))
        for f in ("refs.json", "claims.json", "notes.json"):
            shutil.copy(FIX / f, s.w.p(f))
        for f in (FIX / "sources").iterdir():
            shutil.copy(f, s.w.p("sources", f.name))
        manifest = pl_resolve.resolve_all(
            s.w.root, s.w.refs(), s.w.manifest(), None, refresh=False, download=True
        )
        jsave(s.w.p("manifest.json"), manifest)
        out["manifest"] = manifest
        out["validate"] = pl_core.validate(s.w)
        out["coverage"] = pl_core.coverage(s.w)
        out["prepare"] = pl_core.prepare(s.w)
        out["batches"] = {f.name: jload(f) for f in sorted(s.w.p("batches").glob("*.json"))}
        sim = jload(FIX / "simulated_verdicts.json")
        for bf in sorted(s.w.p("batches").glob("batch-*.json")):
            batch = jload(bf)
            items = []
            for c in batch["claims"]:
                for r in c["refs"]:
                    v = sim.get(f"{c['claim_id']}|{r['ref_id']}")
                    if v:
                        items.append({"claim_id": c["claim_id"], "ref_id": r["ref_id"], **v})
            jsave(s.w.p(batch["output_file"]), items)
        out["verify"] = pl_core.verify(s.w)
        out["verdicts"] = s.w.verdicts()
        out["prepare_again"] = pl_core.prepare(s.w)
        out["status_rows"] = pl._status_data(s.w)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            pl.cmd_status(argparse.Namespace(work=str(s.w.root)))
        out["cmd_status"] = json.loads(buf.getvalue())
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            pl.cmd_browser_queue(argparse.Namespace(work=str(s.w.root), min_claims=1))
        out["browser_queue"] = json.loads(buf.getvalue())
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            pl.cmd_request_sources(
                argparse.Namespace(
                    work=str(s.w.root),
                    folder="~/Downloads/Test",
                    proxy="https://doi-org.example.idm.oclc.org",
                    min_claims=0,
                    missing_only=False,
                )
            )
        out["request_sources"] = json.loads(buf.getvalue())
        out["source_request_md"] = s.w.p("source_request.md").read_text("utf-8")

    @classmethod
    def _collect_suggestions(cls, out, s):
        # suggestions
        out["suggest_prepare"] = pl_core.suggest_prepare(s.w)
        out["suggest_batches"] = {
            f.name: jload(f) for f in sorted(s.w.p("suggest_batches").glob("*.json"))
        }
        s.w.p("suggest_parts").mkdir(exist_ok=True)
        jsave(
            s.w.p("suggest_parts", "suggest-001.json"),
            [
                {
                    "claim_id": "c3",
                    "para": "p5",
                    "original": (
                        "Sleep spindles strongly improve memory consolidation in every "
                        "age group [3]."
                    ),
                    "suggested": (
                        "Sleep spindles were associated with overnight retention in adults " "[3]."
                    ),
                    "kind": "rewrite",
                    "refs": ["3"],
                    "rationale": "The source found no association in children.",
                }
            ],
        )
        jsave(
            s.w.p("suggest_parts", "references.json"),
            [
                {
                    "para": "p12",
                    "original": "[4] Novak, P. (2018). School start times and "
                    "attendance: a district "
                    "trial. "
                    "Education Policy Quarterly.",
                    "suggested": "[4] Novak, P. (2018). School start times and "
                    "attendance: a district "
                    "trial. "
                    "Education Policy Quarterly, 12(3), 1-20.",
                    "kind": "reference",
                    "refs": ["4"],
                    "rationale": "Volume and pages confirmed by the publisher page.",
                }
            ],
        )
        out["merge_suggestions"] = pl_core.merge_suggestions(s.w)
        out["merge_suggestions_again"] = pl_core.merge_suggestions(s.w)
        out["validate_with_suggestions"] = pl_core.validate(s.w)
        out["suggest_prepare_again"] = pl_core.suggest_prepare(s.w)
        out["build_data"] = pl_core.build_data(s.w)
        out["suggestion_notes"] = pl_core.suggestion_notes(s.w)
        report = pl_core.build_report(s.w).read_text("utf-8")
        out["report_html"] = re.sub(
            r'"(generated|version)": "[^"]*"', r'"\1": "<scrubbed>"', report
        )

    @classmethod
    def _collect_carry_over(cls, out, s):
        # carry-over to a revised draft
        revised = (FIX / "paper.md").read_text("utf-8").replace("by 9% in", "by 8% in")
        revised = revised.replace("first bell.", "first bell. A new sentence cites [2].")
        rev_path = cls.tmp / "paper-v2.md"
        rev_path.write_text(revised, "utf-8")
        w2 = Work(cls.tmp / "paper-v2-check")
        w2.root.mkdir()
        jsave(w2.p("paper.json"), pl_ingest.ingest(rev_path))
        shutil.copy(FIX / "refs.json", w2.p("refs.json"))
        out["carry_over"] = pl_core.carry_over(w2, s.w)
        out["carry_over_claims"] = w2.claims()
        out["carry_over_verdicts"] = w2.verdicts()
        out["carry_over_suggestions"] = w2.suggestions()
        out["carry_over_manifest"] = w2.manifest()
        out["append_claim_parts"] = cls._append_parts(w2)

    @classmethod
    def _collect_link_cites(cls, out, s):
        # link-cites
        w3 = Work(cls.tmp / "link-check")
        w3.root.mkdir()
        jsave(w3.p("paper.json"), {"paragraphs": [{"id": "p1", "kind": "body", "text": "x"}]})
        jsave(
            w3.p("refs.json"),
            [
                {"id": "unicef2024", "raw": "UNICEF. (2024). Card deck."},
                {
                    "id": "unicef2024b",
                    "raw": "UNICEF Innocenti \u2013 Global Office. (2024). Report.",
                },
                {"id": "liu2026", "raw": "Liu, G., Christian, B. (2026). AI assistance."},
                {"id": "vaughn2020", "raw": "Vaughn, M., Jang, B. G. (2020). Review."},
                {"id": "fanon2008", "raw": "Fanon, F. (2008). Black skin, white masks. Grove."},
                {"id": "cast2018", "raw": "CAST (2018). UDL guidelines."},
                {"id": "nd", "raw": "WHO (n.d.). Sleep facts."},
            ],
        )
        jsave(
            w3.p("claims.json"),
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
                {
                    "id": "c4",
                    "para": "p1",
                    "text": "Fanon (2008) argued; see also (e.g., CAST, 2018).",
                    "cites": [],
                },
                {
                    "id": "c5",
                    "para": "p1",
                    "text": "Unknown (Nobody, 1999) and Fanon's (2008) view.",
                    "cites": [],
                },
            ],
        )
        out["link_cites_dry"] = pl_core.link_cites(w3, write=False)
        out["link_cites"] = pl_core.link_cites(w3)
        out["link_cites_claims"] = w3.claims()
        out["ref_keys"] = pl_core._ref_keys(w3.refs())
        out["aggregate"] = [
            pl_core.aggregate(x)
            for x in (
                [],
                ["supported", "contradicted"],
                ["supported", "unverifiable"],
                ["overstated", "contradicted"],
            )
        ]
        out["verify_verdict_cases"] = cls._verify_verdict_cases(s.w)

    @classmethod
    def _collect_text(cls, out, s):
        # --- text utilities ---------------------------------------------------------
        src = (
            "Results. Mean sleep fell by 18 minutes per night.\n\n"
            "In children under 12 we found no association between spindle density and retention.\f"
            "Page two has the follow-up result: recall was 12% lower.\n\n"
            "A hyphen-\nated line break appears here in the ori- ginal text of the source document."
        )
        out["locate_quote"] = [
            pl_text.locate_quote(src, q)
            for q in (
                "Mean sleep fell by 18 minutes per night.",
                "retention. Page two has the follow-up result",
                "Mean sleep fell by 20 minutes per night.",
                (
                    "In children under 12 we found an association between spindle "
                    "density and retention."
                ),
                "Participants recalled 30% fewer words in a field study.",
                "a hyphenated line break appears here",
                (
                    "In children under 12 we found no association between spindle "
                    "density and retention, which"
                ),
                (
                    "In children under 12 we found no asociation between spindel "
                    "density and retention."
                ),
                "too short",
            )
        ]
        out["page_at"] = [pl_text.page_at(src, i) for i in (3, src.index("Page two"))]
        out["find_span"] = [
            pl_text.find_span("a  b c", "a b"),
            pl_text.find_span("a  b c", "b c"),
            pl_text.find_span("a  b c", "zz"),
            pl_text.find_span("a  b c", ""),
        ]
        out["context_window"] = pl_text.context_window(
            "First sentence here. Second one says the key thing. Third sentence follows. Fourth.",
            27,
            45,
            before=40,
            after=40,
        )
        long_text = (
            "Cats are small.\n\n" * 5
            + "Spindle density showed r = 0.21 with retention in adults.\n\n"
            + "Dogs bark.\n\n" * 5
        )
        out["chunk_passages"] = pl_text.chunk_passages(long_text, window=20, stride=10)
        out["bm25"] = pl_text.bm25_rank(long_text, "spindle density retention 0.21", k=3)
        out["bm25_empty"] = pl_text.bm25_rank("", "x")
        out["title_similarity"] = [
            pl_text.title_similarity(
                "Sleep spindles and retention", "Sleep Spindles and Retention."
            ),
            pl_text.title_similarity("Sleep spindles", "School start times"),
            pl_text.title_similarity("", "x"),
        ]
        out["html_to_text"] = pl_text.html_to_text(_html_page("A title", 5))
        out["jats_to_text"] = pl_text.jats_to_text(JATS.encode())[:600]
        out["clean_extracted"] = pl_text.clean_extracted("a\u00adb \t\nc\r\nd\n\n\n\ne  ")
        out["extract_docx_text"] = pl_text.extract_file_text(s.docx_path)
        html_path = cls.tmp / "page.html"
        html_path.write_text(_html_page("Page title", 5), "utf-8")
        out["extract_html_text"] = pl_text.extract_file_text(html_path)
        xml_path = cls.tmp / "page.xml"
        xml_path.write_bytes(JATS.encode())
        out["extract_xml_text"] = pl_text.extract_file_text(xml_path)[:300]
        bad_xml = cls.tmp / "bad.xml"
        bad_xml.write_text("<html><body><p>not jats &amp; not closed</body></html>", "utf-8")
        out["extract_bad_xml_text"] = pl_text.extract_file_text(bad_xml)
        out["short_hash"] = pl_text.short_hash("abc")

    @classmethod
    def _collect_resolve_helpers(cls, out, s):
        # --- resolve helpers ---------------------------------------------------------
        out["normalize_doi"] = [
            pl_resolve.normalize_doi(x)
            for x in (
                None,
                "",
                "https://doi.org/10.1000/ABC.",
                "doi: 10.1000/x,",
                "nothing",
                "10.12/a/b)",
            )
        ]
        out["doi_slug"] = pl_resolve.doi_slug("10.1000/a.b-c/d")
        out["surname"] = [
            pl_resolve.surname(x) for x in ("Alvarez, M.", "Maria Alvarez", "", "Alvarez")
        ]
        out["abstract_from_inverted"] = pl_resolve.abstract_from_inverted({"b": [1], "a": [0, 2]})
        out["strip_tags"] = pl_resolve.strip_tags("<p>Hi <b>there</b></p>\n x")
        out["title_in_text"] = [
            pl_resolve.title_in_text(
                "Secular trends in adolescent sleep duration",
                "PNAS secular trends adolescent sleep duration abstract",
            ),
            pl_resolve.title_in_text(
                "Secular trends in adolescent sleep duration", "unrelated words only here"
            ),
            pl_resolve.title_in_text("", "anything"),
        ]
        out["eric_id"] = [
            pl_resolve.eric_id(r)
            for r in ({"url": "https://eric.ed.gov/?id=ED677055"}, {"raw": "x"})
        ]
        s.crossref_msg = {
            "title": ["Restricted sleep and word recall in teenagers"],
            "author": [{"family": "Okafor", "given": "T"}, {"given": "X"}],
            "issued": {"date-parts": [[2020, 1]]},
            "container-title": ["Learning and Sleep Research"],
            "DOI": "10.1000/OKAFOR",
            "abstract": "<jats:p>Forty-eight teenagers learned a list.</jats:p>",
            "updated-by": [{"type": "retraction"}],
        }
        s.openalex_work = {
            "title": "Restricted sleep and word recall in teenagers",
            "authorships": [{"author": {"display_name": "Tobi Okafor"}}, {"author": {}}],
            "primary_location": {
                "source": {"display_name": "LSR"},
                "pdf_url": "https://oa.example/p.pdf",
            },
            "best_oa_location": {"pdf_url": "https://oa.example/best.pdf"},
            "locations": [{"pdf_url": "https://oa.example/p.pdf"}],
            "publication_year": 2020,
            "doi": "https://doi.org/10.1000/okafor",
            "abstract_inverted_index": {"Forty-eight": [0], "teenagers": [1]},
            "is_retracted": True,
            "ids": {"pmcid": "https://www.ncbi.nlm.nih.gov/pmc/articles/PMC123"},
            "open_access": {"oa_url": "https://oa.example/page"},
            "id": "https://openalex.org/W1",
        }
        out["candidate_from_crossref"] = pl_resolve.candidate_from_crossref(s.crossref_msg)
        out["candidate_from_openalex"] = pl_resolve.candidate_from_openalex(s.openalex_work)
        s.refs = {r["id"]: r for r in jload(FIX / "refs.json")}
        corp = {
            "id": "corp",
            "raw": "NICHD Early Child Care Research Network. (2005). Pathways to reading.",
        }
        fanon = {
            "id": "fanon",
            "raw": "Fanon, F. (2008). Black skin, white masks (R. Philcox, Trans.). Grove Press.",
        }
        out["first_surname"] = [
            pl_resolve.first_surname(r) for r in (s.refs["1"], corp, fanon, {"raw": ""})
        ]
        out["author_segment"] = [
            pl_resolve._author_segment(r) for r in (s.refs["1"], corp, {"raw": "no year"})
        ]
        out["author_mismatch"] = [
            pl_resolve.author_mismatch(fanon, {"authors": ["Webber"]}),
            pl_resolve.author_mismatch(fanon, {"authors": ["Fanon"]}),
            pl_resolve.author_mismatch(corp, {"authors": ["Smith"]}),
            pl_resolve.author_mismatch(s.refs["1"], {"authors": ["Chen", "Alvarez"]}),
            pl_resolve.author_mismatch(s.refs["1"], {"authors": []}),
        ]
        cand = out["candidate_from_crossref"]
        out["score_candidate"] = [
            pl_resolve.score_candidate(s.refs["2"], cand),
            pl_resolve.score_candidate(s.refs["1"], cand),
            pl_resolve.score_candidate(dict(s.refs["2"], year=2010), cand),
            pl_resolve.score_candidate(dict(s.refs["2"], year="bad"), cand),
        ]
        out["citation_string"] = [
            pl_resolve.citation_string(s.refs["2"], cand),
            pl_resolve.citation_string(s.refs["3"], None),
            pl_resolve.citation_string(
                {"id": "r", "raw": "Smith, J., Doe, A., & Roe, B. (2001). Title. Venue."}, None
            ),
            pl_resolve.citation_string(
                {"id": "r", "raw": "Smith, J., & Doe, A. (2001). Title."}, None
            ),
            pl_resolve.citation_string(corp, None),
            pl_resolve.citation_string({"id": "r", "raw": "WHO (n.d.). Facts."}, None),
            pl_resolve.citation_string({"id": "only-id"}, None),
            pl_resolve.citation_string({"id": "r", "title": "T &amp; U", "year": 1999}, None),
        ]
        out["display_fields"] = [
            pl_resolve.display_fields(s.refs["2"], "verified", cand, None),
            pl_resolve.display_fields(dict(s.refs["2"], doi="10.1/x"), "partial_match", cand, None),
            pl_resolve.display_fields(dict(s.refs["2"], url="https://u"), "not_found", None, None),
        ]
        out["looks_english"] = [
            pl_resolve.looks_english("short"),
            pl_resolve.looks_english("the of and " * 10),
            pl_resolve.looks_english("der die das und " * 10),
        ]
        out["abstract_matches"] = [
            pl_resolve.abstract_matches("Restricted sleep", "the of and restricted " * 6),
            pl_resolve.abstract_matches("Restricted sleep", "the of and nothing " * 6),
            pl_resolve.abstract_matches("", "the of and nothing " * 6),
            pl_resolve.abstract_matches("x", "der die das und " * 10),
        ]
        errs = []
        out["accept_html"] = [
            pl_resolve._accept_html(
                pl_text.html_to_text(_html_page("Attendance trial")), "Attendance trial", errs, "t"
            ),
            pl_resolve._accept_html("short page", "T", errs, "t"),
            pl_resolve._accept_html("Buy article PDF " + "x " * 8000, "T", errs, "t"),
            pl_resolve._accept_html("y " * 8000, "Completely different title words", errs, "t"),
        ]
        out["accept_html_errors"] = errs
        out["find_user_file"] = [
            str(pl_resolve.find_user_file(s.w.root, s.refs["1"], None)),
            str(pl_resolve.find_user_file(s.w.root, s.refs["4"], "10.1/x")),
            str(pl_resolve.find_user_file(cls.tmp / "nowhere", s.refs["1"], None)),
        ]

    @classmethod
    def _collect_downloads(cls, out, s):
        # network-dependent paths with canned responses
        up_json = json.dumps(
            {
                "best_oa_location": {"url_for_pdf": "https://pdf.example/best.pdf"},
                "oa_locations": [{"url": "https://page.example/oa", "url_for_pdf": None}],
            }
        ).encode()
        ss_json = json.dumps({"openAccessPdf": {"url": "https://ss.example/p.pdf"}}).encode()
        eric_json = json.dumps(
            {
                "response": {
                    "docs": [
                        {
                            "id": "ED1",
                            "title": "School start times and attendance: a district trial",
                            "e_fulltextauth": True,
                        }
                    ]
                }
            }
        ).encode()
        epmc_json = json.dumps(
            {
                "resultList": {
                    "result": [
                        {
                            "isOpenAccess": "Y",
                            "pmcid": "PMC9",
                            "abstractText": "<p>Later start times raised attendance.</p>",
                        }
                    ]
                }
            }
        ).encode()
        page_html = _html_page("School start times and attendance: a district trial").encode()
        routes_a = [
            ("api.unpaywall.org", (up_json, "application/json")),
            ("api.semanticscholar.org", (ss_json, "application/json")),
            ("api.ies.ed.gov", (eric_json, "application/json")),
            ("europepmc/webservices/rest/search", (epmc_json, "application/json")),
            ("europepmc/webservices/rest/PMC9/fullTextXML", (JATS.encode(), "application/xml")),
        ]
        ref4 = dict(s.refs["4"], doi="10.1000/novak", url="https://pub.example/novak")
        cand4 = {
            "doi": "10.1000/novak",
            "pdf_urls": ["https://oa.example/novak.pdf"],
            "oa_url": "https://oa.example/novak",
        }
        dl = cls.tmp / "dl-a"
        dl.mkdir()
        net = FakeNetwork(routes_a)
        pl_http.http_get = net
        errs = []
        text, how, orig = pl_resolve.try_download_fulltext(ref4, cand4, dl, "a@b.org", errs)
        out["download_a"] = {
            "text": (text or "")[:200],
            "how": how,
            "file": str(orig),
            "errors": errs,
            "calls": net.calls,
        }
        routes_b = [
            ("api.unpaywall.org", (up_json, "application/json")),
            ("pub.example/novak", (page_html, "text/html")),
            ("europepmc", pl_http.HttpError("HTTP 503 for epmc")),
        ]
        dl = cls.tmp / "dl-b"
        dl.mkdir()
        net = FakeNetwork(routes_b)
        pl_http.http_get = net
        errs = []
        text, how, orig = pl_resolve.try_download_fulltext(ref4, cand4, dl, "a@b.org", errs)
        out["download_b"] = {
            "text": (text or "")[:200],
            "how": how,
            "file": str(orig),
            "errors": errs,
            "calls": net.calls,
        }
        net = FakeNetwork([])
        pl_http.http_get = net
        errs = []
        out["download_untrusted"] = [
            pl_resolve.try_download_fulltext(ref4, cand4, dl, None, errs, trusted=False),
            errs,
            net.calls,
        ]
        out["eric_fulltext_urls"] = pl_resolve.eric_fulltext_urls(
            {"url": "https://eric.ed.gov/?id=ED1", "title": "x"}, None, []
        )
        out["semantic_scholar_pdf"] = pl_resolve.semantic_scholar_pdf(None, None, [])
        out["europe_pmc_lookup"] = pl_resolve.europe_pmc_lookup("10.1/x", None, None, [])

    @classmethod
    def _collect_metadata(cls, out, s):
        # metadata resolution with canned Crossref and OpenAlex
        cr_doi = json.dumps({"message": s.crossref_msg}).encode()
        cr_search = json.dumps(
            {"message": {"items": [s.crossref_msg, dict(s.crossref_msg, title=["Other"])]}}
        ).encode()
        oa_doi = json.dumps(s.openalex_work).encode()
        oa_search = json.dumps({"results": [s.openalex_work]}).encode()
        net = FakeNetwork(
            [
                ("api.crossref.org/works/10.", (cr_doi, "application/json")),
                ("api.crossref.org/works?", (cr_search, "application/json")),
                ("api.openalex.org/works/doi:", (oa_doi, "application/json")),
                ("api.openalex.org/works?", (oa_search, "application/json")),
            ]
        )
        pl_http.http_get = net
        errs = []
        out["resolve_metadata_doi"] = pl_resolve.resolve_metadata(
            dict(s.refs["2"], doi="10.1000/okafor"), "a@b.org", errs
        ) + (errs,)
        errs = []
        out["resolve_metadata_title"] = pl_resolve.resolve_metadata(s.refs["2"], None, errs) + (
            errs,
        )
        errs = []
        out["resolve_metadata_weak"] = pl_resolve.resolve_metadata(
            {"id": "x", "raw": "Zzz qqq. (1999). Nothing like it."}, None, errs
        ) + (errs,)
        out["resolve_metadata_none"] = pl_resolve.resolve_metadata({"id": "x"}, None, [])
        out["resolve_metadata_calls"] = net.calls
        wr = Work(cls.tmp / "resolve-check")
        wr.root.mkdir()
        out["resolve_one_doi"] = pl_resolve.resolve_one(
            wr.root,
            dict(s.refs["2"], doi="10.1000/okafor"),
            None,
            "a@b.org",
            refresh=False,
            download=True,
        )
        prior = dict(out["resolve_one_doi"])
        out["resolve_one_reuse"] = pl_resolve.resolve_one(
            wr.root,
            dict(s.refs["2"], doi="10.1000/okafor"),
            prior,
            "a@b.org",
            refresh=False,
            download=False,
        )
        out["resolve_one_title_offline_dl"] = pl_resolve.resolve_one(
            wr.root, s.refs["2"], None, None, refresh=False, download=True
        )
        wrong_prior = dict(
            prior, matched=dict(prior["matched"], authors=["Someone"]), doi="10.9/other"
        )
        out["resolve_one_reuse_mismatch"] = pl_resolve.resolve_one(
            wr.root,
            dict(s.refs["2"], doi="10.1000/okafor"),
            wrong_prior,
            None,
            refresh=False,
            download=False,
        )
        pl_http.http_get = _offline
        out["resolve_one_offline"] = pl_resolve.resolve_one(
            wr.root, s.refs["3"], None, None, refresh=False, download=True
        )
        out["resolve_one_broken_ref"] = pl_resolve.resolve_all(wr.root, [{"id": "bad"}], {}, None)

    @classmethod
    def _collect_docx(cls, out, s):
        # --- docx export ----------------------------------------------------------------
        src_docx, out_docx = cls.tmp / "in.docx", cls.tmp / "out.docx"
        with zipfile.ZipFile(src_docx, "w") as z:
            z.writestr("word/document.xml", DOCX_XML)
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
        paper = out["ingest_docx"]
        sugs = [
            {
                "id": "s1",
                "para": "p2",
                "original": "Short sleep is always linked to poor recall (Lee, 2020).",
                "suggested": (
                    "Short sleep was linked to poorer recall in one lab study (Lee, " "2020)."
                ),
            },
            {"id": "s2", "para": "p4", "original": "Orphan fragment.", "suggested": ""},
            {
                "id": "s3",
                "para": "p3",
                "original": "paragraph with tab.",
                "suggested": "paragraph with a tab added.",
            },
            {"id": "s4", "para": "p3", "original": "missing text", "suggested": "x"},
            {"id": "s5", "para": "p9", "original": "x", "suggested": "y"},
            {"id": "s6", "para": "p2", "original": "Short sleep", "suggested": "Overlapping"},
        ]
        notes = {
            "s1": [
                "Athetize s1",
                [{"t": "Why: ", "b": True, "color": "1F4E79"}, {"t": "one lab study.\x0c"}],
                [],
            ]
        }
        out["docx_apply"] = pl_docx.apply(
            src_docx, out_docx, paper, sugs, author="Test", notes=notes
        )
        with zipfile.ZipFile(out_docx) as z:
            out["docx_files"] = sorted(z.namelist())
            out["docx_document"] = re.sub(
                r'w:date="[^"]*"', 'w:date="<d>"', z.read("word/document.xml").decode()
            )
            out["docx_comments"] = re.sub(
                r'w:date="[^"]*"', 'w:date="<d>"', z.read("word/comments.xml").decode()
            )
        out["word_edits"] = [
            pl_docx.word_edits(
                "systems hear, score, and represent children",
                "systems transcribe speech of children",
            ),
            pl_docx.word_edits("same", "same"),
            pl_docx.word_edits("", "added"),
            pl_docx.word_edits("a b c d", "a c d e"),
        ]
        out["word_diff"] = pl_core.word_diff(
            "systems hear, score, and represent children", "systems transcribe speech of children"
        )

    @classmethod
    def _collect_cli(cls, out, s):
        # --- pl.py helpers ------------------------------------------------------------
        targets = {
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
            "hall2010b": {
                "id": "hall2010b",
                "title": "The negative consequences of becoming a good reader",
                "authors": ["Hall, L."],
            },
        }
        steele_text = (
            "American Psychologist 1997\n"
            "A Threat in the Air: How Stereotypes Shape Intellectual Identity "
            "and Performance\nClaude M. Steele\n"
        ) + "x " * 400
        out["match_file_to_ref"] = [
            pl._match_file_to_ref(Path("hall2010.pdf"), "", targets),
            pl._match_file_to_ref(Path("x__hall2010.pdf"), "", targets),
            pl._match_file_to_ref(Path("download (3).pdf"), steele_text, targets),
            pl._match_file_to_ref(
                Path("download (3).pdf"), steele_text.replace("Steele", "Someone"), targets
            ),
            pl._match_file_to_ref(Path("notes.txt"), "grocery list: milk, eggs", targets),
            pl._match_file_to_ref(
                Path("negative consequences of becoming a good reader.pdf"), "Hall " * 10, targets
            ),
        ]
        out["ref_title"] = [
            pl._ref_title(r)
            for r in (
                {"title": "Black skin, white masks (R"},
                targets["steele1997"],
                {"raw": "no match"},
            )
        ]
        out["ref_year"] = [
            pl._ref_year(r) for r in ({"year": 2001}, targets["steele1997"], {"raw": "none"})
        ]
        folder = cls.tmp / "downloads"
        folder.mkdir()
        (folder / "Novak 2018 school start times.txt").write_text(
            "School start times and attendance: a district trial\nP. Novak\n"
            + "attendance rose. " * 100,
            "utf-8",
        )
        (folder / "random.txt").write_text("grocery list", "utf-8")
        (folder / ".hidden.txt").write_text("x", "utf-8")
        shutil.copy(FIX / "sources" / "1.txt", folder / "1.txt")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            pl.cmd_import_sources(
                argparse.Namespace(
                    folders=[str(folder)],
                    work=str(s.w.root),
                    dry_run=True,
                    recursive=False,
                    all=False,
                    since=None,
                )
            )
        out["import_sources_dry"] = json.loads(buf.getvalue())
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            pl.cmd_import_sources(
                argparse.Namespace(
                    folders=[str(folder)],
                    work=str(s.w.root),
                    dry_run=False,
                    recursive=False,
                    all=False,
                    since=None,
                )
            )
        out["import_sources"] = json.loads(buf.getvalue())
        out["manifest_after_import"] = s.w.manifest()
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            pl.cmd_add_source(
                argparse.Namespace(
                    work=str(s.w.root),
                    ref="4",
                    source=str(folder / "Novak 2018 school start times.txt"),
                )
            )
        out["add_source_stdout"] = buf.getvalue()
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            pl.cmd_passages(
                argparse.Namespace(work=str(s.w.root), ref="4", query="attendance rose", k=2)
            )
            pl.cmd_passages(argparse.Namespace(work=str(s.w.root), ref="zzz", query="x", k=2))
        out["passages_stdout"] = buf.getvalue()
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            pl.cmd_config(argparse.Namespace(action="set-email", value="me@example.org"))
            pl.cmd_config(argparse.Namespace(action="set-folder", value="~/Downloads/X"))
            pl.cmd_config(argparse.Namespace(action="set-proxy", value=None))
            pl.cmd_config(argparse.Namespace(action="show", value=None))
        out["config_stdout"] = buf.getvalue()
        out["config_file"] = jload(pl_core.config_path())
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            pl.cmd_doctor(argparse.Namespace())
        doctor = json.loads(buf.getvalue())
        for k in ("python", "pdf_extractor", "headless_browser"):
            doctor.pop(k, None)
        doctor["notes"] = [n for n in doctor["notes"] if "PDF" not in n and "playwright" not in n]
        out["doctor"] = doctor
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            pl.cmd_paragraphs(
                argparse.Namespace(work=str(s.w.root), from_="p5", to="p7", kind="body")
            )
            pl.cmd_coverage(argparse.Namespace(work=str(s.w.root), para="p5"))
            pl.cmd_merge_suggestions(argparse.Namespace(work=str(s.w.root)))
            pl.cmd_build(argparse.Namespace(work=str(s.w.root), out=None))
        out["misc_stdout"] = buf.getvalue()
        out["search_urls"] = pl_core._search_urls(
            s.refs["1"], {"citation": "Alvarez et al. (2019)"}
        )
        out["clean_raw"] = pl_core._clean_raw(
            "Smith (2020). _Title_ [detail unverified: x]. Venue [Open access]."
        )
        out["safe_json"] = pl_core.safe_json_for_html({"x": "</script><b>&\u2028"})
        out["merge_claim_parts"] = cls._merge_parts(s.w)

    @classmethod
    def _append_parts(cls, w2):
        parts = w2.p("claims_parts")
        parts.mkdir(exist_ok=True)
        jsave(
            parts / "add-01.json",
            [
                {
                    "para": "p7",
                    "text": "A new sentence cites [2].",
                    "type": "empirical",
                    "cites": ["2"],
                },
                {"para": "p7", "text": "Taken together, these results", "cites": []},
                {"para": "p7", "text": "not present", "cites": []},
            ],
        )
        res = pl_core.append_claim_parts(w2)
        return [res, w2.claims(), pl_core.coverage(w2)]

    @classmethod
    def _merge_parts(cls, w):
        parts = w.p("claims_parts")
        for f in parts.glob("*.json"):
            f.unlink()
        jsave(
            parts / "part-02.json",
            [
                {
                    "para": "p7",
                    "text": "We therefore recommend delaying the first bell.",
                    "cites": [],
                }
            ],
        )
        jsave(
            parts / "part-01.json",
            [
                {
                    "id": "zz",
                    "para": "p5",
                    "text": "About 70% of high school students report insufficient sleep.",
                    "cites": [],
                    "type": "numeric",
                },
                {"para": "p99", "text": "x"},
            ],
        )
        n = pl_core.merge_claim_parts(w)
        return [n, w.claims()]

    @classmethod
    def _verify_verdict_cases(cls, w):
        entry = w.manifest()["refs"]["3"]
        claim = {"claim_id": "cx", "claim_hash": "h"}
        ref = {
            "ref_id": "3",
            "evidence_level": "full_text",
            "evidence_hash": entry["evidence_hash"],
        }
        cases = [
            {"verdict": "unsupported", "confidence": 0.9, "analysis": "Nothing found."},
            {
                "verdict": "SUPPORTED",
                "confidence": "high",
                "evidence_quote": "no association between spindle density and overnight retention",
                "evidence_quotes": ["Spindle density was measured", 7],
                "analysis": "a",
            },
            {
                "verdict": "made_up",
                "confidence": 2,
                "evidence_quote": "not in the source at all, definitely",
                "analysis": "",
            },
            {
                "verdict": "contradicted",
                "confidence": -1,
                "evidence_quote": "",
                "evidence_quotes": [
                    "no association between spindle density and overnight retention"
                ],
            },
        ]
        out = [pl_core.verify_verdict(w, v, claim, ref, entry) for v in cases]
        out.append(
            pl_core.verify_verdict(w, cases[0], claim, dict(ref, evidence_level="abstract"), entry)
        )
        out.append(
            pl_core.verify_verdict(w, cases[1], claim, dict(ref, evidence_level="none"), entry)
        )
        return out

    def test_matches_golden(self):
        if os.environ.get("ATHETIZE_UPDATE_GOLDEN"):
            GOLDEN.write_text(
                json.dumps(self.actual, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
                "utf-8",
            )
        self.assertTrue(
            GOLDEN.exists(), f"{GOLDEN} is missing; run with ATHETIZE_UPDATE_GOLDEN=1 to create it"
        )
        golden = json.loads(GOLDEN.read_text("utf-8"))
        actual = json.loads(json.dumps(self.actual, ensure_ascii=False, sort_keys=True))
        self.assertEqual(sorted(actual), sorted(golden), "set of recorded sections differs")
        for key in sorted(golden):
            with self.subTest(section=key):
                self.assertEqual(actual[key], golden[key])


if __name__ == "__main__":
    unittest.main()
