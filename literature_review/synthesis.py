"""Evidence-cited synthesis: deterministic baseline plus bounded LLM generation."""

# allow: SIZE_OK — Task 1A milestone contract requires the deterministic and LLM
# synthesis entry points to be defined and importable from this single public module;
# splitting them would break the mandated literature_review.synthesis import path.

import json
import re
from typing import Final, TypeVar

from pydantic import BaseModel

from literature_review.coverage import (
    build_coverage_packs,
    classify_section,
    detect_limitation_chunks,
)
from literature_review.llm_evidence import JsonGenerationClient
from literature_review.models import (
    ChunkReference,
    CoveragePackPolicy,
    EvidenceAssessmentResponse,
    EvidenceChunk,
    FutureDirection,
    LlmNoteClaim,
    LlmPaperSummaryNote,
    LlmSynthesisBatch,
    LlmSynthesisDirection,
    PaperAssessment,
    PaperSource,
    PaperSummary,
    PaperSummaryClaim,
    SynthesisResponse,
)

from langfuse import observe

__all__ = [
    "SynthesisError",
    "SynthesisOutputSyntaxError",
    "build_coverage_packs",
    "build_deterministic_paper_notes",
    "build_deterministic_synthesis",
    "build_paper_notes_prompt",
    "build_synthesis_prompt",
    "detect_limitation_chunks",
    "summarize_paper_notes",
    "synthesize_report",
]

_QUOTE_MAX_CHARS = 240
_CONVERGENCE_MIN_PAPERS = 2
_CONVERGENCE_RELEVANCE_SCORE = 4
_USABLE_RECOMMENDATIONS = frozenset({"include", "consider"})
_APPENDIX_BOUNDARY_REGEX: Final[re.Pattern[str]] = re.compile(
    r"(?i)^(?:appendix|supplementary\s+material)\b"
)
_MARKER_REGEX: Final[re.Pattern[str]] = re.compile(r"\[([^\[\]]+)\]")
_NOTE_ASPECTS: Final[tuple[str, ...]] = (
    "contribution",
    "method",
    "experiments",
    "results",
    "limitations",
    "other",
)
_EXCLUDED_SUMMARIES_LIMITATION: Final[str] = (
    "{count} excluded paper evidence summaries not included in synthesis"
)
_MODEL_T = TypeVar("_MODEL_T", bound=BaseModel)


class SynthesisError(RuntimeError):
    """Raised when synthesis stage fails."""


class SynthesisOutputSyntaxError(SynthesisError):
    """Raised when a synthesis-stage model response is not syntactically valid JSON."""


def build_deterministic_paper_notes(
    paper_chunks: list[EvidenceChunk],
    assessment: PaperAssessment,
    policy: CoveragePackPolicy,
) -> PaperSummary:
    """Build evidence-cited reading notes for one paper from its own chunks."""
    pack = build_coverage_packs(paper_chunks, policy).get(assessment.paper_id, [])
    if not pack:
        raise SynthesisError(f"No evidence chunks available for paper {assessment.paper_id}.")
    claims = _claims_from_chunks(pack)
    covered_ids = {ref.chunk_id for claim in claims for ref in claim.evidence}
    limitation_chunks = [
        chunk for chunk in detect_limitation_chunks(paper_chunks) if chunk.chunk_id not in covered_ids
    ]
    limitation_claims = _claims_from_chunks(limitation_chunks, aspect="limitations")
    all_claims = claims + limitation_claims
    referenced = {ref.chunk_id for claim in all_claims for ref in claim.evidence}
    return PaperSummary(
        paper_id=assessment.paper_id,
        claims=all_claims,
        coverage_chunk_ids=sorted(referenced),
    )


def _claims_from_chunks(
    chunks: list[EvidenceChunk],
    aspect: str | None = None,
) -> list[PaperSummaryClaim]:
    return [
        PaperSummaryClaim(
            text=chunk.text[:_QUOTE_MAX_CHARS],
            aspect=aspect or classify_section(chunk.text)[1],
            evidence=[
                ChunkReference(
                    chunk_id=chunk.chunk_id,
                    paper_id=chunk.paper_id,
                    page_start=chunk.page_start,
                    page_end=chunk.page_end,
                    quote=chunk.text[:_QUOTE_MAX_CHARS],
                )
            ],
        )
        for chunk in chunks
    ]


