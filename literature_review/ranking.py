"""Deterministic filtering and baseline ranking for retrieved papers."""

import math
import re

from literature_review.embedding_retriever import Encoder, encode_query
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
    """Remove unsuitable papers and retain the best-metadata same-title record."""
    by_title: dict[str, Paper] = {}
    for paper in papers:
        normalized_title = " ".join(paper.title.lower().split())
        citation_count = paper.citation_count or 0
        if policy.min_year is not None and paper.year < policy.min_year:
            continue
        if policy.max_year is not None and paper.year > policy.max_year:
            continue
        if citation_count < policy.min_citation_count:
            continue
        current = by_title.get(normalized_title)
        if current is None or duplicate_preference_key(paper) > duplicate_preference_key(current):
            by_title[normalized_title] = paper
    return list(by_title.values())


def duplicate_preference_key(paper: Paper) -> tuple[int, bool, int, int]:
    """Prefer a same-title record with stronger available metadata.

    This is a pragmatic provider-record choice, not proof that two title-equal
    records are the same scholarly work. Exact DOI-based matching can replace it
    when DOI metadata is added.
    """
    return (
        paper.citation_count or 0,
        paper.venue is not None,
        len(paper.abstract),
        paper.year,
    )


def rank_papers(papers: list[Paper], query: str) -> list[RankedPaper]:
    """Rank papers using lexical relevance, citations, and recency.

    This is intentionally a transparent baseline, not a claim of semantic relevance.
    """
    terms = query_terms(query)
    scored: list[tuple[Paper, float, list[str]]] = []
    for paper in papers:
        title = paper.title.lower()
        abstract = paper.abstract.lower()
        title_hits = sum(term in title for term in terms)
        abstract_hits = sum(term in abstract for term in terms)
        matched = sorted(term for term in terms if term in title or term in abstract)
        lexical_score = (3 * title_hits + abstract_hits) / (4 * max(len(terms), 1))
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
                f"Matched {len(matched)} query terms; citation & recency signals "
                "contribute equal weight in the normalized baseline score."
            ),
        )
        for index, (paper, score, matched) in enumerate(scored, start=1)
    ]


def rank_papers_embedding(
    papers: list[Paper], query: str, encoder: Encoder
) -> list[RankedPaper]:
    """Rank papers by embedding cosine similarity plus citation and recency.

    The query is prefixed with the official BGE retrieval prefix (via
    ``encode_query``); each paper's title and abstract are joined into a single
    text and encoded in one batch. Cosine similarity is clipped at zero so an
    unrelated paper is never dragged below its citation/recency contribution.
    """
    query_vector = encode_query(query, encoder)
    texts = [f"{paper.title}\n{paper.abstract}" for paper in papers]
    vectors = encoder(texts)
    if len(vectors) != len(papers):
        raise ValueError(
            f"encoder returned {len(vectors)} vectors; expected {len(papers)}"
        )

    scored: list[tuple[Paper, float]] = []
    for paper, vector in zip(papers, vectors, strict=True):
        similarity = max(
            sum(q * v for q, v in zip(query_vector, vector, strict=False)), 0.0
        )
        citation_score = min(math.log1p(paper.citation_count or 0) / math.log(1001), 1.0)
        recency_score = min(max((paper.year - 2020) / 10, 0.0), 1.0)
        total_score = similarity + citation_score + recency_score
        scored.append((paper, total_score))

    scored.sort(key=lambda item: (-item[1], -(item[0].citation_count or 0), -item[0].year))
    return [
        RankedPaper(
            paper=paper,
            rank=index,
            score=round(score, 3),
            matched_terms=[],
            rationale=(
                "Semantic ranking using bge-small-en-v1.5 embeddings of the query "
                "vs. title+abstract; citation & recency contribute equal weight."
            ),
        )
        for index, (paper, score) in enumerate(scored, start=1)
    ]


def filter_and_rank(
    response: SearchResponse,
    policy: FilterPolicy,
    *,
    encoder: Encoder | None = None,
) -> RankedSearchResponse:
    """Apply a policy and rank the surviving papers for one search query.

    With ``encoder`` set, the semantic embedding ranking is used; without one,
    the deterministic lexical baseline is used exactly as before.
    """
    papers = filter_papers(response.papers, policy)
    if encoder is not None:
        ranked = rank_papers_embedding(papers, response.request.query, encoder)
    else:
        ranked = rank_papers(papers, response.request.query)
    return RankedSearchResponse(
        search_response=response,
        filter_policy=policy,
        ranked_papers=ranked,
    )
