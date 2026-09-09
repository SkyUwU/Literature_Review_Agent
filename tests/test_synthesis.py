import json
import re
import unittest

# allow: SIZE_OK — Assignment requires appending the LLM-path cases to this exact
# existing test module without modifying or relocating the pre-existing test cases.

from literature_review.models import (
    CoveragePackPolicy,
    EvidenceAggregationPolicy,
    EvidenceAssessmentResponse,
    EvidenceChunk,
    EvidenceCitation,
    EvidenceRetrievalPolicy,
    EvidenceRerankResponse,
    EvidenceRetrievalResponse,
    EvidenceSummary,
    PaperAssessment,
    PaperSource,
    PaperSummary,
)
from literature_review.synthesis import (
    SynthesisError,
    build_coverage_packs,
    build_deterministic_paper_notes,
    build_deterministic_synthesis,
    detect_limitation_chunks,
    summarize_paper_notes,
    synthesize_report,
)


def make_chunk(paper_id: str, number: int, page: int, text: str) -> EvidenceChunk:
    return EvidenceChunk(
        chunk_id=f"{paper_id}-p{page}-{page}-c{number}",
        paper_id=paper_id,
        page_start=page,
        page_end=page,
        text=text,
    )


def sectioned_chunks(paper_id: str) -> list[EvidenceChunk]:
    pages_and_texts = [
        (1, "Abstract This paper studies evidence selection for review agents."),
        (1, "Introduction Prior systems summarize papers without page-level provenance."),
        (2, "The corpus contains many noisy full-text pages that need deterministic handling."),
        (2, "Method We split each PDF into overlapping chunks and rank them lexically first."),
        (3, "Experiments We evaluate recall and precision on three scientific benchmarks."),
        (3, "Results The lexical baseline retrieves relevant chunks with high recall."),
        (4, "Discussion The results suggest cheap retrieval covers most sections."),
        (4, "Limitations Our evaluation is restricted to English computer-science papers."),
    ]
    return [
        make_chunk(paper_id, number, page, text)
        for number, (page, text) in enumerate(pages_and_texts, start=1)
    ]


def plain_chunks(paper_id: str, count: int = 4) -> list[EvidenceChunk]:
    return [
        make_chunk(paper_id, number, number, f"Neutral body paragraph {number} describes pipeline parts.")
        for number in range(1, count + 1)
    ]


def compact_sectioned_chunks(paper_id: str) -> list[EvidenceChunk]:
    pages_and_texts = [
        (1, "Abstract We study evidence-cited synthesis for literature review agents."),
        (2, "Method We compose template paragraphs from bounded retrieved chunks."),
        (3, "Results The deterministic report cites every supplied chunk identifier."),
        (4, "Limitations Our pipeline remains insufficient for scanned low-resource papers."),
    ]
    return [
        make_chunk(paper_id, number, page, text)
        for number, (page, text) in enumerate(pages_and_texts, start=1)
    ]


def make_assessment(paper_id: str, relevance_score: int) -> PaperAssessment:
    return PaperAssessment(
        paper_id=paper_id,
        relevance_score=relevance_score,
        evidence_quality_score=4,
        recommendation="include",
        rationale=f"Evidence-based aggregate for {paper_id} derived from the supplied retrieved chunks.",
        evidence=[
            EvidenceCitation(
                chunk_id=f"{paper_id}-p2-2-c1",
                page_start=2,
                page_end=2,
                summary=f"Trusted fixture summary for one chunk of {paper_id} with sufficient length.",
                relevance_score=8,
                evidence_quality_score=6,
            )
        ],
    )


