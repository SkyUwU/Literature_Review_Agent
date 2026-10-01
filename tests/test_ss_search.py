"""Tests for the Semantic Scholar adapter: mocked network only."""

import unittest
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse
from unittest.mock import patch

from literature_review.models import SearchRequest
from literature_review.ss_search import (
    ABSTRACT_PLACEHOLDER,
    SS_FIELDS,
    SsSearchError,
    build_search_url,
    paper_from_ss,
    search_ss,
    ss_get_json,
)


def ss_record(**overrides: object) -> dict:
    record: dict = {
        "paperId": "abc123",
        "title": "A Test Paper",
        "year": 2024,
        "authors": [{"name": "Ada Lovelace"}],
        "abstract": "This is a sufficiently long abstract for testing purposes.",
        "externalIds": {"DOI": "10.1145/ABC.123"},
        "citationCount": 12,
        "openAccessPdf": {"url": "https://example.org/paper.pdf"},
        "venue": "TestConf",
        "url": "https://www.semanticscholar.org/paper/abc123",
    }
    record.update(overrides)
    return record


class BuildUrlTests(unittest.TestCase):
    def test_build_search_url_includes_query_fields_and_year(self) -> None:
        url = build_search_url(
            SearchRequest(query="literature review agent", limit=5, year_from=2024)
        )
        parameters = parse_qs(urlparse(url).query)
        self.assertEqual(parameters["query"], ["literature review agent"])
        self.assertEqual(parameters["limit"], ["5"])
        self.assertEqual(parameters["year"], ["2024-"])
        self.assertEqual(parameters["fields"], [",".join(SS_FIELDS)])

    def test_build_search_url_omits_year_when_unbounded(self) -> None:
        url = build_search_url(SearchRequest(query="literature review agent", limit=3))
        parameters = parse_qs(urlparse(url).query)
        self.assertNotIn("year", parameters)

    def test_build_search_url_includes_year_range_and_multiple_venues(self) -> None:
        url = build_search_url(
            SearchRequest(
                query="literature review agent",
                limit=10,
                year_from=2024,
                year_to=2026,
                venues=("NeurIPS", "International conference on machine learning"),
            )
        )
        parameters = parse_qs(urlparse(url).query)
        self.assertEqual(parameters["year"], ["2024-2026"])
        self.assertEqual(
            parameters["venue"],
            ["NeurIPS,International conference on machine learning"],
        )


class PaperFromSsTests(unittest.TestCase):
    def test_normalises_valid_record(self) -> None:
        paper = paper_from_ss(ss_record())
        self.assertIsNotNone(paper)
        assert paper is not None
        self.assertEqual(paper.paper_id, "abc123")
        self.assertEqual(paper.doi, "10.1145/abc.123")
        self.assertEqual(paper.citation_count, 12)
        self.assertEqual(str(paper.open_access_pdf_url), "https://example.org/paper.pdf")
        self.assertEqual(paper.venue, "TestConf")

    def test_missing_external_ids_yields_none_doi(self) -> None:
        paper = paper_from_ss(ss_record(externalIds=None))
        self.assertIsNotNone(paper)
        assert paper is not None
        self.assertIsNone(paper.doi)

    def test_empty_external_ids_yields_none_doi(self) -> None:
        paper = paper_from_ss(ss_record(externalIds={}))
        assert paper is not None
        self.assertIsNone(paper.doi)

    def test_null_open_access_pdf_yields_none(self) -> None:
        paper = paper_from_ss(ss_record(openAccessPdf=None))
        assert paper is not None
        self.assertIsNone(paper.open_access_pdf_url)

    def test_empty_open_access_pdf_url_yields_none(self) -> None:
        paper = paper_from_ss(ss_record(openAccessPdf={"url": ""}))
        assert paper is not None
        self.assertIsNone(paper.open_access_pdf_url)

    def test_null_abstract_is_replaced_by_placeholder(self) -> None:
        paper = paper_from_ss(ss_record(abstract=None))
        assert paper is not None
        self.assertEqual(paper.abstract, ABSTRACT_PLACEHOLDER)
        self.assertGreaterEqual(len(paper.abstract), 20)

    def test_short_abstract_is_replaced_by_placeholder(self) -> None:
        paper = paper_from_ss(ss_record(abstract="Too short"))
        assert paper is not None
        self.assertEqual(paper.abstract, ABSTRACT_PLACEHOLDER)

    def test_missing_url_falls_back_to_semantic_scholar_page(self) -> None:
        paper = paper_from_ss(ss_record(url=None))
        assert paper is not None
        self.assertEqual(
            str(paper.url), "https://www.semanticscholar.org/paper/abc123"
        )

    def test_missing_citation_count_defaults_to_zero(self) -> None:
        paper = paper_from_ss(ss_record(citationCount=None))
        assert paper is not None
        self.assertEqual(paper.citation_count, 0)

    def test_records_missing_required_fields_are_skipped(self) -> None:
        self.assertIsNone(paper_from_ss(ss_record(paperId=None)))
        self.assertIsNone(paper_from_ss(ss_record(title=None)))
        self.assertIsNone(paper_from_ss(ss_record(year=None)))
        self.assertIsNone(paper_from_ss(ss_record(authors=[])))
        self.assertIsNone(paper_from_ss(ss_record(year=1800)))

    def test_venue_none_when_absent(self) -> None:
        paper = paper_from_ss(ss_record(venue=None))
        assert paper is not None
        self.assertIsNone(paper.venue)


