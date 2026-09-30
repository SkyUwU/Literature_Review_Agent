"""LLM screening layer: keep/maybe/reject decisions plus global gap analysis (M5e).

The screening layer sits between embedding ranking and PDF download. Each query's
top candidates are sampled into three diversity buckets (authority / frontier /
cross-domain), then all sampled candidates are handed to the LLM in **one** call
together with the gap analysis (``screen_candidates``). The LLM returns copy-style
decisions keyed by the global document id (``[DOC_n]``) shown in the prompt, so the
program composes the real ``paper_id`` provenance back. Unknown ids (hallucinated
papers outside the candidate list) are dropped with a warning; missing or
duplicated decisions for real candidates stay fatal.
"""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

from pydantic import BaseModel, Field

from literature_review.llm_evidence import (
    GroqJsonClient,
    JsonGenerationClient,
    generate_validated,
    strip_code_fence,
)
from literature_review.models import RankedPaper

__all__ = [
    "FollowUpQuery",
    "GapAnalysis",
    "SampledCandidates",
    "ScreenDecision",
    "ScreeningError",
    "ScreeningResult",
    "build_screening_prompt",
    "sample_candidates",
    "screen_candidates",
]

_PRIORITIES = ("keep", "maybe", "reject")
DEFAULT_PER_QUERY_TARGET = 24
# Keep a screening prompt well below Groq's commonly used 8K TPM tier, leaving
# room for the JSON response. This is a conservative chars/4 token estimate.
GROQ_SCREENING_MAX_ESTIMATED_PROMPT_TOKENS = 3_000
GROQ_SCREENING_MIN_INTERVAL_SECONDS = 61.0
_BUCKET_A_RATIO = 0.25
_BUCKET_B_RATIO = 0.50
_BUCKET_C_RATIO = 0.25


@dataclass
class SampledCandidates:
    """Bucket-sampled candidates plus the paper_id -> a/b/c label map.

    ``buckets`` records, for every paper in ``papers``, the diversity bucket that
    actually claimed it (only set when the pool was large enough to warrant
    bucketing; small pools return an empty map).
    """

    papers: list[RankedPaper]
    buckets: dict[str, str] = field(default_factory=dict)


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
    """LLM-facing decision keyed by the document id shown in the prompt."""

    doc_id: str = Field(min_length=1, pattern=r"^\[?DOC_\d+\]?$")
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


def _normalize_doc_id(doc_id: str) -> str:
    """Canonical document id: strip brackets/whitespace and uppercase.

    Accepts both ``[DOC_1]`` (as shown in the prompt) and ``DOC_1``.
    """
    return doc_id.strip().upper().replace("[", "").replace("]", "")


def _doc_id_map(
    query_candidates: dict[str, SampledCandidates],
) -> dict[str, tuple[str, RankedPaper]]:
    """Assign one global ``DOC_n`` id per candidate, in prompt display order.

    The numbering is sequential across all queries (query order, then bucket
    order inside a query) so the id is unique regardless of query grouping.
    """
    mapping: dict[str, tuple[str, RankedPaper]] = {}
    counter = 0
    for query, sampled in query_candidates.items():
        for item in sampled.papers:
            counter += 1
            mapping[f"DOC_{counter}"] = (query, item)
    return mapping


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
) -> SampledCandidates:
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
        return SampledCandidates(papers=list(ranked_papers), buckets={})

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
    buckets: dict[str, str] = {}
    seen: set[str] = set()
    for name, bucket in (("a", a), ("b", b), ("c", c)):
        for item in bucket[: budgets[name]]:
            if item.paper.paper_id not in seen:
                sampled.append(item)
                buckets[item.paper.paper_id] = name
                seen.add(item.paper.paper_id)
    return SampledCandidates(papers=sampled, buckets=buckets)


_ROLE_TEXT = (
    "You are a senior scholarly review assistant. Your task is to evaluate the "
    "candidate papers retrieved for the user's research topic, select the core papers "
    "of substantial reference value, and analyze the information gaps in the current "
    "literature pool."
)

