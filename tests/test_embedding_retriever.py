import unittest

from literature_review.embedding_retriever import QUERY_PREFIX, retrieve_evidence_embedding
from literature_review.models import EvidenceChunk, EvidenceRetrievalPolicy, EvidenceRetrievalResponse


def chunk(chunk_id: str, text: str) -> EvidenceChunk:
    return EvidenceChunk(
        chunk_id=chunk_id,
        paper_id="paper-1",
        page_start=1,
        page_end=1,
        text=text,
    )


def vector_for(text: str) -> list[float]:
    """Deterministic pseudo-embedding: later chunks are more similar to the query."""
    return [0.1, 0.1 * len(text), 0.2]


class RecordingEncoder:
    """Fake encoder that records every input string and returns fixed vectors."""

    def __init__(self) -> None:
        self._inputs: list[str] = []

    def __call__(self, texts: list[str]) -> list[list[float]]:
        self._inputs.extend(texts)
        return [vector_for(text) for text in texts]


class EmbeddingRetrieverTests(unittest.TestCase):
    def test_top_k_ordering_follows_encoder_similarity(self) -> None:
        encoder = RecordingEncoder()
        response = retrieve_evidence_embedding(
            [
                chunk("c1", "a short but valid evidence chunk text here now"),
                chunk("c2", "a much longer chunk of text that yields a higher vector norm overall"),
            ],
            "literature review agent",
            EvidenceRetrievalPolicy(top_k=1),
            encoder=encoder,
        )

        # c2's vector has a larger second component, so it is more similar to the query vector.
        self.assertEqual(response.ranked_chunks[0].chunk.chunk_id, "c2")
        self.assertIsInstance(response, EvidenceRetrievalResponse)

    def test_query_is_prefixed_and_chunks_are_not(self) -> None:
        encoder = RecordingEncoder()
        retrieve_evidence_embedding(
            [chunk("c1", "some evidence chunk text that is long enough to validate")],
            "literature review agent",
            EvidenceRetrievalPolicy(top_k=1),
            encoder=encoder,
        )

        # First input is the prefixed query; the rest are unprefixed chunk texts.
        self.assertTrue(encoder._inputs[0].startswith(QUERY_PREFIX))
        self.assertFalse(QUERY_PREFIX in encoder._inputs[1])

    def test_empty_chunk_list_is_handled(self) -> None:
        encoder = RecordingEncoder()
        response = retrieve_evidence_embedding(
            [],
            "literature review agent",
            EvidenceRetrievalPolicy(top_k=3),
            encoder=encoder,
        )
        self.assertEqual(response.ranked_chunks, [])


if __name__ == "__main__":
    unittest.main()
