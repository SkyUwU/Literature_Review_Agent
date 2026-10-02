"""M1 contracts and context propagation, using only fake providers/encoders."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest import mock

from pydantic import ValidationError

import literature_review.main as main
import literature_review.pipeline as pipeline
from literature_review.functional import aggregate_functional, build_functional_prompt, score_chunks_functionally, select_quota_threshold
from literature_review.models import CoveragePackPolicy, LlmFunctionalAssessment, SearchPlan, TaskInterpretation
from literature_review.planning import PlanningError, build_llm_plan_prompt, create_llm_plan, create_rule_based_plan
from literature_review.screening import _screen_candidates_groq_batches, build_screening_prompt, screen_candidates
from literature_review.synthesis import (
    NOTES_PROMPT_POLICY_VERSION, build_directions_prompt, build_outline_prompt,
    build_paper_notes_prompt, build_section_report_prompt, summarize_paper_notes, synthesize_report,
)
from test_functional import evidence_chunks
from test_planning import FakePlanClient, valid_plan
from test_screening import FakeClipboardClient, _ranked, _sampled, _screening_payload
from test_section_report import Client, body, outline
from test_synthesis import llm_synthesis_fixture, valid_directions_payload
from test_pipeline import FakeEncoder, PAPER_TEXT, SynthesisFakeClient, make_document
from literature_review.pdf_downloader import DownloadResult


IDEA = "LLM-based automated literature review"


def interpretation():
    return TaskInterpretation.model_validate(valid_plan()["task_interpretation"])


class TaskAlignmentTests(unittest.TestCase):
    def assert_context(self, prompt, idea=IDEA):
        self.assertIn(f"Original research idea (authoritative): {idea}", prompt)
        self.assertIn("Task interpretation (aid only)", prompt)
        self.assertIn("Task disambiguation", prompt)
        self.assertIn("not required keywords, fixed domains", prompt)
        self.assertIn("manuscript peer review", prompt)
        self.assertIn("network packet routing", prompt)

    def test_planner_single_response_requires_interpretation_before_queries(self):
        client = FakePlanClient([json.dumps(valid_plan())])
        plan = create_llm_plan(IDEA, client, enable_overlap_repair=False)
        self.assertEqual(len(client.prompts), 1)
        self.assertEqual(plan.idea, IDEA)
        self.assertEqual(plan.task_interpretation, interpretation())
        self.assertIn("first output task_interpretation, then queries", client.prompts[0])
        self.assertIn("task_interpretation", client.schemas[0]["required"])
        self.assertEqual(list(client.schemas[0]["properties"])[0], "task_interpretation")
        self.assertNotIn("idea", client.schemas[0]["properties"])
        self.assertIn("Task anchors may repeat", client.prompts[0])

    def test_missing_or_malformed_interpretation_has_only_one_repair(self):
        for invalid in (None, {"task": " "}):
            bad = valid_plan()
            bad["task_interpretation"] = invalid
            client = FakePlanClient([json.dumps(bad), json.dumps(valid_plan())])
            plan = create_llm_plan(IDEA, client)
            self.assertEqual(len(client.prompts), 2)
            self.assertEqual(plan.idea, IDEA)
            self.assertIn(IDEA, client.prompts[1])
            self.assertIn("first output task_interpretation", client.prompts[1])
            failing = FakePlanClient([json.dumps(bad), json.dumps(bad)])
            with self.assertRaises(PlanningError):
                create_llm_plan(IDEA, failing)
            self.assertEqual(len(failing.prompts), 2)

    def test_legacy_plans_and_unspecified_fields(self):
        old = valid_plan()
        old.pop("task_interpretation")
        self.assertIsNone(SearchPlan.model_validate(old).task_interpretation)
        self.assertIsNone(create_rule_based_plan(IDEA).task_interpretation)
        result = TaskInterpretation(task="Study routing", research_object="unspecified",
                                    expected_output="unspecified", scope_boundaries=[])
        self.assertEqual(result.scope_boundaries, [])
        with self.assertRaises(ValidationError):
            result.model_validate({**result.model_dump(), "scope_boundaries": [" "]})

    def test_cross_domain_input_is_preserved_in_every_judgment_prompt(self):
        for topic in (IDEA, "mixture-of-experts token assignment", "medical image segmentation"):
            summary = TaskInterpretation(task=topic, research_object="unspecified", expected_output="unspecified")
            prompts = [build_screening_prompt({"source query": _sampled(_ranked("W1"))}, topic, summary),
                       build_functional_prompt(topic, evidence_chunks(), task_interpretation=summary),
                       build_outline_prompt(topic, [], 0, summary),
                       build_section_report_prompt(topic, outline(), 0, [], summary),
                       build_directions_prompt(topic, [], summary)]
            for prompt in prompts:
                self.assert_context(prompt, topic)
            self.assertIn(topic, build_llm_plan_prompt(topic))

    def test_screening_repair_retains_interpretation_and_task_comparison(self):
        client = FakeClipboardClient([
            json.dumps(_screening_payload([("[DOC_1]", "invalid")])),
            json.dumps(_screening_payload([("[DOC_1]", "keep")]))])
        screen_candidates({"source query": _sampled(_ranked("W1"))}, client,
                          main_query=IDEA, task_interpretation=interpretation())
        self.assertEqual(len(client.prompts), 2)
        for prompt in client.prompts:
            self.assert_context(prompt)
            self.assertIn("concrete mechanism", prompt)
        self.assertIn("For each decision", client.prompts[0])

    def test_groq_batch_and_gap_repair_keep_task_context(self):
        candidates = {"source query": _sampled(_ranked("W1"))}
        client = FakeClipboardClient([
            json.dumps(_screening_payload([("[DOC_1]", "keep")])),
            '{"missing_pieces":',
            json.dumps({"covered_areas": [], "missing_pieces": [], "follow_up_queries": []})])
        _screen_candidates_groq_batches(candidates, client, IDEA, interpretation())
        self.assertEqual(len(client.prompts), 3)
        for prompt in client.prompts:
            self.assert_context(prompt)

    def test_scoring_repair_keeps_original_evidence_and_authoritative_idea(self):
        client = Client(['{"assessments":', {"assessments": [
            {"chunk_id": "1", "rationale": "Different task, only transferable evaluation mechanism.", "utility_score": 5},
            {"chunk_id": "2", "rationale": "Provides a concrete mechanism within the original task.", "utility_score": 9}]}])
        score_chunks_functionally(IDEA, evidence_chunks(), client, task_interpretation=interpretation())
        self.assertEqual(len(client.prompts), 2)
        for prompt in client.prompts:
            self.assert_context(prompt)
            self.assertIn("primary scoring criterion", prompt)
            self.assertIn(evidence_chunks()[0].text, prompt)

    def test_formula_unchanged_and_description_reports_components(self):
        chunks = evidence_chunks()
        assessments = [LlmFunctionalAssessment(chunk_id=c.chunk_id, rationale="Substantive evidence rationale.", utility_score=v)
                       for c, v in zip(chunks, [0, 10])]
        scores = aggregate_functional(assessments, {"paper-1": chunks})
        score = scores["paper-1"]
        self.assertEqual((score.utility_score, score.max_score, score.mean_score, score.max_weight), (8.5, 10, 5, 0.7))
        selected = select_quota_threshold(scores, {"paper-1": "source query"}, set(),
                                         n_first_round=2, n_follow_up=1, threshold=6)
        self.assertIn("Aggregated functional utility 8.5", selected[0].rationale)
        self.assertIn("max_weight=0.7", selected[0].rationale)
        self.assertNotIn("Mean functional utility", selected[0].rationale)

    def test_report_schema_marker_and_section_repairs_keep_context(self):
        response, packs, note = llm_synthesis_fixture()
        bad_outline = outline().model_dump()
        bad_outline["sections"][0]["supporting_claim_ids"] = ["claim-99"]
        client = Client(['{"sections":', bad_outline, outline().model_dump(),
                         body("[claim-99]"), body("[claim-1]"), body("[claim-4]"),
                         '{"future_directions":', valid_directions_payload()])
        synthesize_report(response, packs, [note], client, query=IDEA,
                          task_interpretation=interpretation())
        self.assertEqual(len(client.prompts), 8)
        for prompt in client.prompts:
            self.assert_context(prompt)
            self.assertIn("need for validation", prompt)
        self.assertNotIn('"claim_id": "claim-4"', client.prompts[3])

    def test_notes_preserve_paper_task_and_do_not_receive_research_idea(self):
        chunks = evidence_chunks(1)
        client = Client(['{"claims":', {"claims": [
            {"text": "The paper studies peer review focus within manuscript acceptance assessment.",
             "chunk_ids": ["C1"], "aspect": "Method"}]}])
        result = summarize_paper_notes("paper-1", chunks, client, CoveragePackPolicy())
        for prompt in client.prompts:
            self.assertIn("Preserve the paper's own task", prompt)
            self.assertIn("Website controls", prompt)
            self.assertNotIn(IDEA, prompt)
            self.assertNotIn("Task interpretation", prompt)
        self.assertIn("peer review", result.claims[0].text)

    def test_end_to_end_persists_plan_and_passes_context_to_follow_up_and_pipeline(self):
        topic = IDEA
        planner = FakePlanClient([json.dumps(valid_plan())])
        candidate = _ranked("W1")
        screen = FakeClipboardClient([
            json.dumps(_screening_payload([("[DOC_1]", "keep")], follow_ups=[
                {"query": "literature review evaluation", "target_gap": "Evaluation gap", "reason": "Fill this gap"}])),
            json.dumps(_screening_payload([("[DOC_1]", "keep")]))])
        with TemporaryDirectory() as folder, mock.patch.object(main, "_search_and_rank", side_effect=[[candidate], [], [], [candidate]]), \
             mock.patch.object(main, "download_and_backfill", return_value=DownloadResult(
                 downloaded_paths=[Path(folder) / "W1.pdf"], downloaded_paper_ids=["W1"])), \
             mock.patch.object(main, "extract_pdf_text", return_value=make_document("W1", PAPER_TEXT)), \
             mock.patch.object(main.pipeline, "run_synthesis_pipeline", return_value=object()) as synth:
            result = main.run_end_to_end(topic, dest_dir=Path(folder), client_plan=planner,
                                       client_screen=screen, client_synth=object(), encoder=FakeEncoder())
        for prompt in screen.prompts:
            self.assert_context(prompt)
        self.assertEqual(synth.call_args.args[1], topic)
        self.assertEqual(synth.call_args.kwargs["task_interpretation"], interpretation())
        run = result["papers"].run
        self.assertEqual(run["query"], topic)
        self.assertEqual(run["search_plan"]["idea"], topic)
        self.assertEqual(run["task_interpretation"], interpretation().model_dump())

    def test_pipeline_context_propagation_and_checkpoint_policy(self):
        document = make_document("paper-1", PAPER_TEXT)
        with TemporaryDirectory() as folder:
            checkpoint = Path(folder) / "notes"
            client = SynthesisFakeClient()
            # Stop at synthesis after genuine scoring and notes to inspect the saved manifest.
            with mock.patch.object(pipeline, "synthesize_report", return_value=mock.Mock(
                paper_summaries=[], paper_sources=[], report="Fixture report",
            )) as report:
                pipeline.run_synthesis_pipeline([document], IDEA, client, encoder=FakeEncoder(),
                                                task_interpretation=interpretation(), notes_pacing_seconds=0,
                                                notes_checkpoint_dir=checkpoint)
            self.assertEqual(report.call_args.kwargs["task_interpretation"], interpretation())
            scoring = [p for p in client.prompts if "## Chunk" in p]
            self.assertTrue(scoring)
            for prompt in scoring:
                self.assert_context(prompt)
            for prompt in client.prompts:
                if prompt.startswith("Summarize this single paper"):
                    self.assertNotIn(IDEA, prompt)
            manifest = json.loads((checkpoint / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["notes_prompt_policy"], NOTES_PROMPT_POLICY_VERSION)
            manifest.pop("notes_prompt_policy")
            (checkpoint / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaises(pipeline.NotesCheckpointError):
                pipeline.run_synthesis_pipeline([document], IDEA, client, encoder=FakeEncoder(),
                                                notes_checkpoint_dir=checkpoint, notes_pacing_seconds=0)


if __name__ == "__main__":
    unittest.main()