_CANDIDATES_INTRO = (
    "The candidate papers below are organized by (1) the retrieval sub-query that "
    "found them and (2) their scholarly role (Category A foundational / Category B "
    "frontier / Category C applied)."
)

_GUIDANCE_TEXT = (
    "1. **Global review first**: Before making any decisions, browse ALL sub-queries "
    "and ALL candidate categories to establish a complete picture of the literature "
    "landscape, then evaluate as a whole. Balance representative papers across "
    "sub-topics so every sub-direction is represented, and avoid biasing decisions "
    "toward a single sub-direction (do not rush to exhaust your keep decisions on "
    "the first papers you see).\n"
    "2. **Layered judgment**:\n"
    "   - If a sub-query displays the `Category A/B/C` headers:\n"
    "     * Category A (Foundational): value its role as a core starting point or "
    "classic baseline of the field; do not reject it simply because it was published "
    "earlier.\n"
    "     * Category B (Frontier): value whether it proposes an innovative "
    "architecture, mechanism, or latest breakthrough.\n"
    "     * Category C (Applied): value whether it provides cross-domain integration "
    "or a novel application context.\n"
    "   - If a sub-query is marked `Un-bucketed` or has few candidates:\n"
    "     * evaluate directly whether the paper provides substantive methodological or "
    "experimental support for the overall research topic.\n"
    "   - If a sub-query is marked `Empty retrieval`:\n"
    "     * record this blind spot under `missing_pieces` and propose a precise "
    "follow-up search in `follow_up_queries`.\n"
    "3. **Decision labels**:\n"
    '   - "keep": highly relevant to the research topic, or of key representativeness; '
    "it should be downloaded now.\n"
    '   - "maybe": uncertain value — keep it as a buffer in case the keep set is too '
    "small to fill the review.\n"
    '   - "reject": off-topic, redundant, or low-quality; reject it explicitly.\n'
    "4. Base each decision on the paper's title, abstract, year, and citation count "
    "only. Every candidate must receive exactly one decision; make decisions only "
    "about the papers listed below — never output a document id not shown above."
)

_OUTPUT_TEXT = (
    "Return exactly one JSON object matching the provided schema. In every decision "
    'set "doc_id" to the candidate\'s exact document id as listed above (e.g. '
    '"[DOC_1]", copy it word for word).\n\n'
    "The output has two parts:\n\n"
    "1. Per-paper decisions (`decisions`): exactly one entry per candidate, each with\n"
    "   - `doc_id`: the document id shown above, copied verbatim;\n"
    '   - `priority`: "keep", "maybe", or "reject";\n'
    "   - `reason`: a brief scholarly justification, 1-2 sentences.\n\n"
    "2. Global information-gap reflection (the feedback loop): honestly state what "
    "the current pool already covers and what is still missing, as a roadmap for the "
    "next search round:\n"
    "   - `covered_areas`: what the selected papers already cover well;\n"
    "   - `missing_pieces`: the key aspects or evidence still absent to fully support "
    "the research topic;\n"
    "   - `follow_up_queries`: 1-3 precise search queries that would fill the gaps "
    "(must target the missing aspects and avoid repeating the initial sub-queries "
    "above; empty list [] if coverage is sufficient).\n\n"
    "Form example:\n"
    "{\n"
    '  "decisions": [\n'
    '    {"doc_id": "[DOC_1]", "priority": "keep", "reason": "..."},\n'
    '    {"doc_id": "[DOC_2]", "priority": "maybe", "reason": "..."}\n'
    "  ],\n"
    '  "covered_areas": ["..."],\n'
    '  "missing_pieces": ["..."],\n'
    '  "follow_up_queries": ["..."]\n'
    "}\n\n"
    "No Markdown, no explanation, no preamble. Your reply must be the JSON object "
    "itself and nothing else."
)

_BUCKET_HEADERS = {
    "a": "### Category A: Foundational (High-Impact / Baseline)",
    "b": "### Category B: Frontier (Recent Frontier)",
    "c": "### Category C: Applied (Domain / Integration)",
}

