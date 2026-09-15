import unittest

from pydantic import ValidationError

from literature_review.demo import build_demo_report
from literature_review.models import (
    AssessmentPolicy,
    ChunkReference,
    CoveragePackPolicy,
    EvidenceAggregationPolicy,
    EvidenceAssessmentResponse,
    EvidenceChunk,
    EvidenceCitation,
    EvidenceRetrievalPolicy,
    EvidenceRetrievalResponse,
    EvidenceRerankResponse,
    EvidenceSummary,
    FutureDirection,
    LlmEvidenceAssessment,
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

    def test_score_must_be_between_one_and_ten(self) -> None:
        with self.assertRaises(ValidationError):
            PaperAssessment(
                paper_id="example",
                utility_score=11,
                recommendation="include",
                rationale="This rationale is intentionally long enough to validate.",
            )

    def test_float_paper_scores_are_accepted(self) -> None:
        assessment = PaperAssessment(
            paper_id="example",
            utility_score=7.5,
            recommendation="include",
            rationale="This rationale is intentionally long enough to validate.",
        )

        self.assertEqual(assessment.utility_score, 7.5)

    def test_chunk_assessment_score_above_ten_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            LlmEvidenceAssessment(
                chunk_id="c1",
                summary="This summary is long enough to pass validation.",
                relevance_score=11,
                evidence_quality_score=10,
                rationale_relevance="This rationale is intentionally long enough to validate.",
                rationale_quality="This rationale is intentionally long enough to validate.",
            )

    def test_chunk_assessment_rejects_recommendation_field(self) -> None:
        with self.assertRaises(ValidationError):
            LlmEvidenceAssessment(
                chunk_id="c1",
                summary="This summary is long enough to pass validation.",
                relevance_score=8,
                evidence_quality_score=7,
                recommendation="include",
                rationale_relevance="This rationale is intentionally long enough to validate.",
                rationale_quality="This rationale is intentionally long enough to validate.",
            )

    def test_chunk_assessment_field_order_matches_prompt_contract(self) -> None:
        properties = list(LlmEvidenceAssessment.model_json_schema()["properties"])
        self.assertEqual(
            properties,
            [
                "chunk_id",
                "summary",
                "rationale_relevance",
                "rationale_quality",
                "relevance_score",
                "evidence_quality_score",
            ],
        )

    def test_functional_assessment_field_order_matches_prompt_contract(self) -> None:
        from literature_review.models import LlmFunctionalAssessment

        properties = list(LlmFunctionalAssessment.model_json_schema()["properties"])
        self.assertEqual(properties, ["chunk_id", "rationale", "utility_score"])

    def test_functional_assessment_score_above_ten_rejected(self) -> None:
        from literature_review.models import LlmFunctionalAssessment

        with self.assertRaises(ValidationError):
            LlmFunctionalAssessment(
                chunk_id="c1",
                rationale="This rationale is intentionally long enough to validate.",
                utility_score=11,
            )

    def test_functional_assessment_rejects_short_rationale(self) -> None:
        from literature_review.models import LlmFunctionalAssessment

        with self.assertRaises(ValidationError):
            LlmFunctionalAssessment(
                chunk_id="c1",
                rationale="too short",
                utility_score=8,
            )

    def test_functional_assessment_rejects_extra_field(self) -> None:
        from literature_review.models import LlmFunctionalAssessment

        with self.assertRaises(ValidationError):
            LlmFunctionalAssessment(
                chunk_id="c1",
                rationale="This rationale is intentionally long enough to validate.",
                utility_score=8,
                recommendation="include",
            )

    def test_functional_assessment_batch_requires_at_least_one_assessment(self) -> None:
        from literature_review.models import LlmFunctionalAssessmentBatch

        with self.assertRaises(ValidationError):
            LlmFunctionalAssessmentBatch(assessments=[])

    def test_paper_assessment_rejects_retired_consider_recommendation(self) -> None:
        with self.assertRaises(ValidationError):
            PaperAssessment(
                paper_id="example",
                utility_score=7.0,
                recommendation="consider",
                rationale="This rationale is intentionally long enough to validate.",
            )

    def test_functional_scoring_policy_defaults(self) -> None:
        from literature_review.models import FunctionalScoringPolicy

        policy = FunctionalScoringPolicy()
        self.assertEqual(policy.batch_size, 8)
        self.assertEqual(policy.top_chunks_per_paper, 2)
        self.assertEqual(policy.n_first_round, 2)
        self.assertEqual(policy.n_follow_up, 1)
        self.assertEqual(policy.threshold, 6.0)
        self.assertEqual(policy.min_words, 4)

    def test_functional_paper_score_round_trip(self) -> None:
        from literature_review.models import FunctionalPaperScore

        score = FunctionalPaperScore(
            paper_id="p1",
            utility_score=7.5,
            n_samples=2,
            evidence=[
                EvidenceCitation(
                    chunk_id="p1-p1-1-c1",
                    rationale="This rationale is intentionally long enough to validate.",
                    utility_score=8,
                )
            ],
        )

        restored = FunctionalPaperScore.model_validate_json(score.model_dump_json())

        self.assertEqual(restored, score)

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
    return PaperSummaryClaim(claim_id="claim-1", text="A" * 20, aspect="abs", evidence=[chunk_reference()])


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
        supporting_claim_ids=["claim-1"],
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
                    relevance_score=8,
                    evidence_quality_score=8,
                    rationale_relevance="It directly reports a method relevant to the topic.",
                    rationale_quality="The method description is specific and directly supports the claim.",
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
                utility_score=8.0,
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
        self.assertEqual(policy.llm_input_cap, 80)

    def test_synthesis_response_round_trip(self) -> None:
        response = SynthesisResponse(
            paper_sources=[PaperSource(paper_id="p1", source_path="/tmp/test.pdf")],
            evidence_assessment_response=evidence_assessment_response(),
            paper_summaries=[paper_summary()],
            report="X" * 100,
            future_directions=[
                FutureDirection(
                    title="abc",
                    rationale="A" * 20,
                    supporting_paper_ids=["p1"],
                    supporting_chunk_ids=["p1-p1-2-c1"],
                )
            ],
            limitations=[],
            generated_by="deterministic",
        )

        restored = SynthesisResponse.model_validate_json(response.model_dump_json())

        self.assertEqual(restored, response)

    def test_future_direction_supporting_surfaces_require_one(self) -> None:
        # S1: exactly one of claim ids (LLM path) / chunk ids (deterministic
        # path) must be non-empty; a direction with neither is rejected.
        with self.assertRaises(ValidationError):
            FutureDirection(title="abc", rationale="A" * 20, supporting_paper_ids=["p1"])

        claim_direction = FutureDirection(
            title="abc",
            rationale="A" * 20,
            supporting_paper_ids=["p1"],
            supporting_claim_ids=["claim-1"],
        )
        self.assertEqual(claim_direction.supporting_claim_ids, ["claim-1"])
        self.assertEqual(claim_direction.supporting_chunk_ids, [])

        chunk_direction = FutureDirection(
            title="abc",
            rationale="A" * 20,
            supporting_paper_ids=["p1"],
            supporting_chunk_ids=["p1-p2-2-c1"],
        )
        self.assertIsNone(chunk_direction.supporting_claim_ids)
        self.assertEqual(chunk_direction.supporting_chunk_ids, ["p1-p2-2-c1"])

    def test_evidence_aggregation_policy_expanded(self) -> None:
        policy = EvidenceAggregationPolicy()

        self.assertEqual(policy.include_relevance_score, 8)
        self.assertEqual(policy.include_evidence_quality_score, 6)
        self.assertEqual(policy.consider_relevance_score, 6)
        self.assertEqual(policy.prior_score, 5.5)
        self.assertEqual(policy.shrinkage_strength, 1)

    def test_assessment_policy_new_defaults(self) -> None:
        policy = AssessmentPolicy()

        self.assertEqual(policy.include_relevance_score, 8)
        self.assertEqual(policy.include_evidence_quality_score, 6)
        self.assertEqual(policy.consider_relevance_score, 6)

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
                FutureDirection(
                    title="abc",
                    rationale="A" * 20,
                    supporting_paper_ids=["p1"],
                    supporting_claim_ids=["claim-1"],
                )
            ],
            limitations=[],
            generated_by="deterministic",
        )

        self.assertIn("[chunk-id]", response.report)

    # -- C2c: claim-level synthesis contract --------------------------------

    def test_llm_synthesis_direction_requires_supporting_claim_ids(self) -> None:
        with self.assertRaises(ValidationError):
            LlmSynthesisDirection(
                title="abc",
                rationale="A" * 20,
                supporting_claim_ids=[],
            )

    def test_llm_synthesis_direction_round_trip_claim_ids(self) -> None:
        direction = llm_synthesis_direction()

        restored = LlmSynthesisDirection.model_validate_json(direction.model_dump_json())

        self.assertEqual(restored, direction)
        self.assertEqual(restored.supporting_claim_ids, ["claim-1"])

    def test_synthesis_response_claim_chunks_round_trip(self) -> None:
        response = SynthesisResponse(
            paper_sources=[PaperSource(paper_id="p1", source_path="/tmp/test.pdf")],
            evidence_assessment_response=evidence_assessment_response(),
            paper_summaries=[paper_summary()],
            claim_chunks={"claim-1": ["p1-p1-2-c1"]},
            report="[claim-1] " + "X" * 100,
            future_directions=[
                FutureDirection(
                    title="abc",
                    rationale="A" * 20,
                    supporting_paper_ids=["p1"],
                    supporting_claim_ids=["claim-1"],
                )
            ],
            limitations=[],
            generated_by="deterministic",
        )

        restored = SynthesisResponse.model_validate_json(response.model_dump_json())

        self.assertEqual(restored, response)
        self.assertEqual(restored.claim_chunks, {"claim-1": ["p1-p1-2-c1"]})

    def test_synthesis_response_claim_chunks_defaults_empty(self) -> None:
        response = SynthesisResponse(
            paper_sources=[PaperSource(paper_id="p1", source_path="/tmp/test.pdf")],
            evidence_assessment_response=evidence_assessment_response(),
            paper_summaries=[paper_summary()],
            report="X" * 100,
            future_directions=[
                FutureDirection(
                    title="abc",
                    rationale="A" * 20,
                    supporting_paper_ids=["p1"],
                    supporting_chunk_ids=["p1-p1-2-c1"],
                )
            ],
            limitations=[],
            generated_by="deterministic",
        )

        self.assertEqual(response.claim_chunks, {})


if __name__ == "__main__":
    unittest.main()
