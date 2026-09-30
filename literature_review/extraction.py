"""Extract page-numbered text from local PDF files without using an LLM."""

from pathlib import Path

import pymupdf4llm
from pypdf import PdfReader

from literature_review.models import FullTextDocument, PageText


class PdfExtractionError(RuntimeError):
    """Raised when a local PDF cannot provide usable text evidence."""


def extract_pdf_text(path: str | Path, paper_id: str) -> FullTextDocument:
    """Extract usable text from each PDF page and preserve original page numbers.

    Primary path (``extraction_method="pymupdf4llm"``) converts each page to
    markdown so tables stay intact as ``|`` rows; a pypdf fallback keeps the old
    raw-text behaviour for PDFs the markdown converter cannot handle (e.g.
    scans without OCR). Page numbers come from pymupdf4llm's per-page metadata
    (1-based).
    """
    source_path = Path(path)
    if not source_path.is_file():
        raise PdfExtractionError(f"PDF file not found: {source_path}")

    try:
        pages_md = pymupdf4llm.to_markdown(source_path, page_chunks=True)
        pages = [
            PageText(
                page_number=int(page["metadata"]["page_number"]),
                text=text,
            )
            for page in pages_md
            if (text := (page.get("text") or "").strip()) and len(text) >= 20
        ]
        if pages:
            return FullTextDocument(
                paper_id=paper_id,
                source_path=str(source_path),
                pages=pages,
                extraction_method="pymupdf4llm",
            )
        _warn_markdown_fallback(source_path, reason="no usable markdown")
    except Exception as error:  # noqa: BLE001 - fall back to pypdf on any markdown failure
        _warn_markdown_fallback(source_path, reason=f"{type(error).__name__}: {error}")

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


def _warn_markdown_fallback(source_path: Path, *, reason: str) -> None:
    """Emit a stderr warning that the pymupdf4llm path was skipped."""
    import sys

    print(
        f"warning: pymupdf4llm extraction failed for {source_path.name} "
        f"({reason}); falling back to pypdf raw text.",
        file=sys.stderr,
    )
