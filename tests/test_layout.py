"""The report shows a Word manuscript the way it was written.

Headings set in bold with no style, type styles inside a paragraph, tables with merged
cells and the paper's own pictures are read from the Word file, kept by ingest and passed
to the report page.
"""

import base64
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

import pl_core
import pl_docx_read
import pl_ingest
import pl_report
from pl_core import Work, jsave

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
NAMESPACES = (
    f'xmlns:w="{W_NS}" '
    'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
    'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"'
)
IMAGE_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/image"
# the smallest PNG file there is: one transparent pixel
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhf"
    "DwAChwGA60e6kgAAAABJRU5ErkJggg=="
)
STYLES = (
    f'<w:styles xmlns:w="{W_NS}">'
    '<w:style w:type="table" w:styleId="TableGrid"><w:tblPr><w:tblBorders>'
    '<w:top w:val="single"/><w:insideH w:val="single"/></w:tblBorders></w:tblPr></w:style>'
    '<w:style w:type="table" w:styleId="TableNormal"><w:tblPr/></w:style>'
    "</w:styles>"
)


def run(text, props=""):
    return f'<w:r><w:rPr>{props}</w:rPr><w:t xml:space="preserve">{text}</w:t></w:r>'


def para(*runs, props=""):
    return f"<w:p><w:pPr>{props}</w:pPr>{''.join(runs)}</w:p>"


def bold(text, size=26):
    return run(text, f'<w:b/><w:sz w:val="{size}"/>')


def picture(rel, width=2743200, height=1905000):
    """An empty paragraph that holds one drawing, as Word writes a figure."""
    return (
        "<w:p><w:r><w:drawing><wp:inline>"
        f'<wp:extent cx="{width}" cy="{height}"/>'
        f'<a:graphic><a:graphicData><a:blip r:embed="{rel}"/></a:graphicData></a:graphic>'
        "</wp:inline></w:drawing></w:r></w:p>"
    )


def cell(content, props=""):
    return f"<w:tc><w:tcPr>{props}</w:tcPr>{content}</w:tc>"


def table(rows, props='<w:tblStyle w:val="TableGrid"/>', grid=()):
    cols = "".join(f'<w:gridCol w:w="{w}"/>' for w in grid)
    body = "".join(f"<w:tr>{''.join(r)}</w:tr>" for r in rows)
    return f"<w:tbl><w:tblPr>{props}</w:tblPr><w:tblGrid>{cols}</w:tblGrid>{body}</w:tbl>"


NO_BORDERS = (
    '<w:tblStyle w:val="TableGrid"/><w:tblBorders>'
    '<w:top w:val="none"/><w:bottom w:val="nil"/></w:tblBorders>'
)
TIMELINE = table(
    [
        [
            cell(para(bold("TASKS")), '<w:vMerge w:val="restart"/>'),
            cell(para(bold("YEAR 1")), '<w:gridSpan w:val="2"/>'),
        ],
        [cell(para(), "<w:vMerge/>"), cell(para(bold("Q1"))), cell(para(bold("Q2")))],
        [cell(para(run("Reduce the images"))), cell(para(run("*"))), cell(para())],
    ],
    grid=(4000, 400, 400),
)
BODY = (
    para(bold("Science/Technical/Management Plan", 28))
    + para(bold("1. Introduction"))
    + para(
        run("Escape of H"),
        run("2", '<w:vertAlign w:val="subscript"/>'),
        run(" from "),
        run("Titan", "<w:i/>"),
        run(" is fast."),
    )
    + para(bold("3.1 Datasets to be used"))
    + table(
        [[cell(picture("rId7") + para(bold("Figure 1:"), run(" Densities.", "<w:i/>")))]],
        props=NO_BORDERS,
        grid=(4538,),
    )
    + para(bold("A whole sentence that the author chose to set in bold type."))
    + para(run("Centred line of text"), props='<w:jc w:val="center"/>')
    + TIMELINE
    + picture("rId8")
    + picture("rId9")
)
RELS = (
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    f'<Relationship Id="rId7" Type="{IMAGE_REL}" Target="media/image1.png"/>'
    f'<Relationship Id="rId8" Type="{IMAGE_REL}" Target="media/image2.emf"/>'
    f'<Relationship Id="rId9" Type="{IMAGE_REL}" Target="media/image3.png"/>'
    "</Relationships>"
)


