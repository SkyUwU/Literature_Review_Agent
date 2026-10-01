"""Regression tests for bounded follow-up search and page-count diagnostics."""

import contextlib
import io
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from pydantic import ValidationError

import literature_review.main as main_module
from literature_review.llm_evidence import GroqJsonClient
from literature_review.models import FullTextDocument, PageText, SearchRequest
from literature_review.pdf_downloader import DownloadResult
from literature_review.planning import PlanningError, build_llm_plan_prompt, create_llm_plan
from literature_review.screening import (
    FollowUpQuery, SampledCandidates, _LlmScreeningOutput, build_screening_prompt, screen_candidates,
)
from literature_review.search import search_papers
from test_main import record_for
from test_planning import FakePlanClient, valid_plan
from test_screening import FakeClipboardClient, _ranked, _sampled, _screening_payload


def follow_up(query="review agent benchmark"):
    return {"query": query, "target_gap": "benchmark evaluation", "reason": "Fill the evaluation gap"}


class FollowUpValidationTests(unittest.TestCase):
    def test_initial_and_follow_up_prompts_share_task_anchoring(self):
        for topic in ("LLM-based automated literature review", "mixture-of-experts token assignment"):
            prompts = [build_llm_plan_prompt(topic), build_screening_prompt(
                {"initial query": _sampled(_ranked("W1"))}, topic)]
            for prompt in prompts:
                with self.subTest(topic=topic, prompt=prompt[:40]):
                    self.assertIn(topic, prompt)
                    self.assertIn("Task anchoring", prompt)
                    self.assertIn("Disambiguate acronyms", prompt)
                    self.assertIn("Do not replace the requested task", prompt)
                    self.assertIn("illustrations, not required keywords", prompt)
                    self.assertNotIn("For a question about automated literature reviews", prompt)

    def test_plan_and_screening_repairs_retain_original_research_question(self):
        topic = "LLM-based automated literature review"
        bad_plan = valid_plan()
        bad_plan["queries"][0]["query"] = "one"
        planner = FakePlanClient([json.dumps(bad_plan), json.dumps(valid_plan())])
        create_llm_plan(topic, planner, enable_overlap_repair=False)
        bad_screen = _screening_payload([("[DOC_1]", "low")])
        good_screen = _screening_payload([("[DOC_1]", "keep")])
        screener = FakeClipboardClient([json.dumps(bad_screen), json.dumps(good_screen)])
        screen_candidates({"initial query": _sampled(_ranked("W1"))}, screener, main_query=topic)
        for prompt in [planner.prompts[1], screener.prompts[1]]:
            self.assertIn(f"Original research question: {topic}", prompt)
            self.assertIn("Task anchoring", prompt)

    def test_prompt_example_matches_the_output_schema(self):
        prompt = build_screening_prompt({"initial query": _sampled(_ranked("W1"))})
        example = prompt.split("Form example:\n", 1)[1].split("\n\nNo Markdown", 1)[0]
        parsed = _LlmScreeningOutput.model_validate_json(example)
        self.assertEqual(len(parsed.follow_up_queries), 1)
        self.assertIn("2-4 whitespace-separated words", prompt)

    def test_short_query_bounds_and_normalization(self):
        for query in ("review agents", "automated literature review agents"):
            self.assertEqual(FollowUpQuery(**follow_up(query)).query, query)
        self.assertEqual(FollowUpQuery(**follow_up(" review  agents\n")).query, "review agents")
        for query in ("agents", "LLM automated systematic review pipeline title abstract screening"):
            with self.subTest(query=query), self.assertRaises(ValidationError):
                FollowUpQuery(**follow_up(query))

    def test_long_follow_up_is_rewritten_once_without_changing_decisions(self):
        bad = _screening_payload([("[DOC_1]", "keep")], follow_ups=[follow_up(
            "LLM automated systematic review pipeline title abstract screening"
        )])
        good = _screening_payload([("[DOC_1]", "keep")], follow_ups=[follow_up()])
        client = FakeClipboardClient([json.dumps(bad), json.dumps(good)])
        result = screen_candidates({"initial query": _sampled(_ranked("W1"))}, client)
        self.assertEqual(len(client.prompts), 2)
        self.assertIn("2-4 whitespace-separated words", client.prompts[1])
        self.assertEqual(result.gap.follow_up_queries[0].query, "review agent benchmark")
        self.assertEqual(result.decisions["initial query"][0].priority, "keep")

    def test_invalid_priority_still_fails_after_one_repair(self):
        bad = json.dumps(_screening_payload([("[DOC_1]", "low")]))
        client = FakeClipboardClient([bad, bad])
        with self.assertRaises(ValidationError):
            screen_candidates({"initial query": _sampled(_ranked("W1"))}, client)
        self.assertEqual(len(client.prompts), 2)
        self.assertEqual(
            _LlmScreeningOutput.model_json_schema()["$defs"]["_LlmScreenDecision"]["properties"]["priority"]["enum"],
            ["keep", "maybe", "reject"],
        )

    def test_llm_plan_uses_same_length_validation_and_repair_budget(self):
        bad = valid_plan()
        bad["queries"][0]["query"] = "LLM automated systematic review pipeline title abstract screening"
        client = FakePlanClient([json.dumps(bad), json.dumps(valid_plan())])
        plan = create_llm_plan("review agents", client)
        self.assertEqual(len(client.prompts), 2)
        self.assertTrue(all(2 <= len(item.query.split()) <= 4 for item in plan.queries))
        client = FakePlanClient([json.dumps(bad), json.dumps(bad)])
        with self.assertRaises(PlanningError):
            create_llm_plan("review agents", client)
        self.assertEqual(len(client.prompts), 2)

    def test_groq_gap_prompt_and_repair_enforce_short_object_queries(self):
        batch = _screening_payload([("[DOC_1]", "keep")])
        bad = {"follow_up_queries": [follow_up("LLM review generation evaluation citation accuracy benchmarks") ]}
        good = {"follow_up_queries": [follow_up()]}
        client = mock.Mock(spec=GroqJsonClient)
        client.generate_json.side_effect = [json.dumps(batch), json.dumps(bad), json.dumps(good)]
        result = screen_candidates({"initial query": _sampled(_ranked("W1"))}, client)
        self.assertEqual(result.gap.follow_up_queries[0].query, "review agent benchmark")
        self.assertEqual(client.generate_json.call_count, 3)
        for call in client.generate_json.call_args_list[1:]:
            self.assertIn("2-4 whitespace-separated words", call.args[0])