def _usable_assessments(response: EvidenceAssessmentResponse) -> list[PaperAssessment]:
    return [
        assessment
        for assessment in response.assessments
        if assessment.recommendation in _USABLE_RECOMMENDATIONS
    ]


def _make_direction(
    title: str,
    rationale: str,
    paper_ids: list[str],
    chunk_ids: list[str],
) -> LlmSynthesisDirection | None:
    if not chunk_ids:
        return None
    return LlmSynthesisDirection(
        title=title,
        rationale=rationale,
        supporting_paper_ids=paper_ids,
        supporting_chunk_ids=chunk_ids,
    )


def _convergence_direction(strong: list[PaperAssessment]) -> LlmSynthesisDirection | None:
    if len(strong) < _CONVERGENCE_MIN_PAPERS:
        return None
    paper_ids = [item.paper_id for item in strong]
    chunk_ids = sorted({citation.chunk_id for item in strong for citation in item.evidence})
    return _make_direction(
        "Consolidate converging high-relevance evidence",
        f"{len(strong)} papers ({', '.join(paper_ids)}) independently reach relevance score "
        f"{_CONVERGENCE_RELEVANCE_SCORE} or higher on their retrieved chunks.",
        paper_ids,
        chunk_ids,
    )


def _limitation_directions(
    notes: list[PaperSummary],
) -> list[LlmSynthesisDirection]:
    directions: list[LlmSynthesisDirection] = []
    for note in notes:
        limitation_claims = [c for c in note.claims if c.aspect == "limitations"]
        if not limitation_claims:
            continue
        chunk_ids = sorted({ref.chunk_id for claim in limitation_claims for ref in claim.evidence})
        direction = _make_direction(
            f"Target the stated limitations of {note.paper_id}",
            f"The authors of {note.paper_id} state limitations in chunks "
            f"{', '.join(chunk_ids)}; future work should address them directly.",
            [note.paper_id],
            chunk_ids,
        )
        if direction is not None:
            directions.append(direction)
    return directions


def _fallback_direction(assessments: list[PaperAssessment]) -> LlmSynthesisDirection | None:
    if not assessments:
        return None
    best = min(
        assessments,
        key=lambda item: (-item.relevance_score, -item.evidence_quality_score, item.paper_id),
    )
    return _make_direction(
        f"Extend the strongest single-paper evidence of {best.paper_id}",
        f"No cross-paper convergence or stated limitations were found; build on the highest-scoring "
        f"assessment of {best.paper_id} and its cited chunks.",
        [best.paper_id],
        sorted({citation.chunk_id for citation in best.evidence}),
    )


def _deterministic_directions(
    response: EvidenceAssessmentResponse,
    notes: list[PaperSummary],
) -> list[LlmSynthesisDirection]:
    assessments = _usable_assessments(response)
    convergence = _convergence_direction(
        [item for item in assessments if item.relevance_score >= _CONVERGENCE_RELEVANCE_SCORE]
    )
    limitation_directions = _limitation_directions(notes)
    if convergence is not None:
        return [convergence, *limitation_directions]
    if limitation_directions:
        return limitation_directions
    fallback = _fallback_direction(assessments)
    return [fallback] if fallback is not None else []


def _render_report(
    notes: list[PaperSummary],
    assessments: list[PaperAssessment],
    response: EvidenceAssessmentResponse,
    source_paths: dict[str, str],
) -> str:
    lines = ["# Deterministic evidence-cited synthesis", ""]
    for note in notes:
        lines.append(f"## Reading notes for {note.paper_id}")
        lines.extend(
            f"- {claim.aspect}: {claim.text} [{reference.chunk_id}]"
            for claim in note.claims
            for reference in claim.evidence
        )
        lines.append("")

    lines.append("## Cross-paper assessment synthesis")
    lines.extend(
        f"- {assessment.paper_id}: {assessment.rationale} "
        + " ".join(f"[{citation.chunk_id}]" for citation in assessment.evidence)
        for assessment in assessments
    )

    summary_ids_by_paper: dict[str, list[str]] = {}
    for summary in response.evidence_rerank_response.summaries:
        summary_ids_by_paper.setdefault(summary.paper_id, []).append(summary.chunk_id)
    lines.append("")
    lines.append("## 材料來源清單")
    lines.extend(
        f"- {note.paper_id}: source={source_paths.get(note.paper_id, 'unavailable')}; "
        f"evidence summaries={', '.join(summary_ids_by_paper.get(note.paper_id, [])) or 'none'}; "
        f"notes cover={', '.join(note.coverage_chunk_ids)}"
        for note in notes
    )
    return "\n".join(lines)


