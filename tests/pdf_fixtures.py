"""Small real PDFs for offline downloader tests."""
import pymupdf


def pdf_fixture(text: str) -> bytes:
    with pymupdf.open() as document:
        page = document.new_page()
        page.insert_textbox(pymupdf.Rect(30, 30, 570, 800), text, fontsize=10)
        return document.tobytes()


def downloader_pdf_fixture() -> bytes:
    return pdf_fixture('Shared Work\n' + '\n'.join(f'W{i} title about literature reviews' for i in range(1, 30)) +
                       '\nW123 title about literature reviews')
