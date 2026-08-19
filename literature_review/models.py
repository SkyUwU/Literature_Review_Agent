"""Validated data contracts shared by every stage of the workflow."""

from typing import Literal

from pydantic import BaseModel, Field, HttpUrl


class ResearchIdea(BaseModel):
    """The research problem provided by a user."""

    title: str = Field(min_length=3, description="A short name for the idea.")
    description: str = Field(
        min_length=20,
        description="The problem, proposed approach, and intended setting.",
    )
    keywords: list[str] = Field(min_length=1)
    research_questions: list[str] = Field(default_factory=list)


class Paper(BaseModel):
    """Metadata and available evidence for one candidate paper."""

    paper_id: str = Field(description="A stable identifier, such as an arXiv ID or DOI.")
    title: str = Field(min_length=1)
    authors: list[str] = Field(min_length=1)
    year: int = Field(ge=1900, le=2100)
    abstract: str = Field(min_length=20)
    url: HttpUrl
    venue: str | None = None
    citation_count: int | None = Field(default=None, ge=0)


class SearchRequest(BaseModel):
    """Search constraints supplied to one scholarly-paper provider."""

    query: str = Field(min_length=3)
    limit: int = Field(default=10, ge=1, le=100)
    year_from: int | None = Field(default=None, ge=1900, le=2100)
    year_to: int | None = Field(default=None, ge=1900, le=2100)


class SearchResponse(BaseModel):
    """Normalized results returned by a scholarly-paper provider."""

    provider: str
    request: SearchRequest
    total_candidates: int = Field(ge=0)
    papers: list[Paper]
    skipped_candidates: int = Field(ge=0)


class FilterPolicy(BaseModel):
    """Transparent rules for excluding unsuitable search candidates."""

    min_year: int | None = Field(default=None, ge=1900, le=2100)
    max_year: int | None = Field(default=None, ge=1900, le=2100)
    min_citation_count: int = Field(default=0, ge=0)


class RankedPaper(BaseModel):
    """A candidate paper with a reproducible baseline-ranking explanation."""

    paper: Paper
    rank: int = Field(ge=1)
    score: float = Field(ge=0)
    matched_terms: list[str]
    rationale: str


class RankedSearchResponse(BaseModel):
    """The filtered and ranked view of one search response."""

    search_response: SearchResponse
    filter_policy: FilterPolicy
    ranked_papers: list[RankedPaper]


class PaperAssessment(BaseModel):
    """Why a paper is or is not useful for the current idea."""

    paper_id: str
    relevance_score: int = Field(ge=1, le=5)
    evidence_quality_score: int = Field(ge=1, le=5)
    recommendation: Literal["include", "consider", "exclude"]
    rationale: str = Field(min_length=20)


class FutureDirection(BaseModel):
    """A proposed next step with links back to supporting papers."""

    title: str = Field(min_length=3)
    rationale: str = Field(min_length=20)
    supporting_paper_ids: list[str] = Field(min_length=1)


class LiteratureReviewReport(BaseModel):
    """The final, human-readable and agent-readable output of Task 1A."""

    idea: ResearchIdea
    papers: list[Paper] = Field(min_length=1)
    assessments: list[PaperAssessment] = Field(min_length=1)
    synthesis: str = Field(min_length=20)
    future_directions: list[FutureDirection] = Field(min_length=1)
    limitations: list[str] = Field(default_factory=list)