def build_deterministic_synthesis(
    evidence_assessment_response: EvidenceAssessmentResponse,
    coverage_packs: dict[str, list[EvidenceChunk]],
    paper_sources: list[PaperSource],
    policy: CoveragePackPolicy,
) -> SynthesisResponse:
    """Compose the template report, per-paper notes, and rule-based directions."""
    assessments = _usable_assessments(evidence_assessment_response)
    if not assessments:
        raise SynthesisError("Synthesis needs at least one include/consider paper assessment.")

    notes: list[PaperSummary] = []
    for assessment in assessments:
        pack = coverage_packs.get(assessment.paper_id)
        if not pack:
            raise SynthesisError(f"Missing coverage pack for paper {assessment.paper_id}.")
        notes.append(build_deterministic_paper_notes(pack, assessment, policy))

    directions = [
        FutureDirection(**direction.model_dump())
        for direction in _deterministic_directions(evidence_assessment_response, notes)
    ]
    if not directions:
        raise SynthesisError("Could not derive any deterministic future direction.")
    source_paths = {source.paper_id: source.source_path for source in paper_sources}
    return SynthesisResponse(
        paper_sources=paper_sources,
        evidence_assessment_response=evidence_assessment_response,
        paper_summaries=notes,
        report=_render_report(notes, assessments, evidence_assessment_response, source_paths),
        future_directions=directions,
        limitations=[
            "Section-sampled coverage, not a full-text reading.",
            "Every factual sentence carries an inline [chunk_id] citation; this is not a whole-paper review.",
        ],
        generated_by="deterministic",
    )


def _strip_code_fence(raw_output: str) -> str:
    normalized = raw_output.strip()
    if normalized.startswith("```") and normalized.endswith("```"):
        lines = normalized.splitlines()
        normalized = "\n".join(lines[1:-1]).strip()
    last_brace = normalized.rfind("}")
    if last_brace != -1:
        normalized = normalized[: last_brace + 1]
    return normalized


def _parse_llm_model(model: type[_MODEL_T], raw_output: str) -> _MODEL_T:
    """Validate one JSON object while reporting schema failures without echoing model text."""
    try:
        return model.model_validate_json(_strip_code_fence(raw_output))
    except ValueError as error:
        details = "invalid JSON or schema mismatch"
        syntax_invalid = False
        if hasattr(error, "errors"):
            issues = error.errors(include_url=False)
            if issues:
                location = ".".join(str(part) for part in issues[0]["loc"])
                details = f"{location}: {issues[0]['msg']}"
                syntax_invalid = issues[0].get("type") == "json_invalid"
        if syntax_invalid:
            raise SynthesisOutputSyntaxError(f"LLM output is malformed JSON ({details}).") from error
        raise SynthesisError(f"LLM output failed {model.__name__} validation ({details}).") from error


def _build_repair_prompt(raw_output: str) -> str:
    return (
        "The previous response was malformed JSON. Return a repaired version as exactly one JSON object, "
        "without Markdown or explanation. Preserve the intended content and follow the requested schema. "
        f"Previous response:\n{raw_output}"
    )


def _build_schema_repair_prompt(raw_output: str, error_details: str) -> str:
    return (
        "The previous response had schema validation errors. Return a repaired version "
        "as exactly one JSON object, without Markdown or explanation. "
        "Fix the specific issue described below while preserving all other correct content. "
        f"Validation error: {error_details}\n"
        f"Previous response:\n{raw_output}"
    )


