import unittest

from literature_review.models import ResearchIdea
from literature_review.planning import create_rule_based_plan


class PlanningTests(unittest.TestCase):
    def test_plan_covers_keywords_title_and_research_questions(self) -> None:
        idea = ResearchIdea(
            title="Literature review agent",
            description="An agent that searches, assesses, and synthesizes scholarly papers.",
            keywords=["literature review", "agent"],
            research_questions=["How can an agent provide traceable literature reviews?"],
        )

        plan = create_rule_based_plan(idea)

        self.assertEqual(plan.generated_by, "rule_based")
        self.assertEqual(len(plan.queries), 3)
        self.assertIn("prior surveys", plan.perspectives)

    def test_plan_removes_duplicate_queries_and_respects_limit(self) -> None:
        idea = ResearchIdea(
            title="Agent review",
            description="An agent project used to study transparent academic literature reviews.",
            keywords=["agent", "review"],
            research_questions=["agent review", "agent review"],
        )

        plan = create_rule_based_plan(idea, max_queries=2)

        self.assertEqual(len(plan.queries), 2)
        self.assertEqual(len({item.query.lower() for item in plan.queries}), 2)