_EMPTY_RETRIEVAL_TEXT = (
    "### Empty retrieval\n"
    "No candidates were found for this sub-query; note this blind spot in "
    "`missing_pieces` and propose a follow-up search in `follow_up_queries`."
)


def _year_str(year: int | None) -> str:
    return "n/a" if year is None else str(year)


def _citations_str(citation_count: int | None) -> str:
    return "N/A" if citation_count is None else str(citation_count)


def _format_candidate(doc_index: int, item: RankedPaper) -> str:
    paper = item.paper
    title = " ".join(paper.title.split())
    abstract = " ".join(paper.abstract.split())
    return (
        f'- [DOC_{doc_index}] "{title}" ({_year_str(paper.year)}, '
        f"citations: {_citations_str(paper.citation_count)})\n"
        f"  Abstract: {abstract}"
    )


def build_screening_prompt(
    query_candidates: dict[str, SampledCandidates],
    main_query: str | None = None,
) -> str:
    """Build the single-call screening prompt over every query's sampled candidates.

    The prompt is fully in English: the research idea, the rating guidance, and the
    Candidate papers list reference normalized display ids (``[DOC_n]``) — **no rank
    or score** is shown (avoiding relative-scoring bias). The candidates sit in the
    middle; the JSON output requirements are the *last* instruction, to reduce
    preamble noise around the returned object. Each query renders its candidates
    under the diversity bucket headers ``Category A/B/C`` when bucketing applied,
    under ``Un-bucketed`` otherwise, or under ``Empty retrieval`` when the query
    produced no candidates. ``main_query`` is the user's original research idea;
    when ``None`` the ``## Research topic`` section is omitted entirely.
    """
    blocks: list[str] = [_ROLE_TEXT]
    if main_query:
        blocks.extend(["## Research topic", " ".join(main_query.split())])
    blocks.extend(["## Rating guidance and principles", _GUIDANCE_TEXT])
    blocks.extend(["## Candidate papers", _CANDIDATES_INTRO])

    counter = 0
    for query_index, (query, sampled) in enumerate(query_candidates.items(), start=1):
        blocks.append(f"## Query {query_index}: {query}")
        papers = sampled.papers
        if not papers:
            blocks.append(_EMPTY_RETRIEVAL_TEXT)
            continue
        bucket_map = sampled.buckets
        if bucket_map:
            for name in ("a", "b", "c"):
                bucket_papers = [
                    item for item in papers if bucket_map.get(item.paper.paper_id) == name
                ]
                if not bucket_papers:
                    continue
                blocks.append(_BUCKET_HEADERS[name])
                for item in bucket_papers:
                    counter += 1
                    blocks.append(_format_candidate(counter, item))
        else:
            blocks.append("### Un-bucketed")
            for item in papers:
                counter += 1
                blocks.append(_format_candidate(counter, item))

    blocks.extend(["## Output", _OUTPUT_TEXT])
    return "\n\n".join(blocks)


def _build_screening_repair_prompt(
    raw_output: str,
    error: BaseException,
    query_candidates: dict[str, SampledCandidates],
) -> str:
    """Ask the model to repair a screening output that cannot be resolved."""
    doc_map = _doc_id_map(query_candidates)
    lines = "\n".join(
        f"- [{doc_id}] ({_year_str(item.paper.year)}, "
        f"citations: {_citations_str(item.paper.citation_count)}) {item.paper.title}"
        for doc_id, (query, item) in doc_map.items()
    )
    return (
        "The previous screening output could not be resolved. "
        f"{error} Return a repaired JSON object with the same schema, where every "
        "decision copies its document id EXACTLY from the candidate list below "
        "(e.g. [DOC_1]):\n"
        f"{lines}\n"
        "Include decisions for ALL candidates and keep the covered/missing/follow-up "
        "analysis. No Markdown or explanation.\n"
        f"Previous output:\n{raw_output}"
    )


