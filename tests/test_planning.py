import json
import unittest

from literature_review.models import SearchPlan
from literature_review.planning import (
    PlanningError,
    build_llm_plan_prompt,
    create_llm_plan,
    create_rule_based_plan,
    max_query_overlap,
    query_overlap,
)


class PlanningTests(unittest.TestCase):
    def test_plan_derived_from_bare_query(self) -> None:
        plan = create_rule_based_plan("literature review agent")

        self.assertEqual(plan.generated_by, "rule_based")
        self.assertGreaterEqual(len(plan.queries), 1)
        self.assertIn("core topic", plan.perspectives)
        self.assertEqual(plan.idea, "literature review agent")

    def test_plan_respects_max_queries_and_keeps_them_unique(self) -> None:
        plan = create_rule_based_plan("agent review", max_queries=3)

        self.assertLessEqual(len(plan.queries), 3)
        self.assertGreaterEqual(len(plan.queries), 3)
        self.assertEqual(plan.idea, "agent review")
        self.assertEqual(len({item.query.lower() for item in plan.queries}), len(plan.queries))

    def test_rule_based_plan_rejects_below_searchplan_lower_bound(self) -> None:
        with self.assertRaises(ValueError):
            create_rule_based_plan("agent review", max_queries=2)

    def test_rule_based_plan_default_meets_searchplan_lower_bound(self) -> None:
        plan = create_rule_based_plan("agent review")

        self.assertGreaterEqual(len(plan.queries), 3)


class FakePlanClient:
    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.prompts: list[str] = []
        self.schemas: list[dict | None] = []

    def generate_json(self, prompt: str, schema: dict | None = None) -> str:
        self.prompts.append(prompt)
        self.schemas.append(schema)
        return self.responses.pop(0)


def valid_plan() -> dict[str, object]:
    return {
        "queries": [
            {
                "query": "literature review agent",
                "purpose": "Find core papers on literature review agents.",
            },
            {
                "query": "systematic survey automation tools",
                "purpose": "Find automation tooling for systematic surveys.",
            },
            {
                "query": "comparative evaluation benchmarks",
                "purpose": "Find evaluation benchmarks for comparative surveys.",
            },
        ],
        "perspectives": ["core topic", "surveys", "evaluation"],
        "rationale": "Cover the core topic, prior surveys, and evaluation benchmarks in separate queries.",
        "generated_by": "llm",
    }


class QueryOverlapTests(unittest.TestCase):
    def test_overlap_flags_near_duplicate_keyword_reuse(self) -> None:
        self.assertEqual(query_overlap("literature review agent AI", "AI literature review agent tools"), 0.8)

    def test_overlap_allows_synonym_rewriting(self) -> None:
        self.assertLess(query_overlap("literature review agent", "systematic review automation"), 0.5)

    def test_overlap_identical_queries_is_one(self) -> None:
        self.assertEqual(query_overlap("literature review agent", "literature review agent"), 1.0)

    def test_overlap_disjoint_queries_is_zero(self) -> None:
        self.assertEqual(query_overlap("chromosome sequencing", "neural text generation"), 0.0)

    def test_overlap_ignores_case_and_punctuation(self) -> None:
        self.assertEqual(query_overlap("Literature Review, Agent!", "literature review agent"), 1.0)

    def test_max_overlap_picks_highest_pair(self) -> None:
        queries = ["literature review agent AI", "AI literature review agent tools", "chromosome sequencing"]
        self.assertEqual(max_query_overlap(queries), 0.8)


def overlapping_plan() -> dict[str, object]:
    return {
        "queries": [
            {"query": "literature review agent AI", "purpose": "Find core papers on AI literature review agents."},
            {"query": "AI literature review agent tools", "purpose": "Find tooling papers for AI literature review agents."},
            {"query": "agent-based literature review systems", "purpose": "Find system papers for agent-based literature review."},
        ],
        "perspectives": ["core topic"],
        "rationale": "Cover the core topic from overlapping angles.",
        "generated_by": "llm",
    }