def make_evidence_assessment_response(
    assessments: list[PaperAssessment],
) -> EvidenceAssessmentResponse:
    summaries = [
        EvidenceSummary(
            chunk_id=citation.chunk_id,
            paper_id=assessment.paper_id,
            page_start=citation.page_start,
            page_end=citation.page_end,
            summary=citation.summary,
            relevance_score=citation.relevance_score,
            evidence_quality_score=citation.evidence_quality_score,
            rationale_relevance="Fixture relevance rationale long enough to satisfy the schema.",
            rationale_quality="Fixture quality rationale long enough to satisfy the schema.",
        )
        for assessment in assessments
        for citation in assessment.evidence
    ]
    rerank_response = EvidenceRerankResponse(
        retrieval_response=EvidenceRetrievalResponse(
            query="How should literature review evidence be synthesized?",
            policy=EvidenceRetrievalPolicy(top_k=max(1, len(summaries))),
            ranked_chunks=[],
        ),
        summaries=summaries,
        limitations=["Only the retrieved chunks were assessed."],
    )
    return EvidenceAssessmentResponse(
        evidence_rerank_response=rerank_response,
        aggregation_policy=EvidenceAggregationPolicy(),
        assessments=assessments,
        limitations=["Assessments cover only the bounded retrieved evidence."],
    )


class CoveragePackTests(unittest.TestCase):
    def test_coverage_packs_with_sections(self) -> None:
        pack = build_coverage_packs(
            sectioned_chunks("p1"), CoveragePackPolicy(max_chunks_per_paper=6)
        )

        self.assertEqual(list(pack), ["p1"])
        selected = pack["p1"]
        self.assertEqual(
            [chunk.chunk_id for chunk in selected],
            [
                "p1-p1-1-c1",
                "p1-p1-1-c2",
                "p1-p2-2-c4",
                "p1-p3-3-c5",
                "p1-p3-3-c6",
                "p1-p4-4-c8",
            ],
        )
        self.assertEqual([chunk.page_start for chunk in selected], [1, 1, 2, 3, 3, 4])
        self.assertNotIn("p1-p4-4-c7", [chunk.chunk_id for chunk in selected])

    def test_coverage_packs_stride_fallback(self) -> None:
        pack = build_coverage_packs(
            plain_chunks("p2", count=9), CoveragePackPolicy(max_chunks_per_paper=3)
        )

        self.assertEqual(
            [chunk.chunk_id for chunk in pack["p2"]],
            ["p2-p1-1-c1", "p2-p4-4-c4", "p2-p7-7-c7"],
        )
        self.assertEqual(pack["p2"][0].page_start, 1)

    def test_coverage_packs_empty_input_returns_empty_dict(self) -> None:
        self.assertEqual(build_coverage_packs([], CoveragePackPolicy()), {})


def refs_region_chunks(paper_id: str, start_number: int, page: int, header: str) -> list[EvidenceChunk]:
    """Build a references/appendix region: a header chunk followed by an entry chunk."""
    return [
        make_chunk(
            paper_id,
            start_number,
            page,
            f"{header}\n[1] Smith, J. (2020). A survey of evidence selection for review agents.",
        ),
        make_chunk(
            paper_id,
            start_number + 1,
            page,
            "[2] Doe, A. (2021). Another entry about automatic summarization of papers that pads.",
        ),
    ]


