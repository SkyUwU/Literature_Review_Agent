import contextlib
import io
import json
import os
import re
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import literature_review.pipeline as pipeline_module
from literature_review.models import (
    ChunkPolicy,
    ChunkReference,
    EvidenceChunk,
    EvidenceRetrievalPolicy,
    FullTextDocument,
    PageText,
    PaperSummary,
    PaperSummaryClaim,
)
from literature_review.pipeline import (
    NotesIncompleteError,
    NotesCheckpointError,
    expand_pdf_inputs,
    run_evidence_pipeline,
    run_synthesis_pipeline,
)


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


class PrepareDocumentsAppendixTests(unittest.TestCase):
    def _document(self) -> FullTextDocument:
        return FullTextDocument(
            paper_id="paper-appendix",
            source_path="paper.pdf",
            extraction_method="test",
            pages=[PageText(page_number=1, text="Usable paper text for appendix preparation tests.")],
        )

    def test_appendix_classification_with_optional_titles(self) -> None:
        chunks = [
            EvidenceChunk(
                chunk_id=f"chunk-{index}",
                paper_id="paper-appendix",
                section=section,
                text="Detailed evidence with enough words for the preparation policy.",
            )
            for index, section in enumerate([
                "Introduction", "Appendix > Additional Results", "Appendix > Miscellaneous Material"
            ])
        ]
        for titles in (None, {}, {"paper-appendix": "A Study of Agents"}):
            with self.subTest(titles=titles), mock.patch.object(
                pipeline_module, "chapter_chunk_document", return_value=chunks
            ):
                prepared = pipeline_module._prepare_documents(
                    [self._document()], ChunkPolicy(), paper_titles=titles
                )
                self.assertEqual([chunk.chunk_id for chunk in prepared[0][1]], ["chunk-0", "chunk-1"])

    def test_paper_title_does_not_make_unknown_appendix_eligible(self) -> None:
        title = "Evaluation of Literature Review Agents"
        document = self._document().model_copy(update={"pages": [PageText(
            page_number=1,
            text=(
                f"# {title}\n\n## Introduction\n\n"
                "This introduction explains the motivation and scope of the study.\n\n"
                "## Appendix\n\n### Miscellaneous Material\n\n"
                "This material has no recognized evidence category for the review.\n\n"
                "### Additional Results\n\n"
                "The additional results provide useful evidence about measured performance."
            ),
        )]})
        prepared = pipeline_module._prepare_documents(
            [document], ChunkPolicy(), paper_titles={document.paper_id: title}
        )
        sections = [chunk.section for chunk in prepared[0][1]]
        self.assertTrue(any("Additional Results" in section for section in sections))
        self.assertFalse(any("Miscellaneous Material" in section for section in sections))

    def test_synthesis_passes_titles_to_document_preparation(self) -> None:
        titles = {"paper-appendix": "Evaluation of Literature Review Agents"}
        with mock.patch.object(
            pipeline_module, "_prepare_documents", side_effect=RuntimeError("preparation sentinel")
        ) as prepare:
            with self.assertRaisesRegex(RuntimeError, "preparation sentinel"):
                run_synthesis_pipeline(
                    [self._document()], "review agents", FakeClient(), paper_titles=titles
                )
        self.assertIs(prepare.call_args.kwargs["paper_titles"], titles)


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

def direction_payload(cited_paper_id: str, claim_id: str) -> dict[str, object]:
    rationale = (
        "The retained study states evaluation restrictions that motivate broader multilingual benchmarks."
        if cited_paper_id == "paper-1"
        else "Both studies state evaluation restrictions that motivate broader multilingual benchmarks."
    )
    return {
        "title": "Harden multilingual evaluation coverage",
        "rationale": rationale,
        "supporting_claim_ids": [claim_id],
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
                "chunk_ids": ["C1"],
                "aspect": "contribution",
            }
        ],
    }


