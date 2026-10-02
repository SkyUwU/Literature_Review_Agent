"""Validated data contracts shared by every stage of the workflow."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, computed_field, model_validator


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
    """A traceable search strategy, authored by rules or later by an LLM.

    ``idea`` is optional so a plan can be produced from a bare query string
    (the LLM and rule-based planners both accept a query-only input). When the
    plan is authored from a bare query, ``idea`` holds that query string so the
    input provenance is preserved; a richer ``ResearchIdea`` is retained for
    future inputs. Sub-queries must be 3-4 short keyword phrases: the schema
    lower bound keeps plans focused and the upper bound caps per-query cost.
    """

    idea: ResearchIdea | str | None = None
    queries: list[PlannedQuery] = Field(min_length=3, max_length=4)
    perspectives: list[str] = Field(min_length=1)
    generated_by: Literal["rule_based", "llm"]
    rationale: str = Field(min_length=20)


class PublicationVenue(BaseModel):
    """Provider-reported publication metadata, distinct from a PDF host.

    This is provenance for a candidate venue, not independent confirmation of
    conference acceptance or of the main-conference track.
    """

    name: str = Field(min_length=1)
    provider: Literal["semantic_scholar", "openalex"]
    metadata_path: str = Field(min_length=1)
    source_id: str | None = None
    source_type: str | None = None
    source_url: str | None = None
    verification: Literal["provider_reported"] = "provider_reported"


class Paper(BaseModel):
    """Metadata and available evidence for one candidate paper."""

    paper_id: str = Field(description="A stable identifier, such as an arXiv ID or DOI.")
    doi: str | None = Field(
        default=None,
        description="Normalised bare DOI, e.g. 10.1145/xxx (no URL prefix, lowercase).",
    )
    title: str = Field(min_length=1)
    authors: list[str] = Field(min_length=1)
    year: int = Field(ge=1900, le=2100)
    abstract: str = Field(min_length=20)
    url: HttpUrl
    venue: str | None = None
    publication_venues: list[PublicationVenue] = Field(default_factory=list)
    citation_count: int | None = Field(default=None, ge=0)
    open_access_pdf_url: HttpUrl | None = Field(
        default=None, description="Provider-reported OA PDF URL, independent of publication venue."
    )

    @computed_field
    @property
    def pdf_host(self) -> str | None:
        """Hostname of the configured PDF URL; redirects are not inspected."""
        return self.open_access_pdf_url.host if self.open_access_pdf_url else None

    @computed_field
    @property
    def venue_verification(self) -> Literal["provider_reported", "unconfirmed"]:
        return "provider_reported" if self.publication_venues else "unconfirmed"


class DownloadedPaperEntry(BaseModel):
    """One paper actually downloaded (a single file in the run folder)."""

    paper: Paper
    query: str = Field(min_length=1)
    local_path: str
    priority: Literal["keep", "maybe"] | None = None
    """Screening decision that admitted the paper for download; None on the legacy path."""


class PapersOutput(BaseModel):
    """Persisted overview of a run's downloaded papers and their provenance."""

    run: dict[str, object]
    papers: list[DownloadedPaperEntry] = Field(default_factory=list)


class SearchRequest(BaseModel):
    """Search constraints supplied to one scholarly-paper provider.

    ``venues`` requests provider-side venue filtering where supported; callers
    still apply the shared local policy as the final cross-provider check.
    """

    query: str = Field(min_length=3)
    limit: int = Field(default=10, ge=1, le=100)
    year_from: int | None = Field(default=None, ge=1900, le=2100)
    year_to: int | None = Field(default=None, ge=1900, le=2100)
    venues: tuple[str, ...] = Field(default_factory=tuple)


class SearchResponse(BaseModel):
    """Normalized results returned by a scholarly-paper provider."""

    provider: str
    request: SearchRequest
    total_candidates: int = Field(ge=0, description="Raw records received in this response page.")
    total_matches: int | None = Field(default=None, ge=0, description="Provider-wide match count, when reported.")
    papers: list[Paper]
    skipped_candidates: int = Field(ge=0)


class FilterPolicy(BaseModel):
    """Transparent rules for excluding unsuitable search candidates."""

    min_year: int | None = Field(default=None, ge=1900, le=2100)
    max_year: int | None = Field(default=None, ge=1900, le=2100)
    min_citation_count: int = Field(default=0, ge=0)
    venues: tuple[str, ...] = ()
    """Normalized venue alias tokens allowed by the top-venue whitelist.

    Empty tuple means no venue restriction. A paper survives only when its
    normalized venue contains one of these tokens (see ``ranking.filter_papers``).
    """


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
    """A page-bounded excerpt available for relevance ranking and citation.

    ``section`` carries the markdown heading path (e.g. ``"Section > Subsection"``)
    when the chunk came from the section-aware splitter; legacy chunks leave it
    None. Page bounds are optional: the markdown converter supplies a page
    number when available, otherwise the caller records None (no page-recovery
    engineering; sorting treats None as 0).
    """

    chunk_id: str
    paper_id: str
    section: str | None = Field(default=None, description="Markdown heading path, e.g. 'Section > Subsection'.")
    page_start: int | None = Field(default=None, ge=1)
    page_end: int | None = Field(default=None, ge=1)
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
    max_chunks_per_paper: int | None = Field(default=None, ge=1, le=50)


