import unittest

from pydantic import ValidationError

from literature_review.demo import build_demo_report
from literature_review.models import (
    ChunkReference,
    CoveragePackPolicy,
    EvidenceAggregationPolicy,
    EvidenceAssessmentResponse,
    EvidenceChunk,
    EvidenceRetrievalPolicy,
    EvidenceRetrievalResponse,
    EvidenceRerankResponse,
    EvidenceSummary,
    FutureDirection,
    LlmNoteClaim,
    LlmPaperSummaryNote,
    LlmSynthesisBatch,
    LlmSynthesisDirection,
    PaperAssessment,
    PaperSource,
    PaperSummary,
    PaperSummaryClaim,
    RankedEvidenceChunk,
    SearchRequest,
    SynthesisResponse,
)


class ModelTests(unittest.TestCase):
    def test_demo_report_is_valid(self) -> None:
        report = build_demo_report()
        self.assertEqual(report.assessments[0].recommendation, "include")

    def test_score_must_be_between_one_and_five(self) -> None:
        with self.assertRaises(ValidationError):
            PaperAssessment(
                paper_id="example",
                relevance_score=6,
                evidence_quality_score=3,
                recommendation="include",
                rationale="This rationale is intentionally long enough to validate.",
            )

    def test_search_limit_must_be_positive(self) -> None:
        with self.assertRaises(ValidationError):
            SearchRequest(query="literature review", limit=0)


def chunk_reference() -> ChunkReference:
    return ChunkReference(
        chunk_id="p1-p1-2-c1",
        paper_id="p1",
        page_start=1,
        page_end=2,
        quote="A" * 20,
    )


def summary_claim() -> PaperSummaryClaim:
    return PaperSummaryClaim(text="A" * 20, aspect="abs", evidence=[chunk_reference()])


def paper_summary() -> PaperSummary:
    return PaperSummary(
        paper_id="p1",
        claims=[summary_claim()],
        coverage_chunk_ids=["p1-p1-2-c1"],
    )


def note_claim() -> LlmNoteClaim:
    return LlmNoteClaim(text="A" * 20, chunk_ids=["p1-p1-2-c1"], aspect="abs")


def llm_synthesis_direction() -> LlmSynthesisDirection:
    return LlmSynthesisDirection(
        title="abc",
        rationale="A" * 20,
        supporting_paper_ids=["p1"],
        supporting_chunk_ids=["p1-p1-2-c1"],
    )


def evidence_assessment_response() -> EvidenceAssessmentResponse:
    chunk = EvidenceChunk(
        chunk_id="p1-p1-2-c1",
        paper_id="p1",
        page_start=2,
        page_end=3,
        text="This evidence chunk describes a retrieval method in sufficient detail for testing.",
    )
    return EvidenceAssessmentResponse(
        evidence_rerank_response=EvidenceRerankResponse(
            retrieval_response=EvidenceRetrievalResponse(
                query="How should literature review evidence be ranked?",
                policy=EvidenceRetrievalPolicy(top_k=1),
                ranked_chunks=[
                    RankedEvidenceChunk(
                        chunk=chunk,
                        rank=1,
                        score=3,
                        matched_terms=["evidence"],
                        rationale="test",
                    )
                ],
            ),
            summaries=[
                EvidenceSummary(
                    chunk_id=chunk.chunk_id,
                    summary="The chunk reports a method relevant to evidence ranking.",
                    relevance_score=4,
                    evidence_quality_score=4,
                    recommendation="include",
                    rationale="It directly reports a method relevant to the topic.",
                    paper_id=chunk.paper_id,
                    page_start=chunk.page_start,
                    page_end=chunk.page_end,
                )
            ],
            limitations=["Test fixture limitation."],
        ),
        aggregation_policy=EvidenceAggregationPolicy(),
        assessments=[
            PaperAssessment(
                paper_id="p1",
                relevance_score=5,
                evidence_quality_score=4,
                recommendation="include",
                rationale="This rationale is intentionally long enough to validate.",
            )
        ],
        limitations=["Metadata-only assessments are not full-text reviews."],
    )


