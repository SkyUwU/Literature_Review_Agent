import unittest

from literature_review.evidence import chapter_chunk_document, chunk_document
from literature_review.models import ChunkPolicy, FullTextDocument, PageText


class EvidenceTests(unittest.TestCase):
    def test_chunks_keep_paper_and_page_provenance(self) -> None:
        document = FullTextDocument(
            paper_id="paper-1",
            source_path="papers/paper-1.pdf",
            extraction_method="test",
            pages=[PageText(page_number=3, text=" ".join(f"word{i}" for i in range(130)))],
        )

        chunks = chunk_document(document, ChunkPolicy(max_words=50, overlap_words=10))

        self.assertEqual(len(chunks), 3)
        self.assertEqual([chunk.page_start for chunk in chunks], [3, 3, 3])
        self.assertEqual(chunks[0].paper_id, "paper-1")
        self.assertIn("word40", chunks[0].text)
        self.assertIn("word40", chunks[1].text)

    def test_rejects_overlap_that_cannot_advance(self) -> None:
        document = FullTextDocument(
            paper_id="paper-1",
            source_path="papers/paper-1.pdf",
            extraction_method="test",
            pages=[PageText(page_number=1, text="A sufficiently long page text for this validation test.")],
        )

        with self.assertRaises(ValueError):
            chunk_document(document, ChunkPolicy(max_words=50, overlap_words=50))

    def test_chunk_can_span_consecutive_pages(self) -> None:
        document = FullTextDocument(
            paper_id="paper-1",
            source_path="papers/paper-1.pdf",
            extraction_method="test",
            pages=[
                PageText(page_number=1, text=" ".join(f"first{i}" for i in range(35))),
                PageText(page_number=2, text=" ".join(f"second{i}" for i in range(35))),
            ],
        )

        chunks = chunk_document(document, ChunkPolicy(max_words=50, overlap_words=10))

        self.assertEqual((chunks[0].page_start, chunks[0].page_end), (1, 2))
        self.assertIn("first34", chunks[0].text)
        self.assertIn("second14", chunks[0].text)


