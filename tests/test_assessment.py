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
        self.assertEqual(assessment.relevance_score, 5)
        self.assertEqual(assessment.evidence_quality_score, 4)
        self.assertEqual(assessment.recommendation, "include")
        self.assertEqual([item.chunk_id for item in assessment.evidence], [chunk_one.chunk_id, chunk_two.chunk_id])
        self.assertEqual((assessment.evidence[0].page_start, assessment.evidence[0].page_end), (2, 3))
        self.assertIn("paper-1-p7-7-c2 (pages 7-7)", assessment.rationale)
        self.assertIn("top-k", result.limitations[-1])


if __name__ == "__main__":
    unittest.main()