class ReferencesCoverageTests(unittest.TestCase):
    def test_t1_references_region_chunks_excluded(self) -> None:
        body = [
            make_chunk("p-t1", 1, 1, "Abstract Body text about evidence selection for review agents."),
            make_chunk("p-t1", 2, 1, "Introduction More body text describing the review agent pipeline."),
            make_chunk("p-t1", 3, 2, "Method We rank bounded evidence chunks deterministically."),
            make_chunk("p-t1", 4, 3, "Results The retrieved scores are plausible and provenance-preserving."),
            make_chunk("p-t1", 5, 4, "Limitations Only bounded evidence was assessed in this study."),
        ]
        refs = refs_region_chunks("p-t1", 6, 5, "References")
        chunks = body + refs

        pack = build_coverage_packs(chunks, CoveragePackPolicy(max_chunks_per_paper=4))

        selected = {chunk.chunk_id for chunk in pack["p-t1"]}
        self.assertEqual(selected, {"p-t1-p1-1-c1", "p-t1-p2-2-c3", "p-t1-p3-3-c4", "p-t1-p4-4-c5"})
        self.assertFalse({chunk.chunk_id for chunk in refs} & selected)

    def test_t2_no_references_regression(self) -> None:
        body = [
            make_chunk("p-t2", 1, 1, "Abstract Body text about evidence selection for review agents."),
            make_chunk("p-t2", 2, 1, "Introduction More body text describing the review agent pipeline."),
            make_chunk("p-t2", 3, 2, "Method We rank bounded evidence chunks deterministically."),
            make_chunk("p-t2", 4, 3, "Results The retrieved scores are plausible and provenance-preserving."),
            make_chunk("p-t2", 5, 4, "Limitations Only bounded evidence was assessed in this study."),
        ]

        pack = build_coverage_packs(body, CoveragePackPolicy(max_chunks_per_paper=4))

        self.assertEqual(len(pack["p-t2"]), 4)

    def test_t3_references_before_appendix(self) -> None:
        body = [
            make_chunk("p-t3", 1, 1, "Abstract Body text about evidence selection for review agents."),
            make_chunk("p-t3", 2, 1, "Introduction More body text describing the review agent pipeline."),
            make_chunk("p-t3", 3, 2, "Method We rank bounded evidence chunks deterministically."),
            make_chunk("p-t3", 4, 3, "Results The retrieved scores are plausible and provenance-preserving."),
            make_chunk("p-t3", 5, 4, "Limitations Only bounded evidence was assessed in this study."),
        ]
        refs = refs_region_chunks("p-t3", 6, 5, "References")
        appendix = refs_region_chunks("p-t3", 8, 6, "Appendix")
        chunks = body + refs + appendix

        pack = build_coverage_packs(chunks, CoveragePackPolicy(max_chunks_per_paper=7))

        selected = {chunk.chunk_id for chunk in pack["p-t3"]}
        expected = {chunk.chunk_id for chunk in body} | {chunk.chunk_id for chunk in appendix}
        self.assertEqual(selected, expected)
        self.assertFalse({chunk.chunk_id for chunk in refs} & selected)

    def test_t4_appendix_before_references(self) -> None:
        body = [
            make_chunk("p-t4", 1, 1, "Abstract Body text about evidence selection for review agents."),
            make_chunk("p-t4", 2, 1, "Introduction More body text describing the review agent pipeline."),
            make_chunk("p-t4", 3, 2, "Method We rank bounded evidence chunks deterministically."),
            make_chunk("p-t4", 4, 3, "Results The retrieved scores are plausible and provenance-preserving."),
            make_chunk("p-t4", 5, 4, "Limitations Only bounded evidence was assessed in this study."),
        ]
        appendix = refs_region_chunks("p-t4", 6, 5, "Appendix")
        refs = refs_region_chunks("p-t4", 8, 6, "References")
        chunks = body + appendix + refs

        pack = build_coverage_packs(chunks, CoveragePackPolicy(max_chunks_per_paper=7))

        selected = {chunk.chunk_id for chunk in pack["p-t4"]}
        expected = {chunk.chunk_id for chunk in body} | {chunk.chunk_id for chunk in appendix}
        self.assertEqual(selected, expected)
        self.assertFalse({chunk.chunk_id for chunk in refs} & selected)

    def test_t5_bibliography_synonym_excluded(self) -> None:
        body = [
            make_chunk("p-t5", 1, 1, "Abstract Body text about evidence selection for review agents."),
            make_chunk("p-t5", 2, 1, "Introduction More body text describing the review agent pipeline."),
            make_chunk("p-t5", 3, 2, "Method We rank bounded evidence chunks deterministically."),
            make_chunk("p-t5", 4, 3, "Results The retrieved scores are plausible and provenance-preserving."),
            make_chunk("p-t5", 5, 4, "Limitations Only bounded evidence was assessed in this study."),
        ]
        refs = refs_region_chunks("p-t5", 6, 5, "Bibliography")
        chunks = body + refs

        pack = build_coverage_packs(chunks, CoveragePackPolicy(max_chunks_per_paper=4))

        self.assertFalse({chunk.chunk_id for chunk in refs} & {c.chunk_id for c in pack["p-t5"]})

    def test_t6_no_section_headers_with_references_fallback(self) -> None:
        body = [
            make_chunk("p-t6", 1, 1, "Neutral body paragraph one describes pipeline parts in detail."),
            make_chunk("p-t6", 2, 1, "Neutral body paragraph two describes pipeline parts in detail."),
            make_chunk("p-t6", 3, 2, "Neutral body paragraph three describes pipeline parts in detail."),
            make_chunk("p-t6", 4, 3, "Neutral body paragraph four describes pipeline parts in detail."),
        ]
        refs = refs_region_chunks("p-t6", 5, 4, "References")
        chunks = body + refs

        pack = build_coverage_packs(chunks, CoveragePackPolicy(max_chunks_per_paper=3))

        selected = {chunk.chunk_id for chunk in pack["p-t6"]}
        self.assertEqual(selected, {"p-t6-p1-1-c1", "p-t6-p1-1-c2", "p-t6-p2-2-c3"})
        self.assertFalse({chunk.chunk_id for chunk in refs} & selected)
        self.assertEqual(pack["p-t6"][0].page_start, 1)


