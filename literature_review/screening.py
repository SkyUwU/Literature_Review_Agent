"""LLM screening layer: keep/maybe/reject decisions plus global gap analysis (M5e).

The screening layer sits between embedding ranking and PDF download. Each query's
top candidates are sampled into three diversity buckets (authority / frontier /
cross-domain), then all sampled candidates are handed to the LLM in **one** call
together with the gap analysis (``screen_candidates``). The LLM returns copy-style
decisions keyed by the paper *title* (never by index), so the program composes the
real ``paper_id`` provenance back — avoiding the index-tampering failure mode
observed in the RCS stage (M5b/M5b.1 evidence).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable

from pydantic import BaseModel, Field

from literature_review.llm_evidence import JsonGenerationClient, generate_validated, strip_code_fence
from literature_review.models import RankedPaper

__all__ = [
    "FollowUpQuery",
    "GapAnalysis",
    "ScreenDecision",
    "ScreeningError",
    "ScreeningResult",
    "build_screening_prompt",
    "sample_candidates",
    "screen_candidates",
]

_PRIORITIES = ("keep", "maybe", "reject")
DEFAULT_PER_QUERY_TARGET = 24
_BUCKET_A_RATIO = 0.25
_BUCKET_B_RATIO = 0.50
_BUCKET_C_RATIO = 0.25


class ScreenDecision(BaseModel):
    """One keep/maybe/reject decision for a candidate paper, resolved to its id."""

    paper_id: str
    priority: str = Field(pattern="^(keep|maybe|reject)$")
    reason: str = Field(min_length=5, description="Decision rationale; required for every priority.")
    paper_title: str = Field(min_length=1, description="Trusted title copied back by the program.")


class FollowUpQuery(BaseModel):
    """One follow-up search query suggested by the gap analysis."""

    query: str = Field(min_length=3)
    target_gap: str = Field(min_length=3)
    reason: str = Field(min_length=3)


class GapAnalysis(BaseModel):
    """What the current candidate pool already covers and what it is missing."""

    covered_areas: list[str] = Field(default_factory=list)
    missing_pieces: list[str] = Field(default_factory=list)
    follow_up_queries: list[FollowUpQuery] = Field(default_factory=list, max_length=3)


class ScreeningResult(BaseModel):
    """Per-query decisions plus the global gap analysis from one screening call."""

    decisions: dict[str, list[ScreenDecision]] = Field(default_factory=dict)
    gap: GapAnalysis = Field(default_factory=GapAnalysis)
    screened_at: datetime


class _LlmScreenDecision(BaseModel):
    """LLM-facing decision keyed by the verbatim paper title (copy-style output)."""

    title: str = Field(min_length=1)
    priority: str = Field(pattern="^(keep|maybe|reject)$")
    reason: str = Field(min_length=5)


class _LlmScreeningOutput(BaseModel):
    """The single JSON object an LLM must return for all candidates plus the gap."""

    decisions: list[_LlmScreenDecision] = Field(min_length=1)
    covered_areas: list[str] = Field(default_factory=list)
    missing_pieces: list[str] = Field(default_factory=list)
    follow_up_queries: list[FollowUpQuery] = Field(default_factory=list, max_length=3)


class ScreeningError(RuntimeError):
    """Raised when an LLM screening output cannot be resolved to the candidates."""


def _normalize_title(title: str) -> str:
    return " ".join(title.lower().split())


def _rank_percentile(item: RankedPaper, count: int) -> float:
    """0.0 = best-ranked candidate, 1.0 = worst (per-query distribution)."""
    if count <= 1:
        return 0.0
    return (item.rank - 1) / (count - 1)


def _citation_percentile(
    item: RankedPaper, sorted_by_citation: list[RankedPaper]
) -> float:
    """Fraction of candidates with equal-or-higher citation count (0.0 = most cited)."""
    count = len(sorted_by_citation)
    if count <= 1:
        return 0.0
    position = sorted_by_citation.index(item)
    return position / (count - 1)


def _sample_bucket(
    ranked_papers: list[RankedPaper],
    *,
    max_rank_pct: float,
    min_rank_pct: float = 0.0,
    citation_top_pct: float | None = None,
    recent_year_cutoff: int | None = None,
) -> list[RankedPaper]:
    """Return bucket members, preserving the caller's rank order."""
    count = len(ranked_papers)
    by_citation = sorted(ranked_papers, key=lambda r: r.paper.citation_count or 0, reverse=True)
    selected: list[RankedPaper] = []
    for item in ranked_papers:
        rank_pct = _rank_percentile(item, count)
        if not (min_rank_pct <= rank_pct <= max_rank_pct):
            continue
        if citation_top_pct is not None:
            if item.paper.citation_count is None:
                continue
            if _citation_percentile(item, by_citation) > citation_top_pct:
                continue
        if recent_year_cutoff is not None and (item.paper.year or 0) < recent_year_cutoff:
            continue
        selected.append(item)
    return selected


