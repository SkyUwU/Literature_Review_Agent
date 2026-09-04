import json
import unittest

from literature_review.models import SearchPlan
from literature_review.planning import (
    PlanningError,
    build_llm_plan_prompt,
    create_llm_plan,
    create_rule_based_plan,
)


class PlanningTests(unittest.TestCase):
    def test_plan_derived_from_bare_query(self) -> None:
        plan = create_rule_based_plan("literature review agent")

        self.assertEqual(plan.generated_by, "rule_based")
        self.assertGreaterEqual(len(plan.queries), 1)
        self.assertIn("core topic", plan.perspectives)
        self.assertEqual(plan.idea, "literature review agent")

    def test_plan_respects_max_queries_and_keeps_them_unique(self) -> None:
        plan = create_rule_based_plan("agent review", max_queries=2)

        self.assertLessEqual(len(plan.queries), 2)
        self.assertEqual(plan.idea, "agent review")
        self.assertEqual(len({item.query.lower() for item in plan.queries}), len(plan.queries))


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
            }
        ],
        "perspectives": ["core topic", "surveys"],
        "rationale": "Cover the core topic and prior surveys in separate queries.",
        "generated_by": "llm",
    }


class LlmPlanTests(unittest.TestCase):
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
