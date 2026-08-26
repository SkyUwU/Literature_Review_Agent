import contextlib
import io
import json
import re
import tempfile
import unittest
from pathlib import Path

from literature_review.models import ChunkPolicy, EvidenceRetrievalPolicy, FullTextDocument, PageText
from literature_review.pipeline import expand_pdf_inputs, run_evidence_pipeline, run_synthesis_pipeline


class FakeClient:
    def generate_json(self, _prompt: str) -> str:
        return json.dumps(
            {
                "assessments": [
                    {
                        "chunk_id": "paper-1-p1-1-c1",
                        "summary": "This chunk provides evidence relevant to the literature review agent query.",
                        "relevance_score": 5,
                        "evidence_quality_score": 3,
                        "recommendation": "include",
                        "rationale": "The chunk directly discusses the requested literature review agent topic.",
                    }
                ]
            }
        )


class PipelineTests(unittest.TestCase):
    def test_runs_full_flow_with_fake_client(self) -> None:
        document = FullTextDocument(
            paper_id="paper-1",
            source_path="data/papers/paper-1.pdf",
            extraction_method="test",
            pages=[
                PageText(
                    page_number=1,
                    text=" ".join(["literature", "review", "agent", "evidence"] * 20),
                )
            ],
        )

        result = run_evidence_pipeline(
            document,
            "literature review agent",
            FakeClient(),
            chunk_policy=ChunkPolicy(max_words=50, overlap_words=10),
            retrieval_policy=EvidenceRetrievalPolicy(top_k=1),
        )

        self.assertEqual(result.summaries[0].paper_id, "paper-1")
        self.assertEqual(result.summaries[0].page_start, 1)


PAPER_TEXT = (
    "Abstract We study evidence selection for review agents with page-level provenance. "
    "Method We split each paper into overlapping chunks and rank them lexically before summarization. "
    "Results The lexical baseline retrieves relevant chunks across three benchmarks. "
    "Limitations Our evaluation remains restricted to English computer science papers."
)

DIRECTION_PAYLOAD = {
    "title": "Harden multilingual evaluation coverage",
    "rationale": "Both studies state evaluation restrictions that motivate broader multilingual benchmarks.",
    "supporting_paper_ids": ["paper-2"],
    "supporting_chunk_ids": ["paper-2-p1-1-c1"],
}


def make_document(paper_id: str, text: str) -> FullTextDocument:
    return FullTextDocument(
        paper_id=paper_id,
        source_path=f"data/papers/{paper_id}.pdf",
        extraction_method="test",
        pages=[PageText(page_number=1, text=text)],
    )


def assessment_payload(chunk_id: str) -> dict[str, object]:
    return {
        "chunk_id": chunk_id,
        "summary": f"The chunk {chunk_id} provides relevant evidence about review agents.",
        "relevance_score": 5,
        "evidence_quality_score": 3,
        "recommendation": "include",
        "rationale": "The chunk directly discusses the requested literature review agent topic.",
    }


def note_payload(paper_id: str) -> dict[str, object]:
    return {
        "claims": [
            {
                "text": f"The study in {paper_id} reports evidence selection results for review agents.",
                "chunk_ids": [f"{paper_id}-p1-1-c1"],
                "aspect": "contribution",
            }
        ],
        "stated_limitations": [],
    }


