import unittest
from collections import Counter

from literature_review.embedding_retriever import QUERY_PREFIX, retrieve_evidence_embedding, _score_ranked, encode_chunks
from literature_review.models import EvidenceChunk, EvidenceRetrievalPolicy, EvidenceRetrievalResponse
from literature_review.assessment import aggregate_evidence_assessments


def chunk(chunk_id: str, text: str) -> EvidenceChunk:
    return EvidenceChunk(
        chunk_id=chunk_id,
        paper_id="paper-1",
        page_start=1,
        page_end=1,
        text=text,
    )


def chunk_for(paper_id: str, chunk_id: str, text: str) -> EvidenceChunk:
    return EvidenceChunk(
        chunk_id=f"{paper_id}-{chunk_id}",
        paper_id=paper_id,
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


class ConstantEncoder:
    """Fake encoder returning an identical vector for every input (ties sorted by id)."""

    def __call__(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0, 0.0] for _ in texts]


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

    def test_encode_chunks_default_passes_raw_chunk_text(self) -> None:
        encoder = RecordingEncoder()
        chunks = [chunk("c1", "raw evidence chunk text that is long enough to validate")]
        encode_chunks(chunks, encoder)
        self.assertEqual(encoder._inputs, ["raw evidence chunk text that is long enough to validate"])

    def test_encode_chunks_text_for_rewrites_encoded_text(self) -> None:
        encoder = RecordingEncoder()
        chunks = [chunk("c1", "some evidence chunk text that is long enough to validate")]
        encode_chunks(chunks, encoder, text_for=lambda c: f"[wrapped] {c.text}")
        self.assertEqual(encoder._inputs, ["[wrapped] some evidence chunk text that is long enough to validate"])


def rerank_scores(response: EvidenceRetrievalResponse, texts_by_chunk: dict[str, list[int]]) -> object:
    from literature_review.models import EvidenceRerankResponse, EvidenceSummary

    summaries = []
    for item in response.ranked_chunks:
        rel, qual = texts_by_chunk[item.chunk.chunk_id]
        summaries.append(
            EvidenceSummary(
                chunk_id=item.chunk.chunk_id,
                paper_id=item.chunk.paper_id,
                page_start=item.chunk.page_start,
                page_end=item.chunk.page_end,
                summary="This is a valid evidence summary with enough text to validate.",
                relevance_score=rel,
                evidence_quality_score=qual,
                rationale_relevance="Relevance rationale with enough text to validate the evidence summary.",
                rationale_quality="Quality rationale with enough text to validate the evidence summary.",
            )
        )
    return EvidenceRerankResponse(retrieval_response=response, summaries=summaries)


class PerPaperCapTests(unittest.TestCase):
    def test_no_cap_keeps_legacy_top_k_behavior(self) -> None:
        chunks = [
            chunk_for("p-a", f"c{n}", f"evidence text number {n}") for n in range(1, 9)
        ]
        response = retrieve_evidence_embedding(
            chunks, "allocation", EvidenceRetrievalPolicy(top_k=3, max_chunks_per_paper=None),
            encoder=ConstantEncoder(),
        )
        self.assertEqual(len(response.ranked_chunks), 3)
        self.assertTrue(all(r.chunk.paper_id == "p-a" for r in response.ranked_chunks))

    def test_cap_limits_per_paper_and_backfills_to_next(self) -> None:
        chunks = [
            chunk_for("p-a", "c1", "generic evidence chunk text number one"),
            chunk_for("p-a", "c2", "generic evidence chunk text number two"),
            chunk_for("p-a", "c3", "generic evidence chunk text number three"),
            chunk_for("p-b", "c1", "generic evidence chunk text number four"),
            chunk_for("p-b", "c2", "generic evidence chunk text number five"),
        ]
        response = retrieve_evidence_embedding(
            chunks, "allocation", EvidenceRetrievalPolicy(top_k=4, max_chunks_per_paper=2),
            encoder=ConstantEncoder(),
        )
        counts = Counter(r.chunk.paper_id for r in response.ranked_chunks)
        self.assertEqual(counts["p-a"], 2)
        self.assertEqual(counts["p-b"], 2)
        self.assertEqual(len(response.ranked_chunks), 4)

    def test_cap_undershoots_top_k_collects_all_without_error(self) -> None:
        chunks = [
            chunk_for("p-a", f"c{n}", f"evidence text number {n}") for n in range(1, 4)
        ] + [
            chunk_for("p-b", f"c{n}", f"evidence text number {n}") for n in range(1, 4)
        ]
        response = retrieve_evidence_embedding(
            chunks, "allocation", EvidenceRetrievalPolicy(top_k=10, max_chunks_per_paper=6),
            encoder=ConstantEncoder(),
        )
        self.assertEqual(len(response.ranked_chunks), 6)
        self.assertEqual(Counter(r.chunk.paper_id for r in response.ranked_chunks)["p-a"], 3)

    def test_cap_six_top_k_32_spreads_budget_across_many_papers(self) -> None:
        papers = [f"p-{n}" for n in range(5)]
        chunks = [
            chunk_for(p, f"c{i}", f"generic evidence text for paper {p} number {i}")
            for p in papers
            for i in range(8)
        ]
        response = retrieve_evidence_embedding(
            chunks, "allocation", EvidenceRetrievalPolicy(top_k=32, max_chunks_per_paper=6),
            encoder=ConstantEncoder(),
        )
        counts = Counter(r.chunk.paper_id for r in response.ranked_chunks)
        self.assertEqual(len(response.ranked_chunks), 30)
        self.assertTrue(all(count <= 6 for count in counts.values()))
        self.assertGreaterEqual(len(counts), 5)
        self.assertTrue(all(p in counts for p in papers))


class ShrinkageStrengthTests(unittest.TestCase):
    def test_default_shrinkage_strength_is_one(self) -> None:
        from literature_review.models import EvidenceAggregationPolicy

        self.assertEqual(EvidenceAggregationPolicy().shrinkage_strength, 1)

def test_shrunk_mean_with_strength_one(self) -> None:
    from literature_review.models import EvidenceAggregationPolicy

    # (10 + 1*5.5) / 2 = 7.75 -> 7.8 (shrinkage m=1 pulls a single 10 slightly toward prior 5.5)
    response = _score_ranked(
        [chunk_for("p-a", "c1", "generic evidence text number one")],
        [[1.0, 0.0, 0.0]],
        [1.0, 0.0, 0.0],
        "allocation",
        EvidenceRetrievalPolicy(top_k=1, max_chunks_per_paper=1),
    )
    reranked = rerank_scores(response, {"p-a-c1": [10, 10]})
    result = aggregate_evidence_assessments(reranked, EvidenceAggregationPolicy())
    self.assertEqual(result.assessments[0].utility_score, 7.8)


if __name__ == "__main__":
    unittest.main()