class ChapterChunkTests(unittest.TestCase):
    """Section-aware markdown splitting (pymupdf4llm heading path)."""

    def _document(self, text: str) -> FullTextDocument:
        return FullTextDocument(
            paper_id="paper-1",
            source_path="papers/paper-1.pdf",
            extraction_method="test",
            pages=[PageText(page_number=1, text=text)],
        )

    def test_section_path_reflects_heading_hierarchy(self) -> None:
        markdown = (
            "# **The Paper**\n\n"
            "## **1 Introduction**\n\n"
            "This paragraph introduces the idea behind the literature review agent.\n\n"
            "### **1.1 Background**\n\n"
            "This paragraph provides background context for the proposed system design.\n\n"
            "### **1.2 Scope**\n\n"
            "This paragraph narrows the scope to deterministic evidence selection methods."
        )
        chunks = chapter_chunk_document(self._document(markdown), ChunkPolicy(max_words=200, overlap_words=10))

        self.assertEqual(
            [chunk.section for chunk in chunks],
            [
                "The Paper > 1 Introduction",
                "The Paper > 1 Introduction > 1.1 Background",
                "The Paper > 1 Introduction > 1.2 Scope",
            ],
        )

    def test_chunk_ids_are_paper_globally_sequential(self) -> None:
        markdown = (
            "# **Title**\n\n"
            "## **2 Method**\n\n"
            "This first method paragraph explains the deterministic splitting strategy.\n\n"
            "## **4 Results**\n\n"
            "This results paragraph reports the boundary preserving evaluation numbers."
        )
        chunks = chapter_chunk_document(self._document(markdown), ChunkPolicy(max_words=200, overlap_words=10))

        self.assertEqual(
            [chunk.section for chunk in chunks],
            ["Title > 2 Method", "Title > 4 Results"],
        )
        self.assertEqual([chunk.chunk_id for chunk in chunks], ["paper-1-c1", "paper-1-c2"])

    def test_no_heading_text_gets_section_none(self) -> None:
        chunks = chapter_chunk_document(
            self._document("A plain paragraph with no markdown headings anywhere in this text at all."),
            ChunkPolicy(max_words=200, overlap_words=10),
        )

        self.assertEqual(len(chunks), 1)
        self.assertIsNone(chunks[0].section)
        self.assertEqual(chunks[0].chunk_id, "paper-1-c1")

    def test_page_bounds_are_none_without_page_recovery(self) -> None:
        markdown = (
            "## **3 Experiments**\n\n"
            "This experiments paragraph describes the benchmark we used for evaluation."
        )
        chunks = chapter_chunk_document(self._document(markdown), ChunkPolicy(max_words=200, overlap_words=10))

        self.assertIsNone(chunks[0].page_start)
        self.assertIsNone(chunks[0].page_end)

    def test_paragraphs_group_without_breaking_boundaries(self) -> None:
        def paragraph(prefix: str, count: int = 20) -> str:
            return " ".join(f"{prefix}word{i}" for i in range(count))

        paragraphs = (
            f"{paragraph('first')}\n\n"
            f"{paragraph('second')}\n\n"
            f"{paragraph('third')}"
        )
        chunks = chapter_chunk_document(
            self._document(f"## **1 Introduction**\n\n{paragraphs}"),
            ChunkPolicy(max_words=50, overlap_words=5),
        )

        # 40 words fit in one chunk (two 20-word paragraphs); the third starts a new one.
        self.assertEqual(len(chunks), 2)
        self.assertTrue(chunks[0].text.startswith("firstword0"))
        self.assertTrue(chunks[0].text.endswith("secondword19"))
        self.assertTrue(chunks[1].text.startswith("thirdword0"))
        self.assertFalse("thirdword0" in chunks[0].text)
        self.assertFalse("firstword0" in chunks[1].text)

    def test_table_block_is_kept_whole(self) -> None:
        rows = "\n".join(f"| Row{i:02d} | value{i} | score{i}.0 |" for i in range(20))
        table = "| System | Metric | Score |\n" + rows
        chunks = chapter_chunk_document(
            self._document(f"## **5 Results**\n\n{table}"),
            ChunkPolicy(max_words=50, overlap_words=2),
        )

        self.assertEqual(len(chunks), 1)
        self.assertIn("| System | Metric | Score |", chunks[0].text)
        self.assertIn("| Row19 | value19 | score19.0 |", chunks[0].text)

    def test_overlong_paragraph_uses_sliding_window_fallback(self) -> None:
        words = " ".join(f"token{i}" for i in range(80))
        chunks = chapter_chunk_document(
            self._document(f"### **6 Discussion**\n\n{words}"),
            ChunkPolicy(max_words=50, overlap_words=5),
        )

        self.assertGreaterEqual(len(chunks), 2)
        self.assertIn("token0", chunks[0].text)
        self.assertIn("token79", chunks[-1].text)
        # overlap only exists on the sliding-window fallback path
        self.assertIn("token45", chunks[0].text)
        self.assertIn("token45", chunks[1].text)

    def test_rejects_overlap_that_cannot_advance(self) -> None:
        with self.assertRaises(ValueError):
            chapter_chunk_document(
                self._document("A sufficiently long paragraph for the overlap validation test here."),
                ChunkPolicy(max_words=50, overlap_words=50),
            )

    def test_watermark_short_text_is_dropped_before_construction(self) -> None:
        markdown = (
            "_ARTICLE_\n\n"
            "# **Prospects of Retrieval-Augmented Generation**\n\n"
            "This body paragraph explains the retrieval augmented generation pipeline."
        )
        chunks = chapter_chunk_document(self._document(markdown), ChunkPolicy(max_words=200, overlap_words=10))

        # Pydantic text min_length=20 raises at build time, before the
        # word-count drop in _prepare_documents can filter this group.
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0].chunk_id, "paper-1-c1")
        self.assertIn("retrieval augmented generation pipeline", chunks[0].text)

    def test_short_noise_group_does_not_shift_subsequent_chunk_ids(self) -> None:
        markdown = (
            "## **1 Introduction**\n\n"
            "This introduction paragraph gives the background for the whole paper.\n\n"
            "2\n\n"
            "## **2 Method**\n\n"
            "This method paragraph explains the deterministic splitting strategy."
        )
        chunks = chapter_chunk_document(self._document(markdown), ChunkPolicy(max_words=200, overlap_words=10))

        self.assertEqual(
            [chunk.section for chunk in chunks],
            ["1 Introduction", "2 Method"],
        )
        self.assertEqual([chunk.chunk_id for chunk in chunks], ["paper-1-c1", "paper-1-c2"])


if __name__ == "__main__":
    unittest.main()