class EvidenceRetrievalResponse(BaseModel):
    """Top evidence chunks for one research question or search query."""

    query: str = Field(min_length=3)
    policy: EvidenceRetrievalPolicy
    ranked_chunks: list[RankedEvidenceChunk]


class LlmEvidenceAssessment(BaseModel):
    """Structured assessment requested from an LLM for one retrieved chunk.

    Field order matters: an autoregressive model emits fields in schema order,
    so ``summary`` and both rationales come *before* the scores. This forces
    the model to restate the chunk and write its reasoning before assigning
    numbers (three-layer structure: restate -> relation -> score, M5b).
    """

    model_config = ConfigDict(extra="forbid")

    chunk_id: str
    summary: str = Field(min_length=20)
    rationale_relevance: str = Field(min_length=20)
    rationale_quality: str = Field(min_length=20)
    relevance_score: int = Field(ge=1, le=10)
    evidence_quality_score: int = Field(ge=1, le=10)


class LlmEvidenceAssessmentBatch(BaseModel):
    """The JSON object an LLM must return for one bounded evidence batch."""

    assessments: list[LlmEvidenceAssessment] = Field(min_length=1)


class LlmFunctionalAssessment(BaseModel):
    """Functional (utility) assessment requested from an LLM for one evidence chunk.

    Field order matters: ``rationale`` comes *before* ``utility_score`` so an
    autoregressive model must restate the chunk's contribution to the research
    idea before assigning a number (M4 I / A10: understand first, then score).
    ``utility_score`` rates the chunk's contribution to the research idea on a
    1-10 scale, independent of the query wording (C2b).
    """

    model_config = ConfigDict(extra="forbid")

    chunk_id: str
    rationale: str = Field(min_length=20)
    utility_score: int = Field(ge=0, le=10)


class LlmFunctionalAssessmentBatch(BaseModel):
    """The JSON object an LLM must return for one bounded functional-scoring batch."""

    assessments: list[LlmFunctionalAssessment] = Field(min_length=1)


class EvidenceSummary(LlmEvidenceAssessment):
    """LLM assessment enriched with trusted local evidence provenance."""

    paper_id: str
    page_start: int | None = Field(default=None, ge=1)
    page_end: int | None = Field(default=None, ge=1)


class EvidenceRerankResponse(BaseModel):
    """Second-stage LLM assessments tied to the first retrieval result."""

    retrieval_response: EvidenceRetrievalResponse
    summaries: list[EvidenceSummary]
    limitations: list[str]


class EvidenceCitation(BaseModel):
    """Trusted provenance and functional (utility) score for one chunk in a paper assessment.

    C2b: the dual relevance/quality dimensions are retired; ``utility_score``
    (1-10, int) is a single functional score rated by the LLM for the chunk's
    contribution to the research idea. ``rationale`` restates the chunk's
    content and its contribution (understand first, then score, M4 I / A10).
    """

    model_config = ConfigDict(extra="forbid")

    chunk_id: str
    page_start: int | None = Field(default=None, ge=1)
    page_end: int | None = Field(default=None, ge=1)
    rationale: str = Field(min_length=20)
    utility_score: int = Field(ge=0, le=10)


class PaperAssessment(BaseModel):
    """Why a paper is or is not useful for the current idea (C2b two-way).

    ``utility_score`` (float, 1-10) is the aggregated functional score of the
    paper's sampled evidence; ``recommendation`` is two-way only
    (include|exclude) — the ``consider`` band is retired. ``rationale`` must
    explain the score and the quota/threshold decision.
    """

    paper_id: str
    utility_score: float = Field(ge=0, le=10)
    recommendation: Literal["include", "exclude"]
    rationale: str = Field(min_length=20)
    evidence: list[EvidenceCitation] = Field(default_factory=list)


