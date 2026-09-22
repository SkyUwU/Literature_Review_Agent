import sys
import unittest
from urllib.parse import parse_qs, urlparse
from unittest import mock

from literature_review import search as search_module
from literature_review.models import (
    FilterPolicy,
    RankedSearchResponse,
    SearchRequest,
    SearchResponse,
)
from literature_review.ranking import default_venues
from literature_review.search import build_search_url, norm_doi, search_papers


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

    def test_norm_doi_strips_prefix_and_lowercases(self) -> None:
        self.assertEqual(norm_doi("https://doi.org/10.1145/ABC.123"), "10.1145/abc.123")
        self.assertEqual(norm_doi("http://doi.org/10.1/X"), "10.1/x")
        self.assertEqual(norm_doi("doi:10.1145/Abc"), "10.1145/abc")
        self.assertEqual(norm_doi("  10.9/Trim  "), "10.9/trim")
        self.assertIsNone(norm_doi(None))
        self.assertIsNone(norm_doi("   "))
        self.assertIsNone(norm_doi(123))

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

    def test_search_propagates_normalised_doi_and_none_when_absent(self) -> None:
        def fake_fetcher(_: str) -> dict:
            return {
                "meta": {"count": 2},
                "results": [
                    {
                        "id": "https://openalex.org/W901",
                        "doi": "https://doi.org/10.1145/XYZ.9",
                        "title": "A DOI Paper",
                        "authorships": [{"author": {"display_name": "Ada Lovelace"}}],
                        "publication_year": 2025,
                        "abstract_inverted_index": {
                            "This": [0], "abstract": [1], "is": [2], "sufficient": [3],
                            "for": [4], "a": [5], "test": [6], "paper.": [7],
                        },
                        "primary_location": {
                            "landing_page_url": "https://example.org/doi-paper",
                            "source": {"display_name": "TestConf"},
                        },
                        "cited_by_count": 5,
                    },
                    {
                        "id": "https://openalex.org/W902",
                        "title": "No DOI Paper",
                        "authorships": [{"author": {"display_name": "Grace Hopper"}}],
                        "publication_year": 2025,
                        "abstract_inverted_index": {
                            "This": [0], "abstract": [1], "is": [2], "sufficient": [3],
                            "for": [4], "a": [5], "test": [6], "paper.": [7],
                        },
                        "primary_location": {
                            "landing_page_url": "https://example.org/no-doi",
                            "source": {"display_name": "TestConf"},
                        },
                        "cited_by_count": 1,
                    },
                ],
            }

        response = search_papers(
            SearchRequest(query="literature review agent", limit=2),
            json_fetcher=fake_fetcher,
        )

        self.assertEqual(response.papers[0].doi, "10.1145/xyz.9")
        self.assertIsNone(response.papers[1].doi)

    def test_search_main_cli_threads_venues_into_filter_policy(self) -> None:
        response = SearchResponse(
            provider="openalex",
            request=SearchRequest(query="test", limit=1),
            total_candidates=0,
            papers=[],
            skipped_candidates=0,
        )
        captured: list[FilterPolicy] = []

        def capture_policy(search_response, policy, **_kwargs) -> RankedSearchResponse:
            captured.append(policy)
            return RankedSearchResponse(
                search_response=search_response,
                filter_policy=policy,
                ranked_papers=[],
            )

        with (
            mock.patch(
                "literature_review.search.search_papers", return_value=response
            ) as search_mock,
            mock.patch(
                "literature_review.search.filter_and_rank",
                side_effect=capture_policy,
            ) as rank_mock,
        ):
            with mock.patch.object(
                sys,
                "argv",
                [
                    "literature_review.search",
                    "test",
                    "--rank",
                    "--venues",
                    "neurips, icml",
                    "--year-from",
                    "2019",
                ],
            ), mock.patch("sys.stdout"):
                search_module.main()

        search_mock.assert_called_once()
        self.assertEqual(rank_mock.call_count, 1)
        self.assertEqual(captured[0].min_year, 2019)
        self.assertEqual(
            captured[0].venues,
            (
                "neurips",
                "nips",
                "annualconferenceonneuralinformationprocessingsystems",
                "icml",
                "internationalconferenceonmachinelearning",
            ),
        )

    def test_search_main_cli_defaults_venues_to_top_list(self) -> None:
        response = SearchResponse(
            provider="openalex",
            request=SearchRequest(query="test", limit=1),
            total_candidates=0,
            papers=[],
            skipped_candidates=0,
        )
        captured: list[FilterPolicy] = []

        def capture_policy(search_response, policy, **_kwargs) -> RankedSearchResponse:
            captured.append(policy)
            return RankedSearchResponse(
                search_response=search_response,
                filter_policy=policy,
                ranked_papers=[],
            )

        with mock.patch("literature_review.search.search_papers", return_value=response):
            with mock.patch(
                "literature_review.search.filter_and_rank", side_effect=capture_policy
            ):
                with mock.patch.object(
                    sys, "argv", ["literature_review.search", "test", "--rank"]
                ), mock.patch("sys.stdout"):
                    search_module.main()

        self.assertEqual(captured[0].venues, default_venues())
