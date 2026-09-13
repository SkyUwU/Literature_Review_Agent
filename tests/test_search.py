import unittest
from urllib.parse import parse_qs, urlparse

from literature_review.models import SearchRequest
from literature_review.search import build_search_url, search_papers


class SearchTests(unittest.TestCase):
    def test_build_search_url_includes_constraints(self) -> None:
        url = build_search_url(
            SearchRequest(query="literature review agent", limit=3, year_from=2024, year_to=2026)
        )
        parameters = parse_qs(urlparse(url).query)
        self.assertEqual(parameters["search"], ["literature review agent"])
        self.assertEqual(parameters["per-page"], ["3"])
        self.assertEqual(
            parameters["filter"], ["from_publication_date:2024-01-01,to_publication_date:2026-12-31"]
        )

    def test_search_normalizes_valid_records_and_skips_incomplete_ones(self) -> None:
        def fake_fetcher(_: str) -> dict:
            return {
                "meta": {"count": 2},
                "results": [
                    {
                        "id": "https://openalex.org/W123",
                        "title": "A Test Paper",
                        "authorships": [{"author": {"display_name": "Ada Lovelace"}}],
                        "publication_year": 2025,
                        "abstract_inverted_index": {
                            "This": [0], "abstract": [1], "is": [2], "sufficient": [3],
                            "for": [4], "a": [5], "test": [6], "paper.": [7],
                        },
                        "primary_location": {
                            "landing_page_url": "https://example.org/paper",
                            "source": {"display_name": "TestConf"},
                        },
                        "cited_by_count": 12,
                    },
                    {
                        "id": "https://openalex.org/W456",
                        "title": "Incomplete Paper",
                        "authorships": [{"author": {"display_name": "Grace Hopper"}}],
                        "publication_year": 2025,
                        "abstract_inverted_index": None,
                    },
                ],
            }

        response = search_papers(
            SearchRequest(query="literature review agent", limit=2),
            json_fetcher=fake_fetcher,
        )

        self.assertEqual(response.total_candidates, 2)
        self.assertEqual(response.skipped_candidates, 1)
        self.assertEqual(response.papers[0].paper_id, "https://openalex.org/W123")
        self.assertEqual(response.papers[0].citation_count, 12)

    def test_truncated_abstract_record_is_skipped_not_crash(self) -> None:
        def fake_fetcher(_: str) -> dict:
            return {
                "meta": {"count": 1},
                "results": [
                    {
                        "id": "https://openalex.org/W789",
                        "title": "Truncated Abstract Paper",
                        "authorships": [{"author": {"display_name": "Alan Turing"}}],
                        "publication_year": 2025,
                        "abstract_inverted_index": {"How": [0], "LLMs": [1]},
                        "primary_location": {
                            "landing_page_url": "https://example.org/truncated",
                            "source": {"display_name": "TestConf"},
                        },
                        "cited_by_count": 3,
                    },
                ],
            }

        # "How LLMs" (9 chars) is shorter than Paper.abstract min_length=20;
        # paper_from_openalex must skip it instead of raising at construction.
        response = search_papers(
            SearchRequest(query="literature review agent", limit=1),
            json_fetcher=fake_fetcher,
        )

        self.assertEqual(response.total_candidates, 1)
        self.assertEqual(response.skipped_candidates, 1)
        self.assertEqual(response.papers, [])