class FunctionalScoringPolicy(BaseModel):
    """Settings for the C2b functional evidence-scoring stage.

    ``batch_size`` (env ``FUNCTIONAL_BATCH_SIZE`` at the construction site)
    bounds how many chunks are scored per LLM call; ``top_chunks_per_paper``
    caps the per-paper sample before scoring; ``n_first_round`` /
    ``n_follow_up`` are per-query download quotas; ``threshold`` (env
    ``FUNCTIONAL_THRESHOLD``) is the minimum mean utility score a paper needs
    to satisfy the quota part of selection. ``max_weight`` (S3, 2026-09-12)
    weights the per-paper maximum in ``max_weight * max + (1 - max_weight) *
    mean`` aggregation — a smoothed max adoption so one outstanding chunk is
    not drowned by the mean, while the mean still suppresses a single outlier
    inflating the score. 0.7 is the starting value (policy-tunable).
    """

    batch_size: int = Field(default=4, ge=1, le=50)
    top_chunks_per_paper: int = Field(default=2, ge=1, le=10)
    n_first_round: int = Field(default=2, ge=1, le=10)
    n_follow_up: int = Field(default=1, ge=1, le=10)
    threshold: float = Field(default=6.0, ge=1, le=10)
    min_words: int = Field(default=4, ge=1, le=50)
    max_weight: float = Field(default=0.7, ge=0, le=1)


class FunctionalPaperScore(BaseModel):
    """One paper's aggregated functional score before quota/threshold selection.

    Carries the mean utility score of the paper's sampled chunks (sample size
    recorded because fairness is only guaranteed among equal-size samples) and
    the functional citations backing it. The final include/exclude decision is
    made by the selector (C2b Todo 3), not by this container.
    """

    paper_id: str
    utility_score: float = Field(ge=0, le=10)
    n_samples: int = Field(ge=1)
    evidence: list[EvidenceCitation] = Field(default_factory=list)


class AssessmentPolicy(BaseModel):
    """Explainable thresholds for a metadata-only paper assessment."""

    include_relevance_score: int = Field(default=8, ge=1, le=10)
    include_evidence_quality_score: int = Field(default=6, ge=1, le=10)
    consider_relevance_score: int = Field(default=6, ge=1, le=10)


class EvidenceAggregationPolicy(BaseModel):
    """Transparent thresholds for aggregating supplied chunk assessments per paper."""

    include_relevance_score: int = Field(default=8, ge=1, le=10)
    include_evidence_quality_score: int = Field(default=6, ge=1, le=10)
    consider_relevance_score: int = Field(default=6, ge=1, le=10)
    prior_score: float = Field(default=5.5, ge=1, le=10)
    shrinkage_strength: int = Field(default=1, ge=0)


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
    """A proposed next step with links back to supporting papers.

    Two coexisting citation surfaces (S1, 2026-09-12): the LLM path fills
    ``supporting_claim_ids`` (claim tags, never chunk ids — chunk tracing
    lives in the response-level ``claim_chunks`` lookup table); the
    deterministic path (no LLM, no claim system) fills ``supporting_chunk_ids``
    directly. Exactly one of the two must be non-empty.
    """

    title: str = Field(min_length=3)
    rationale: str = Field(min_length=20)
    supporting_paper_ids: list[str] = Field(min_length=1)
    supporting_claim_ids: list[str] | None = Field(default=None)
    supporting_chunk_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _at_least_one_supporting_surface(self) -> "FutureDirection":
        if not self.supporting_claim_ids and not self.supporting_chunk_ids:
            raise ValueError(
                "FutureDirection needs at least one supporting surface: "
                "supporting_claim_ids (LLM path) or supporting_chunk_ids "
                "(deterministic path)."
            )
        return self


class LiteratureReviewReport(BaseModel):
    """The final, human-readable and agent-readable output of Task 1A."""

    idea: ResearchIdea | str | None = None
    papers: list[Paper] = Field(min_length=1)
    assessments: list[PaperAssessment] = Field(min_length=1)
    synthesis: str = Field(min_length=20)
    future_directions: list[FutureDirection] = Field(min_length=1)
    limitations: list[str] = Field(default_factory=list)


class ChunkReference(BaseModel):
    """One cited evidence chunk that backs a summary claim."""

    chunk_id: str
    paper_id: str
    page_start: int | None = Field(default=None, ge=1)
    page_end: int | None = Field(default=None, ge=1)
    quote: str = Field(min_length=20)


class PaperSummaryClaim(BaseModel):
    """One claim in a per-paper summary, tied to cited evidence chunks."""

    claim_id: str = Field(default="", min_length=1)
    text: str = Field(min_length=20)
    aspect: str = Field(min_length=3)
    evidence: list[ChunkReference] = Field(min_length=1)


class PaperSummary(BaseModel):
    """An evidence-backed summary of one selected paper."""

    paper_id: str
    claims: list[PaperSummaryClaim] = Field(min_length=1)
    coverage_chunk_ids: list[str] = Field(min_length=1)


class LlmNoteClaim(BaseModel):
    """One claim requested from an LLM for a per-paper note."""

    text: str = Field(min_length=20)
    chunk_ids: list[str] = Field(min_length=1)
    aspect: str = Field(min_length=3)


