import unittest

from literature_review.assessment import assess_selected_papers
from literature_review.models import AssessmentPolicy, SelectionPolicy
from literature_review.selection import select_papers
from tests.test_selection import ranked_response


class AssessmentTests(unittest.TestCase):
    def test_assessment_recommends_medium_relevance_paper_for_consideration(self) -> None:
        selected = select_papers(ranked_response(), policy=SelectionPolicy(max_papers=1))

        response = assess_selected_papers(selected, AssessmentPolicy())

        self.assertEqual(response.assessments[0].relevance_score, 3)
        self.assertEqual(response.assessments[0].recommendation, "consider")
        self.assertIn("full-text", response.limitations[0])

    def test_low_ranked_paper_is_excluded(self) -> None:
        ranked = ranked_response()
        selected = select_papers(ranked, policy=SelectionPolicy(max_papers=3))

        response = assess_selected_papers(selected, AssessmentPolicy())

        self.assertEqual(response.assessments[-1].recommendation, "exclude")


if __name__ == "__main__":
    unittest.main()