def sample_candidates(
    ranked_papers: list[RankedPaper],
    *,
    per_query_target: int = DEFAULT_PER_QUERY_TARGET,
) -> list[RankedPaper]:
    """Sample a bounded, diversity-preserving candidate window for one query.

    Bucket conditions use the query's own rank / citation / year distribution
    (not absolute embedding values)::

        A authority (~25%): rank pct <= 20% AND citation pct <= 30%
        B frontier  (~50%): rank pct <= 50% AND published in the last 1-2 years
        C cross     (~25%): rank pct 40-70% AND citation pct <= 30%

    Output order is A -> B -> C with rank order preserved inside each bucket;
    duplicates between buckets are kept once. When the ranked list is at or
    below ``per_query_target`` the whole list is returned (bucketing only
    matters for large candidate pools, avoiding the K3 same-type monopoly).
    """
    if len(ranked_papers) <= per_query_target:
        return list(ranked_papers)

    max_year = max((item.paper.year or 0) for item in ranked_papers)
    a = _sample_bucket(
        ranked_papers, max_rank_pct=0.20, citation_top_pct=0.30
    )
    b = _sample_bucket(
        ranked_papers, max_rank_pct=0.50, recent_year_cutoff=max_year - 1
    )
    c = _sample_bucket(
        ranked_papers, min_rank_pct=0.40, max_rank_pct=0.70, citation_top_pct=0.30
    )
    budgets = {
        "a": int(per_query_target * _BUCKET_A_RATIO),
        "b": int(per_query_target * _BUCKET_B_RATIO),
        "c": int(per_query_target * _BUCKET_C_RATIO),
    }
    sampled: list[RankedPaper] = []
    seen: set[str] = set()
    for name, bucket in (("a", a), ("b", b), ("c", c)):
        for item in bucket[: budgets[name]]:
            if item.paper.paper_id not in seen:
                sampled.append(item)
                seen.add(item.paper.paper_id)
    return sampled


def build_screening_prompt(query_candidates: dict[str, list[RankedPaper]]) -> str:
    """Build the single-call screening prompt over every query's sampled candidates.

    Each candidate is presented as ``### [n] year title`` + abstract — **no rank
    or score** (avoiding relative-scoring bias, mirroring the RCS input
    reduction spirit). The output contract is copy-style: decisions reference
    papers by their exact title, which the program resolves back to paper ids.
    """
    blocks: list[str] = [
        "You are a scholarly screening reviewer. Below are candidate papers grouped "
        "by the search query that retrieved them. Decide the disposition of every "
        "candidate paper and perform a global gap analysis of the whole pool.\n",
        "Decision rules:\n"
        '- "keep": high-quality paper that is directly useful for the research topic; '
        "it should be downloaded now.\n"
        '- "maybe": uncertain value — keep it as a buffer in case the keep set is '
        "too small to fill the review.\n"
        '- "reject": off-topic, redundant, or low-quality; reject it explicitly.\n'
        "- Every candidate must receive exactly one decision. Base each decision on "
        "the paper's title, abstract, and year only.\n",
        "Then produce a global gap analysis over ALL candidates: name the areas the "
        "pool already covers, the key pieces still missing (e.g. a missing benchmark "
        "or methodology family), and at most 3 follow-up queries that would fill the "
        "missing pieces. If the pool is already sufficient, return an empty "
        "follow-up list.\n",
        "Return exactly one JSON object matching the provided schema. In every "
        'decision, set "title" to the candidate\'s EXACT title as listed below '
        "(copy it word for word).",
    ]

    for query_index, (query, ranked) in enumerate(query_candidates.items(), start=1):
        blocks.append(f"## Query {query_index}: {query}")
        for index, item in enumerate(ranked, start=1):
            paper = item.paper
            title = " ".join(paper.title.split())
            abstract = " ".join(paper.abstract.split())
            blocks.append(f"### [{index}] {paper.year} {title}\n{abstract}")
    return "\n\n".join(blocks)


