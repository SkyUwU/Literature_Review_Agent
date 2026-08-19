import unittest

from literature_review.models import FilterPolicy, Paper, SearchRequest, SearchResponse
from literature_review.ranking import filter_and_rank, filter_papers, rank_papers


def paper(title: str, year: int, citations: int) -> Paper:
    return Paper(
        paper_id=f"https://example.org/{title.replace(' ', '-')}",
        title=title,
        authors=["Test Author"],
        year=year,
        abstract="This paper studies literature review agents with evidence grounding.",
        url=f"https://example.org/{title.replace(' ', '-')}",
        citation_count=citations,
    )


class RankingTests(unittest.TestCase):
    def test_filter_removes_duplicate_and_old_papers(self) -> None:
        papers = [
            paper("Literature Review Agent", 2025, 10),
            paper("literature review agent", 2025, 20),
            paper("Older Agent", 2020, 100),
        ]
        filtered = filter_papers(papers, FilterPolicy(min_year=2024))
        self.assertEqual([item.title for item in filtered], ["Literature Review Agent"])

    def test_rank_prefers_query_term_in_title(self) -> None:
        title_match = paper("Literature Review Agent", 2024, 5)
        abstract_match = paper("Scientific Tools", 2025, 100)
        ranked = rank_papers([abstract_match, title_match], "literature review agent")
        self.assertEqual(ranked[0].paper.title, "Literature Review Agent")
        self.assertIn("literature", ranked[0].matched_terms)

    def test_filter_and_rank_preserves_search_provenance(self) -> None:
        response = SearchResponse(
            provider="openalex",
            request=SearchRequest(query="literature review agent"),
            total_candidates=1,
            papers=[paper("Literature Review Agent", 2025, 10)],
            skipped_candidates=0,
        )
        result = filter_and_rank(response, FilterPolicy(min_year=2024))
        self.assertEqual(result.search_response.provider, "openalex")
        self.assertEqual(result.ranked_papers[0].rank, 1)