class SynthesisFakeClient:
    """Answers the three prompt kinds produced by the synthesis workflow."""

    def __init__(self) -> None:
        self.prompts: list[str] = []
        self.cited_claim_ids: list[str] = []
        self.note_paper_ids: list[str] = []

    def generate_json(self, prompt: str, schema: dict | None = None) -> str:
        self.prompts.append(prompt)
        if prompt.startswith("Summarize this single paper"):
            for paper_id in ("paper-1", "paper-2"):
                if f"Paper ID: {paper_id}\n" in prompt:
                    self.note_paper_ids.append(paper_id)
                    return json.dumps(note_payload(paper_id))
        if prompt.startswith("Plan a thematic outline"):
            real_claim_ids = re.findall(r'"claim_id": "(claim-\d+)"', prompt)
            return json.dumps(
                {
                    "sections": [
                        {
                            "title": "Evidence and scope",
                            "purpose": "groups the supplied claims about evidence-cited review agents.",
                            "supporting_claim_ids": real_claim_ids,
                        }
                    ]
                }
            )
        if prompt.startswith("Write a fluent literature-review"):
            real_claim_ids = re.findall(r'"claim_id": "(claim-\d+)"', prompt)
            self.cited_claim_ids.extend(real_claim_ids)
            report = (
                "# Evidence-cited synthesis\n"
                + "".join(
                    f"The reviewed study supplies retrieved evidence for its claims "
                    f"in [{claim_id}].\n"
                    for claim_id in real_claim_ids
                )
            )
            return json.dumps({"report": report})
        if prompt.startswith("Propose future research directions"):
            claim_id = f"claim-{self.note_paper_ids.index('paper-2') + 1}"
            return json.dumps({"future_directions": [direction_payload("paper-2", claim_id)]})
        indexes = re.findall(r"## Chunk (\d+)", prompt)
        return json.dumps({"assessments": [assessment_payload(index) for index in indexes]})


