import unittest

from literature_review.evidence_ranking import retrieve_evidence
from literature_review.models import EvidenceChunk, EvidenceRetrievalPolicy


def chunk(chunk_id: str, text: str) -> EvidenceChunk:
    return EvidenceChunk(
        chunk_id=chunk_id,
        paper_id="paper-1",
        page_start=1,
        page_end=1,
        text=text,
    )


class EvidenceRankingTests(unittest.TestCase):
    def test_retrieval_prefers_chunks_with_more_query_evidence(self) -> None:
        response = retrieve_evidence(
            [
                chunk("c1", "This section discusses generic scientific writing and methods."),
                chunk("c2", "The literature review agent retrieves evidence for a review."),
            ],
            "literature review agent",
            EvidenceRetrievalPolicy(top_k=1),
        )

        self.assertEqual(response.ranked_chunks[0].chunk.chunk_id, "c2")
        self.assertEqual(response.ranked_chunks[0].matched_terms, ["agent", "literature", "review"])

    def test_retrieval_keeps_zero_match_chunks_when_fewer_than_top_k(self) -> None:
        response = retrieve_evidence(
            [chunk("c1", "This section discusses generic scientific writing and methods.")],
            "literature review agent",
            EvidenceRetrievalPolicy(top_k=3),
        )

        self.assertEqual(len(response.ranked_chunks), 1)
        self.assertEqual(response.ranked_chunks[0].score, 0)


if __name__ == "__main__":
    unittest.main()
