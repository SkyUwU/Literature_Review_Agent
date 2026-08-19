"""Selection of a small, traceable reading set from ranked search results."""

from literature_review.models import (
    RankedSearchResponse,
    SelectedPaperSet,
    SelectionPolicy,
)


def select_papers(
    ranked_response: RankedSearchResponse,
    policy: SelectionPolicy,
) -> SelectedPaperSet:
    """Select the highest-ranked papers that meet an optional score threshold.

    This deliberately makes no claim about full-text quality. That assessment
    belongs to a later reading stage with explicit source evidence.
    """
    eligible = [
        ranked_paper
        for ranked_paper in ranked_response.ranked_papers
        if policy.min_score is None or ranked_paper.score >= policy.min_score
    ]
    return SelectedPaperSet(
        ranked_search_response=ranked_response,
        selection_policy=policy,
        selected_papers=eligible[: policy.max_papers],
    )