class LlmPaperSummaryNote(BaseModel):
    """The JSON object an LLM must return to summarize one paper."""

    claims: list[LlmNoteClaim] = Field(min_length=1)
    coverage_chunk_ids: list[str] = Field(default_factory=list)


class LlmSynthesisDirection(BaseModel):
    """A future direction requested from an LLM during synthesis.

    C2c: the LLM cites ``supporting_claim_ids`` (claim tags, never chunk IDs);
    S1 (2026-09-12): the consumer copies the claim tags straight into the
    external ``FutureDirection.supporting_claim_ids`` — no chunk expansion;
    chunk tracing lives in the response-level ``claim_chunks`` lookup table.
    LLM Input Hygiene (2026-09-15): the LLM only returns
    ``supporting_claim_ids``; ``supporting_paper_ids`` is expanded
    programmatically from the claim-to-paper mapping.
    """

    title: str = Field(min_length=3)
    rationale: str = Field(min_length=20)
    supporting_claim_ids: list[str] = Field(min_length=1)


class LlmSynthesisBatch(BaseModel):
    """The JSON object an LLM must return for one bounded synthesis batch."""

    report: str = Field(min_length=100)
    future_directions: list[LlmSynthesisDirection] = Field(min_length=1)


class LlmOutlineSection(BaseModel):
    """One thematic section of the two-stage synthesis outline (C2e, call 1).

    ``supporting_claim_ids`` are claim tags copied verbatim from the supplied
    note claims; the outline stage (``build_outline_prompt``) drives 2-4 such
    sections, and the report stage writes each section from its own claims.
    """

    title: str = Field(min_length=3)
    purpose: str = Field(min_length=10)
    supporting_claim_ids: list[str] = Field(min_length=1)


class LlmSynthesisOutline(BaseModel):
    """The JSON object an LLM must return for the two-stage outline call."""

    sections: list[LlmOutlineSection] = Field(min_length=1)


class LlmSynthesisReportBatch(BaseModel):
    """The JSON body an LLM returns for one report section (also legacy report calls).

    Unlike the legacy ``LlmSynthesisBatch``, the report call carries no
    ``future_directions``: they are generated independently after all sections.
    """

    report: str = Field(min_length=100)


class LlmSynthesisDirectionsBatch(BaseModel):
    """The JSON object an LLM must return for the independent directions call (call 3)."""

    future_directions: list[LlmSynthesisDirection] = Field(min_length=1)


class PaperSource(BaseModel):
    """Local file provenance for one paper used in synthesis."""

    paper_id: str
    source_path: str = Field(min_length=1)
    claim_ids: list[str] = Field(default_factory=list)  # the global claim tags (claim-N) owned by this paper


class CoveragePackPolicy(BaseModel):
    """Reproducible settings for bounding the evidence sent to synthesis.

    ``llm_input_cap`` is a section-aware cap for Gemini notes: sections are
    covered first — every heading and each section's
    first chunk are kept, and only when the cap overflows does selection fall
    back to within-section strided sampling. OpenAI notes keep all selected
    chunks in one request; Groq notes keep all chunks and split them into
    section-bounded TPM-sized requests.
    """

    max_chunks_per_paper: int = Field(default=6, ge=1, le=50)
    llm_input_cap: int = Field(default=80, ge=1, le=200)


class ReportSection(BaseModel):
    """Locally assembled metadata for one validated report section."""

    section_index: int = Field(ge=1)
    title: str
    report: str = Field(min_length=100)
    allowed_claim_ids: list[str]
    cited_claim_ids: list[str]


class SynthesisResponse(BaseModel):
    """Evidence-cited synthesis that preserves every upstream provenance layer.

    ``report`` carries ``[claim-N]`` markers (C2c): ``claim-1..N`` are the
    global consecutive tags assigned in paper-summary document order; the
    mapping to supporting chunks lives in ``claim_chunks``, so the outer
    report surface switches from ``[chunk_id]`` to ``[claim-N]`` plus one
    shared lookup table (S1). ``FutureDirection`` carries both surfaces:
    ``supporting_claim_ids`` is filled directly from the LLM-level direction
    (no chunk expansion, chunk tracing via ``claim_chunks``), while the
    deterministic path fills ``supporting_chunk_ids`` directly.
    """

    paper_sources: list[PaperSource] = Field(min_length=1)
    evidence_assessment_response: EvidenceAssessmentResponse | None = None
    paper_assessments: list[PaperAssessment] = Field(default_factory=list)
    paper_summaries: list[PaperSummary] = Field(min_length=1)
    claim_chunks: dict[str, list[str]] = Field(default_factory=dict)
    report: str = Field(min_length=100)
    outline: LlmSynthesisOutline | None = None
    report_sections: list[ReportSection] = Field(default_factory=list)
    future_directions: list[FutureDirection] = Field(min_length=1)
    limitations: list[str]
    generated_by: Literal["deterministic", "llm"]
