"""Transparent metadata and bounded-evidence paper assessment helpers."""

import math

from literature_review.models import (
    AssessmentPolicy,
    AssessmentResponse,
    EvidenceAggregationPolicy,
    EvidenceAssessmentResponse,
    EvidenceCitation,
    EvidenceRerankResponse,
    EvidenceSummary,
    PaperAssessment,
    SelectedPaperSet,
)

# C2b: the functional include boundary on the shared 1-10 scale. The retired
# rel/qual rule (8/6) is not reusable for the legacy compare path.
_UTILITY_INCLUDE_THRESHOLD = 6.0


def relevance_score(rank_score: float) -> int:
    """Map a baseline rank score (0-3) to a bounded 1-10 scale.

    Deprecated: the main pipeline no longer uses this metadata rule; it survives
    only for the search.py --assess CLI and will be removed in a cleanup milestone.
    """
    return min(10, max(1, math.ceil(rank_score * 10 / 3)))


def evidence_quality_score(citation_count: int | None, venue: str | None) -> int:
    """Score available metadata, not the scientific quality of a paper.

    Deprecated: same note as relevance_score.
    """
    score = 4  # The selection pipeline already requires an abstract and authors.
    if venue:
        score += 2
    if (citation_count or 0) >= 10:
        score += 2
    if (citation_count or 0) >= 100:
        score += 2
    return min(score, 10)


def assess_selected_papers(
    selected_set: SelectedPaperSet,
    policy: AssessmentPolicy,
) -> AssessmentResponse:
    """Recommend reading priority using only retrieved metadata and abstracts.

    Deprecated: the main pipeline assesses papers from bounded chunk evidence;
    this metadata rule stays only for the search.py --assess CLI.
    """
    assessments: list[PaperAssessment] = []
    for ranked_paper in selected_set.selected_papers:
        paper = ranked_paper.paper
        relevance = relevance_score(ranked_paper.score)
        evidence_quality = evidence_quality_score(paper.citation_count, paper.venue)
        # C2b: two-way recommendation only; the retired "consider" band maps to
        # "exclude" so the metadata rule keeps its conservative reading-priority
        # meaning (include keeps requiring its own policy thresholds).
        if (
            relevance >= policy.include_relevance_score
            and evidence_quality >= policy.include_evidence_quality_score
        ):
            recommendation = "include"
        else:
            recommendation = "exclude"
        assessments.append(
            PaperAssessment(
                paper_id=paper.paper_id,
                utility_score=float(relevance),
                recommendation=recommendation,
                rationale=(
                    f"Metadata assessment: baseline rank score {ranked_paper.score:.3f}; "
                    f"citation count {paper.citation_count or 0}; "
                    f"venue metadata {'available' if paper.venue else 'unavailable'}; "
                    f"legacy relevance {relevance}, legacy quality {evidence_quality}."
                ),
            )
        )
    return AssessmentResponse(
        selected_paper_set=selected_set,
        assessment_policy=policy,
        assessments=assessments,
        limitations=[
            "This is a metadata-and-abstract assessment, not a full-text quality review.",
            "Citation counts can disadvantage recent papers and vary across providers.",
        ],
    )


def _shrunk_mean(scores: list[int], policy: EvidenceAggregationPolicy) -> float:
    """Blend the sample mean toward the prior score when few chunks support a paper.

    Formula: round((n*mean + m*prior)/(n+m), 1)
    where n=len(scores), m=policy.shrinkage_strength, prior=policy.prior_score.
    """
    n = len(scores)
    if n == 0:
        raise ValueError("_shrunk_mean requires at least one score")
    m = policy.shrinkage_strength
    prior = policy.prior_score
    mean = sum(scores) / n
    return round((n * mean + m * prior) / (n + m), 1)


def aggregate_evidence_assessments(
    rerank_response: EvidenceRerankResponse,
    policy: EvidenceAggregationPolicy,
) -> EvidenceAssessmentResponse:
    """Aggregate LLM chunk summaries into per-paper, page-traceable assessments.

    The result is limited to the chunks supplied by first-stage retrieval; it does
    not claim to judge the paper outside that bounded evidence set.

    C2b compare/legacy: the retired dual rel/qual contract is translated to the
    functional contract — ``utility_score`` is the relevance shrunk mean and the
    two-way recommendation uses the functional include boundary 6.0 (the retired
    8/6 rule is not reusable, per the C2b scale decision).
    """
    summaries_by_paper: dict[str, list[EvidenceSummary]] = {}
    for summary in rerank_response.summaries:
        summaries_by_paper.setdefault(summary.paper_id, []).append(summary)

    assessments: list[PaperAssessment] = []
    for paper_id, summaries in summaries_by_paper.items():
        relevance = _shrunk_mean([summary.relevance_score for summary in summaries], policy)
        evidence_quality = _shrunk_mean(
            [summary.evidence_quality_score for summary in summaries], policy
        )
        recommendation = "include" if relevance >= _UTILITY_INCLUDE_THRESHOLD else "exclude"

        evidence = [
            EvidenceCitation(
                chunk_id=summary.chunk_id,
                page_start=summary.page_start,
                page_end=summary.page_end,
                rationale=summary.rationale_relevance,
                utility_score=summary.relevance_score,
            )
            for summary in summaries
        ]
        sources = ", ".join(
            f"{item.chunk_id} (pages {item.page_start}-{item.page_end})" for item in evidence
        )
        assessments.append(
            PaperAssessment(
                paper_id=paper_id,
                utility_score=relevance,
                recommendation=recommendation,
                rationale=(
                    f"Evidence-based aggregate from {len(evidence)} chunk(s) "
                    f"(shrunk mean, prior p={policy.prior_score}, "
                    f"strength m={policy.shrinkage_strength}): {sources}. "
                    f"Aggregate relevance score {relevance}; aggregate evidence quality score {evidence_quality}."
                ),
                evidence=evidence,
            )
        )

    return EvidenceAssessmentResponse(
        evidence_rerank_response=rerank_response,
        aggregation_policy=policy,
        assessments=assessments,
        limitations=[
            *rerank_response.limitations,
            "Each paper assessment is limited to the supplied top-k retrieved chunks, not the whole paper.",
            "Chunk counts partly reflect retrieval allocation across papers, not absolute paper quality.",
        ],
    )
