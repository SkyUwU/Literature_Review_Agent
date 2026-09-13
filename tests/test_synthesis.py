import json
import re
import unittest

# allow: SIZE_OK — Assignment requires appending the LLM-path cases to this exact
# existing test module without modifying or relocating the pre-existing test cases.

from literature_review.models import (
    ChunkReference,
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
    PaperSummaryClaim,
)
from literature_review.coverage import classify_chunk, drop_noise_sections
from literature_review.synthesis import (
    SynthesisError,
    _bounded_chunks_for_llm,
    _group_chunks_by_section,
    build_claim_chunks,
    build_coverage_packs,
    build_deterministic_paper_notes,
    build_deterministic_synthesis,
    build_paper_notes_prompt,
    detect_limitation_chunks,
    merge_numbered_section,
    summarize_paper_notes,
    synthesize_report,
    top_level_section,
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


def chapter_chunks(paper_id: str) -> list[EvidenceChunk]:
    """Chunks carrying a markdown heading path (C2a ``_heading_path`` shape).

    Section paths use ``"Title"`` as the placeholder document title, so tests
    pass ``paper_title="Title"`` to exercise the title-skip signal.
    """
    paths_and_pages = [
        ("Title > Abstract", 1, "Abstract This paper studies evidence-cited literature review agents."),
        ("Title > 1 Introduction", 1, "Introduction Prior systems lack page-level provenance for claims."),
        ("Title > 1 Introduction", 1, "Introduction More context that pads the introduction section text."),
        ("Title > 2 Method", 2, "Method We group chunks by their top-level section before scoring."),
        ("Title > 2 Method > 2.1 Ranking", 2, "Method detail Ranking orders chunks within the method section."),
        ("Title > 3 Experiments", 3, "Experiments We evaluate coverage across three public benchmarks."),
        ("Title > 4 Results", 3, "Results Section-first sampling keeps every chapter represented."),
        ("Title > 5 Limitations", 4, "Limitations Our evaluation is restricted to English papers only."),
    ]
    return [
        EvidenceChunk(
            chunk_id=f"{paper_id}-c{number}",
            paper_id=paper_id,
            page_start=page,
            page_end=page,
            section=path,
            text=text,
        )
        for number, (path, page, text) in enumerate(paths_and_pages, start=1)
    ]


def make_assessment(paper_id: str, utility_score: int) -> PaperAssessment:
    return PaperAssessment(
        paper_id=paper_id,
        utility_score=utility_score,
        recommendation="include",
        rationale=f"Evidence-based aggregate for {paper_id} derived from the supplied retrieved chunks.",
        evidence=[
            EvidenceCitation(
                chunk_id=f"{paper_id}-p2-2-c1",
                page_start=2,
                page_end=2,
                rationale=f"Trusted fixture rationale for one chunk of {paper_id} with sufficient length.",
                utility_score=8,
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
            summary=citation.rationale,
            relevance_score=citation.utility_score,
            evidence_quality_score=citation.utility_score,
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
            [make_assessment("p1", 7), make_assessment("p2", 8)]
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
            utility_score=2.0,
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
        "supporting_claim_ids": ["claim-4"],
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
        report = (
            "# Evidence-cited synthesis\n"
            "The reviewed study proposes evidence-cited synthesis for literature review agents "
            "[claim-2]. Its template pipeline composes paragraphs from bounded retrieved chunks "
            "[claim-1].\n\n"
        )

        result = synthesize_report(response, packs, [note], FakeSynthesisClient(report, [valid_direction_payload()]))

        self.assertEqual(result.generated_by, "llm")
        self.assertNotIn("材料來源清單", result.report)
        markers = re.findall(r"\[([^\[\]]+)\]", result.report)
        self.assertGreaterEqual(len(markers), 2)
        self.assertTrue(set(markers).issubset(set(result.claim_chunks)))
        direction = result.future_directions[0]
        self.assertEqual(direction.supporting_claim_ids, ["claim-4"])
        self.assertEqual(direction.supporting_chunk_ids, [])
        self.assertEqual(
            result.claim_chunks,
            {
                "claim-1": ["p1-p1-1-c1"],
                "claim-2": ["p1-p2-2-c2"],
                "claim-3": ["p1-p3-3-c3"],
                "claim-4": ["p1-p4-4-c4"],
            },
        )
        self.assertEqual([item.paper_id for item in result.paper_summaries], ["p1"])
        self.assertEqual(result.paper_sources[0].paper_id, "p1")

    def test_synthesize_report_unknown_marker(self) -> None:
        response, packs, note = llm_synthesis_fixture()
        report = (
            "The reviewed study proposes evidence-cited synthesis, and one sentence cites an identifier "
            "that was never supplied to the model [unknown-id], so validation must reject it."
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

    def test_synthesize_report_unknown_claim_marker_repair_retry(self) -> None:
        response, packs, note = llm_synthesis_fixture()
        good_report = (
            "The reviewed study proposes evidence-cited synthesis for literature review agents "
            "[claim-1]. A second grounded sentence keeps the report long enough to validate."
        )
        bad_report = (
            "The reviewed study proposes evidence-cited synthesis for literature review agents "
            "[claim-99]. A second grounded sentence keeps the report long enough to validate."
        )

        class RepairingClient:
            def __init__(self) -> None:
                self.prompts: list[str] = []
                self.responses: list[dict[str, object]] = [
                    {"report": bad_report, "future_directions": [valid_direction_payload()]},
                    {"report": good_report, "future_directions": [valid_direction_payload()]},
                ]

            def generate_json(self, prompt: str, schema: dict | None = None) -> str:
                self.prompts.append(prompt)
                return json.dumps(self.responses.pop(0))

        client = RepairingClient()

        result = synthesize_report(response, packs, [note], client)

        self.assertEqual(len(client.prompts), 2)
        self.assertIn("claim-99", client.prompts[1])
        direction = result.future_directions[0]
        self.assertEqual(direction.supporting_claim_ids, ["claim-4"])
        self.assertEqual(direction.supporting_chunk_ids, [])


class SectionClassificationTests(unittest.TestCase):
    """classify_chunk: section heading first, legacy text fallback."""

    def _chunk(self, section: str | None, text: str = "A sufficiently long chunk text for the section classification test.") -> EvidenceChunk:
        return EvidenceChunk(chunk_id="p1-c1", paper_id="p1", section=section, text=text)

    def test_recognized_sections_map_to_priorities(self) -> None:
        cases = [
            ("**Paper** > **7 Method**", (7, "method")),
            ("**Paper** > **4 Experiments**", (5, "experiments")),
            ("**Paper** > **2 Limitations**", (2, "limitations")),
            ("**Paper** > **References**", (14, "references")),
            ("**Paper** > **Bibliography**", (14, "references")),
            ("**Paper** > **Acknowledgements**", (14, "acknowledgments")),
            ("**Paper** > **1 Abstract**", (1, "abstract")),
            ("**Paper** > **Appendix B**", (13, "appendix")),
        ]
        for section, expected in cases:
            with self.subTest(section=section):
                self.assertEqual(classify_chunk(self._chunk(section)), expected)

    def test_unrecognized_section_falls_to_other(self) -> None:
        self.assertEqual(
            classify_chunk(self._chunk("**Paper** > **12 Case Studies**")),
            (12, "other"),
        )

    def test_no_section_uses_legacy_text_classification(self) -> None:
        self.assertEqual(
            classify_chunk(self._chunk(None, "Method We rank bounded evidence chunks deterministically for the review.")),
            (7, "method"),
        )
        self.assertEqual(
            classify_chunk(self._chunk(None, "Plain paragraph with no recognizable section header inside it at all.")),
            (12, "other"),
        )


class DropNoiseSectionsTests(unittest.TestCase):
    """Blacklist filter for chapter-aware chunks."""

    def _chunk(self, paper_id: str, section: str | None) -> EvidenceChunk:
        return EvidenceChunk(
            chunk_id=f"{paper_id}-c1",
            paper_id=paper_id,
            section=section,
            text="A sufficiently long chunk text for the noise filter classification test.",
        )

    def test_drops_references_acknowledgments_and_appendix(self) -> None:
        chunks = [
            self._chunk("p1", "**Paper** > **3 Method**"),
            self._chunk("p2", "**Paper** > **References**"),
            self._chunk("p3", "**Paper** > **Acknowledgements**"),
            self._chunk("p4", "**Paper** > **Appendix A**"),
        ]

        kept = drop_noise_sections(chunks)

        self.assertEqual([chunk.paper_id for chunk in kept], ["p1"])

    def test_keeps_all_core_and_other_sections(self) -> None:
        chunks = [
            self._chunk("p1", "**Paper** > **1 Introduction**"),
            self._chunk("p2", "**Paper** > **4 Results**"),
            self._chunk("p3", "**Paper** > **9 Future Work**"),
            self._chunk("p4", "**Paper** > **12 Survey of Tools**"),
        ]

        kept = drop_noise_sections(chunks)

        self.assertEqual([chunk.paper_id for chunk in kept], ["p1", "p2", "p3", "p4"])

    def test_keeps_chunks_without_section(self) -> None:
        chunks = [self._chunk("p1", None)]

        kept = drop_noise_sections(chunks)

        self.assertEqual([chunk.paper_id for chunk in kept], ["p1"])

    def test_drop_appendix_false_keeps_appendix(self) -> None:
        chunks = [self._chunk("p1", "**Paper** > **Appendix A**")]

        kept = drop_noise_sections(chunks, drop_appendix=False)

        self.assertEqual([chunk.paper_id for chunk in kept], ["p1"])


# -- C2c: section-aware notes input (Todo 1) ---


class TopLevelSectionTests(unittest.TestCase):
    """top_level_section: layer-independent extraction of the first non-title aspect."""

    def test_multi_level_path_defaults_to_first_section(self) -> None:
        self.assertEqual(
            top_level_section("Title > 2 Related Work > 2.1 Surveys", paper_title="Title"),
            "2 Related Work",
        )
        self.assertEqual(
            top_level_section("Title > 2 Related Work", paper_title="Title"),
            "2 Related Work",
        )

    def test_single_level_path_is_kept(self) -> None:
        self.assertEqual(top_level_section("2 Related Work"), "2 Related Work")
        self.assertEqual(top_level_section("1 Introduction"), "1 Introduction")

    def test_numbered_segments_never_skipped(self) -> None:
        self.assertEqual(top_level_section("1 Introduction > 1.1 Background"), "1 Introduction")

    def test_paper_title_signal_skips_title_segment(self) -> None:
        # ① 正規化比對:相等
        self.assertEqual(
            top_level_section(
                "AutoGen: Enabling Multi-Agent Conversation > 2 Related Work",
                paper_title="AutoGen: Enabling Multi-Agent Conversation",
            ),
            "2 Related Work",
        )
        # ① 正規化比對:paper title 為 segment 開頭(受截斷)
        self.assertEqual(
            top_level_section(
                "AutoGen: Enabling > 3 Experiments",
                paper_title="AutoGen: Enabling Multi-Agent Conversation",
            ),
            "3 Experiments",
        )

    def test_position_signal_skips_unnumbered_first_segment(self) -> None:
        # 無 paper_title 時,多層路徑首段(位置②)判為標題
        self.assertEqual(top_level_section("Title > 2 Related Work"), "2 Related Work")
        # 單層無編號不跳(誤判偏向當章節)
        self.assertEqual(top_level_section("Related Work"), "Related Work")

    def test_title_only_path_returns_none(self) -> None:
        self.assertIsNone(
            top_level_section(
                "AutoGen: Enabling Multi-Agent Conversation",
                paper_title="AutoGen: Enabling Multi-Agent Conversation",
            )
        )
        self.assertIsNone(top_level_section("Title", paper_title="Title"))
        self.assertIsNone(top_level_section(None))
        self.assertIsNone(top_level_section(""))

    def test_mixed_levels_are_consistent(self) -> None:
        # 部分章節有小節、部分無 → 一致取第一段
        self.assertEqual(
            top_level_section("Title > 2 Method > 2.1 Ranking", paper_title="Title"),
            "2 Method",
        )
        self.assertEqual(top_level_section("Title > 2 Method", paper_title="Title"), "2 Method")

    def test_strips_markdown_bold_markers(self) -> None:
        self.assertEqual(
            top_level_section("**Title** > **7 Method**", paper_title="Title"),
            "7 Method",
        )


class MergeNumberedSectionTests(unittest.TestCase):
    """merge_numbered_section: dotted sub-headings collapse to their top number (S5)."""

    def test_dotted_numbered_aspects_collapse_to_top_level_number(self) -> None:
        self.assertEqual(merge_numbered_section("2.1 Multi-Agent"), "2")
        self.assertEqual(merge_numbered_section("2.1.3 X"), "2")

    def test_single_numbered_and_unnumbered_aspects_are_kept(self) -> None:
        self.assertEqual(merge_numbered_section("1 Introduction"), "1 Introduction")
        self.assertEqual(merge_numbered_section("Background"), "Background")
        self.assertEqual(merge_numbered_section("2 Method"), "2 Method")

    def test_group_chunks_by_section_merges_dotted_subheadings(self) -> None:
        # 層級壓平(全部 #)時「2.1/2.2」小節 → 合併成同一個 aspect「2」
        chunks = [
            make_chunk("p1", 1, 1, "Surveys under the second chapter keep the fragment aspect stable."),
            make_chunk("p1", 2, 2, "Benchmarks under the second chapter stay in the same aspect group."),
        ]
        chunks[0].section = "2.1 Surveys"
        chunks[1].section = "2.2 Benchmarks"

        groups = _group_chunks_by_section(chunks)

        self.assertEqual(list(groups), ["2"])
        self.assertEqual(len(groups["2"]), 2)


class SectionAwareNotesInputTests(unittest.TestCase):
    """Section grouping and cap behavior of the per-paper note input."""

    def _ids(self, chunks: list[EvidenceChunk]) -> list[str]:
        return [chunk.chunk_id for chunk in chunks]

    def test_group_chunks_by_section(self) -> None:
        groups = _group_chunks_by_section(chapter_chunks("p1"), paper_title="Title")

        self.assertEqual(
            list(groups),
            [
                "Abstract",
                "1 Introduction",
                "2 Method",
                "3 Experiments",
                "4 Results",
                "5 Limitations",
            ],
        )
        self.assertEqual(self._ids(groups["1 Introduction"]), ["p1-c2", "p1-c3"])
        self.assertEqual(self._ids(groups["2 Method"]), ["p1-c4", "p1-c5"])

    def test_legacy_chunks_group_under_other(self) -> None:
        legacy = [
            make_chunk("p1", 1, 1, "Abstract legacy text without a heading path."),
            make_chunk("p1", 2, 2, "Method legacy paragraph without a heading path."),
        ]

        groups = _group_chunks_by_section(legacy)

        self.assertEqual(list(groups), ["other"])
        self.assertEqual(len(groups["other"]), 2)

    def test_cap_all_kept_when_under_cap(self) -> None:
        chunks = chapter_chunks("p1")
        self.assertEqual(len(chunks), 8)

        selected = _bounded_chunks_for_llm(chunks, 80, paper_title="Title")

        self.assertEqual(self._ids(selected), self._ids(chunks))

    def test_cap_keeps_every_section_opener(self) -> None:
        chunks = chapter_chunks("p1")

        selected = _bounded_chunks_for_llm(chunks, 5, paper_title="Title")

        self.assertEqual(
            self._ids(selected),
            ["p1-c1", "p1-c2", "p1-c4", "p1-c6", "p1-c7"],
        )
        self.assertNotIn("p1-c8", self._ids(selected))

    def test_cap_round_robin_extra_across_sections(self) -> None:
        chunks = chapter_chunks("p1")

        selected = _bounded_chunks_for_llm(chunks, 7, paper_title="Title")

        # 6 節 opener + 1 extra(第一個有剩餘的節 = Introduction)
        self.assertEqual(
            self._ids(selected),
            ["p1-c1", "p1-c2", "p1-c3", "p1-c4", "p1-c6", "p1-c7", "p1-c8"],
        )

    def test_prompt_groups_chunks_by_section(self) -> None:
        chunks = chapter_chunks("p1")

        prompt = build_paper_notes_prompt("p1", chunks, paper_title="Title")

        self.assertIn("Cover every section", prompt)
        self.assertIn("Never use a subsection name", prompt)
        for chunk in chunks:
            self.assertIn(chunk.chunk_id, prompt)
        self.assertNotIn("sections_by_aspect", prompt)
        self.assertNotIn("contribution", prompt)  # _NOTE_ASPECTS 白名單退役

    def test_prompt_groups_by_top_level_not_subsection(self) -> None:
        chunks = chapter_chunks("p1")

        prompt = build_paper_notes_prompt("p1", chunks, paper_title="Title")

        # 頂層章節才列為 aspect 選項,小節名(2.1)不出現在 section_titles
        self.assertIn('"2 Method"', prompt)
        self.assertNotIn("2.1", prompt)


class SectionAwareFakeE2ETests(unittest.TestCase):
    """Fake client e2e: every section is covered by at least one claim."""

    def test_every_section_has_a_claim(self) -> None:
        chunks = chapter_chunks("p1")
        payload = {
            "claims": [
                {
                    "text": "The abstract studies evidence-cited literature review agents.",
                    "chunk_ids": ["p1-c1"],
                    "aspect": "Abstract",
                },
                {
                    "text": "The introduction motivates page-level claim provenance.",
                    "chunk_ids": ["p1-c2"],
                    "aspect": "1 Introduction",
                },
                {
                    "text": "The method groups chunks by top-level section before scoring.",
                    "chunk_ids": ["p1-c4"],
                    "aspect": "2 Method",
                },
                {
                    "text": "Experiments evaluate coverage across three public benchmarks.",
                    "chunk_ids": ["p1-c6"],
                    "aspect": "3 Experiments",
                },
                {
                    "text": "Results show every chapter stays represented after sampling.",
                    "chunk_ids": ["p1-c7"],
                    "aspect": "4 Results",
                },
                {
                    "text": "Limitations restrict the evaluation to English papers only.",
                    "chunk_ids": ["p1-c8"],
                    "aspect": "5 Limitations",
                },
            ],
        }
        client = FakeNoteClient(payload)

        note = summarize_paper_notes(
            "p1", chunks, client, CoveragePackPolicy(), paper_title="Title"
        )

        self.assertEqual(
            [claim.aspect for claim in note.claims],
            [
                "Abstract",
                "1 Introduction",
                "2 Method",
                "3 Experiments",
                "4 Results",
                "5 Limitations",
            ],
        )
        self.assertEqual(
            note.coverage_chunk_ids,
            ["p1-c1", "p1-c2", "p1-c4", "p1-c6", "p1-c7", "p1-c8"],
        )


# -- C2c Todo 2: claim→chunks 機械展開 --


def make_note(paper_id: str, claim_chunks: list[list[str]]) -> PaperSummary:
    claims = [
        PaperSummaryClaim(
            text=f"Claim {index} of {paper_id} backed by bounded retrieved evidence chunks.",
            aspect="contribution",
            evidence=[
                ChunkReference(
                    chunk_id=chunk_id,
                    paper_id=paper_id,
                    page_start=1,
                    page_end=1,
                    quote="A sufficiently long quoted evidence span supporting the claim.",
                )
                for chunk_id in chunks
            ],
        )
        for index, chunks in enumerate(claim_chunks, start=1)
    ]
    coverage_chunk_ids = sorted(
        {reference.chunk_id for claim in claims for reference in claim.evidence}
    )
    return PaperSummary(
        paper_id=paper_id,
        claims=claims,
        coverage_chunk_ids=coverage_chunk_ids,
    )


class ClaimChunksTests(unittest.TestCase):
    """C2c Todo 2: global claim-1..N numbering and the claim→chunks table."""

    def test_global_numbering_contiguous_across_papers(self) -> None:
        table = build_claim_chunks(
            [
                make_note("p-a", [["p-a-c1"], ["p-a-c2", "p-a-c3"]]),
                make_note("p-b", [["p-b-c4"]]),
            ]
        )

        self.assertEqual(list(table), ["claim-1", "claim-2", "claim-3"])
        self.assertEqual(table["claim-1"], ["p-a-c1"])
        self.assertEqual(table["claim-2"], ["p-a-c2", "p-a-c3"])
        self.assertEqual(table["claim-3"], ["p-b-c4"])

    def test_multi_chunk_claim_sorted_and_deduplicated(self) -> None:
        table = build_claim_chunks([make_note("p-a", [["p-a-c3", "p-a-c1", "p-a-c1"]])])

        self.assertEqual(table["claim-1"], ["p-a-c1", "p-a-c3"])

    def test_each_claim_maps_to_non_empty_chunks(self) -> None:
        table = build_claim_chunks([make_note("p-a", [["p-a-c1"], ["p-a-c2"]])])

        self.assertTrue(all(table[claim_id] for claim_id in table))

    def test_empty_summaries_returns_empty_table(self) -> None:
        self.assertEqual(build_claim_chunks([]), {})

    def test_direction_unknown_claim_id_rejected(self) -> None:
        response, packs, note = llm_synthesis_fixture()
        report = (
            "The reviewed study proposes evidence-cited synthesis for literature review agents "
            "[claim-1]. A second grounded sentence keeps the report long enough to validate."
        )
        direction = valid_direction_payload()
        direction["supporting_claim_ids"] = ["claim-99"]

        with self.assertRaises(SynthesisError):
            synthesize_report(
                response, packs, [note], FakeSynthesisClient(report, [direction])
            )


if __name__ == "__main__":
    unittest.main()