class LimitationDetectionTests(unittest.TestCase):
    def test_detect_limitation_chunks_hit(self) -> None:
        chunks = [
            make_chunk(
                "p1", 1, 4, "The main limitation of our approach is the small evaluation scope."
            )
        ]

        self.assertEqual(
            [chunk.chunk_id for chunk in detect_limitation_chunks(chunks)], ["p1-p4-4-c1"]
        )

    def test_detect_limitation_chunks_miss(self) -> None:
        chunks = [
            make_chunk(
                "p1", 1, 5, "We thoroughly validate every component across three public benchmarks."
            )
        ]

        self.assertEqual(detect_limitation_chunks(chunks), [])


class DeterministicDirectionTests(unittest.TestCase):
    def test_convergence_direction(self) -> None:
        chunks = [*plain_chunks("p1"), *plain_chunks("p2")]
        response = make_evidence_assessment_response(
            [make_assessment("p1", 5), make_assessment("p2", 4)]
        )
        packs = build_coverage_packs(chunks, CoveragePackPolicy())
        sources = [
            PaperSource(paper_id="p1", source_path="data/papers/p1.pdf"),
            PaperSource(paper_id="p2", source_path="data/papers/p2.pdf"),
        ]

        result = build_deterministic_synthesis(response, packs, sources, CoveragePackPolicy())

        self.assertEqual(len(result.future_directions), 1)
        direction = result.future_directions[0]
        self.assertEqual(direction.supporting_paper_ids, ["p1", "p2"])
        self.assertEqual(direction.supporting_chunk_ids, ["p1-p2-2-c1", "p2-p2-2-c1"])

    def test_fallback_direction(self) -> None:
        response = make_evidence_assessment_response([make_assessment("p1", 4)])
        packs = build_coverage_packs(plain_chunks("p1"), CoveragePackPolicy())
        sources = [PaperSource(paper_id="p1", source_path="data/papers/p1.pdf")]

        result = build_deterministic_synthesis(response, packs, sources, CoveragePackPolicy())

        self.assertEqual(len(result.future_directions), 1)
        direction = result.future_directions[0]
        self.assertEqual(direction.supporting_paper_ids, ["p1"])
        self.assertEqual(direction.supporting_chunk_ids, ["p1-p2-2-c1"])

    def test_rejects_synthesis_without_usable_assessments(self) -> None:
        excluded = PaperAssessment(
            paper_id="p1",
            relevance_score=2,
            evidence_quality_score=2,
            recommendation="exclude",
            rationale="This paper was excluded by the aggregation policy thresholds.",
        )
        response = make_evidence_assessment_response([excluded])

        with self.assertRaises(SynthesisError):
            build_deterministic_synthesis(
                response,
                build_coverage_packs(plain_chunks("p1"), CoveragePackPolicy()),
                [PaperSource(paper_id="p1", source_path="data/papers/p1.pdf")],
                CoveragePackPolicy(),
            )