def _resolve_output(
    output: _LlmScreeningOutput,
    query_candidates: dict[str, SampledCandidates],
) -> ScreeningResult:
    """Resolve doc-id-keyed LLM decisions back to paper ids, grouped per query.

    Unknown doc ids (hallucinated papers outside the candidate list) are dropped
    with a warning instead of aborting the run; missing and duplicated decisions
    for real candidates stay fatal.
    """
    doc_map = _doc_id_map(query_candidates)
    expected_ids: set[str] = set(doc_map)

    resolved: dict[str, list[ScreenDecision]] = {query: [] for query in query_candidates}
    seen_ids: set[str] = set()
    for decision in output.decisions:
        doc_id = _normalize_doc_id(decision.doc_id)
        if doc_id in seen_ids:
            raise ScreeningError(
                f"Duplicate decision for document {decision.doc_id!r}; every candidate "
                "must receive exactly one decision."
            )
        hit = doc_map.get(doc_id)
        if hit is None:
            print(
                f"WARNING: screening output references unknown document "
                f"{decision.doc_id!r}; dropping this decision (not in the "
                "candidate list).",
                file=sys.stderr,
            )
            continue
        seen_ids.add(doc_id)
        query, item = hit
        resolved[query].append(
            ScreenDecision(
                paper_id=item.paper.paper_id,
                priority=decision.priority,
                reason=decision.reason,
                paper_title=item.paper.title,
            )
        )

    missing = expected_ids - seen_ids
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
    query_candidates: dict[str, SampledCandidates],
    client: JsonGenerationClient,
    *,
    main_query: str | None = None,
    parse: Callable[[str], ScreeningResult] | None = None,
) -> ScreeningResult:
    """Run one LLM screening + gap call over every query's sampled candidates.

    ``query_candidates`` maps a query string to its sampled ``SampledCandidates``.
    ``main_query`` is the user's original research idea shown in the prompt (``None``
    omits the ``## Research topic`` section). Uses the shared call-once-parse-repair
    helper: one initial call, and a single repair retry when the output fails Pydantic
    validation **or cannot be resolved** back to the supplied candidate document ids
    (missing / duplicated). Unknown doc ids (hallucinated papers outside the list) are
    dropped with a warning. ``parse`` may be injected for tests and must return a
    ``ScreeningResult``.
    """
    if isinstance(client, GroqJsonClient):
        return _screen_candidates_groq_batches(query_candidates, client, main_query)

    prompt = build_screening_prompt(query_candidates, main_query)
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


class _PacedScreeningClient:
    """Space every Groq screening attempt to stay under the TPM window."""

    def __init__(self, client: JsonGenerationClient) -> None:
        self._client = client
        self._last_call: float | None = None

    def generate_json(self, prompt: str, schema: dict | None = None) -> str:
        if self._last_call is not None:
            remaining = GROQ_SCREENING_MIN_INTERVAL_SECONDS - (time.monotonic() - self._last_call)
            if remaining > 0:
                print(
                    f"[screening] Groq TPM pacing wait_s={remaining:.1f}",
                    file=sys.stderr,
                )
                time.sleep(remaining)
        estimate = (len(prompt) + 3) // 4
        if estimate > GROQ_SCREENING_MAX_ESTIMATED_PROMPT_TOKENS:
            raise ScreeningError(
                "A Groq screening prompt exceeded the configured estimated-token budget "
                f"({estimate}>{GROQ_SCREENING_MAX_ESTIMATED_PROMPT_TOKENS})."
            )
        self._last_call = time.monotonic()
        print(
            f"[screening] Groq prompt_estimated_tokens={estimate}",
            file=sys.stderr,
        )
        return self._client.generate_json(prompt, schema)