class LlmPlanOverlapRepairTests(unittest.TestCase):
    def test_overlapping_plan_repairs_once_to_distinct_facets(self) -> None:
        repaired = dict(valid_plan())
        repaired["queries"] = [
            {"query": "AI systematic review automation", "purpose": "Find automation papers for systematic reviews."},
            {"query": "LLM research assistant agents", "purpose": "Find LLM research assistant agent papers."},
            {"query": "survey quality evaluation benchmarks", "purpose": "Find survey quality evaluation benchmarks."},
        ]
        client = FakePlanClient([json.dumps(overlapping_plan()), json.dumps(repaired)])

        plan = create_llm_plan("literature review agent", client)

        self.assertLessEqual(max_query_overlap([q.query for q in plan.queries]), 0.5)
        self.assertEqual(len(client.prompts), 2)

    def test_overlap_repair_strictly_improved_but_still_overlapping_is_accepted(self) -> None:
        improved = dict(valid_plan())
        improved["queries"] = [
            {"query": "literature review agent AI", "purpose": "Find core papers on AI literature review agents."},
            {"query": "AI review agent frameworks", "purpose": "Find agent framework papers for AI reviews."},
            {"query": "systematic survey automation", "purpose": "Find systematic survey automation papers."},
        ]
        self.assertEqual(max_query_overlap([q["query"] for q in improved["queries"]]), 0.6)
        client = FakePlanClient([json.dumps(overlapping_plan()), json.dumps(improved)])

        plan = create_llm_plan("literature review agent", client)

        self.assertGreater(max_query_overlap([q.query for q in plan.queries]), 0.5)
        self.assertEqual(plan.queries[1].query, "AI review agent frameworks")
        self.assertEqual(len(client.prompts), 2)

    def test_overlap_repair_failure_keeps_first_plan(self) -> None:
        equal_overlap = dict(overlapping_plan())
        equal_overlap["queries"] = [
            {"query": "literature review agent AI", "purpose": "Find core papers on AI literature review agents."},
            {"query": "AI literature review agent tools", "purpose": "Find tooling papers for AI literature review agents."},
            {"query": "literature review agent systems", "purpose": "Find system papers for literature review agents."},
        ]
        self.assertEqual(max_query_overlap([q["query"] for q in equal_overlap["queries"]]), 0.8)
        client = FakePlanClient([json.dumps(overlapping_plan()), json.dumps(equal_overlap)])

        plan = create_llm_plan("literature review agent", client)

        self.assertEqual(len(client.prompts), 2)
        self.assertEqual(plan.queries[0].query, "literature review agent AI")

    def test_overlap_repair_disabled_never_issues_second_call(self) -> None:
        client = FakePlanClient([json.dumps(overlapping_plan()), json.dumps(valid_plan())])

        plan = create_llm_plan("literature review agent", client, enable_overlap_repair=False)

        self.assertEqual(len(client.prompts), 1)
        self.assertEqual(plan.queries[0].query, "literature review agent AI")

    def test_schema_repair_consuming_budget_skips_overlap_repair(self) -> None:
        client = FakePlanClient(['{"queries": [', json.dumps(overlapping_plan())])

        plan = create_llm_plan("literature review agent", client)

        self.assertEqual(len(client.prompts), 2)
        self.assertEqual(plan.queries[0].query, "literature review agent AI")

    def test_two_query_plan_gets_one_schema_repair(self) -> None:
        two_query = dict(valid_plan())
        two_query["queries"] = two_query["queries"][:2]
        client = FakePlanClient([json.dumps(two_query), json.dumps(valid_plan())])

        plan = create_llm_plan("literature review agent", client)

        self.assertEqual(len(client.prompts), 2)
        self.assertEqual(len(plan.queries), 3)


class LlmPlanTests(unittest.TestCase):
    def test_llm_prompt_requires_distinct_facets_and_varied_terms(self) -> None:
        prompt = build_llm_plan_prompt("literature review agent")

        self.assertIn("Each sub-query must target a distinct facet", prompt)
        self.assertIn("avoid near-duplicate queries", prompt)
        self.assertIn("do not reuse the same head terms", prompt)
        self.assertIn("synonyms, hyponyms, and alternative phrasings", prompt)
        self.assertIn("Stay on-topic", prompt)

    def test_llm_prompt_m5e_requires_three_dimensions_and_keyword_pools(self) -> None:
        prompt = build_llm_plan_prompt("literature review agent")

        self.assertIn("3 complementary research dimensions", prompt)
        self.assertIn("(1) the core task name", prompt)
        self.assertIn("(2) key methodology", prompt)
        self.assertIn("(3) evaluation benchmarks", prompt)
        self.assertIn("keyword pool for each dimension", prompt)
        self.assertIn("combine terms taken from different dimensions", prompt)

    def test_llm_prompt_tightened_to_three_to_four_short_keyword_queries(self) -> None:
        prompt = build_llm_plan_prompt("literature review agent")

        self.assertIn("exactly 3 to 4 items", prompt)
        self.assertIn("Keyword Length (Strict)", prompt)
        self.assertIn("For example (good):", prompt)
        self.assertIn("For example (bad):", prompt)

    def test_t_llm_1_valid_plan_returned(self) -> None:
        client = FakePlanClient([json.dumps(valid_plan())])

        plan = create_llm_plan("literature review agent", client)

        self.assertIsInstance(plan, SearchPlan)
        self.assertEqual(plan.generated_by, "llm")
        self.assertEqual(plan.idea, "literature review agent")
        self.assertEqual(plan.queries[0].query, "literature review agent")
        self.assertIn("literature review agent", build_llm_plan_prompt("literature review agent"))

    def test_t_llm_2_schema_sent_as_second_argument(self) -> None:
        client = FakePlanClient([json.dumps(valid_plan())])
        schema = SearchPlan.model_json_schema()

        create_llm_plan("agent survey", client)

        self.assertEqual(len(client.prompts), 1)
        self.assertEqual(client.schemas[0], schema)

    def test_t_llm_3_malformed_json_repairs_exactly_once(self) -> None:
        client = FakePlanClient(['{"queries": [', json.dumps(valid_plan())])

        plan = create_llm_plan("agent survey", client)

        self.assertEqual(plan.generated_by, "llm")
        self.assertEqual(len(client.prompts), 2)  # one repair call after the first failure

    def test_t_llm_4_unrepairable_output_raises_planning_error(self) -> None:
        client = FakePlanClient(["not valid json at all", "still not valid json"])

        with self.assertRaises(PlanningError):
            create_llm_plan("agent survey", client)
