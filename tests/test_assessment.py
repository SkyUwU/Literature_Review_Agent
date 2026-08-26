import unittest

from literature_review.assessment import aggregate_evidence_assessments, assess_selected_papers
from literature_review.models import (
    AssessmentPolicy,
    EvidenceAggregationPolicy,
    EvidenceChunk,
    EvidenceRerankResponse,
    EvidenceRetrievalPolicy,
    EvidenceRetrievalResponse,
    EvidenceSummary,
    RankedEvidenceChunk,
    SelectionPolicy,
)
from literature_review.selection import select_papers
from tests.test_selection import ranked_response


def _rerank_response_with_scores(scores: list[tuple[int, int]]) -> EvidenceRerankResponse:
    """Build a single-paper rerank response with one retrieved chunk per (relevance, quality) pair."""
    chunks = [
        EvidenceChunk(
            chunk_id=f"paper-1-p{index + 1}-{index + 1}-c{index + 1}",
            paper_id="paper-1",
            page_start=index + 1,
            page_end=index + 1,
            text=f"Chunk {index + 1} contains enough testing text about the evidence ranking method.",
        )
        for index in range(len(scores))
    ]
    retrieved = EvidenceRetrievalResponse(
        query="How should evidence be ranked?",
        policy=EvidenceRetrievalPolicy(top_k=len(chunks)),
        ranked_chunks=[
            RankedEvidenceChunk(
                chunk=chunk, rank=rank, score=3, matched_terms=["evidence"], rationale="test"
            )
            for rank, chunk in enumerate(chunks, start=1)
        ],
    )
    summaries = [
        EvidenceSummary(
            chunk_id=chunk.chunk_id,
            paper_id="paper-1",
            page_start=chunk.page_start,
            page_end=chunk.page_end,
            summary="The method description directly supports the evidence ranking research question.",
            relevance_score=relevance,
            evidence_quality_score=quality,
            recommendation="consider",
            rationale="It supplies method description evidence grounded in the requested topic.",
        )
        for chunk, (relevance, quality) in zip(chunks, scores, strict=True)
    ]
    return EvidenceRerankResponse(
        retrieval_response=retrieved,
        summaries=summaries,
        limitations=["LLM summaries are limited to supplied chunks."],
    )


class AssessmentTests(unittest.TestCase):
    def test_assessment_recommends_medium_relevance_paper_for_consideration(self) -> None:
        selected = select_papers(ranked_response(), policy=SelectionPolicy(max_papers=1))

        response = assess_selected_papers(selected, AssessmentPolicy())

        self.assertEqual(response.assessments[0].relevance_score, 3)
        self.assertEqual(response.assessments[0].recommendation, "consider")
        self.assertIn("full-text", response.limitations[0])

    def test_low_ranked_paper_is_excluded(self) -> None:
        ranked = ranked_response()
        selected = select_papers(ranked, policy=SelectionPolicy(max_papers=3))

        response = assess_selected_papers(selected, AssessmentPolicy())

        self.assertEqual(response.assessments[-1].recommendation, "exclude")

    def test_aggregates_chunk_evidence_with_page_traceability(self) -> None:
        chunk_one = EvidenceChunk(
            chunk_id="paper-1-p2-3-c1",
            paper_id="paper-1",
            page_start=2,
            page_end=3,
            text="This chunk contains enough testing text about an evidence ranking method and its evaluation.",
        )
        chunk_two = EvidenceChunk(
            chunk_id="paper-1-p7-7-c2",
            paper_id="paper-1",
            page_start=7,
            page_end=7,
            text="This chunk contains enough testing text about evaluation findings and documented limitations.",
        )
        retrieved = EvidenceRetrievalResponse(
            query="How should evidence be ranked?",
            policy=EvidenceRetrievalPolicy(top_k=2),
            ranked_chunks=[
                RankedEvidenceChunk(chunk=chunk_one, rank=1, score=3, matched_terms=["evidence"], rationale="test"),
                RankedEvidenceChunk(chunk=chunk_two, rank=2, score=2, matched_terms=["ranked"], rationale="test"),
            ],
        )
        reranked = EvidenceRerankResponse(
            retrieval_response=retrieved,
            summaries=[
                EvidenceSummary(
                    chunk_id=chunk_one.chunk_id,
                    paper_id="paper-1",
                    page_start=2,
                    page_end=3,
                    summary="The method description directly supports the evidence ranking research question.",
                    relevance_score=5,
                    evidence_quality_score=4,
                    recommendation="include",
                    rationale="It supplies a method description grounded in the requested evidence.",
                ),
                EvidenceSummary(
                    chunk_id=chunk_two.chunk_id,
                    paper_id="paper-1",
                    page_start=7,
                    page_end=7,
                    summary="The findings provide supporting evaluation evidence for the proposed ranking method.",
                    relevance_score=4,
                    evidence_quality_score=3,
                    recommendation="consider",
                    rationale="It supplies evaluation findings that support the requested ranking topic.",
                ),
            ],
            limitations=["LLM summaries are limited to supplied chunks."],
        )

        result = aggregate_evidence_assessments(reranked, EvidenceAggregationPolicy())

        assessment = result.assessments[0]
        self.assertEqual(assessment.paper_id, "paper-1")
        self.assertEqual(assessment.relevance_score, 4)
        self.assertEqual(assessment.evidence_quality_score, 3)
        self.assertEqual(assessment.recommendation, "include")
        self.assertEqual([item.chunk_id for item in assessment.evidence], [chunk_one.chunk_id, chunk_two.chunk_id])
        self.assertEqual((assessment.evidence[0].page_start, assessment.evidence[0].page_end), (2, 3))
        self.assertIn("paper-1-p7-7-c2 (pages 7-7)", assessment.rationale)
        self.assertTrue(any("top-k" in item for item in result.limitations))


class ShrunkMeanAggregationTests(unittest.TestCase):
    def test_shrunk_mean_single_chunk(self) -> None:
        result = aggregate_evidence_assessments(
            _rerank_response_with_scores([(4, 4)]), EvidenceAggregationPolicy()
        )

        self.assertEqual(result.assessments[0].relevance_score, 3)

    def test_shrunk_mean_many_chunks(self) -> None:
        scores = [(5, 4), (4, 4), (4, 4), (5, 4), (4, 4), (5, 4), (4, 4), (5, 4)]

        result = aggregate_evidence_assessments(_rerank_response_with_scores(scores), EvidenceAggregationPolicy())

        self.assertEqual(result.assessments[0].relevance_score, 4)

    def test_shrunk_mean_zero_strength_behaves_like_half_up_mean(self) -> None:
        policy = EvidenceAggregationPolicy(prior_score=3, shrinkage_strength=0)

        result = aggregate_evidence_assessments(_rerank_response_with_scores([(5, 4), (4, 3)]), policy)

        self.assertEqual(result.assessments[0].relevance_score, 5)
        self.assertEqual(result.assessments[0].evidence_quality_score, 4)

    def test_limitations_includes_retrieval_allocation(self) -> None:
        result = aggregate_evidence_assessments(
            _rerank_response_with_scores([(5, 4)]), EvidenceAggregationPolicy()
        )

        self.assertTrue(any("retrieval allocation" in item for item in result.limitations))


if __name__ == "__main__":
    unittest.main()
