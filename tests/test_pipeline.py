import contextlib
import io
import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import literature_review.pipeline as pipeline_module
from literature_review.models import ChunkPolicy, EvidenceRetrievalPolicy, FullTextDocument, PageText
from literature_review.pipeline import expand_pdf_inputs, run_evidence_pipeline, run_synthesis_pipeline


class FakeClient:
    def generate_json(self, _prompt: str, schema: dict | None = None) -> str:
        return json.dumps(
            {
                "assessments": [
                    {
                        "chunk_id": "1",
                        "summary": "This chunk provides evidence relevant to the literature review agent query.",
                        "relevance_score": 10,
                        "evidence_quality_score": 10,
                        "rationale_relevance": "The chunk directly discusses the requested literature review agent topic.",
                        "rationale_quality": "The chunk supplies concrete evidence with sufficient detail.",
                    }
                ]
            }
        )


class FakeEncoder:
    """Injected encoder so pipeline embedding tests never build the real model."""

    def __call__(self, texts: list[str]) -> list[list[float]]:
        return [[0.1, 0.1 * len(text), 0.2] for text in texts]


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
            encoder=FakeEncoder(),
        )

        self.assertEqual(result.summaries[0].paper_id, "paper-1")
        self.assertEqual(result.summaries[0].page_start, 1)


