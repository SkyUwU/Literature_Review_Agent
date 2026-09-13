"""Search scholarly papers through the OpenAlex API."""

import argparse
import json
import sys
import time
from collections.abc import Callable
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from literature_review.models import (
    AssessmentPolicy,
    FilterPolicy,
    Paper,
    SearchRequest,
    SearchResponse,
    SelectionPolicy,
)
from literature_review.ranking import filter_and_rank
from literature_review.selection import select_papers
from literature_review.assessment import assess_selected_papers

OPENALEX_SEARCH_URL = "https://api.openalex.org/works"
USER_AGENT = "LiteratureReviewAgent/0.1 (academic-project)"
REQUESTED_FIELDS = (
    "id,title,authorships,publication_year,abstract_inverted_index,"
    "primary_location,cited_by_count,best_oa_location"
)


class PaperSearchError(RuntimeError):
    """Raised when a scholarly-paper provider cannot complete a request."""


JsonFetcher = Callable[[str], dict[str, Any]]


def build_search_url(request: SearchRequest) -> str:
    """Build the provider URL without making a network request."""
    parameters: dict[str, str | int] = {
        "search": request.query,
        "per-page": request.limit,
        "select": REQUESTED_FIELDS,
    }
    filters: list[str] = []
    if request.year_from is not None:
        filters.append(f"from_publication_date:{request.year_from}-01-01")
    if request.year_to is not None:
        filters.append(f"to_publication_date:{request.year_to}-12-31")
    if filters:
        parameters["filter"] = ",".join(filters)
    return f"{OPENALEX_SEARCH_URL}?{urlencode(parameters)}"


def fetch_json(url: str) -> dict[str, Any]:
    """Fetch JSON with a timeout and turn network failures into domain errors."""
    for attempt in range(3):
        try:
            request = Request(url, headers={"User-Agent": USER_AGENT})
            with urlopen(request, timeout=20) as response:  # noqa: S310 - fixed HTTPS provider URL
                return json.load(response)
        except HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace").strip()
            if detail:
                detail = detail[:500]
                raise PaperSearchError(f"OpenAlex returned HTTP {error.code}: {detail}") from error
            raise PaperSearchError(f"OpenAlex returned HTTP {error.code}.") from error
        except URLError as error:
            if attempt == 2:
                raise PaperSearchError(f"Could not connect to OpenAlex: {error.reason}") from error
            time.sleep(2**attempt)

    raise AssertionError("The retry loop must return or raise.")


def reconstruct_abstract(inverted_index: dict[str, list[int]] | None) -> str | None:
    """Convert OpenAlex's position-based abstract representation into plain text."""
    if not inverted_index:
        return None
    positions = {position: word for word, indexes in inverted_index.items() for position in indexes}
    return " ".join(positions[position] for position in range(max(positions) + 1) if position in positions)


def paper_from_openalex(record: dict[str, Any]) -> Paper | None:
    """Normalize one provider record; skip records without usable abstract evidence."""
    paper_id = record.get("id")
    title = record.get("title")
    abstract = reconstruct_abstract(record.get("abstract_inverted_index"))
    year = record.get("publication_year")
    authors = [
        authorship["author"]["display_name"]
        for authorship in record.get("authorships", [])
        if authorship.get("author", {}).get("display_name")
    ]

    if not all([paper_id, title, abstract, year]) or not authors:
        return None
    if len(abstract.strip()) < 20:
        # Paper.abstract min_length=20 raises at construction; skip truncated
        # OpenAlex abstracts here instead of crashing the whole search.
        return None

    best_oa = record.get("best_oa_location") or {}
    pdf_url = best_oa.get("pdf_url")

    return Paper(
        paper_id=paper_id,
        title=title,
        authors=authors,
        year=year,
        abstract=abstract,
        url=(record.get("primary_location") or {}).get("landing_page_url") or paper_id,
        venue=((record.get("primary_location") or {}).get("source") or {}).get("display_name"),
        citation_count=record.get("cited_by_count"),
        open_access_pdf_url=pdf_url,
    )


def search_papers(
    request: SearchRequest,
    *,
    json_fetcher: JsonFetcher = fetch_json,
) -> SearchResponse:
    """Search, normalize, and minimally filter papers from OpenAlex."""
    payload = json_fetcher(build_search_url(request))
    records = payload.get("results", [])
    papers = [paper for record in records if (paper := paper_from_openalex(record))]
    return SearchResponse(
        provider="openalex",
        request=request,
        total_candidates=(payload.get("meta") or {}).get("count", len(records)),
        papers=papers,
        skipped_candidates=len(records) - len(papers),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Search OpenAlex papers.")
    parser.add_argument("query", help="Plain-text research query")
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--year-from", type=int)
    parser.add_argument("--year-to", type=int)
    parser.add_argument("--rank", action="store_true", help="Filter and rank the retrieved papers")
    parser.add_argument("--min-citations", type=int, default=0)
    parser.add_argument("--top-k", type=int, help="Select the top K ranked papers for reading")
    parser.add_argument("--min-score", type=float, help="Minimum ranking score for selection")
    parser.add_argument("--assess", action="store_true", help="Create metadata-only assessments for selected papers")
    arguments = parser.parse_args()

    request = SearchRequest(
        query=arguments.query,
        limit=arguments.limit,
        year_from=arguments.year_from,
        year_to=arguments.year_to,
    )
    try:
        response = search_papers(request)
    except PaperSearchError as error:
        print(f"Search failed: {error}", file=sys.stderr)
        raise SystemExit(1) from error
    should_rank = (
        arguments.rank
        or arguments.top_k is not None
        or arguments.min_score is not None
        or arguments.assess
    )
    if should_rank:
        ranked_response = filter_and_rank(
            response,
            FilterPolicy(
                min_year=arguments.year_from,
                max_year=arguments.year_to,
                min_citation_count=arguments.min_citations,
            ),
        )
        if arguments.top_k is not None or arguments.min_score is not None or arguments.assess:
            selected_response = select_papers(
                ranked_response,
                SelectionPolicy(
                    max_papers=arguments.top_k or 5,
                    min_score=arguments.min_score,
                ),
            )
            if arguments.assess:
                assessed_response = assess_selected_papers(selected_response, AssessmentPolicy())
                print(assessed_response.model_dump_json(indent=2))
                return
            print(selected_response.model_dump_json(indent=2))
            return
        print(ranked_response.model_dump_json(indent=2))
        return
    print(response.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