def write_docx(path, body=BODY, big=PNG):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(
            "word/document.xml", f"<w:document {NAMESPACES}><w:body>{body}</w:body></w:document>"
        )
        z.writestr("word/_rels/document.xml.rels", RELS)
        z.writestr("word/styles.xml", STYLES)
        z.writestr("word/media/image1.png", PNG)
        z.writestr("word/media/image2.emf", b"not a picture a browser can show")
        z.writestr("word/media/image3.png", big)


class LayoutCase(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.docx = self.dir / "Titan_plan.docx"
        write_docx(self.docx)


class ReadLayoutTests(LayoutCase):
    def setUp(self):
        super().setUp()
        self.paras, self.tables = pl_docx_read.read_docx(self.docx)
        self.by_text = {p["text"]: p for p in self.paras}

    def test_bold_lines_and_type_size_are_reported(self):
        title = self.by_text["Science/Technical/Management Plan"]
        self.assertTrue(title["bold_all"])
        self.assertEqual(title["size"], 28)
        self.assertNotIn("bold_all", self.by_text["Escape of H2 from Titan is fast."])

    def test_type_styles_are_counted_on_the_text(self):
        p = self.by_text["Escape of H2 from Titan is fast."]
        self.assertEqual(p["fmt"], [[11, 12, "sub"], [18, 23, "i"]])
        self.assertEqual(p["fmt_text"], p["text"])
        caption = self.by_text["Figure 1: Densities."]
        self.assertEqual(caption["fmt"], [[0, 9, "b"], [9, 20, "i"]])

    def test_centred_paragraph(self):
        self.assertEqual(self.by_text["Centred line of text"]["align"], "center")
        self.assertNotIn("align", self.by_text["1. Introduction"])

    def test_picture_travels_with_the_paragraph_after_it(self):
        caption = self.by_text["Figure 1: Densities."]
        self.assertEqual(
            caption["figures"], [{"file": "word/media/image1.png", "w": 288, "h": 200}]
        )
        self.assertEqual(caption["tbl"], [0, 0, 0])

    def test_pictures_after_the_last_paragraph_are_kept(self):
        after = self.paras[-1]["figures_after"]
        self.assertEqual(
            [f["file"] for f in after], ["word/media/image2.emf", "word/media/image3.png"]
        )

    def test_table_cells_and_their_merges(self):
        self.assertEqual(self.by_text["YEAR 1"]["tbl"], [1, 0, 1])
        self.assertEqual(self.by_text["Q2"]["tbl"], [1, 1, 2])
        figure_box, timeline = self.tables
        self.assertEqual(figure_box, {"rows": [[[1, 1]]], "borders": False, "cols": [4538]})
        self.assertEqual(
            timeline["rows"],
            [[[1, 2], [2, 1]], [[1, 0], [1, 1], [1, 1]], [[1, 1], [1, 1], [1, 1]]],
        )
        self.assertTrue(timeline["borders"])
        self.assertEqual(timeline["cols"], [4000, 400, 400])

    def test_table_without_a_ruled_style_has_no_borders(self):
        plain = self.dir / "plain.docx"
        write_docx(plain, table([[cell(para(run("a"))), cell(para(run("b")))]], props=""))
        self.assertFalse(pl_docx_read.read_docx(plain)[1][0]["borders"])

    def test_read_docx_paragraphs_gives_the_same_paragraphs(self):
        self.assertEqual(pl_docx_read.read_docx_paragraphs(self.docx), self.paras)


class IngestLayoutTests(LayoutCase):
    def setUp(self):
        super().setUp()
        self.paper = pl_ingest.ingest(self.docx)
        self.by_text = {p["text"]: p for p in self.paper["paragraphs"]}

    def test_bold_line_is_a_heading_but_not_the_title(self):
        first = self.by_text["Science/Technical/Management Plan"]
        self.assertEqual((first["kind"], first["level"]), ("heading", 1))
        self.assertEqual(self.paper["title"], "Titan_plan")

    def test_heading_depth_follows_the_numbering(self):
        self.assertEqual(self.by_text["1. Introduction"]["level"], 1)
        self.assertEqual(self.by_text["3.1 Datasets to be used"]["level"], 2)

    def test_heading_depth_follows_the_style_when_it_has_one(self):
        self.assertEqual(pl_ingest._heading_level("heading 3", "2. Methods"), 3)
        self.assertEqual(pl_ingest._heading_level("title", "Escape from Titan"), 1)

    def test_bold_sentence_and_bold_table_cell_are_not_headings(self):
        sentence = self.by_text["A whole sentence that the author chose to set in bold type."]
        self.assertEqual(sentence["kind"], "body")
        self.assertFalse(pl_ingest._is_bold_heading({"bold_all": True, "tbl": [0, 0, 0]}, "Tasks"))
        self.assertFalse(pl_ingest._is_bold_heading({"bold_all": True}, "* * *"))
        self.assertTrue(pl_ingest._is_bold_heading({"bold_all": True}, "Management Plan"))

    def test_layout_is_kept_with_the_paragraph(self):
        body = self.by_text["Escape of H2 from Titan is fast."]
        self.assertEqual(body["fmt"], [[11, 12, "sub"], [18, 23, "i"]])
        self.assertEqual(self.by_text["Centred line of text"]["align"], "center")
        self.assertEqual(self.by_text["Figure 1: Densities."]["tbl"], [0, 0, 0])
        self.assertEqual(len(self.paper["tables"]), 2)
        self.assertNotIn("bold_all", body)
        self.assertNotIn("fmt_text", body)

    def test_type_styles_are_dropped_when_they_do_not_fit_the_stored_text(self):
        read = {"fmt": [[0, 2, "b"]], "fmt_text": "ab c", "align": "center"}
        self.assertEqual(pl_ingest._layout(read, "ab  c"), {"align": "center"})
        self.assertEqual(pl_ingest._layout(read, "ab c")["fmt"], [[0, 2, "b"]])

    def test_other_file_types_have_no_tables(self):
        note = self.dir / "note.txt"
        note.write_text("A plain paragraph of text.\n\nAnother one.", "utf-8")
        self.assertNotIn("tables", pl_ingest.ingest(note))


class ReportLayoutTests(LayoutCase):
    def _work(self):
        w = Work(self.dir / "Titan_plan-check")
        w.root.mkdir()
        jsave(w.p("paper.json"), pl_ingest.ingest(self.docx))
        shutil.copy(self.docx, w.p("manuscript.docx"))
        return w

    def test_report_data_carries_the_layout(self):
        data = pl_core.build_data(self._work())
        by_text = {p["text"]: p for p in data["paragraphs"]}
        self.assertEqual(by_text["3.1 Datasets to be used"]["level"], 2)
        self.assertEqual(by_text["Figure 1: Densities."]["figures"][0]["w"], 288)
        self.assertEqual(by_text["Q1"]["tbl"], [1, 1, 1])
        self.assertTrue(data["tables"][1]["borders"])

    def test_pictures_are_put_in_the_report_and_unshowable_ones_left_out(self):
        figures = pl_core.build_data(self._work())["figures"]
        self.assertEqual(sorted(figures), ["word/media/image1.png", "word/media/image3.png"])
        kind, _, data = figures["word/media/image1.png"].partition(",")
        self.assertEqual(kind, "data:image/png;base64")
        self.assertEqual(base64.b64decode(data), PNG)

    def test_pictures_past_the_size_limit_are_left_out(self):
        work = self._work()
        with mock.patch.object(pl_report, "MAX_PICTURE_BYTES", len(PNG) + 5):
            figures = pl_core.build_data(work)["figures"]
        self.assertEqual(list(figures), ["word/media/image1.png"])

    def test_paper_without_pictures_needs_no_manuscript_copy(self):
        work = Work(self.dir / "text-check")
        work.root.mkdir()
        note = self.dir / "note.txt"
        note.write_text("A plain paragraph of text.", "utf-8")
        jsave(work.p("paper.json"), pl_ingest.ingest(note))
        data = pl_core.build_data(work)
        self.assertEqual((data["figures"], data["tables"]), ({}, []))

    def test_report_page_has_the_layout_data(self):
        html = pl_core.build_report(self._work()).read_text("utf-8")
        self.assertIn('"tables": [{"rows"', html)
        self.assertIn("data:image/png;base64,", html)


if __name__ == "__main__":
    unittest.main()
