import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pymupdf

from literature_review.extraction import PdfExtractionError, extract_pdf_text


class FakePage:
    def __init__(self, text: str | None) -> None:
        self.text = text

    def extract_text(self) -> str | None:
        return self.text


class FakeReader:
    is_encrypted = False

    def __init__(self, _path: Path) -> None:
        self.pages = [
            FakePage("This first extracted page contains enough text for a test."),
            FakePage(""),
            FakePage("This third extracted page also contains enough evidence text."),
        ]


class ExtractionTests(unittest.TestCase):
    @patch("literature_review.extraction.PdfReader", FakeReader)
    @patch.object(Path, "is_file", return_value=True)
    def test_extracts_usable_text_and_preserves_original_page_numbers(self, _is_file: object) -> None:
        document = extract_pdf_text("data/papers/example.pdf", "paper-1")

        self.assertEqual([page.page_number for page in document.pages], [1, 3])
        self.assertEqual(document.extraction_method, "pypdf")

    def test_missing_pdf_is_reported(self) -> None:
        with self.assertRaises(PdfExtractionError):
            extract_pdf_text("missing-file.pdf", "paper-1")


class MarkdownTableFixtureTests(unittest.TestCase):
    """Synthetic table PDF sanity check.

    The PDF is authored and parsed with the same pymupdf stack, so this only
    proves reproducibility; the authoritative table evidence is the real-sample
    compare in ``.omo/evidence/c2a-table-compare.md``.
    """

    def test_table_rows_survive_markdown_extraction(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "table.pdf"
            doc = pymupdf.open()
            page = doc.new_page()
            page.insert_text(
                (72, 72),
                "| System | Precision | Recall |\n"
                "| Alpha  | 0.91      | 0.87   |\n"
                "| Beta   | 0.88      | 0.90   |",
            )
            doc.save(path)
            doc.close()

            document = extract_pdf_text(path, "table-paper")

            self.assertEqual(document.extraction_method, "pymupdf4llm")
            joined = "\n".join(page.text for page in document.pages)
            self.assertIn("| System | Precision | Recall |", joined)
            self.assertIn("| Alpha  | 0.91      | 0.87   |", joined)
            self.assertIn("| Beta   | 0.88      | 0.90   |", joined)


if __name__ == "__main__":
    unittest.main()
