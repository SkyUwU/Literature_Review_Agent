import unittest
from collections.abc import Sequence

from literature_review.models import FilterPolicy, Paper, SearchRequest, SearchResponse
from literature_review.ranking import (
    filter_and_rank,
    filter_papers,
    rank_papers,
    rank_papers_embedding,
)


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


class KeywordEncoder:
    """Deterministic fake encoder: keyword presence decides the vector.

    Texts containing ``keyword`` map to [1, 0] (max query similarity),
    texts containing "negative" map to [-1, 0] (below the clip floor),
    everything else maps to [0, 1] (orthogonal to the query).
    """

    def __init__(self, keyword: str) -> None:
        self.keyword = keyword

    def __call__(self, texts: Sequence[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in texts:
            lowered = text.lower()
            if self.keyword in lowered:
                vectors.append([1.0, 0.0])
            elif "negative" in lowered:
                vectors.append([-1.0, 0.0])
            else:
                vectors.append([0.0, 1.0])
        return vectors


class RankingTests(unittest.TestCase):
    def test_filter_removes_duplicate_and_old_papers(self) -> None:
        papers = [
            paper("Literature Review Agent", 2025, 10),
            paper("literature review agent", 2025, 20),
            paper("Older Agent", 2020, 100),
        ]
        filtered = filter_papers(papers, FilterPolicy(min_year=2024))
        self.assertEqual([item.citation_count for item in filtered], [20])

    def test_filter_prefers_duplicate_with_better_metadata(self) -> None:
        weaker = paper("Literature Review Agent", 2025, 10)
        stronger = paper("literature review agent", 2024, 10)
        stronger.venue = "Journal of Test Cases"
        stronger.abstract = "This is a longer abstract with evidence about literature review agents and retrieval."

        filtered = filter_papers([weaker, stronger], FilterPolicy())

        self.assertEqual(filtered, [stronger])

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

    # -- embedding ranking (Todo 1) -----------------------------------------

    def test_embedding_ranks_by_cosine_similarity(self) -> None:
        encoder = KeywordEncoder("semantic")
        ranked = rank_papers_embedding(
            [
                paper("Unrelated Tool", 2024, 5),
                paper("Semantic Survey", 2024, 5),
            ],
            "semantic ranking",
            encoder,
        )
        self.assertEqual(ranked[0].paper.title, "Semantic Survey")
        self.assertGreater(ranked[0].score, ranked[1].score)
        self.assertEqual(ranked[0].matched_terms, [])
        self.assertIn("Semantic ranking", ranked[0].rationale)

    def test_embedding_three_components_citation_and_recency_compensate(self) -> None:
        encoder = KeywordEncoder("semantic")
        ranked = rank_papers_embedding(
            [
                paper("Semantic Paper", 2021, 0),
                paper("Unrelated Survey", 2025, 1000),
            ],
            "semantic ranking",
            encoder,
        )
        # cosine 1.0 + 0.1 recency vs cosine 0.0 + ~1.0 citation + 0.5 recency
        self.assertEqual(ranked[0].paper.title, "Unrelated Survey")
        self.assertGreater(ranked[0].score, ranked[1].score)

    def test_embedding_negative_similarity_clipped_to_zero(self) -> None:
        encoder = KeywordEncoder("semantic")
        ranked = rank_papers_embedding(
            [paper("Negative Paper", 2024, 0)],
            "semantic ranking",
            encoder,
        )
        self.assertEqual(ranked[0].score, 0.4)

    def test_embedding_rejects_wrong_vector_count(self) -> None:
        def broken_encoder(texts: Sequence[str]) -> list[list[float]]:
            return [[0.0, 1.0] for _ in texts] + [[0.0, 1.0]]

        with self.assertRaisesRegex(ValueError, "encoder returned 3 vectors; expected 2"):
            rank_papers_embedding(
                [paper("One", 2024, 0), paper("Two", 2024, 0)],
                "semantic ranking",
                broken_encoder,
            )

    def test_filter_and_rank_with_encoder_uses_embedding_path(self) -> None:
        response = SearchResponse(
            provider="openalex",
            request=SearchRequest(query="semantic ranking"),
            total_candidates=2,
            papers=[
                paper("Unrelated Tool", 2024, 5),
                paper("Semantic Survey", 2024, 5),
            ],
            skipped_candidates=0,
        )
        result = filter_and_rank(response, FilterPolicy(min_year=2023), encoder=KeywordEncoder("semantic"))
        self.assertEqual(result.ranked_papers[0].paper.title, "Semantic Survey")
        self.assertEqual(result.ranked_papers[0].matched_terms, [])
        self.assertIn("Semantic ranking", result.ranked_papers[0].rationale)

    def test_filter_and_rank_without_encoder_keeps_lexical_path(self) -> None:
        response = SearchResponse(
            provider="openalex",
            request=SearchRequest(query="literature review agent"),
            total_candidates=1,
            papers=[paper("Literature Review Agent", 2025, 10)],
            skipped_candidates=0,
        )
        result = filter_and_rank(response, FilterPolicy(min_year=2024))
        self.assertIn("Matched", result.ranked_papers[0].rationale)
        self.assertNotEqual(result.ranked_papers[0].matched_terms, [])
