"""Create an inspectable search plan before sending queries to paper providers."""

from literature_review.models import PlannedQuery, ResearchIdea, SearchPlan


def create_rule_based_plan(idea: ResearchIdea, max_queries: int = 5) -> SearchPlan:
    """Create a small fallback plan without an LLM or external API.

    A later LLM planner will return the same SearchPlan contract, so the search
    pipeline does not need to change when the planning strategy improves.
    """
    candidates = [
        (" ".join(idea.keywords), "Find papers using the core concepts supplied by the researcher."),
        (f"{idea.title} literature review", "Find surveys and prior literature reviews for the proposed topic."),
        *(
            (question, "Find evidence relevant to this research question.")
            for question in idea.research_questions
        ),
    ]
    queries: list[PlannedQuery] = []
    seen_queries: set[str] = set()
    for query, purpose in candidates:
        normalized = " ".join(query.split()).lower()
        if len(normalized) < 3 or normalized in seen_queries:
            continue
        seen_queries.add(normalized)
        queries.append(PlannedQuery(query=query, purpose=purpose))
        if len(queries) == max_queries:
            break

    return SearchPlan(
        idea=idea,
        queries=queries,
        perspectives=["core concepts", "prior surveys", "research questions"],
        generated_by="rule_based",
        rationale=(
            "The fallback plan expands the supplied keywords, topic title, and research "
            "questions into separate, inspectable search intents."
        ),
    )