class PaperNotesTests(unittest.TestCase):
    def test_build_deterministic_paper_notes_structure(self) -> None:
        chunks = sectioned_chunks("p1")
        note = build_deterministic_paper_notes(
            chunks, make_assessment("p1", 5), CoveragePackPolicy(max_chunks_per_paper=6)
        )

        self.assertEqual(note.paper_id, "p1")
        self.assertEqual(
            [claim.aspect for claim in note.claims],
            ["abstract", "introduction", "method", "experiments", "results", "limitations"],
        )
        self.assertTrue(all(len(claim.evidence) == 1 for claim in note.claims))
        self.assertEqual(note.claims[0].evidence[0].quote, chunks[0].text[:240])
        limitation_claims = [c for c in note.claims if c.aspect == "limitations"]
        self.assertEqual([claim.aspect for claim in limitation_claims], ["limitations"])
        self.assertEqual(
            note.coverage_chunk_ids,
            sorted(
                {
                    reference.chunk_id
                    for claim in note.claims
                    for reference in claim.evidence
                }
            ),
        )
        self.assertIn("p1-p4-4-c8", note.coverage_chunk_ids)

    def test_build_deterministic_paper_notes_requires_chunks(self) -> None:
        with self.assertRaises(SynthesisError):
            build_deterministic_paper_notes(
                [], make_assessment("p1", 5), CoveragePackPolicy()
            )


class DeterministicSynthesisTests(unittest.TestCase):
    def test_build_deterministic_synthesis_full(self) -> None:
        chunks = compact_sectioned_chunks("p1")
        assessment = make_assessment("p1", 5)
        response = make_evidence_assessment_response([assessment])
        packs = build_coverage_packs(chunks, CoveragePackPolicy())
        sources = [PaperSource(paper_id="p1", source_path="data/papers/p1.pdf")]

        result = build_deterministic_synthesis(response, packs, sources, CoveragePackPolicy())

        serialized = result.model_dump_json()
        self.assertTrue(serialized.startswith("{"))
        self.assertEqual(result.generated_by, "deterministic")
        self.assertEqual([note.paper_id for note in result.paper_summaries], ["p1"])
        self.assertEqual(result.paper_sources[0].source_path, "data/papers/p1.pdf")
        self.assertEqual(result.limitations[0], "Section-sampled coverage, not a full-text reading.")
        self.assertEqual(
            result.limitations[1],
            "Every factual sentence carries an inline [chunk_id] citation; this is not a whole-paper review.",
        )
        self.assertIn("材料來源清單", result.report)
        markers = re.findall(r"\[([^\[\]]+)\]", result.report)
        self.assertGreaterEqual(len(markers), 1)
        supplied = set(result.paper_summaries[0].coverage_chunk_ids) | {
            citation.chunk_id for citation in assessment.evidence
        }
        self.assertTrue(set(markers).issubset(supplied))
        limitation_direction = result.future_directions[0]
        self.assertEqual(limitation_direction.supporting_paper_ids, ["p1"])
        self.assertEqual(limitation_direction.supporting_chunk_ids, ["p1-p4-4-c4"])


class FakeNoteClient:
    def __init__(self, response: dict[str, object]) -> None:
        self.response = response
        self.prompt = ""

    def generate_json(self, prompt: str, schema: dict | None = None) -> str:
        self.prompt = prompt
        return json.dumps(self.response)


