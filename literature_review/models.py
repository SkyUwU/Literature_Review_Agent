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


class PlannedQuery(BaseModel):
    """One search query and the information need it is intended to cover."""

    query: str = Field(min_length=3)
    purpose: str = Field(min_length=10)


class SearchPlan(BaseModel):
    """A traceable search strategy, authored by rules or later by an LLM."""

    idea: ResearchIdea
    queries: list[PlannedQuery] = Field(min_length=1, max_length=10)
    perspectives: list[str] = Field(min_length=1)
    generated_by: Literal["rule_based", "llm"]
    rationale: str = Field(min_length=20)


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


class SelectionPolicy(BaseModel):
    """Explicit rules for choosing papers to pass to the reading stage."""

    max_papers: int = Field(default=5, ge=1, le=50)
    min_score: float | None = Field(default=None, ge=0)


class SelectedPaperSet(BaseModel):
    """Ranked papers selected as input for later reading and synthesis."""

    ranked_search_response: RankedSearchResponse
    selection_policy: SelectionPolicy
    selected_papers: list[RankedPaper]


class PageText(BaseModel):
    """Text extracted from one numbered page of a paper."""

    page_number: int = Field(ge=1)
    text: str = Field(min_length=20)


class FullTextDocument(BaseModel):
    """Extracted paper text, retained with its local source and page numbers."""

    paper_id: str
    source_path: str = Field(min_length=1)
    pages: list[PageText] = Field(min_length=1)
    extraction_method: str = Field(min_length=3)


class ChunkPolicy(BaseModel):
    """Reproducible settings for splitting full text into evidence units."""

    max_words: int = Field(default=250, ge=50, le=1_000)
    overlap_words: int = Field(default=30, ge=0, le=500)


class EvidenceChunk(BaseModel):
    """A page-bounded excerpt available for relevance ranking and citation."""

    chunk_id: str
    paper_id: str
    page_start: int = Field(ge=1)
    page_end: int = Field(ge=1)
    text: str = Field(min_length=20)


class RankedEvidenceChunk(BaseModel):
    """A retrieved evidence chunk with an explainable first-stage score."""

    chunk: EvidenceChunk
    rank: int = Field(ge=1)
    score: float = Field(ge=0)
    matched_terms: list[str]
    rationale: str


class EvidenceRetrievalPolicy(BaseModel):
    """Bounded, low-cost retrieval settings before LLM contextual summarization."""

    top_k: int = Field(default=10, ge=1, le=100)


class EvidenceRetrievalResponse(BaseModel):
    """Top evidence chunks for one research question or search query."""

    query: str = Field(min_length=3)
    policy: EvidenceRetrievalPolicy
    ranked_chunks: list[RankedEvidenceChunk]


class LlmEvidenceAssessment(BaseModel):
    """Structured assessment requested from an LLM for one retrieved chunk."""

    chunk_id: str
    summary: str = Field(min_length=20)
    relevance_score: int = Field(ge=1, le=5)
    evidence_quality_score: int = Field(ge=1, le=5)
    recommendation: Literal["include", "consider", "exclude", "insufficient_evidence"]
    rationale: str = Field(min_length=20)


class LlmEvidenceAssessmentBatch(BaseModel):
    """The JSON object an LLM must return for one bounded evidence batch."""

    assessments: list[LlmEvidenceAssessment] = Field(min_length=1)


class EvidenceSummary(LlmEvidenceAssessment):
    """LLM assessment enriched with trusted local evidence provenance."""

    paper_id: str
    page_start: int = Field(ge=1)
    page_end: int = Field(ge=1)


class EvidenceRerankResponse(BaseModel):
    """Second-stage LLM assessments tied to the first retrieval result."""

    retrieval_response: EvidenceRetrievalResponse
    summaries: list[EvidenceSummary]
    limitations: list[str]


class EvidenceCitation(BaseModel):
    """Trusted provenance and LLM assessment for one chunk used in a paper assessment."""

    chunk_id: str
    page_start: int = Field(ge=1)
    page_end: int = Field(ge=1)
    summary: str = Field(min_length=20)
    relevance_score: int = Field(ge=1, le=5)
    evidence_quality_score: int = Field(ge=1, le=5)
    recommendation: Literal["include", "consider", "exclude", "insufficient_evidence"]


class PaperAssessment(BaseModel):
    """Why a paper is or is not useful for the current idea."""

    paper_id: str
    relevance_score: int = Field(ge=1, le=5)
    evidence_quality_score: int = Field(ge=1, le=5)
    recommendation: Literal["include", "consider", "exclude"]
    rationale: str = Field(min_length=20)
    evidence: list[EvidenceCitation] = Field(default_factory=list)


