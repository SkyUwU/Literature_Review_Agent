"""Extract page-numbered text from local PDF files without using an LLM."""

from pathlib import Path

from pypdf import PdfReader

from literature_review.models import FullTextDocument, PageText


class PdfExtractionError(RuntimeError):
    """Raised when a local PDF cannot provide usable text evidence."""


def extract_pdf_text(path: str | Path, paper_id: str) -> FullTextDocument:
    """Extract usable text from each PDF page and preserve original page numbers."""
    source_path = Path(path)
    if not source_path.is_file():
        raise PdfExtractionError(f"PDF file not found: {source_path}")

    reader = PdfReader(source_path)
    if reader.is_encrypted and not reader.decrypt(""):
        raise PdfExtractionError("PDF is encrypted and cannot be read without a password.")

    pages = [
        PageText(page_number=index, text=text)
        for index, page in enumerate(reader.pages, start=1)
        if (text := (page.extract_text() or "").strip()) and len(text) >= 20
    ]
    if not pages:
        raise PdfExtractionError("No usable text was extracted from this PDF.")
    return FullTextDocument(
        paper_id=paper_id,
        source_path=str(source_path),
        pages=pages,
        extraction_method="pypdf",
    )
