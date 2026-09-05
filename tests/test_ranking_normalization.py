"""Tests for the normalized (0..1) lexical score introduced in M3B.

Kept as a separate module so the existing test_ranking.py is not rewritten.
"""

import unittest

from literature_review.models import Paper
from literature_review.ranking import rank_papers


def make_paper(
    paper_id: str,
    title: str,
    abstract: str,
    year: int = 2025,
    citation_count: int = 0,
) -> Paper:
    return Paper(
        paper_id=paper_id,
        title=title,
        authors=["Ada Lovelace"],
        url=f"https://example.org/{paper_id}",
        abstract=abstract,
        year=year,
        citation_count=citation_count,
    )


class RankingNormalizationTests(unittest.TestCase):
    def test_lexical_score_is_normalized_to_unit_scale(self) -> None:
        # 3 query terms all matched -> lexical 1.0; citation 0 (0 cites);
        # recency (2025-2020)/10 = 0.5. Total = 1.0 + 0.0 + 0.5 = 1.5 and stays <= 3.
        papers = [
            make_paper("p1", "agent literature review methods", "agent literature review methods in abstract."),
            make_paper("p2", "completely unrelated title", "completely unrelated abstract body text."),
        ]

        ranked = rank_papers(papers, "agent literature review")

        self.assertEqual(ranked[0].paper.paper_id, "p1")
        self.assertLessEqual(ranked[0].score, 3.0)
        self.assertAlmostEqual(ranked[0].score, 1.5, places=3)
        self.assertAlmostEqual(ranked[1].score, 0.5, places=3)

    def test_lexical_score_never_exceeds_one_but_citation_adds(self) -> None:
        # All terms matched (lexical=1.0) plus a strong citation signal must stay
        # within the 0..3 window: 1.0 + 1.0 + 0.5 = 2.5 for a highly cited 2025 paper.
        papers = [
            make_paper("p1", "agent literature review methods", "agent literature review methods in abstract.", 2025, 5000),
        ]

        ranked = rank_papers(papers, "agent literature review")

        self.assertEqual(len(ranked), 1)
        self.assertAlmostEqual(ranked[0].score, 2.5, places=3)


if __name__ == "__main__":
    unittest.main()