def _screen_candidates_groq_batches(
    query_candidates: dict[str, SampledCandidates],
    client: JsonGenerationClient,
    main_query: str | None,
) -> ScreeningResult:
    """Screen bounded candidate batches, then globally combine their gap summaries."""
    paced = _PacedScreeningClient(client)
    batches: list[dict[str, SampledCandidates]] = []
    current: dict[str, SampledCandidates] = {}

    def add_candidate(target: dict[str, SampledCandidates], query: str, item: RankedPaper,
                      bucket: str | None) -> None:
        sampled = target.setdefault(query, SampledCandidates(papers=[]))
        sampled.papers.append(item)
        if bucket is not None:
            sampled.buckets[item.paper.paper_id] = bucket

    for query, sampled in query_candidates.items():
        for item in sampled.papers:
            bucket = sampled.buckets.get(item.paper.paper_id)
            candidate = {key: SampledCandidates(papers=list(value.papers), buckets=dict(value.buckets))
                         for key, value in current.items()}
            add_candidate(candidate, query, item, bucket)
            prompt = build_screening_prompt(candidate, main_query)
            if (len(prompt) + 3) // 4 > GROQ_SCREENING_MAX_ESTIMATED_PROMPT_TOKENS:
                if not current:
                    raise ScreeningError(
                        f"Candidate {item.paper.paper_id} alone exceeds the Groq screening prompt budget."
                    )
                batches.append(current)
                current = {}
                add_candidate(current, query, item, bucket)
                if (len(build_screening_prompt(current, main_query)) + 3) // 4 > GROQ_SCREENING_MAX_ESTIMATED_PROMPT_TOKENS:
                    raise ScreeningError(
                        f"Candidate {item.paper.paper_id} alone exceeds the Groq screening prompt budget."
                    )
            else:
                current = candidate
    if current:
        batches.append(current)

    merged_decisions: dict[str, list[ScreenDecision]] = {
        query: [] for query in query_candidates
    }
    batch_summaries: list[dict[str, object]] = []
    for index, batch in enumerate(batches, start=1):
        candidate_count = sum(len(sampled.papers) for sampled in batch.values())
        print(
            f"[screening] Groq batch={index}/{len(batches)} candidates={candidate_count}",
            file=sys.stderr,
        )
        result = _screen_batch(batch, paced, main_query)
        for query, decisions in result.decisions.items():
            merged_decisions.setdefault(query, []).extend(decisions)
        batch_summaries.append({
            "batch": index,
            "queries": list(batch),
            "covered_areas": result.gap.covered_areas,
            "missing_pieces": result.gap.missing_pieces,
        })

    expected = {item.paper.paper_id for sampled in query_candidates.values() for item in sampled.papers}
    actual = {decision.paper_id for decisions in merged_decisions.values() for decision in decisions}
    if actual != expected:
        raise ScreeningError(
            f"Groq screening batches did not cover every candidate exactly once "
            f"(missing={len(expected - actual)}, extra={len(actual - expected)})."
        )

    gap_prompt = (
        "You are consolidating screening summaries from batches of scholarly papers. "
        "Use only the supplied batch summaries and research topic. Return a JSON object "
        "matching the schema with global covered_areas, missing_pieces, and up to three "
        "precise follow_up_queries. Do not repeat initial search queries unless needed.\n\n"
        f"Research topic: {main_query or '(not provided)'}\n"
        f"Initial search queries: {list(query_candidates)}\n"
        f"Queries with no candidates: {[q for q, s in query_candidates.items() if not s.papers]}\n"
        f"Batch summaries: {batch_summaries}"
    )
    gap = generate_validated(
        paced,
        GapAnalysis,
        gap_prompt,
        GapAnalysis.model_json_schema(),
    )
    return ScreeningResult(
        decisions=merged_decisions,
        gap=gap,
        screened_at=datetime.now(timezone.utc),
    )


def _screen_batch(
    candidates: dict[str, SampledCandidates],
    client: JsonGenerationClient,
    main_query: str | None,
) -> ScreeningResult:
    """Resolve one batch with the standard schema and repair policy."""
    prompt = build_screening_prompt(candidates, main_query)
    schema = _LlmScreeningOutput.model_json_schema()

    def parse(raw: str) -> ScreeningResult:
        return _resolve_output(
            _LlmScreeningOutput.model_validate_json(strip_code_fence(raw)), candidates
        )

    def repair(raw: str, error: BaseException) -> str:
        return _build_screening_repair_prompt(raw, error, candidates)

    return generate_validated(
        client,
        _LlmScreeningOutput,
        prompt,
        schema,
        parse=parse,
        repair_prompt=repair,
    )