class SynthesisModelTests(unittest.TestCase):
    def test_chunk_reference_round_trip(self) -> None:
        reference = chunk_reference()

        restored = ChunkReference.model_validate_json(reference.model_dump_json())

        self.assertEqual(restored, reference)

    def test_paper_summary_claim_round_trip(self) -> None:
        claim = summary_claim()

        restored = PaperSummaryClaim.model_validate_json(claim.model_dump_json())

        self.assertEqual(restored, claim)

    def test_paper_summary_round_trip(self) -> None:
        summary = paper_summary()

        restored = PaperSummary.model_validate_json(summary.model_dump_json())

        self.assertEqual(restored, summary)

    def test_llm_note_claim_round_trip(self) -> None:
        claim = note_claim()

        restored = LlmNoteClaim.model_validate_json(claim.model_dump_json())

        self.assertEqual(restored, claim)

    def test_llm_paper_summary_note_round_trip(self) -> None:
        note = LlmPaperSummaryNote(claims=[note_claim()])

        restored = LlmPaperSummaryNote.model_validate_json(note.model_dump_json())

        self.assertEqual(restored, note)

    def test_llm_synthesis_direction_round_trip(self) -> None:
        direction = llm_synthesis_direction()

        restored = LlmSynthesisDirection.model_validate_json(direction.model_dump_json())

        self.assertEqual(restored, direction)

    def test_llm_synthesis_batch_round_trip(self) -> None:
        batch = LlmSynthesisBatch(report="X" * 100, future_directions=[llm_synthesis_direction()])

        restored = LlmSynthesisBatch.model_validate_json(batch.model_dump_json())

        self.assertEqual(restored, batch)

    def test_paper_source_round_trip(self) -> None:
        source = PaperSource(paper_id="p1", source_path="/tmp/test.pdf")

        restored = PaperSource.model_validate_json(source.model_dump_json())

        self.assertEqual(restored, source)

    def test_coverage_pack_policy_defaults(self) -> None:
        policy = CoveragePackPolicy()

        self.assertEqual(policy.max_chunks_per_paper, 6)
        self.assertEqual(policy.llm_input_cap, 40)

    def test_synthesis_response_round_trip(self) -> None:
        response = SynthesisResponse(
            paper_sources=[PaperSource(paper_id="p1", source_path="/tmp/test.pdf")],
            evidence_assessment_response=evidence_assessment_response(),
            paper_summaries=[paper_summary()],
            report="X" * 100,
            future_directions=[
                FutureDirection(title="abc", rationale="A" * 20, supporting_paper_ids=["p1"])
            ],
            limitations=[],
            generated_by="deterministic",
        )

        restored = SynthesisResponse.model_validate_json(response.model_dump_json())

        self.assertEqual(restored, response)

    def test_future_direction_supporting_chunk_ids_default(self) -> None:
        direction = FutureDirection(
            title="abc", rationale="A" * 20, supporting_paper_ids=["p1"]
        )

        self.assertEqual(direction.supporting_chunk_ids, [])

    def test_evidence_aggregation_policy_expanded(self) -> None:
        policy = EvidenceAggregationPolicy()

        self.assertEqual(policy.prior_score, 3)
        self.assertEqual(policy.shrinkage_strength, 4)

    def test_llm_note_claim_validation_error(self) -> None:
        with self.assertRaises(ValidationError):
            LlmNoteClaim(text="short", chunk_ids=["p1-p1-2-c1"], aspect="abs")

    def test_synthesis_batch_report_with_marker(self) -> None:
        batch = LlmSynthesisBatch(
            report="[chunk-id] " + "X" * 100,
            future_directions=[llm_synthesis_direction()],
        )

        self.assertIn("[chunk-id]", batch.report)

    def test_synthesis_response_with_marker(self) -> None:
        response = SynthesisResponse(
            paper_sources=[PaperSource(paper_id="p1", source_path="/tmp/test.pdf")],
            evidence_assessment_response=evidence_assessment_response(),
            paper_summaries=[paper_summary()],
            report="[chunk-id] " + "X" * 100,
            future_directions=[
                FutureDirection(title="abc", rationale="A" * 20, supporting_paper_ids=["p1"])
            ],
            limitations=[],
            generated_by="deterministic",
        )

        self.assertIn("[chunk-id]", response.report)


if __name__ == "__main__":
    unittest.main()
