import unittest
from pathlib import Path
from unittest.mock import patch

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


if __name__ == "__main__":
    unittest.main()