class PaperDropFakeClient(SynthesisFakeClient):
    """Variant whose future direction cites only the retained paper and which
    scores the *unrelated* (thermodynamics) chunks with utility 1, so paper-2
    falls below the functional threshold and is dropped from notes/synthesis."""

    def generate_json(self, prompt: str, schema: dict | None = None) -> str:
        self.prompts.append(prompt)
        if prompt.startswith("Summarize this single paper") and "Paper ID: paper-1\n" in prompt:
            self.note_paper_ids.append("paper-1")
            return json.dumps(note_payload("paper-1"))
        if prompt.startswith("Plan a thematic outline"):
            real_claim_ids = re.findall(r'"claim_id": "(claim-\d+)"', prompt)
            return json.dumps(
                {
                    "sections": [
                        {
                            "title": "Evidence and scope",
                            "purpose": "groups the supplied claims about evidence-cited review agents.",
                            "supporting_claim_ids": real_claim_ids,
                        }
                    ]
                }
            )
        if prompt.startswith("Write a fluent literature-review"):
            real_claim_ids = re.findall(r'"claim_id": "(claim-\d+)"', prompt)
            report = (
                "# Evidence-cited synthesis\n"
                + "".join(
                    f"The reviewed study supplies retrieved evidence for its claims "
                    f"in [{claim_id}].\n"
                    for claim_id in real_claim_ids
                )
            )
            return json.dumps({"report": report})
        if prompt.startswith("Propose future research directions"):
            claim_id = f"claim-{self.note_paper_ids.index('paper-1') + 1}"
            return json.dumps({"future_directions": [direction_payload("paper-1", claim_id)]})
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
        self.assertEqual(
            result.claim_chunks,
            {"claim-1": ["paper-1-c1"], "claim-2": ["paper-2-c1"]},
        )
        self.assertEqual(len(result.paper_sources), 2)
        self.assertEqual(
            {source.source_path for source in result.paper_sources},
            {"data/papers/paper-1.pdf", "data/papers/paper-2.pdf"},
        )
        self.assertEqual(
            {source.paper_id: source.claim_ids for source in result.paper_sources},
            {"paper-1": ["claim-1"], "paper-2": ["claim-2"]},
        )
        self.assertGreaterEqual(len(result.future_directions), 1)
        markers = re.findall(r"\[([^\[\]]+)\]", result.report)
        self.assertGreaterEqual(len(markers), 1)
        self.assertTrue(set(markers).issubset(set(result.claim_chunks)))
        self.assertNotIn("## 材料來源清單", result.report)

    def test_notes_checkpoint_resumes_only_missing_paper_and_blocks_partial_report(self) -> None:
        documents = [make_document("paper-1", PAPER_TEXT), make_document("paper-2", PAPER_TEXT)]
        checkpoint_dir = Path(tempfile.mkdtemp()) / "run-1"
        self.addCleanup(lambda: shutil.rmtree(checkpoint_dir.parent, ignore_errors=True))
        client = SynthesisFakeClient()

        def summary_for(paper_id: str, chunks: list, *_args, **_kwargs) -> PaperSummary:
            chunk = chunks[0]
            return PaperSummary(
                paper_id=paper_id,
                claims=[
                    PaperSummaryClaim(
                        text=f"The study in {paper_id} reports evidence selection results for review agents.",
                        aspect="method",
                        evidence=[
                            ChunkReference(
                                chunk_id=chunk.chunk_id,
                                paper_id=paper_id,
                                page_start=chunk.page_start,
                                page_end=chunk.page_end,
                                quote=chunk.text[:240],
                            )
                        ],
                    )
                ],
                coverage_chunk_ids=[chunk.chunk_id],
            )

        def fail_first_paper(paper_id: str, chunks: list, *args, **kwargs) -> PaperSummary:
            if paper_id == "paper-1":
                raise RuntimeError("temporary provider outage")
            return summary_for(paper_id, chunks, *args, **kwargs)

        with mock.patch.object(
            pipeline_module, "summarize_paper_notes", side_effect=fail_first_paper
        ), mock.patch.object(pipeline_module, "_notes_pacing_seconds", return_value=0):
            with self.assertRaises(NotesIncompleteError):
                run_synthesis_pipeline(
                    documents,
                    "literature review agent",
                    client,
                    notes_checkpoint_dir=checkpoint_dir,
                    notes_pacing_seconds=0,
                    **self.policy_arguments(),
                )
        self.assertTrue((checkpoint_dir / "manifest.json").is_file())
        self.assertEqual(
            len([
                path
                for path in checkpoint_dir.glob("*.json")
                if path.name not in {"manifest.json", "state.json"}
            ]),
            1,
        )
        checkpoint_state = json.loads((checkpoint_dir / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(checkpoint_state["pending_paper_ids"], ["paper-1"])
        self.assertFalse(any(prompt.startswith("Plan a thematic outline") for prompt in client.prompts))

        with mock.patch.object(
            pipeline_module, "summarize_paper_notes", side_effect=summary_for
        ) as summarize, mock.patch.object(
            pipeline_module, "_notes_pacing_seconds", return_value=0
        ):
            client.note_paper_ids = ["paper-1", "paper-2"]
            result = run_synthesis_pipeline(
                documents,
                "literature review agent",
                client,
                notes_checkpoint_dir=checkpoint_dir,
                notes_pacing_seconds=0,
                **self.policy_arguments(),
            )

        self.assertEqual([call.args[0] for call in summarize.call_args_list], ["paper-1"])
        self.assertEqual({note.paper_id for note in result.paper_summaries}, {"paper-1", "paper-2"})

    def test_notes_checkpoint_rejects_changed_query(self) -> None:
        documents = [make_document("paper-1", PAPER_TEXT), make_document("paper-2", PAPER_TEXT)]
        checkpoint_dir = Path(tempfile.mkdtemp()) / "run-2"
        self.addCleanup(lambda: shutil.rmtree(checkpoint_dir.parent, ignore_errors=True))
        with mock.patch.object(
            pipeline_module,
            "summarize_paper_notes",
            side_effect=lambda paper_id, chunks, *_a, **_k: (_ for _ in ()).throw(
                RuntimeError("provider down")
            ),
        ), mock.patch.object(pipeline_module, "_notes_pacing_seconds", return_value=0):
            with self.assertRaises(NotesIncompleteError):
                run_synthesis_pipeline(
                    documents,
                    "literature review agent",
                    SynthesisFakeClient(),
                    notes_checkpoint_dir=checkpoint_dir,
                    notes_pacing_seconds=0,
                    **self.policy_arguments(),
                )
        with self.assertRaises(NotesCheckpointError):
            run_synthesis_pipeline(
                documents,
                "a different research question",
                SynthesisFakeClient(),
                notes_checkpoint_dir=checkpoint_dir,
                notes_pacing_seconds=0,
                **self.policy_arguments(),
            )

    def test_synthesis_pipeline_prints_section_distribution_by_default(self) -> None:
        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            run_synthesis_pipeline(
                [make_document("paper-1", PAPER_TEXT), make_document("paper-2", PAPER_TEXT)],
                "literature review agent",
                SynthesisFakeClient(),
                **self.policy_arguments(),
            )

        self.assertIn("Section distribution across", stdout.getvalue())

    def test_synthesis_pipeline_print_section_distribution_false_is_quiet(self) -> None:
        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            run_synthesis_pipeline(
                [make_document("paper-1", PAPER_TEXT), make_document("paper-2", PAPER_TEXT)],
                "literature review agent",
                SynthesisFakeClient(),
                print_section_distribution=False,
                **self.policy_arguments(),
            )

        self.assertEqual(stdout.getvalue(), "")

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
        self.assertEqual(result.claim_chunks, {"claim-1": ["paper-2-c1"]})
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
        self.assertEqual(functional_calls, 1)  # FUNCTIONAL_BATCH_SIZE=8; 4 sampled chunks fit one batch
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
        self.assertEqual(result.claim_chunks, {"claim-1": ["paper-1-c1"]})
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

    def test_notes_pacing_default_respects_the_free_tier_ceiling(self) -> None:
        """The default gap must cover 5 requests per minute (12s), not the old 4s."""
        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch("literature_review.pipeline.time.sleep") as sleep:
                run_synthesis_pipeline(
                    [make_document("paper-1", PAPER_TEXT), make_document("paper-2", PAPER_TEXT)],
                    "literature review agent",
                    SynthesisFakeClient(),
                    **self.policy_arguments(),
                )
            sleep.assert_called_once_with(13.0)