def _build_chunk_repair_prompt(raw_output: str, unknown_ids: list[str], valid_ids: list[str]) -> str:
    """Ask the model to correct hallucinated chunk IDs against the allowed set."""
    return (
        "The previous response cited chunk ids that are invalid or absent from the supplied evidence: "
        f"{json.dumps(unknown_ids)}. "
        f"The only valid chunk_ids are: {json.dumps(valid_ids)}. "
        "Never invent, guess, or modify chunk IDs. Only use the exact IDs from this set. "
        "Return a repaired version of the previous JSON, preserving all other content (text, aspect) "
        "but correcting every chunk_ids value and coverage_chunk_ids to use only valid chunk ids. "
        "Return exactly one JSON object, without Markdown code fences or surrounding explanation. "
        f"Previous response:\n{raw_output}"
    )


def _generate_validated(client: JsonGenerationClient, model: type[_MODEL_T], prompt: str, schema: dict) -> _MODEL_T:
    """Call the client once and retry exactly once on any SynthesisError."""
    raw_output = client.generate_json(prompt, schema)
    try:
        return _parse_llm_model(model, raw_output)
    except SynthesisError as exc:
        repair = _build_schema_repair_prompt(raw_output, str(exc))
        return _parse_llm_model(model, client.generate_json(repair, schema))


