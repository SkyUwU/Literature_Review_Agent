"""Transparent, metadata-only initial assessment of selected papers."""

import math

from literature_review.models import (
    AssessmentPolicy,
    AssessmentResponse,
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