class SearchSsTests(unittest.TestCase):
    def test_search_returns_shared_contract_and_keeps_placeholder_papers(self) -> None:
        records = [ss_record(paperId=f"p{i}") for i in range(9)]
        records.append(ss_record(paperId="p-null", abstract=None, url=None))

        with patch("literature_review.ss_search.ss_get_json", return_value={"data": records}):
            response = search_ss(SearchRequest(query="literature review agent", limit=10), api_key="k")

        self.assertEqual(response.provider, "semantic_scholar")
        self.assertEqual(response.total_candidates, 10)
        self.assertEqual(response.skipped_candidates, 0)
        self.assertEqual(len(response.papers), 10)
        self.assertTrue(all(paper.citation_count is not None for paper in response.papers))
        self.assertTrue(
            any(paper.abstract == ABSTRACT_PLACEHOLDER for paper in response.papers)
        )

    def test_search_counts_skipped_records(self) -> None:
        records = [ss_record(), ss_record(paperId=None), ss_record(authors=[])]
        with patch("literature_review.ss_search.ss_get_json", return_value={"data": records}):
            response = search_ss(SearchRequest(query="literature review agent", limit=3), api_key="k")

        self.assertEqual(response.total_candidates, 3)
        self.assertEqual(response.skipped_candidates, 2)
        self.assertEqual(len(response.papers), 1)

    def test_search_tolerates_missing_data_key(self) -> None:
        with patch("literature_review.ss_search.ss_get_json", return_value={}):
            response = search_ss(SearchRequest(query="literature review agent", limit=3), api_key="k")

        self.assertEqual(response.total_candidates, 0)
        self.assertEqual(response.papers, [])


class RateLimitTests(unittest.TestCase):
    def setUp(self) -> None:
        import literature_review.ss_search as ss_search

        ss_search._LAST_REQUEST_AT[0] = 0.0

    def test_429_is_retried_then_succeeds(self) -> None:
        error = HTTPError(
            "https://api.semanticscholar.org", 429, "Too Many Requests", {"Retry-After": "1"}, None
        )
        outcomes: list[object] = [error, {"data": []}]

        def fake_request(url: str, api_key: str) -> dict:
            value = outcomes.pop(0)
            if isinstance(value, Exception):
                raise value
            return value

        with (
            patch("literature_review.ss_search._request_json", side_effect=fake_request),
            patch("literature_review.ss_search.time.sleep") as sleep,
        ):
            result = ss_get_json("https://api.semanticscholar.org", "k")

        self.assertEqual(result, {"data": []})
        self.assertGreaterEqual(sleep.call_count, 2)
        self.assertIn(1.0, [call.args[0] for call in sleep.call_args_list])

    def test_network_error_is_retried_with_backoff(self) -> None:
        calls = {"count": 0}

        def fake_request(url: str, api_key: str) -> dict:
            calls["count"] += 1
            if calls["count"] == 1:
                raise URLError("connection refused")
            return {"data": []}

        with (
            patch("literature_review.ss_search._request_json", side_effect=fake_request),
            patch("literature_review.ss_search.time.sleep") as sleep,
        ):
            result = ss_get_json("https://api.semanticscholar.org", "k")

        self.assertEqual(result, {"data": []})
        self.assertEqual(calls["count"], 2)
        self.assertTrue(sleep.called)

    def test_pacing_sleeps_between_calls(self) -> None:
        with (
            patch("literature_review.ss_search._request_json", return_value={"data": []}) as request,
            patch("literature_review.ss_search.time.sleep") as sleep,
        ):
            ss_get_json("https://api.semanticscholar.org", "k")
            ss_get_json("https://api.semanticscholar.org", "k")

        self.assertEqual(request.call_count, 2)
        self.assertTrue(sleep.called)
        self.assertGreaterEqual(sleep.call_args_list[0].args[0], 1.0)

    def test_429_exhaustion_raises_error(self) -> None:
        error = HTTPError(
            "https://api.semanticscholar.org", 429, "Too Many Requests", {"Retry-After": "0"}, None
        )
        with (
            patch("literature_review.ss_search._request_json", side_effect=error) as request,
            patch("literature_review.ss_search.time.sleep"),
        ):
            with self.assertRaises(SsSearchError):
                ss_get_json("https://api.semanticscholar.org", "k")

        self.assertEqual(request.call_count, 3)

    def test_non_429_http_error_is_not_retried(self) -> None:
        error = HTTPError("https://api.semanticscholar.org", 403, "Forbidden", {}, None)
        with (
            patch("literature_review.ss_search._request_json", side_effect=error) as request,
            patch("literature_review.ss_search.time.sleep"),
        ):
            with self.assertRaises(SsSearchError):
                ss_get_json("https://api.semanticscholar.org", "k")

        self.assertEqual(request.call_count, 1)


if __name__ == "__main__":
    unittest.main()
