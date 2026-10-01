"""Search scholarly papers through the Semantic Scholar Graph API.

The adapter mirrors :mod:`literature_review.search` (same ``SearchRequest`` and
``SearchResponse`` contracts) so the pipeline can swap providers. Semantic
Scholar allows ~1 request/second with an API key, so every call is paced and
HTTP 429 responses are retried with exponential backoff.
"""

from __future__ import annotations

import json
import random
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from literature_review.models import Paper, SearchRequest, SearchResponse
from literature_review.search import norm_doi

SS_SEARCH_URL = "https://api.semanticscholar.org/graph/v1/paper/search"
SS_FIELDS = (
    "title",
    "abstract",
    "year",
    "authors",
    "externalIds",
    "citationCount",
    "openAccessPdf",
    "venue",
    "url",
)
SS_PACE_SECONDS = 2.0  # 2x the keyed 1 RPS limit, so a 429 stays rare
SS_MAX_RETRIES = 3
SS_USER_AGENT = "LiteratureReviewAgent/0.1 (academic-project)"
ABSTRACT_PLACEHOLDER = "Abstract not available for this paper."

_LAST_REQUEST_AT: list[float] = [0.0]


class SsSearchError(RuntimeError):
    """Raised when Semantic Scholar cannot complete a request."""


def build_search_url(request: SearchRequest) -> str:
    """Build the Semantic Scholar URL without making a network request."""
    parameters: dict[str, str | int] = {
        "query": request.query,
        "fields": ",".join(SS_FIELDS),
        "limit": request.limit,
    }
    if request.year_from is not None or request.year_to is not None:
        parameters["year"] = f"{request.year_from or ''}-{request.year_to or ''}"
    if request.venues:
        parameters["venue"] = ",".join(request.venues)
    return f"{SS_SEARCH_URL}?{urlencode(parameters)}"


def _pace(pace_seconds: float = SS_PACE_SECONDS) -> None:
    elapsed = time.monotonic() - _LAST_REQUEST_AT[0]
    remaining = pace_seconds - elapsed
    if remaining > 0:
        time.sleep(remaining)
    _LAST_REQUEST_AT[0] = time.monotonic()


def _request_json(url: str, api_key: str) -> dict[str, Any]:
    request = Request(
        url,
        headers={
            "x-api-key": api_key,
            "User-Agent": SS_USER_AGENT,
            "Accept": "application/json",
        },
    )
    with urlopen(request, timeout=30) as response:  # noqa: S310 - fixed HTTPS provider URL
        return json.load(response)


def _retry_delay(error: HTTPError, attempt: int) -> float:
    retry_after = error.headers.get("Retry-After") if error.headers else None
    if retry_after:
        try:
            return float(retry_after)
        except ValueError:
            pass
    return 2**attempt + random.uniform(0, 0.5)


def ss_get_json(url: str, api_key: str) -> dict[str, Any]:
    """GET one Semantic Scholar endpoint, paced and with 429 backoff."""
    for attempt in range(SS_MAX_RETRIES):
        _pace()
        try:
            return _request_json(url, api_key)
        except HTTPError as error:
            if error.code != 429 or attempt == SS_MAX_RETRIES - 1:
                raise SsSearchError(f"Semantic Scholar returned HTTP {error.code}.") from error
            time.sleep(_retry_delay(error, attempt))
        except URLError as error:
            if attempt == SS_MAX_RETRIES - 1:
                raise SsSearchError(
                    f"Could not connect to Semantic Scholar: {error.reason}"
                ) from error
            time.sleep(2**attempt + random.uniform(0, 0.5))

    raise RuntimeError("The retry loop must return or raise.")


def paper_from_ss(record: dict[str, Any]) -> Paper | None:
    """Normalise one Semantic Scholar record; skip records missing required fields."""
    paper_id = record.get("paperId")
    title = record.get("title")
    year = record.get("year")
    authors = [author["name"] for author in record.get("authors") or [] if author.get("name")]

    if not paper_id or not title or not authors:
        return None
    if not isinstance(year, int) or not 1900 <= year <= 2100:
        return None

    abstract = record.get("abstract")
    if not isinstance(abstract, str) or len(abstract.strip()) < 20:
        abstract = ABSTRACT_PLACEHOLDER

    pdf = record.get("openAccessPdf") or {}
    url = record.get("url") or f"https://www.semanticscholar.org/paper/{paper_id}"

    return Paper(
        paper_id=paper_id,
        doi=norm_doi((record.get("externalIds") or {}).get("DOI")),
        title=title,
        authors=authors,
        year=year,
        abstract=abstract,
        url=url,
        venue=record.get("venue") or None,
        citation_count=record.get("citationCount") or 0,
        open_access_pdf_url=pdf.get("url") or None,
    )


def search_ss(request: SearchRequest, *, api_key: str) -> SearchResponse:
    """Search Semantic Scholar and normalise the response into the shared contract."""
    payload = ss_get_json(build_search_url(request), api_key)
    records = payload.get("data") or []
    papers = [paper for record in records if (paper := paper_from_ss(record)) is not None]
    return SearchResponse(
        provider="semantic_scholar",
        request=request,
        total_candidates=len(records),
        total_matches=payload.get("total"),
        papers=papers,
        skipped_candidates=len(records) - len(papers),
    )