class RetryNoteClient:
    def __init__(self, valid_response: dict[str, object]) -> None:
        self.responses = ['{"claims": [', json.dumps(valid_response)]
        self.prompts: list[str] = []

    def generate_json(self, prompt: str, schema: dict | None = None) -> str:
        self.prompts.append(prompt)
        return self.responses.pop(0)


class ChunkRepairNoteClient:
    def __init__(self, bad_response: dict[str, object], valid_response: dict[str, object]) -> None:
        self.responses = [json.dumps(bad_response), json.dumps(valid_response)]
        self.prompts: list[str] = []

    def generate_json(self, prompt: str, schema: dict | None = None) -> str:
        self.prompts.append(prompt)
        return self.responses.pop(0)


class FakeSynthesisClient:
    def __init__(self, report: str, directions: list[dict[str, object]]) -> None:
        self.payload: dict[str, object] = {"report": report, "future_directions": directions}

    def generate_json(self, prompt: str, schema: dict | None = None) -> str:
        return json.dumps(self.payload)


def valid_note_payload() -> dict[str, object]:
    return {
        "claims": [
            {
                "text": "The paper studies retrieval-augmented evidence selection for review agents.",
                "chunk_ids": ["p1-p1-1-c1"],
                "aspect": "contribution",
            },
            {
                "text": "The method splits each PDF into overlapping chunks before ranking them.",
                "chunk_ids": ["p1-p2-2-c4"],
                "aspect": "method",
            },
            {
                "text": "The evaluation is restricted to English computer-science papers only.",
                "chunk_ids": ["p1-p4-4-c8"],
                "aspect": "limitations",
            },
        ],
    }


def valid_direction_payload() -> dict[str, object]:
    return {
        "title": "Harden extraction for scanned low-resource papers",
        "rationale": "The stated limitation on scanned low-resource papers directly motivates extraction research.",
        "supporting_paper_ids": ["p1"],
        "supporting_chunk_ids": ["p1-p4-4-c4"],
    }


def llm_synthesis_fixture() -> tuple[EvidenceAssessmentResponse, dict[str, list[EvidenceChunk]], PaperSummary]:
    chunks = compact_sectioned_chunks("p1")
    assessment = make_assessment("p1", 5)
    response = make_evidence_assessment_response([assessment])
    packs = build_coverage_packs(chunks, CoveragePackPolicy())
    note = build_deterministic_paper_notes(chunks, assessment, CoveragePackPolicy())
    return response, packs, note


class LlmPaperNotesTests(unittest.TestCase):
    def test_llm_notes_happy_path(self) -> None:
        chunks = sectioned_chunks("p1")
        client = FakeNoteClient(valid_note_payload())

        note = summarize_paper_notes("p1", chunks, client, CoveragePackPolicy(max_chunks_per_paper=6))

        for chunk in chunks:
            self.assertIn(chunk.chunk_id, client.prompt)
        self.assertIn("only", client.prompt)
        self.assertEqual(note.paper_id, "p1")
        self.assertEqual([claim.aspect for claim in note.claims], ["contribution", "method", "limitations"])
        self.assertEqual(note.claims[0].evidence[0].chunk_id, "p1-p1-1-c1")
        self.assertEqual(note.claims[0].evidence[0].paper_id, "p1")
        self.assertEqual(note.claims[0].evidence[0].quote, chunks[0].text[:240])
        limitation_claims = [c for c in note.claims if c.aspect == "limitations"]
        self.assertEqual(limitation_claims[0].evidence[0].chunk_id, "p1-p4-4-c8")
        self.assertEqual(note.coverage_chunk_ids, ["p1-p1-1-c1", "p1-p2-2-c4", "p1-p4-4-c8"])

    def test_llm_notes_unknown_chunk_id(self) -> None:
        client = FakeNoteClient(
            {
                "claims": [
                    {
                        "text": "A claim citing an identifier absent from the supplied paper evidence.",
                        "chunk_ids": ["ghost-id"],
                        "aspect": "contribution",
                    }
                ]
            }
        )

        with self.assertRaises(SynthesisError):
            summarize_paper_notes("p1", sectioned_chunks("p1"), client, CoveragePackPolicy())

    def test_llm_notes_hallucinated_chunk_id_repair_retry(self) -> None:
        hallucinated_payload = {
            "claims": [
                {
                    "text": "A claim that mistakenly cites a plausible but nonexistent chunk id.",
                    "chunk_ids": ["p1-p5-5-c16"],
                    "aspect": "contribution",
                }
            ]
        }
        client = ChunkRepairNoteClient(hallucinated_payload, valid_note_payload())

        note = summarize_paper_notes("p1", sectioned_chunks("p1"), client, CoveragePackPolicy())

        self.assertEqual(len(client.prompts), 2)
        self.assertIn("valid chunk_ids", client.prompts[1])
        self.assertIn("p1-p5-5-c16", client.prompts[1])
        self.assertEqual(note.paper_id, "p1")
        self.assertEqual(note.coverage_chunk_ids, ["p1-p1-1-c1", "p1-p2-2-c4", "p1-p4-4-c8"])

    def test_llm_notes_repair_retry(self) -> None:
        client = RetryNoteClient(valid_note_payload())

        note = summarize_paper_notes("p1", sectioned_chunks("p1"), client, CoveragePackPolicy())

        self.assertEqual(len(client.prompts), 2)
        self.assertIn("schema validation errors", client.prompts[1])
        self.assertEqual(note.coverage_chunk_ids, ["p1-p1-1-c1", "p1-p2-2-c4", "p1-p4-4-c8"])


