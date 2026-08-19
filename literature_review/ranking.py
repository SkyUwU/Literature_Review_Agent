"""Deterministic filtering and baseline ranking for retrieved papers."""

import math
import re

from literature_review.models import (
    FilterPolicy,
    Paper,
    RankedPaper,
    RankedSearchResponse,
    SearchResponse,
)

STOP_WORDS = {"a", "an", "and", "for", "in", "of", "on", "the", "to", "with"}


def query_terms(query: str) -> set[str]:
    """Extract simple, explainable keywords from a natural-language query."""
    return {
        term.lower()
        for term in re.findall(r"[a-zA-Z0-9]+", query)
        if len(term) >= 3 and term.lower() not in STOP_WORDS
    }


def filter_papers(papers: list[Paper], policy: FilterPolicy) -> list[Paper]:
    """Remove duplicate titles and candidates outside explicit quality constraints."""
    filtered: list[Paper] = []
    seen_titles: set[str] = set()
    for paper in papers:
        normalized_title = " ".join(paper.title.lower().split())
        citation_count = paper.citation_count or 0
        if normalized_title in seen_titles:
            continue
        if policy.min_year is not None and paper.year < policy.min_year:
            continue
        if policy.max_year is not None and paper.year > policy.max_year:
            continue
        if citation_count < policy.min_citation_count:
            continue
        seen_titles.add(normalized_title)
        filtered.append(paper)
    return filtered


def rank_papers(papers: list[Paper], query: str) -> list[RankedPaper]:
    """Rank papers using lexical relevance, citations, and recency.

    This is intentionally a transparent baseline, not a claim of semantic relevance.
    """
    terms = query_terms(query)
    scored: list[tuple[Paper, float, list[str]]] = []
    for paper in papers:
        title = paper.title.lower()
        abstract = paper.abstract.lower()
        matched = sorted(term for term in terms if term in title or term in abstract)
        title_matches = sum(term in title for term in terms)
        abstract_matches = sum(term in abstract for term in terms)
        lexical_score = 3.0 * title_matches + abstract_matches
        citation_score = min(math.log1p(paper.citation_count or 0) / math.log(1001), 1.0)
        recency_score = min(max((paper.year - 2020) / 10, 0.0), 1.0)
        total_score = lexical_score + citation_score + recency_score
        scored.append((paper, total_score, matched))

    scored.sort(key=lambda item: (-item[1], -(item[0].citation_count or 0), -item[0].year))
    return [
        RankedPaper(
            paper=paper,
            rank=index,
            score=round(score, 3),
            matched_terms=matched,
            rationale=(
                f"Matched {len(matched)} query terms; includes citation and recency "
                "signals in the baseline score."
            ),
        )
        for index, (paper, score, matched) in enumerate(scored, start=1)
    ]


def filter_and_rank(response: SearchResponse, policy: FilterPolicy) -> RankedSearchResponse:
    """Apply a policy and rank the surviving papers for one search query."""
    papers = filter_papers(response.papers, policy)
    return RankedSearchResponse(
        search_response=response,
        filter_policy=policy,
        ranked_papers=rank_papers(papers, response.request.query),
    )