class EmptyFollowUpTests(unittest.TestCase):
    def test_empty_candidates_do_not_call_any_provider(self):
        for client in (FakeClipboardClient([]), mock.Mock(spec=GroqJsonClient)):
            result = screen_candidates({"empty query": SampledCandidates(papers=[])}, client)
            self.assertEqual(result.decisions, {"empty query": []})
            self.assertEqual(result.gap.follow_up_queries, [])
            self.assertIn("empty query", result.gap.missing_pieces[0])
            if isinstance(client, FakeClipboardClient):
                self.assertEqual(client.prompts, [])
            else:
                client.generate_json.assert_not_called()

    def _run_follow_up(self, has_candidate):
        queries = [follow_up("citation evaluation"), follow_up("agent benchmarks")]
        outputs = [json.dumps(_screening_payload([("[DOC_1]", "keep")], follow_ups=queries))]
        if has_candidate:
            outputs.append(json.dumps(_screening_payload([("[DOC_1]", "maybe")])))
        client = FakeClipboardClient(outputs)
        initial, extra = _ranked("W1"), _ranked("W2")
        searches = [[initial], [], [], [], [], [extra] if has_candidate else []]
        with TemporaryDirectory() as folder, mock.patch.object(
            main_module, "_search_and_rank", side_effect=searches
        ), mock.patch.object(
            main_module, "download_and_backfill", return_value=DownloadResult(
                downloaded_paths=[Path(folder) / "W1.pdf"], downloaded_paper_ids=["W1"]
            )
        ) as download, mock.patch.object(
            main_module, "extract_pdf_text", return_value=FullTextDocument(
                paper_id="W1", source_path=str(Path(folder) / "W1.pdf"), extraction_method="test",
                pages=[PageText(page_number=1, text="Usable evidence from the initial screened paper.")],
            )
        ), mock.patch.object(main_module.pipeline, "run_synthesis_pipeline", return_value=object()) as synth:
            result = main_module.run_end_to_end(
                "review agents", dest_dir=Path(folder), use_llm_plan=False,
                client_screen=client, client_synth=object(), encoder=lambda texts: [[1.0] for _ in texts],
            )
            groups = download.call_args.kwargs["priority_groups"]
            synth.assert_called_once()
            self.assertEqual(synth.call_args.args[1], "review agents")
            self.assertEqual(result["papers"].run["query"], "review agents")
            self.assertEqual(synth.call_args.kwargs["paper_queries"]["W1"], "review agents")
            if has_candidate:
                self.assertEqual(result["screening"]["decisions"]["agent benchmarks"][0]["paper_id"], "W2")
        self.assertEqual([item.paper.paper_id for item in groups["keep"]], ["W1"])
        self.assertEqual(len(result["follow_ups"]), 2)
        return client, groups

    def test_all_empty_follow_ups_preserve_initial_screening(self):
        client, groups = self._run_follow_up(False)
        self.assertEqual(len(client.prompts), 1)
        self.assertEqual(groups["maybe"], [])

    def test_mixed_empty_follow_ups_screen_only_real_candidates(self):
        client, groups = self._run_follow_up(True)
        self.assertEqual(len(client.prompts), 2)
        self.assertEqual([item.paper.paper_id for item in groups["maybe"]], ["W2"])
        self.assertIn("Empty retrieval", client.prompts[1])


class SearchCountTests(unittest.TestCase):
    def test_total_matches_are_separate_from_received_page_and_log(self):
        records = [record_for(f"W{i}") for i in range(100)]
        for record in records[:3]:
            record["abstract_inverted_index"] = None
        response = search_papers(
            SearchRequest(query="review agents", limit=100),
            json_fetcher=lambda url: {"meta": {"count": 216}, "results": records},
        )
        self.assertEqual(response.total_candidates, 100)
        self.assertEqual(response.total_matches, 216)
        self.assertEqual(len(response.papers), 97)
        self.assertEqual(response.skipped_candidates, 3)
        output = io.StringIO()
        with mock.patch.object(main_module, "_search_candidates", return_value=response), contextlib.redirect_stderr(output):
            main_module._search_and_rank("review agents", json_fetcher=lambda url: {}, encoder=None, paper_meta={})
        self.assertIn("returned_candidates=100 total_matches=216 with_abstract=97 skipped=3", output.getvalue())
        self.assertNotIn("provider_total=", output.getvalue())

    def test_missing_total_is_unknown_not_fabricated(self):
        response = search_papers(
            SearchRequest(query="review agents"),
            json_fetcher=lambda url: {"results": [record_for("W1")]},
        )
        self.assertEqual(response.total_candidates, 1)
        self.assertIsNone(response.total_matches)


if __name__ == "__main__":
    unittest.main()
