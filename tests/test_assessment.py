import unittest

from literature_review.assessment import (
    _shrunk_mean,
    aggregate_evidence_assessments,
    assess_selected_papers,
    evidence_quality_score,
    relevance_score,
)
from literature_review.models import (
    AssessmentPolicy,
    EvidenceAggregationPolicy,
    EvidenceChunk,
    EvidenceRerankResponse,
    EvidenceRetrievalPolicy,
    EvidenceRetrievalResponse,
    EvidenceSummary,
    FilterPolicy,
    Paper,
    RankedEvidenceChunk,
    RankedPaper,
    RankedSearchResponse,
    SearchRequest,
    SearchResponse,
    SelectionPolicy,
)
from literature_review.selection import select_papers
from tests.test_selection import ranked_response


def ranked_response_0_3() -> RankedSearchResponse:
    """Metadata test fixture whose baseline scores live in the documented [0, 3] range."""
    papers = [
        Paper(
            paper_id=f"paper-{index}",
            title=f"Paper {index}",
            authors=["Author"],
            year=2024,
            abstract="A sufficiently long abstract for testing paper selection.",
            url=f"https://example.org/paper-{index}",
        )
        for index in range(1, 4)
    ]
    response = SearchResponse(
        provider="test",
        request=SearchRequest(query="test query"),
        total_candidates=3,
        papers=papers,
        skipped_candidates=0,
    )
    return RankedSearchResponse(
        search_response=response,
        filter_policy=FilterPolicy(),
        ranked_papers=[
            RankedPaper(paper=papers[0], rank=1, score=1.8, matched_terms=[], rationale="test"),
            RankedPaper(paper=papers[1], rank=2, score=1.2, matched_terms=[], rationale="test"),
            RankedPaper(paper=papers[2], rank=3, score=0.5, matched_terms=[], rationale="test"),
        ],
    )


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
            rationale_relevance="It supplies method description evidence grounded in the requested topic.",
            rationale_quality="The chunk offers concrete method details with sufficient specificity.",
        )
        for chunk, (relevance, quality) in zip(chunks, scores, strict=True)
    ]
    return EvidenceRerankResponse(
        retrieval_response=retrieved,
        summaries=summaries,
        limitations=["LLM summaries are limited to supplied chunks."],
    )


class AssessmentTests(unittest.TestCase):
    def test_relevance_score_maps_0_3_range_to_1_10(self) -> None:
        self.assertEqual(relevance_score(0), 1)
        self.assertEqual(relevance_score(1.5), 5)
        self.assertEqual(relevance_score(3), 10)

    def test_relevance_score_deprecated_marker(self) -> None:
        self.assertIn("Deprecated", relevance_score.__doc__)

    def test_evidence_quality_score_scales_to_1_10(self) -> None:
        self.assertEqual(evidence_quality_score(None, None), 4)
        self.assertEqual(evidence_quality_score(10, "arXiv"), 8)
        self.assertEqual(evidence_quality_score(100, "arXiv"), 10)

    def test_evidence_quality_score_deprecated_marker(self) -> None:
        self.assertIn("Deprecated", evidence_quality_score.__doc__)

    def test_assessment_excludes_medium_relevance_paper_under_two_way_rule(self) -> None:
        selected = select_papers(ranked_response_0_3(), policy=SelectionPolicy(max_papers=1))

        response = assess_selected_papers(selected, AssessmentPolicy())

        self.assertEqual(response.assessments[0].utility_score, 6.0)
        self.assertEqual(response.assessments[0].recommendation, "exclude")
        self.assertIn("full-text", response.limitations[0])

    def test_low_ranked_paper_is_excluded(self) -> None:
        ranked = ranked_response_0_3()
        selected = select_papers(ranked, policy=SelectionPolicy(max_papers=3))

        response = assess_selected_papers(selected, AssessmentPolicy())

        self.assertEqual(response.assessments[-1].utility_score, 2.0)
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
                    relevance_score=10,
                    evidence_quality_score=8,
                    rationale_relevance="It supplies a method description grounded in the requested evidence.",
                    rationale_quality="The chunk offers concrete method details with sufficient specificity.",
                ),
                EvidenceSummary(
                    chunk_id=chunk_two.chunk_id,
                    paper_id="paper-1",
                    page_start=7,
                    page_end=7,
                    summary="The findings provide supporting evaluation evidence for the proposed ranking method.",
                    relevance_score=8,
                    evidence_quality_score=7,
                    rationale_relevance="It supplies evaluation findings that support the requested ranking topic.",
                    rationale_quality="The chunk reports specific evaluation results with measurable detail.",
                ),
            ],
            limitations=["LLM summaries are limited to supplied chunks."],
        )

        result = aggregate_evidence_assessments(reranked, EvidenceAggregationPolicy())

        assessment = result.assessments[0]
        self.assertEqual(assessment.paper_id, "paper-1")
        self.assertEqual(assessment.utility_score, 7.8)
        self.assertEqual(assessment.recommendation, "include")
        self.assertEqual([item.chunk_id for item in assessment.evidence], [chunk_one.chunk_id, chunk_two.chunk_id])
        self.assertEqual((assessment.evidence[0].page_start, assessment.evidence[0].page_end), (2, 3))
        self.assertEqual(assessment.evidence[0].utility_score, 10)
        self.assertIn("paper-1-p7-7-c2 (pages 7-7)", assessment.rationale)
        self.assertTrue(any("top-k" in item for item in result.limitations))


