"""Deterministic filtering and baseline ranking for retrieved papers."""

import math
import re
import sys

from literature_review.embedding_retriever import Encoder, encode_query
from literature_review.models import (
    FilterPolicy,
    Paper,
    RankedPaper,
    RankedSearchResponse,
    SearchResponse,
)

STOP_WORDS = {"a", "an", "and", "for", "in", "of", "on", "the", "to", "with"}

TOP_VENUE_ALIASES: dict[str, tuple[str, ...]] = {
    # Applied as a post-filter whitelist on the provider-recorded venue string.
    # Keys are canonical short names; values are common written variants
    # (official full names and frequent spellings) seen across providers.
    "NeurIPS": (
        "neurips",
        "nips",
        "annual conference on neural information processing systems",
    ),
    "ICML": ("icml", "international conference on machine learning"),
    "ICLR": ("iclr", "international conference on learning representations"),
    "ACL": (
        "acl",
        "association for computational linguistics",
        "annual meeting of the association for computational linguistics",
    ),
    "EMNLP": ("emnlp", "conference on empirical methods in natural language processing"),
    "NAACL": (
        "naacl",
        "north american chapter of the association for computational linguistics",
    ),
    "SIGIR": (
        "sigir",
        "international acm sigir conference on research and development in information retrieval",
    ),
    "CIKM": ("cikm", "acm international conference on information and knowledge management"),
    "WSDM": ("wsdm", "acm international conference on web search and data mining"),
    "WWW": ("www", "the web conference", "international world wide web conference"),
    "KDD": (
        "kdd",
        "acm sigkdd conference on knowledge discovery and data mining",
        "knowledge discovery and data mining",
    ),
    "RecSys": ("recsys", "acm conference on recommender systems", "acm recommender systems"),
    "CVPR": (
        "cvpr",
        "ieee cvf conference on computer vision and pattern recognition",
        "computer vision and pattern recognition",
    ),
    "ICCV": ("iccv", "ieee international conference on computer vision"),
    "ECCV": ("eccv", "european conference on computer vision"),
    "AAAI": ("aaai", "aaai conference on artificial intelligence"),
    "IJCAI": ("ijcai", "international joint conference on artificial intelligence"),
}


def normalize_venue(text: str) -> str:
    """Lowercase and strip punctuation/whitespace so aliases can be compared."""
    return re.sub(r"[^a-z0-9]", "", text.lower())


def default_venues() -> tuple[str, ...]:
    """All normalized alias tokens (canonical keys + their variants) as a whitelist."""
    names = list(TOP_VENUE_ALIASES) + [
        alias for variants in TOP_VENUE_ALIASES.values() for alias in variants
    ]
    tokens = {normalize_venue(name) for name in names}
    return tuple(sorted(token for token in tokens if token))


def _build_venue_index() -> dict[str, str]:
    """Map every normalized conference key/alias to its canonical name."""
    index: dict[str, str] = {}
    for canonical, aliases in TOP_VENUE_ALIASES.items():
        for name in (canonical, *aliases):
            token = normalize_venue(name)
            if not token:
                continue
            prior = index.get(token)
            if prior is not None and prior != canonical:
                raise ValueError(
                    f"venue alias collision: {token!r} maps to both {prior!r} and {canonical!r}"
                )
            index[token] = canonical
    return index


_VENUE_BY_ALIAS = _build_venue_index()


def resolve_venues(raw: str | None) -> tuple[str, ...]:
    """Resolve a ``--venues`` value into normalized whitelist tokens.

    ``None`` returns the built-in top-venue default; ``""`` / ``"none"``
    disable the restriction; each recognized conference name (canonical key
    or alias, case/punctuation-insensitive) expands to that conference's full
    alias set; unrecognized names are warned on stderr and used as raw
    substring tokens; if every name is unrecognized the restriction is
    disabled with a warning.
    """
    if raw is None:
        return default_venues()
    if raw == "" or raw.strip().lower() == "none":
        return ()
    matched: list[str] = []
    unknown: list[tuple[str, str]] = []
    for part in raw.split(","):
        token = normalize_venue(part)
        if not token:
            continue
        canonical = _VENUE_BY_ALIAS.get(token)
        if canonical is None:
            unknown.append((part, token))
            continue
        for name in (canonical, *TOP_VENUE_ALIASES[canonical]):
            expanded = normalize_venue(name)
            if expanded and expanded not in matched:
                matched.append(expanded)
    if not matched:
        if unknown:
            print(
                f"[venues] 所有輸入均未識別頂會，停用頂會過濾（輸入：{raw!r}）",
                file=sys.stderr,
            )
        return ()
    tokens = list(matched)
    for part, token in unknown:
        print(
            f"[venues] 未識別頂會 {part!r}（→{token!r}），以 raw 子字串過濾",
            file=sys.stderr,
        )
        if token not in tokens:
            tokens.append(token)
    return tuple(tokens)


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
        if policy.venues and not _matches_venues(paper.venue, policy.venues):
            continue
        current = by_title.get(normalized_title)
        if current is None or duplicate_preference_key(paper) > duplicate_preference_key(current):
            by_title[normalized_title] = paper
    return list(by_title.values())


def _matches_venues(venue: str | None, whitelist: tuple[str, ...]) -> bool:
    """True when the normalized venue contains any whitelist token.

    Containment (not equality) tolerates provider strings like
    "Proceedings of the International Conference on Learning Representations (ICLR)".
    """
    if not venue:
        return False
    normalized = normalize_venue(venue)
    return any(token and token in normalized for token in whitelist)


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
