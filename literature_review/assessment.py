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


def relevance_score(rank_score: float) -> int:
    """Map the existing baseline rank score to a bounded, readable scale."""
    return min(5, max(1, math.ceil(rank_score / 3)))


def evidence_quality_score(citation_count: int | None, venue: str | None) -> int:
    """Score available metadata, not the scientific quality of a paper."""
    score = 2  # The selection pipeline already requires an abstract and authors.
    if venue:
        score += 1
    if (citation_count or 0) >= 10:
        score += 1
    if (citation_count or 0) >= 100:
        score += 1
    return min(score, 5)


def assess_selected_papers(
    selected_set: SelectedPaperSet,
    policy: AssessmentPolicy,
) -> AssessmentResponse:
    """Recommend reading priority using only retrieved metadata and abstracts."""
    assessments: list[PaperAssessment] = []
    for ranked_paper in selected_set.selected_papers:
        paper = ranked_paper.paper
        relevance = relevance_score(ranked_paper.score)
        evidence_quality = evidence_quality_score(paper.citation_count, paper.venue)
        if (
            relevance >= policy.include_relevance_score
            and evidence_quality >= policy.include_evidence_quality_score
        ):
            recommendation = "include"
        elif relevance >= policy.consider_relevance_score:
            recommendation = "consider"
        else:
            recommendation = "exclude"
        assessments.append(
            PaperAssessment(
                paper_id=paper.paper_id,
                relevance_score=relevance,
                evidence_quality_score=evidence_quality,
                recommendation=recommendation,
                rationale=(
                    f"Metadata assessment: baseline rank score {ranked_paper.score:.3f}; "
                    f"citation count {paper.citation_count or 0}; "
                    f"venue metadata {'available' if paper.venue else 'unavailable'}."
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


def _shrunk_mean(scores: list[int], policy: EvidenceAggregationPolicy) -> int:
    """Blend the sample mean toward the prior score when few chunks support a paper.

    Formula: floor((n*mean + m*prior)/(n+m) + 0.5)
    where n=len(scores), m=policy.shrinkage_strength, prior=policy.prior_score.
    """
    n = len(scores)
    m = policy.shrinkage_strength
    prior = policy.prior_score
    mean = sum(scores) / n
    return math.floor((n * mean + m * prior) / (n + m) + 0.5)


def aggregate_evidence_assessments(
    rerank_response: EvidenceRerankResponse,
    policy: EvidenceAggregationPolicy,
) -> EvidenceAssessmentResponse:
    """Aggregate LLM chunk summaries into per-paper, page-traceable assessments.

    The result is limited to the chunks supplied by first-stage retrieval; it does
    not claim to judge the paper outside that bounded evidence set.
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
        if (
            relevance >= policy.include_relevance_score
            and evidence_quality >= policy.include_evidence_quality_score
        ):
            recommendation = "include"
        elif relevance >= policy.consider_relevance_score:
            recommendation = "consider"
        else:
            recommendation = "exclude"

        evidence = [
            EvidenceCitation(
                chunk_id=summary.chunk_id,
                page_start=summary.page_start,
                page_end=summary.page_end,
                summary=summary.summary,
                relevance_score=summary.relevance_score,
                evidence_quality_score=summary.evidence_quality_score,
                recommendation=summary.recommendation,
            )
            for summary in summaries
        ]
        sources = ", ".join(
            f"{item.chunk_id} (pages {item.page_start}-{item.page_end})" for item in evidence
        )
        assessments.append(
            PaperAssessment(
                paper_id=paper_id,
                relevance_score=relevance,
                evidence_quality_score=evidence_quality,
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
