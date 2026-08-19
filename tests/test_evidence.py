import unittest

from literature_review.evidence import chunk_document
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


if __name__ == "__main__":
    unittest.main()