class LlmSynthesisReportTests(unittest.TestCase):
    def test_synthesize_report_happy(self) -> None:
        response, packs, note = llm_synthesis_fixture()
        allowed = {citation.chunk_id for citation in response.assessments[0].evidence} | set(
            note.coverage_chunk_ids
        )
        report = (
            "# Evidence-cited synthesis\n"
            "The reviewed study proposes evidence-cited synthesis for literature review agents "
            "[p1-p2-2-c2]. Its template pipeline composes paragraphs from bounded retrieved chunks "
            "[p1-p1-1-c1].\n\n"
            "## 材料來源清單\n"
            "- p1: notes cover=" + ",".join(sorted(allowed)) + "\n"
        )

        result = synthesize_report(response, packs, [note], FakeSynthesisClient(report, [valid_direction_payload()]))

        self.assertEqual(result.generated_by, "llm")
        self.assertIn("材料來源清單", result.report)
        markers = re.findall(r"\[([^\[\]]+)\]", result.report)
        self.assertGreaterEqual(len(markers), 2)
        self.assertTrue(set(markers).issubset(allowed))
        self.assertEqual(result.future_directions[0].supporting_chunk_ids, ["p1-p4-4-c4"])
        self.assertEqual([item.paper_id for item in result.paper_summaries], ["p1"])
        self.assertEqual(result.paper_sources[0].paper_id, "p1")

    def test_synthesize_report_unknown_marker(self) -> None:
        response, packs, note = llm_synthesis_fixture()
        report = (
            "The reviewed study proposes evidence-cited synthesis, and one sentence cites an identifier "
            "that was never supplied to the model [unknown-id], so validation must reject it.\n\n"
            "材料來源清單\n- p1\n"
        )

        with self.assertRaises(SynthesisError):
            synthesize_report(response, packs, [note], FakeSynthesisClient(report, [valid_direction_payload()]))

    def test_synthesize_report_no_markers(self) -> None:
        response, packs, note = llm_synthesis_fixture()
        report = (
            "The reviewed studies converge on evidence-cited synthesis yet this paragraph deliberately "
            "omits every inline citation marker so validation must reject the whole response outright."
        )

        with self.assertRaises(SynthesisError):
            synthesize_report(response, packs, [note], FakeSynthesisClient(report, [valid_direction_payload()]))


if __name__ == "__main__":
    unittest.main()