def _strided_indices(count: int, slots: int) -> list[int]:
    stride = max(1, count // slots)
    return list(range(0, count, stride))[:slots]


def _bounded_chunks_for_llm(chunks: list[EvidenceChunk], cap: int) -> list[EvidenceChunk]:
    """Trim to the cap in document order; body text before an appendix boundary wins."""
    if len(chunks) <= cap:
        return list(chunks)
    appendix_index = next(
        (index for index, chunk in enumerate(chunks) if _APPENDIX_BOUNDARY_REGEX.search(chunk.text)),
        None,
    )
    if appendix_index is None:
        return [chunks[index] for index in _strided_indices(len(chunks), cap)]
    body = chunks[:appendix_index]
    appendix = chunks[appendix_index:]
    if len(body) >= cap:
        return [body[index] for index in _strided_indices(len(body), cap)]
    selected = list(body)
    selected.extend(appendix[index] for index in _strided_indices(len(appendix), cap - len(body)))
    return selected


def build_paper_notes_prompt(paper_id: str, chunks: list[EvidenceChunk]) -> str:
    """Ask the model for grounded per-paper reading notes built only from supplied chunks."""
    payload = [
        {
            "chunk_id": chunk.chunk_id,
            "paper_id": chunk.paper_id,
            "page_start": chunk.page_start,
            "page_end": chunk.page_end,
            "text": chunk.text,
        }
        for chunk in chunks
    ]
    sections_by_aspect: dict[str, list[str]] = {}
    for chunk in chunks:
        _, aspect = classify_section(chunk.text)
        sections_by_aspect.setdefault(aspect, []).append(chunk.chunk_id)
    limitation_ids = sorted({chunk.chunk_id for chunk in detect_limitation_chunks(chunks)})
    valid_ids = sorted(chunk.chunk_id for chunk in chunks)
    return (
        "Summarize this single paper using only the supplied evidence chunks. "
        "Do not use outside knowledge and do not invent claims. "
        "Classify each chunk by section and select representative chunk_ids per aspect. "
        "If a chunk states limitations or future work, give it aspect='limitations'. "
        f"Detected limitation cues: {json.dumps(limitation_ids)}. "
        f"Section classification of the supplied chunks: {json.dumps(sections_by_aspect)}. "
        "Every aspect must be one full English word of at least three letters chosen from: "
        f"{', '.join(_NOTE_ASPECTS)}. "
        "Return exactly one JSON object, without Markdown code fences or surrounding explanation, "
        "containing 'claims' and 'coverage_chunk_ids'. Each claim must contain 'text' of at least "
        "20 characters, 'chunk_ids' as a non-empty subset of the supplied chunk identifiers, and 'aspect'. "
        f"The complete set of valid chunk_ids is: {json.dumps(valid_ids)}. "
        "Never invent, guess, or modify chunk IDs. Only use the exact IDs from this set. "
        f"Paper ID: {paper_id}\n"
        f"Evidence chunks: {json.dumps(payload, ensure_ascii=False)}"
    )


def _check_unknown_ids(
    note: LlmPaperSummaryNote,
    supplied: list[EvidenceChunk],
) -> list[str]:
    """Return the sorted cited chunk ids that are not among the supplied chunks."""
    chunk_by_id = {chunk.chunk_id for chunk in supplied}
    cited_ids = {
        chunk_id for claim in note.claims for chunk_id in claim.chunk_ids
    } | set(note.coverage_chunk_ids)
    return sorted(cited_ids - chunk_by_id)


def _claim_from_note(
    claim: LlmNoteClaim,
    chunk_by_id: dict[str, EvidenceChunk],
) -> PaperSummaryClaim:
    references: list[ChunkReference] = []
    for chunk_id in dict.fromkeys(claim.chunk_ids):
        chunk = chunk_by_id[chunk_id]
        references.append(
            ChunkReference(
                chunk_id=chunk.chunk_id,
                paper_id=chunk.paper_id,
                page_start=chunk.page_start,
                page_end=chunk.page_end,
                quote=chunk.text[:_QUOTE_MAX_CHARS],
            )
        )
    return PaperSummaryClaim(text=claim.text, aspect=claim.aspect if claim.aspect else "limitations", evidence=references)


@observe(name="paper_notes")
def summarize_paper_notes(
    paper_id: str,
    chunks: list[EvidenceChunk],
    client: JsonGenerationClient,
    policy: CoveragePackPolicy,
) -> PaperSummary:
    """Summarize one paper into grounded reading notes through one bounded LLM call."""
    supplied = _bounded_chunks_for_llm(chunks, policy.llm_input_cap)
    if not supplied:
        raise SynthesisError(f"No evidence chunks available for paper {paper_id}.")
    note = _generate_validated(
        client, LlmPaperSummaryNote,
        build_paper_notes_prompt(paper_id, supplied),
        LlmPaperSummaryNote.model_json_schema(),
    )
    chunk_by_id = {chunk.chunk_id: chunk for chunk in supplied}
    unknown_ids = _check_unknown_ids(note, supplied)
    if unknown_ids:
        repair = _build_chunk_repair_prompt(
            json.dumps(note.model_dump()), unknown_ids, sorted(chunk_by_id)
        )
        note = _parse_llm_model(
            LlmPaperSummaryNote, client.generate_json(repair, LlmPaperSummaryNote.model_json_schema())
        )
        unknown_ids = _check_unknown_ids(note, supplied)
        if unknown_ids:
            raise SynthesisError(
                f"LLM note for paper {paper_id} cites unknown chunk ids: {', '.join(unknown_ids)}."
            )
    claims = [_claim_from_note(claim, chunk_by_id) for claim in note.claims]
    coverage_chunk_ids = sorted(
        {reference.chunk_id for claim in claims for reference in claim.evidence}
    )
    if not coverage_chunk_ids:
        raise SynthesisError(f"LLM note for paper {paper_id} cites no evidence chunks.")
    return PaperSummary(
        paper_id=paper_id,
        claims=claims,
        coverage_chunk_ids=coverage_chunk_ids,
    )


def build_synthesis_prompt(
    evidence_assessment_response: EvidenceAssessmentResponse,
    paper_summaries: list[PaperSummary],
) -> str:
    """Ask the model for a fluent report whose facts cite only supplied chunk identifiers."""
    usable_ids = {assessment.paper_id for assessment in _usable_assessments(evidence_assessment_response)}
    summaries = [
        {
            "chunk_id": summary.chunk_id,
            "paper_id": summary.paper_id,
            "page_start": summary.page_start,
            "page_end": summary.page_end,
            "summary": summary.summary,
            "relevance_score": summary.relevance_score,
            "evidence_quality_score": summary.evidence_quality_score,
            "recommendation": summary.recommendation,
        }
        for summary in evidence_assessment_response.evidence_rerank_response.summaries
        if summary.paper_id in usable_ids
    ]
    notes = [
        {
            "paper_id": note.paper_id,
            "claims": [
                {
                    "text": claim.text,
                    "aspect": claim.aspect,
                    "chunk_ids": [reference.chunk_id for reference in claim.evidence],
                }
                for claim in note.claims
            ],
            "coverage_chunk_ids": note.coverage_chunk_ids,
        }
        for note in paper_summaries
        if note.paper_id in usable_ids
    ]
    return (
        "Write a fluent literature-review synthesis report using only the supplied material. "
        "Do not use outside knowledge and do not invent claims. "
        "End every factual sentence with an inline [chunk_id] marker copied exactly from the supplied chunk "
        "identifiers. Close the report with a 材料來源清單 section listing each paper's evidence summaries and "
        "covered chunks. Base limitations and future directions primarily on the stated-limitation and "
        "future-work material. Return exactly one JSON object, without Markdown code fences or surrounding "
        "explanation, shaped as {'report': <report string>, 'future_directions': <array>}; each direction must "
        "contain 'title', 'rationale', 'supporting_paper_ids', and 'supporting_chunk_ids', where every id comes "
        "from the supplied sets. "
        f"Evidence summaries: {json.dumps(summaries, ensure_ascii=False)}\n"
        f"Per-paper notes: {json.dumps(notes, ensure_ascii=False)}"
    )


def _allowed_marker_ids(
    evidence_assessment_response: EvidenceAssessmentResponse,
    paper_summaries: list[PaperSummary],
    usable_ids: set[str],
) -> set[str]:
    return {
        summary.chunk_id
        for summary in evidence_assessment_response.evidence_rerank_response.summaries
        if summary.paper_id in usable_ids
    } | {
        chunk_id
        for note in paper_summaries
        if note.paper_id in usable_ids
        for chunk_id in note.coverage_chunk_ids
    }


@observe(name="synthesis_report")
def synthesize_report(
    evidence_assessment_response: EvidenceAssessmentResponse,
    coverage_packs: dict[str, list[EvidenceChunk]],
    paper_summaries: list[PaperSummary],
    client: JsonGenerationClient,
) -> SynthesisResponse:
    """Produce the final evidence-cited synthesis through one bounded LLM call.

    Local file paths are not part of the assessment contract, so paper sources are
    reported as unavailable here; the pipeline layer that owns real paths can replace them.
    """
    assessments = _usable_assessments(evidence_assessment_response)
    if not assessments:
        raise SynthesisError("Synthesis needs at least one include/consider paper assessment.")
    usable_ids = {assessment.paper_id for assessment in assessments}
    batch = _generate_validated(
        client,
        LlmSynthesisBatch,
        build_synthesis_prompt(evidence_assessment_response, paper_summaries),
        LlmSynthesisBatch.model_json_schema(),
    )
    allowed_ids = _allowed_marker_ids(evidence_assessment_response, paper_summaries, usable_ids)
    markers = _MARKER_REGEX.findall(batch.report)
    if not markers:
        raise SynthesisError("LLM report must contain at least one inline [chunk_id] citation marker.")
    unknown_markers = sorted(set(markers) - allowed_ids)
    if unknown_markers:
        raise SynthesisError(
            f"LLM report cites chunk ids outside the supplied evidence: {', '.join(unknown_markers)}."
        )
    for direction in batch.future_directions:
        unknown_papers = sorted(set(direction.supporting_paper_ids) - usable_ids)
        if unknown_papers:
            raise SynthesisError(
                f"LLM direction cites papers outside the supplied evidence: {', '.join(unknown_papers)}."
            )
        unknown_chunks = sorted(set(direction.supporting_chunk_ids) - allowed_ids)
        if unknown_chunks:
            raise SynthesisError(
                f"LLM direction cites chunks outside the supplied evidence: {', '.join(unknown_chunks)}."
            )
    excluded_count = sum(
        1
        for assessment in evidence_assessment_response.assessments
        if assessment.recommendation == "exclude"
    )
    limitations = [
        "Section-sampled coverage, not a full-text reading.",
        "Every factual sentence carries an inline [chunk_id] citation; this is not a whole-paper review.",
    ]
    if excluded_count:
        limitations.append(_EXCLUDED_SUMMARIES_LIMITATION.format(count=excluded_count))
    return SynthesisResponse(
        paper_sources=[
            PaperSource(paper_id=paper_id, source_path="unavailable")
            for paper_id in sorted(usable_ids & coverage_packs.keys())
        ],
        evidence_assessment_response=evidence_assessment_response,
        paper_summaries=[note for note in paper_summaries if note.paper_id in usable_ids],
        report=batch.report,
        future_directions=[
            FutureDirection(**direction.model_dump()) for direction in batch.future_directions
        ],
        limitations=limitations,
        generated_by="llm",
    )