class ShrunkMeanAggregationTests(unittest.TestCase):
    def test_shrunk_mean_single_chunk_pulled_toward_prior(self) -> None:
        result = aggregate_evidence_assessments(
            _rerank_response_with_scores([(10, 10)]), EvidenceAggregationPolicy()
        )

        self.assertEqual(result.assessments[0].utility_score, 7.8)
        self.assertEqual(result.assessments[0].recommendation, "include")

    def test_shrunk_mean_many_chunks(self) -> None:
        scores = [(10, 8)] * 8

        result = aggregate_evidence_assessments(_rerank_response_with_scores(scores), EvidenceAggregationPolicy())

        self.assertEqual(result.assessments[0].utility_score, 9.5)
        self.assertEqual(result.assessments[0].recommendation, "include")

    def test_shrunk_mean_zero_strength_behaves_like_rounded_mean(self) -> None:
        policy = EvidenceAggregationPolicy(prior_score=5.5, shrinkage_strength=0)

        result = aggregate_evidence_assessments(_rerank_response_with_scores([(5, 4), (4, 3)]), policy)

        self.assertEqual(result.assessments[0].utility_score, 4.5)
        self.assertEqual(result.assessments[0].recommendation, "exclude")

    def test_utility_score_at_8_7_includes(self) -> None:
        result = aggregate_evidence_assessments(
            _rerank_response_with_scores([(9, 8)] * 10), EvidenceAggregationPolicy()
        )

        self.assertEqual(result.assessments[0].utility_score, 8.7)
        self.assertEqual(result.assessments[0].recommendation, "include")

    def test_utility_score_between_6_and_8_still_includes(self) -> None:
        result = aggregate_evidence_assessments(
            _rerank_response_with_scores([(8, 8)] * 6), EvidenceAggregationPolicy()
        )

        self.assertEqual(result.assessments[0].utility_score, 7.6)
        self.assertEqual(result.assessments[0].recommendation, "include")

    def test_utility_score_at_6_5_includes(self) -> None:
        result = aggregate_evidence_assessments(
            _rerank_response_with_scores([(7, 7)] * 2), EvidenceAggregationPolicy()
        )

        self.assertEqual(result.assessments[0].utility_score, 6.5)
        self.assertEqual(result.assessments[0].recommendation, "include")

    def test_utility_score_below_6_0_excludes(self) -> None:
        result = aggregate_evidence_assessments(
            _rerank_response_with_scores([(5, 5)] * 12), EvidenceAggregationPolicy()
        )

        self.assertEqual(result.assessments[0].utility_score, 5.0)
        self.assertEqual(result.assessments[0].recommendation, "exclude")

    def test_empty_scores_raise_error(self) -> None:
        with self.assertRaises(ValueError):
            _shrunk_mean([], EvidenceAggregationPolicy())

    def test_limitations_includes_retrieval_allocation(self) -> None:
        result = aggregate_evidence_assessments(
            _rerank_response_with_scores([(10, 8)]), EvidenceAggregationPolicy()
        )

        self.assertTrue(any("retrieval allocation" in item for item in result.limitations))


if __name__ == "__main__":
    unittest.main()
