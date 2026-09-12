"""Create a local example report without using an API or LLM."""

from literature_review.models import (
    FutureDirection,
    LiteratureReviewReport,
    Paper,
    PaperAssessment,
    ResearchIdea,
)


def build_demo_report() -> LiteratureReviewReport:
    idea = ResearchIdea(
        title="Evidence-grounded literature review agents",
        description=(
            "Build an agent that retrieves research papers for a user idea and "
            "keeps each generated conclusion linked to supporting evidence."
        ),
        keywords=["literature review", "citation grounding", "research agent"],
        research_questions=["How can a report avoid unsupported citations?"],
    )
    paper = Paper(
        paper_id="arXiv:2412.13612",
        title="Large Language Models for Automated Literature Review",
        authors=["X. Tang", "X. Duan", "Z. G. Cai"],
        year=2024,
        abstract=(
            "This example represents a paper whose metadata and abstract would "
            "later be obtained from a scholarly search service."
        ),
        url="https://arxiv.org/abs/2412.13612",
        venue="arXiv",
    )
    assessment = PaperAssessment(
        paper_id=paper.paper_id,
        utility_score=8.0,
        recommendation="include",
        rationale=(
            "The paper directly concerns automated literature review and can help "
            "us compare our retrieval and synthesis design choices."
        ),
    )
    direction = FutureDirection(
        title="Add claim-level evidence links",
        rationale=(
            "A report should expose which papers support each conclusion so a user "
            "can audit a generated future direction instead of trusting it blindly."
        ),
        supporting_paper_ids=[paper.paper_id],
    )
    return LiteratureReviewReport(
        idea=idea,
        papers=[paper],
        assessments=[assessment],
        synthesis=(
            "The initial design focuses on provenance: metadata, assessments, and "
            "future directions all retain a link to their source paper."
        ),
        future_directions=[direction],
        limitations=["This demonstration uses hand-written data; no real search runs yet."],
    )


if __name__ == "__main__":
    report = build_demo_report()
    print(report.model_dump_json(indent=2))
