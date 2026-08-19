import unittest

from pydantic import ValidationError

from literature_review.demo import build_demo_report
from literature_review.models import PaperAssessment, SearchRequest


class ModelTests(unittest.TestCase):
    def test_demo_report_is_valid(self) -> None:
        report = build_demo_report()
        self.assertEqual(report.assessments[0].recommendation, "include")

    def test_score_must_be_between_one_and_five(self) -> None:
        with self.assertRaises(ValidationError):
            PaperAssessment(
                paper_id="example",
                relevance_score=6,
                evidence_quality_score=3,
                recommendation="include",
                rationale="This rationale is intentionally long enough to validate.",
            )

    def test_search_limit_must_be_positive(self) -> None:
        with self.assertRaises(ValidationError):
            SearchRequest(query="literature review", limit=0)


if __name__ == "__main__":
    unittest.main()
