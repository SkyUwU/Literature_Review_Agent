import unittest

from literature_review.models import (
    FilterPolicy,
    Paper,
    RankedPaper,
    RankedSearchResponse,
    SearchRequest,
    SearchResponse,
    SelectionPolicy,
)
from literature_review.selection import select_papers


def ranked_response() -> RankedSearchResponse:
    papers = [
        Paper(
            paper_id=f"paper-{index}",
            title=f"Paper {index}",
            authors=["Author"],
            year=2024,
            abstract="A sufficiently long abstract for testing paper selection.",
            url=f"https://example.org/paper-{index}",
        )
        for index in range(1, 4)
    ]
    response = SearchResponse(
        provider="test",
        request=SearchRequest(query="test query"),
        total_candidates=3,
        papers=papers,
        skipped_candidates=0,
    )
    return RankedSearchResponse(
        search_response=response,
        filter_policy=FilterPolicy(),
        ranked_papers=[
            RankedPaper(paper=papers[0], rank=1, score=9, matched_terms=[], rationale="test"),
            RankedPaper(paper=papers[1], rank=2, score=6, matched_terms=[], rationale="test"),
            RankedPaper(paper=papers[2], rank=3, score=4, matched_terms=[], rationale="test"),
        ],
    )


class SelectionTests(unittest.TestCase):
    def test_selects_top_papers_and_preserves_provenance(self) -> None:
        ranked = ranked_response()

        selected = select_papers(ranked, SelectionPolicy(max_papers=2))

        self.assertEqual([item.paper.paper_id for item in selected.selected_papers], ["paper-1", "paper-2"])
        self.assertIs(selected.ranked_search_response, ranked)

    def test_applies_minimum_score_before_limit(self) -> None:
        selected = select_papers(ranked_response(), SelectionPolicy(max_papers=5, min_score=5))

        self.assertEqual([item.rank for item in selected.selected_papers], [1, 2])


if __name__ == "__main__":
    unittest.main()