def _build_screening_repair_prompt(
    raw_output: str,
    error: BaseException,
    query_candidates: dict[str, list[RankedPaper]],
) -> str:
    """Ask the model to repair a screening output that cannot be resolved."""
    expected = sorted(
        {_normalize_title(item.paper.title) for ranked in query_candidates.values() for item in ranked}
    )
    title_lines = "\n".join(f"- {title}" for title in expected)
    return (
        "The previous screening output could not be resolved. "
        f"{error} Return a repaired JSON object with the same schema, where every "
        "decision copies its paper title EXACTLY from the candidate list below:\n"
        f"{title_lines}\n"
        "Include decisions for ALL candidates and keep the covered/missing/follow-up "
        "analysis. No Markdown or explanation.\n"
        f"Previous output:\n{raw_output}"
    )


def _resolve_output(
    output: _LlmScreeningOutput,
    query_candidates: dict[str, list[RankedPaper]],
) -> ScreeningResult:
    """Resolve title-keyed LLM decisions back to paper ids and group them per query."""
    title_index: dict[str, tuple[str, RankedPaper]] = {}
    expected_titles: set[str] = set()
    for query, ranked in query_candidates.items():
        for item in ranked:
            normalized = _normalize_title(item.paper.title)
            title_index[normalized] = (query, item)
            expected_titles.add(normalized)

    resolved: dict[str, list[ScreenDecision]] = {query: [] for query in query_candidates}
    seen_titles: set[str] = set()
    for decision in output.decisions:
        normalized = _normalize_title(decision.title)
        if normalized in seen_titles:
            raise ScreeningError(
                f"Duplicate decision for paper {decision.title!r}; every candidate "
                "must receive exactly one decision."
            )
        hit = title_index.get(normalized)
        if hit is None:
            raise ScreeningError(
                f"Unknown paper title {decision.title!r} in the screening output."
            )
        seen_titles.add(normalized)
        query, item = hit
        resolved[query].append(
            ScreenDecision(
                paper_id=item.paper.paper_id,
                priority=decision.priority,
                reason=decision.reason,
                paper_title=item.paper.title,
            )
        )

    missing = expected_titles - seen_titles
    if missing:
        raise ScreeningError(
            f"Screening output is missing {len(missing)} candidate paper(s); "
            "expected a decision for every listed candidate."
        )
    return ScreeningResult(
        decisions=resolved,
        gap=GapAnalysis(
            covered_areas=output.covered_areas,
            missing_pieces=output.missing_pieces,
            follow_up_queries=output.follow_up_queries,
        ),
        screened_at=datetime.now(timezone.utc),
    )


def screen_candidates(
    query_candidates: dict[str, list[RankedPaper]],
    client: JsonGenerationClient,
    *,
    parse: Callable[[str], ScreeningResult] | None = None,
) -> ScreeningResult:
    """Run one LLM screening + gap call over every query's sampled candidates.

    ``query_candidates`` maps a query string to its sampled ``RankedPaper`` list.
    Uses the shared call-once-parse-repair helper: one initial call, and a single
    repair retry when the output fails Pydantic validation **or cannot be resolved**
    back to the supplied paper titles (missing / duplicated / unknown titles).
    ``parse`` may be injected for tests and must return a ``ScreeningResult``.
    """
    prompt = build_screening_prompt(query_candidates)
    schema = _LlmScreeningOutput.model_json_schema()

    def default_parse(raw_output: str) -> ScreeningResult:
        model = _LlmScreeningOutput.model_validate_json(strip_code_fence(raw_output))
        return _resolve_output(model, query_candidates)

    parse_output = parse if parse is not None else default_parse

    def _repair(raw_output: str, error: BaseException) -> str:
        return _build_screening_repair_prompt(raw_output, error, query_candidates)

    return generate_validated(
        client,
        _LlmScreeningOutput,
        prompt,
        schema,
        parse=parse_output,
        repair_prompt=_repair,
    )