class SynthesisFakeClient:
    """Answers the three prompt kinds produced by the synthesis workflow."""

    def __init__(self) -> None:
        self.prompts: list[str] = []
        self.cited_chunk_ids: list[str] = []

    def generate_json(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if prompt.startswith("Summarize this single paper"):
            for paper_id in ("paper-1", "paper-2"):
                if f"Paper ID: {paper_id}\n" in prompt:
                    return json.dumps(note_payload(paper_id))
        if prompt.startswith("Write a fluent literature-review"):
            report = (
                "# Evidence-cited synthesis\n"
                + "".join(
                    f"The reviewed study {paper} supplies retrieved evidence for its claims "
                    f"in [{chunk_id}].\n"
                    for paper, chunk_id in zip(
                        ("paper-1", "paper-2"), self.cited_chunk_ids, strict=False
                    )
                )
                + "\n## 材料來源清單\n- cited chunk identifiers appear inline above\n"
            )
            return json.dumps({"report": report, "future_directions": [DIRECTION_PAYLOAD]})
        chunk_ids = re.findall(r'"chunk_id": "([^"]+)"', prompt)
        self.cited_chunk_ids.extend(chunk_ids)
        return json.dumps({"assessments": [assessment_payload(chunk_id) for chunk_id in chunk_ids]})


class ExpandPdfInputsTests(unittest.TestCase):
    def test_expand_pdf_inputs_directory(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "b.pdf").write_bytes(b"%PDF-")
            (root / "a.pdf").write_bytes(b"%PDF-")
            (root / "notes.txt").write_text("ignored")

            result = expand_pdf_inputs([folder])

            self.assertEqual(result, [str(root / "a.pdf"), str(root / "b.pdf")])

    def test_expand_pdf_inputs_mixed_dedup(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "a.pdf").write_bytes(b"%PDF-")

            result = expand_pdf_inputs([str(root / "a.pdf"), folder])

            self.assertEqual(result, [str(root / "a.pdf")])

    def test_expand_pdf_inputs_empty(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(ValueError):
                expand_pdf_inputs([folder])


class SynthesisPipelineTests(unittest.TestCase):
    def policy_arguments(self) -> dict[str, object]:
        return {
            "chunk_policy": ChunkPolicy(max_words=60, overlap_words=10),
            "retrieval_policy": EvidenceRetrievalPolicy(top_k=3),
        }

    def test_synthesis_pipeline_happy(self) -> None:
        result = run_synthesis_pipeline(
            [make_document("paper-1", PAPER_TEXT), make_document("paper-2", PAPER_TEXT)],
            "literature review agent",
            SynthesisFakeClient(),
            **self.policy_arguments(),
        )

        self.assertEqual(result.generated_by, "llm")
        self.assertEqual(len(result.paper_summaries), 2)
        self.assertEqual({note.paper_id for note in result.paper_summaries}, {"paper-1", "paper-2"})
        self.assertEqual(len(result.paper_sources), 2)
        self.assertEqual(
            {source.source_path for source in result.paper_sources},
            {"data/papers/paper-1.pdf", "data/papers/paper-2.pdf"},
        )
        self.assertGreaterEqual(len(result.future_directions), 1)
        markers = re.findall(r"\[([^\[\]]+)\]", result.report)
        self.assertGreaterEqual(len(markers), 1)
        supplied_ids = {
            citation.chunk_id
            for assessment in result.evidence_assessment_response.assessments
            for citation in assessment.evidence
        } | {chunk_id for note in result.paper_summaries for chunk_id in note.coverage_chunk_ids}
        self.assertTrue(set(markers).issubset(supplied_ids))

    def test_synthesis_pipeline_same_paper_id(self) -> None:
        with self.assertRaises(ValueError):
            run_synthesis_pipeline(
                [make_document("paper-1", PAPER_TEXT), make_document("paper-1", PAPER_TEXT)],
                "literature review agent",
                SynthesisFakeClient(),
                **self.policy_arguments(),
            )

    def test_synthesis_pipeline_empty_document(self) -> None:
        empty_document = make_document("empty-paper", "singleword secondword thirdword")
        with contextlib.redirect_stderr(io.StringIO()) as stderr:
            result = run_synthesis_pipeline(
                [empty_document, make_document("paper-2", PAPER_TEXT)],
                "literature review agent",
                SynthesisFakeClient(),
                **self.policy_arguments(),
            )

        self.assertIn("empty-paper", stderr.getvalue())
        self.assertEqual([note.paper_id for note in result.paper_summaries], ["paper-2"])
        self.assertEqual([source.paper_id for source in result.paper_sources], ["paper-2"])