class AssessmentPolicy(BaseModel):
    """Explainable thresholds for a metadata-only paper assessment."""

    include_relevance_score: int = Field(default=4, ge=1, le=5)
    include_evidence_quality_score: int = Field(default=3, ge=1, le=5)
    consider_relevance_score: int = Field(default=3, ge=1, le=5)


class EvidenceAggregationPolicy(BaseModel):
    """Transparent thresholds for aggregating supplied chunk assessments per paper."""

    include_relevance_score: int = Field(default=4, ge=1, le=5)
    include_evidence_quality_score: int = Field(default=3, ge=1, le=5)
    consider_relevance_score: int = Field(default=3, ge=1, le=5)
    prior_score: int = Field(default=3, ge=1, le=5)
    shrinkage_strength: int = Field(default=4, ge=0)


class AssessmentResponse(BaseModel):
    """Initial assessments that retain the selected-paper provenance."""

    selected_paper_set: SelectedPaperSet
    assessment_policy: AssessmentPolicy
    assessments: list[PaperAssessment]
    limitations: list[str]


class EvidenceAssessmentResponse(BaseModel):
    """Paper assessments aggregated from bounded, provenance-preserving chunk evidence."""

    evidence_rerank_response: EvidenceRerankResponse
    aggregation_policy: EvidenceAggregationPolicy
    assessments: list[PaperAssessment] = Field(min_length=1)
    limitations: list[str]


class FutureDirection(BaseModel):
    """A proposed next step with links back to supporting papers."""

    title: str = Field(min_length=3)
    rationale: str = Field(min_length=20)
    supporting_paper_ids: list[str] = Field(min_length=1)
    supporting_chunk_ids: list[str] = Field(default_factory=list)


class LiteratureReviewReport(BaseModel):
    """The final, human-readable and agent-readable output of Task 1A."""

    idea: ResearchIdea
    papers: list[Paper] = Field(min_length=1)
    assessments: list[PaperAssessment] = Field(min_length=1)
    synthesis: str = Field(min_length=20)
    future_directions: list[FutureDirection] = Field(min_length=1)
    limitations: list[str] = Field(default_factory=list)


class ChunkReference(BaseModel):
    """One cited evidence chunk that backs a summary claim."""

    chunk_id: str
    paper_id: str
    page_start: int = Field(ge=1)
    page_end: int = Field(ge=1)
    quote: str = Field(min_length=20)


class PaperSummaryClaim(BaseModel):
    """One claim in a per-paper summary, tied to cited evidence chunks."""

    text: str = Field(min_length=20)
    aspect: str = Field(min_length=3)
    evidence: list[ChunkReference] = Field(min_length=1)


class PaperSummary(BaseModel):
    """An evidence-backed summary of one selected paper."""

    paper_id: str
    claims: list[PaperSummaryClaim] = Field(min_length=1)
    stated_limitations: list[PaperSummaryClaim] = Field(default_factory=list)
    coverage_chunk_ids: list[str] = Field(min_length=1)


class LlmNoteClaim(BaseModel):
    """One claim requested from an LLM for a per-paper note."""

    text: str = Field(min_length=20)
    chunk_ids: list[str] = Field(min_length=1)
    aspect: str = Field(min_length=3)


class LlmPaperSummaryNote(BaseModel):
    """The JSON object an LLM must return to summarize one paper."""

    claims: list[LlmNoteClaim] = Field(min_length=1)
    stated_limitations: list[LlmNoteClaim] = Field(default_factory=list)
    coverage_chunk_ids: list[str] = Field(default_factory=list)


class LlmSynthesisDirection(BaseModel):
    """A future direction requested from an LLM during synthesis."""

    title: str = Field(min_length=3)
    rationale: str = Field(min_length=20)
    supporting_paper_ids: list[str] = Field(min_length=1)
    supporting_chunk_ids: list[str] = Field(min_length=1)


class LlmSynthesisBatch(BaseModel):
    """The JSON object an LLM must return for one bounded synthesis batch."""

    report: str = Field(min_length=100)
    future_directions: list[LlmSynthesisDirection] = Field(min_length=1)


class PaperSource(BaseModel):
    """Local file provenance for one paper used in synthesis."""

    paper_id: str
    source_path: str = Field(min_length=1)


class CoveragePackPolicy(BaseModel):
    """Reproducible settings for bounding the evidence sent to synthesis."""

    max_chunks_per_paper: int = Field(default=6, ge=1, le=50)
    llm_input_cap: int = Field(default=40, ge=1, le=200)


class SynthesisResponse(BaseModel):
    """Evidence-cited synthesis that preserves every upstream provenance layer."""

    paper_sources: list[PaperSource] = Field(min_length=1)
    evidence_assessment_response: EvidenceAssessmentResponse
    paper_summaries: list[PaperSummary] = Field(min_length=1)
    report: str = Field(min_length=100)
    future_directions: list[FutureDirection] = Field(min_length=1)
    limitations: list[str]
    generated_by: Literal["deterministic", "llm"]
