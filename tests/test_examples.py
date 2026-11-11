"""The sample paper under examples/ builds into a Word file that Athetize reads as a paper."""

import importlib.util
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path

import pl_ingest

ROOT = Path(__file__).resolve().parent.parent
BANANAS = ROOT / "examples" / "bananas"


def _load_builder():
    spec = importlib.util.spec_from_file_location("make_docx", BANANAS / "make_docx.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


make_docx = _load_builder()


class SamplePaperTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls.addClassCleanup(shutil.rmtree, cls.tmp, ignore_errors=True)
        cls.docx = make_docx.build(out=cls.tmp / "banana_sample.docx")
        cls.paper = pl_ingest.ingest(cls.docx)

    def test_it_reads_as_a_paper_with_a_title_sections_and_references(self):
        self.assertEqual(
            self.paper["title"], "Bananas Under Pressure: Disease, Ripening and Biofortification"
        )
        headings = [p["text"] for p in self.paper["paragraphs"] if p["kind"] == "heading"]
        self.assertIn("2. Fusarium wilt", headings)
        self.assertIn("References", headings)
        references = [p for p in self.paper["paragraphs"] if p["kind"] == "reference"]
        self.assertEqual(len(references), 9)

    def test_each_figure_stands_before_its_caption(self):
        captions = [p for p in self.paper["paragraphs"] if p["text"].startswith("Figure ")]
        self.assertEqual(len(captions), 2)
        for caption in captions:
            self.assertEqual(len(caption["figures"]), 1)
            self.assertEqual(caption["align"], "center")

    def test_species_names_are_in_italics(self):
        body = next(p for p in self.paper["paragraphs"] if "Musa acuminata" in p["text"])
        at = body["text"].index("Musa acuminata")
        self.assertIn([at, at + len("Musa acuminata"), "i"], body["fmt"])

    def test_every_page_carries_the_sample_watermark(self):
        with zipfile.ZipFile(self.docx) as z:
            header = z.read("word/header1.xml").decode("utf-8")
            document = z.read("word/document.xml").decode("utf-8")
        self.assertIn('string="SAMPLE"', header)
        self.assertIn('<w:headerReference w:type="default" r:id="rIdHeader"/>', document)

    def test_the_text_says_it_is_a_sample(self):
        text = " ".join(p["text"] for p in self.paper["paragraphs"])
        self.assertIn("made-up sample paper", text)


class BuilderPiecesTests(unittest.TestCase):
    def test_blocks_are_told_apart(self):
        parts = make_docx.blocks(
            "# T\n\nAuthors: A\n\n## H\n\nSome\ntext.\n\n![Figure 1. C [1].](f.png)"
        )
        self.assertEqual(
            parts,
            [
                ("title", "T", ""),
                ("byline", "A", ""),
                ("heading", "H", ""),
                ("text", "Some text.", ""),
                ("figure", "Figure 1. C [1].", "f.png"),
            ],
        )

    def test_text_is_escaped_and_italics_marked(self):
        xml = make_docx.runs("a < b in *Musa* & co")
        self.assertIn("a &lt; b in ", xml)
        self.assertIn('<w:i/></w:rPr><w:t xml:space="preserve">Musa</w:t>', xml)
        self.assertIn(" &amp; co", xml)

    def test_png_size_is_read_from_the_file(self):
        self.assertEqual(make_docx.png_size((BANANAS / "fig1_trade.png").read_bytes()), (1040, 380))
        with self.assertRaises(ValueError):
            make_docx.png_size(b"not a picture at all, just some bytes")


if __name__ == "__main__":
    unittest.main()