class EmbeddingPipelineTests(unittest.TestCase):
    def _document(self) -> FullTextDocument:
        return FullTextDocument(
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

    def test_injected_encoder_avoids_real_model_build(self) -> None:
        """Injection must prevent the real default_encoder (model download) from running."""
        with mock.patch(
            "literature_review.embedding_retriever.default_encoder",
            side_effect=AssertionError(
                "default_encoder must not be called when an encoder is injected"
            ),
        ):
            result = run_evidence_pipeline(
                self._document(),
                "literature review agent",
                FakeClient(),
                chunk_policy=ChunkPolicy(max_words=50, overlap_words=10),
                retrieval_policy=EvidenceRetrievalPolicy(top_k=1),
                encoder=FakeEncoder(),
            )

        self.assertEqual(result.summaries[0].paper_id, "paper-1")

    def test_retrieval_path_emits_embedding_rationale(self) -> None:
        """The pipeline's retrieval route produces embedding (semantic) rationale, not lexical terms."""
        from literature_review.models import EvidenceChunk

        chunks = [
            EvidenceChunk(
                chunk_id="paper-1-p1-1-c1",
                paper_id="paper-1",
                page_start=1,
                page_end=1,
                text=" ".join(["literature", "review", "agent", "evidence"] * 20),
            )
        ]
        response = pipeline_module.retrieve_evidence_embedding(
            chunks,
            "literature review agent",
            EvidenceRetrievalPolicy(top_k=1),
            encoder=FakeEncoder(),
        )

        self.assertIn("Semantic retrieval", response.ranked_chunks[0].rationale)
        self.assertEqual(response.ranked_chunks[0].matched_terms, [])


PAPER_TEXT = (
    "Abstract We study evidence selection for review agents with page-level provenance. "
    "Method We split each paper into overlapping chunks and rank them lexically before summarization. "
    "Results The lexical baseline retrieves relevant chunks across three benchmarks. "
    "Limitations Our evaluation remains restricted to English computer science papers."
)

UNRELATED_TEXT = (
    "thermodynamics entropy enthalpy quantum lattice phonon heat capacity "
    "molar mass statistical mechanics"
)

RETAINED_DIRECTION_PAYLOAD = {
    "title": "Harden multilingual evaluation coverage",
    "rationale": "The retained study states evaluation restrictions that motivate broader multilingual benchmarks.",
    "supporting_paper_ids": ["paper-1"],
    "supporting_chunk_ids": ["paper-1-c1"],
}

DIRECTION_PAYLOAD = {
    "title": "Harden multilingual evaluation coverage",
    "rationale": "Both studies state evaluation restrictions that motivate broader multilingual benchmarks.",
    "supporting_paper_ids": ["paper-2"],
    "supporting_chunk_ids": ["paper-2-c1"],
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
        "rationale": f"The chunk {chunk_id} supplies concrete evidence that directly advances the literature review agent research idea.",
        "utility_score": 10,
    }


def note_payload(paper_id: str) -> dict[str, object]:
    return {
        "claims": [
            {
                "text": f"The study in {paper_id} reports evidence selection results for review agents.",
                "chunk_ids": [f"{paper_id}-c1"],
                "aspect": "contribution",
            }
        ],
    }


class SynthesisFakeClient:
    """Answers the three prompt kinds produced by the synthesis workflow."""

    def __init__(self) -> None:
        self.prompts: list[str] = []
        self.cited_chunk_ids: list[str] = []

    def generate_json(self, prompt: str, schema: dict | None = None) -> str:
        self.prompts.append(prompt)
        if prompt.startswith("Summarize this single paper"):
            for paper_id in ("paper-1", "paper-2"):
                if f"Paper ID: {paper_id}\n" in prompt:
                    return json.dumps(note_payload(paper_id))
        if prompt.startswith("Write a fluent literature-review"):
            real_chunk_ids = re.findall(r'"([^"]+-c\d+)"', prompt)
            self.cited_chunk_ids.extend(real_chunk_ids)
            report = (
                "# Evidence-cited synthesis\n"
                + "".join(
                    f"The reviewed study supplies retrieved evidence for its claims "
                    f"in [{chunk_id}].\n"
                    for chunk_id in real_chunk_ids
                )
                + "\n## 材料來源清單\n- cited chunk identifiers appear inline above\n"
            )
            return json.dumps({"report": report, "future_directions": [DIRECTION_PAYLOAD]})
        indexes = re.findall(r"## Chunk (\d+)", prompt)
        return json.dumps({"assessments": [assessment_payload(index) for index in indexes]})


class PaperDropFakeClient(SynthesisFakeClient):
    """Variant whose future direction cites only the retained paper and which
    scores the *unrelated* (thermodynamics) chunks with utility 1, so paper-2
    falls below the functional threshold and is dropped from notes/synthesis."""

    def generate_json(self, prompt: str, schema: dict | None = None) -> str:
        self.prompts.append(prompt)
        if prompt.startswith("Summarize this single paper") and "Paper ID: paper-1\n" in prompt:
            return json.dumps(note_payload("paper-1"))
        if prompt.startswith("Write a fluent literature-review"):
            real_chunk_ids = re.findall(r'"([^"]+-c\d+)"', prompt)
            report = (
                "# Evidence-cited synthesis\n"
                + "".join(
                    f"The reviewed study supplies retrieved evidence for its claims "
                    f"in [{chunk_id}].\n"
                    for chunk_id in real_chunk_ids
                )
                + "\n## 材料來源清單\n- cited chunk identifiers appear inline above\n"
            )
            return json.dumps({"report": report, "future_directions": [RETAINED_DIRECTION_PAYLOAD]})
        blocks = re.findall(
            r"## Chunk (\d+)\nPaper: [^\n]*\| Section: [^\n]*\n(.*?)(?=\n## Chunk |\Z)",
            prompt,
            re.DOTALL,
        )
        return json.dumps(
            {
                "assessments": [
                    {
                        "chunk_id": index,
                        "rationale": "The chunk supplies background context but does not directly advance the literature review agent research idea.",
                        "utility_score": 1 if "thermodynamics" in text else 10,
                    }
                    for index, text in blocks
                ]
            }
        )


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
            "encoder": FakeEncoder(),
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
            for assessment in result.paper_assessments
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

    def test_synthesis_pipeline_functional_single_batch_by_default(self) -> None:
        client = SynthesisFakeClient()
        result = run_synthesis_pipeline(
            [make_document("paper-1", PAPER_TEXT), make_document("paper-2", PAPER_TEXT)],
            "literature review agent",
            client,
            **self.policy_arguments(),
        )

        functional_calls = sum(
            1 for prompt in client.prompts if prompt.startswith("Score each supplied evidence chunk")
        )
        self.assertEqual(functional_calls, 1)  # FUNCTIONAL_BATCH_SIZE=5; 4 sampled chunks fit one batch
        self.assertEqual(len(result.paper_summaries), 2)

    def test_synthesis_pipeline_unrelated_paper_below_threshold_excluded(self) -> None:
        result = run_synthesis_pipeline(
            [make_document("paper-1", PAPER_TEXT), make_document("paper-2", UNRELATED_TEXT)],
            "literature review agent",
            PaperDropFakeClient(),
            chunk_policy=ChunkPolicy(max_words=60, overlap_words=10),
            encoder=FakeEncoder(),
        )

        self.assertEqual(
            [a.paper_id for a in result.paper_assessments],
            ["paper-1"],
        )
        self.assertEqual([note.paper_id for note in result.paper_summaries], ["paper-1"])
        self.assertEqual([source.paper_id for source in result.paper_sources], ["paper-1"])

    def _paced_run(self, notes_pacing_seconds: float) -> tuple[mock.MagicMock, object]:
        with mock.patch("literature_review.pipeline.time.sleep") as sleep:
            result = run_synthesis_pipeline(
                [make_document("paper-1", PAPER_TEXT), make_document("paper-2", PAPER_TEXT)],
                "literature review agent",
                SynthesisFakeClient(),
                notes_pacing_seconds=notes_pacing_seconds,
                **self.policy_arguments(),
            )
        return sleep, result

    def test_notes_pacing_spaces_consecutive_note_calls(self) -> None:
        sleep, result = self._paced_run(4.0)
        self.assertEqual(len(result.paper_summaries), 2)
        sleep.assert_called_once_with(4.0)

    def test_notes_pacing_zero_disables_sleep(self) -> None:
        sleep, result = self._paced_run(0.0)
        self.assertEqual(len(result.paper_summaries), 2)
        sleep.assert_not_called()

    def test_notes_pacing_env_default_is_applied_when_parameter_omitted(self) -> None:
        with mock.patch.dict(os.environ, {"NOTES_PACING_SECONDS": "2.5"}, clear=False):
            with mock.patch("literature_review.pipeline.time.sleep") as sleep:
                run_synthesis_pipeline(
                    [make_document("paper-1", PAPER_TEXT), make_document("paper-2", PAPER_TEXT)],
                    "literature review agent",
                    SynthesisFakeClient(),
                    **self.policy_arguments(),
                )
            sleep.assert_called_once_with(2.5)
