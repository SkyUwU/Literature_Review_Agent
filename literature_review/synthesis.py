"""Evidence-cited synthesis: deterministic baseline plus bounded LLM generation (claim-level markers)."""

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
from literature_review.llm_evidence import JsonGenerationClient, generate_validated, strip_code_fence
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
    "build_claim_chunks",
    "build_coverage_packs",
    "build_deterministic_paper_notes",
    "build_deterministic_synthesis",
    "build_paper_notes_prompt",
    "build_synthesis_prompt",
    "detect_limitation_chunks",
    "render_materials_section",
    "summarize_paper_notes",
    "synthesize_report",
    "top_level_section",
]

_QUOTE_MAX_CHARS = 240
_CONVERGENCE_MIN_PAPERS = 2
_CONVERGENCE_UTILITY_SCORE = 6
_USABLE_RECOMMENDATIONS = frozenset({"include"})
_MARKER_REGEX: Final[re.Pattern[str]] = re.compile(r"\[([^\[\]]+)\]")
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


def build_claim_chunks(paper_summaries: list[PaperSummary]) -> dict[str, list[str]]:
    """Assign global ``claim-1..N`` tags and map each to its supporting chunks.

    C2c design B (2026-09-12): claim tags are the report's citation surface —
    the LLM writes ``[claim-N]`` markers and cites ``supporting_claim_ids``,
    never chunk ids. Numbering is global and consecutive: papers follow
    ``paper_summaries`` document order, and within a paper the claims follow
    their claim order, so the tags are unique across the whole corpus. Each
    tag maps to the paper's evidence chunks cited by that claim (sorted,
    deduplicated), which is the single source of truth for the
    response-level ``claim_chunks`` lookup table.
    """
    claim_chunks: dict[str, list[str]] = {}
    for summary in paper_summaries:
        for claim in summary.claims:
            claim_id = f"claim-{len(claim_chunks) + 1}"
            claim_chunks[claim_id] = sorted(
                {reference.chunk_id for reference in claim.evidence}
            )
    return claim_chunks


def _filter_usable(assessments: list[PaperAssessment]) -> list[PaperAssessment]:
    """Return assessments whose recommendation is in the usable set."""
    return [
        assessment
        for assessment in assessments
        if assessment.recommendation in _USABLE_RECOMMENDATIONS
    ]


def _usable_assessments(response: EvidenceAssessmentResponse) -> list[PaperAssessment]:
    """Legacy wrapper: extract usable assessments from an EvidenceAssessmentResponse."""
    return _filter_usable(response.assessments)


def _make_direction(
    title: str,
    rationale: str,
    paper_ids: list[str],
    chunk_ids: list[str],
) -> FutureDirection | None:
    """Build an external direction for the deterministic path (S1).

    The deterministic path never runs an LLM, so it bypasses the LLM-level
    ``LlmSynthesisDirection`` (claim-citing) contract entirely: there is no
    claim system, and ``FutureDirection.supporting_chunk_ids`` is filled
    directly, leaving ``supporting_claim_ids`` at its default ``None``.
    """
    if not chunk_ids:
        return None
    return FutureDirection(
        title=title,
        rationale=rationale,
        supporting_paper_ids=paper_ids,
        supporting_chunk_ids=chunk_ids,
    )


def _convergence_direction(strong: list[PaperAssessment]) -> FutureDirection | None:
    if len(strong) < _CONVERGENCE_MIN_PAPERS:
        return None
    paper_ids = [item.paper_id for item in strong]
    chunk_ids = sorted({citation.chunk_id for item in strong for citation in item.evidence})
    return _make_direction(
        "Consolidate converging high-utility evidence",
        f"{len(strong)} papers ({', '.join(paper_ids)}) independently reach utility score "
        f"{_CONVERGENCE_UTILITY_SCORE} or higher on their retrieved chunks.",
        paper_ids,
        chunk_ids,
    )


def _limitation_directions(
    notes: list[PaperSummary],
) -> list[FutureDirection]:
    directions: list[FutureDirection] = []
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


def _fallback_direction(assessments: list[PaperAssessment]) -> FutureDirection | None:
    if not assessments:
        return None
    best = min(
        assessments,
        key=lambda item: (-item.utility_score, item.paper_id),
    )
    return _make_direction(
        f"Extend the strongest single-paper evidence of {best.paper_id}",
        f"No cross-paper convergence or stated limitations were found; build on the highest-scoring "
        f"assessment of {best.paper_id} and its cited chunks.",
        [best.paper_id],
        sorted({citation.chunk_id for citation in best.evidence}),
    )


def _deterministic_directions(
    paper_assessments: list[PaperAssessment],
    notes: list[PaperSummary],
) -> list[FutureDirection]:
    assessments = _filter_usable(paper_assessments)
    convergence = _convergence_direction(
        [item for item in assessments if item.utility_score >= _CONVERGENCE_UTILITY_SCORE]
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

    evidence_ids_by_paper: dict[str, list[str]] = {}
    for assessment in assessments:
        evidence_ids_by_paper.setdefault(assessment.paper_id, []).extend(
            citation.chunk_id for citation in assessment.evidence
        )
    lines.append("")
    lines.append("## 材料來源清單")
    lines.extend(
        f"- {note.paper_id}: source={source_paths.get(note.paper_id, 'unavailable')}; "
        f"evidence chunks={', '.join(sorted(set(evidence_ids_by_paper.get(note.paper_id, [])))) or 'none'}; "
        f"notes cover={', '.join(note.coverage_chunk_ids)}"
        for note in notes
    )
    return "\n".join(lines)


def build_deterministic_synthesis(
    evidence_assessment_response: EvidenceAssessmentResponse | None,
    coverage_packs: dict[str, list[EvidenceChunk]],
    paper_sources: list[PaperSource],
    policy: CoveragePackPolicy,
    *,
    paper_assessments: list[PaperAssessment] | None = None,
) -> SynthesisResponse:
    """Compose the template report, per-paper notes, and rule-based directions.

    ``paper_assessments`` carries the C2b functional scoring path; when supplied it
    supersedes ``evidence_assessment_response`` (which is kept for the legacy RCS
    path and may be ``None`` in the functional path).
    """
    assessments = (
        _filter_usable(paper_assessments)
        if paper_assessments is not None
        else _usable_assessments(evidence_assessment_response)
    )
    if not assessments:
        raise SynthesisError("Synthesis needs at least one include paper assessment.")

    notes: list[PaperSummary] = []
    for assessment in assessments:
        pack = coverage_packs.get(assessment.paper_id)
        if not pack:
            raise SynthesisError(f"Missing coverage pack for paper {assessment.paper_id}.")
        notes.append(build_deterministic_paper_notes(pack, assessment, policy))

    directions = _deterministic_directions(assessments, notes)
    if not directions:
        raise SynthesisError("Could not derive any deterministic future direction.")
    source_paths = {source.paper_id: source.source_path for source in paper_sources}
    return SynthesisResponse(
        paper_sources=paper_sources,
        evidence_assessment_response=evidence_assessment_response,
        paper_assessments=assessments,
        paper_summaries=notes,
        claim_chunks=build_claim_chunks(notes),
        report=_render_report(notes, assessments, source_paths),
        future_directions=directions,
        limitations=[
            "Section-sampled coverage, not a full-text reading.",
            "Every factual sentence carries an inline [chunk_id] citation; this is not a whole-paper review.",
        ],
        generated_by="deterministic",
    )


def _parse_llm_model(model: type[_MODEL_T], raw_output: str) -> _MODEL_T:
    """Validate one JSON object while reporting schema failures without echoing model text."""
    try:
        return model.model_validate_json(strip_code_fence(raw_output))
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


def _build_claim_repair_prompt(raw_output: str, unknown_claims: list[str], valid_claims: list[str]) -> str:
    """Ask the model to correct hallucinated claim ids against the allowed set (C2c design B)."""
    return (
        "The previous response cited claim ids that are invalid or absent from the supplied notes: "
        f"{json.dumps(unknown_claims)}. "
        f"The only valid claim ids are: {json.dumps(valid_claims)}. "
        "Never invent, guess, or modify claim ids. Only use the exact ids from this set. "
        "Return a repaired version of the previous JSON, preserving all other content (report, text, "
        "aspect, supporting_paper_ids) but correcting every [claim-N] marker and every "
        "supporting_claim_ids value to use only valid claim ids. "
        "Return exactly one JSON object, without Markdown code fences or surrounding explanation. "
        f"Previous response:\n{raw_output}"
    )


def _generate_validated(client: JsonGenerationClient, model: type[_MODEL_T], prompt: str, schema: dict) -> _MODEL_T:
    """Call the client once and retry exactly once on any SynthesisError."""
    return generate_validated(
        client,
        model,
        prompt,
        schema,
        parse=lambda raw_output: _parse_llm_model(model, raw_output),
        repair_prompt=_build_schema_repair_prompt,
    )


def _strided_indices(count: int, slots: int) -> list[int]:
    stride = max(1, count // slots)
    return list(range(0, count, stride))[:slots]


def top_level_section(section: str | None, paper_title: str | None = None) -> str | None:
    """Return the top-level (first non-title) aspect of a markdown heading path.

    C2c design A (2026-09-12): the rule is layer-independent — split the path on
    ``" > "``, filter empty segments, and only judge the first segment as a
    possible title position. When it is judged the title, drop it and take the
    first remaining segment; when nothing remains, return ``None`` (the caller
    maps it to ``other``).
    """
    if not section:
        return None
    parts = [re.sub(r"\*\*", "", part).strip() for part in section.split(" > ")]
    parts = [part for part in parts if part]
    if not parts:
        return None
    if _is_title_segment(parts[0], parts, paper_title):
        parts = parts[1:]
    return parts[0] if parts else None


def _normalize_title(text: str) -> str:
    """Lowercase the text and strip every non-alphanumeric character."""
    return re.sub(r"[^a-z0-9]", "", text.lower())


def merge_numbered_section(aspect: str) -> str:
    """Collapse a dotted numbered aspect to its top-level number (S5).

    ``2.1 Multi-Agent`` and ``2.1.3 X`` both become ``2``, so sub-section
    headings flattened into the same markdown level stop fragmenting the
    aspect list; single-numbered (``1 Introduction``) and unnumbered
    (``Background``) aspects are kept unchanged.
    """
    match = re.match(r"^(\d+)\.\d+", aspect)
    return match.group(1) if match else aspect


def _is_title_segment(first: str, parts: list[str], paper_title: str | None) -> bool:
    """Judge whether the first path segment is the document title, not a section.

    Three signals (C2c design A): ① the normalized ``paper_title`` equals the
    segment or starts with it (a truncated path); ② position — the first segment
    of a multi-level path sits where a title prefix would be; ③ numbered segments
    are never treated as titles. The misjudgment bias favors keeping segments:
    only signals ① or ② may skip, and a single-segment path is always kept.
    """
    if re.match(r"^\d", first):
        return False
    if paper_title:
        normalized = _normalize_title(first)
        normalized_title = _normalize_title(paper_title)
        if normalized and (
            normalized == normalized_title or normalized_title.startswith(normalized)
        ):
            return True
    return len(parts) > 1


def _group_chunks_by_section(
    chunks: list[EvidenceChunk],
    paper_title: str | None = None,
) -> dict[str, list[EvidenceChunk]]:
    """Group chunks by their top-level section aspect in first-appearance order.

    Legacy chunks without a section path fall into the ``other`` group.
    Dotted numbered sub-headings are collapsed to their top-level number (S5)
    so flattened heading levels do not fragment the aspect groups.
    """
    groups: dict[str, list[EvidenceChunk]] = {}
    for chunk in chunks:
        aspect = merge_numbered_section(
            top_level_section(chunk.section, paper_title) or "other"
        )
        groups.setdefault(aspect, []).append(chunk)
    return groups


def _bounded_chunks_for_llm(
    chunks: list[EvidenceChunk],
    cap: int,
    *,
    paper_title: str | None = None,
) -> list[EvidenceChunk]:
    """Trim to the cap, section-first (C2c): every section's first chunk is kept
    before any extra chunks, and extras are spread across sections in document
    order only after each section is represented."""
    if len(chunks) <= cap:
        return list(chunks)
    groups = _group_chunks_by_section(chunks, paper_title)
    position = {chunk.chunk_id: index for index, chunk in enumerate(chunks)}
    selected: list[EvidenceChunk] = [group[0] for group in groups.values()]
    slack = cap - len(selected)
    if slack <= 0:
        return selected[:cap]
    chosen: set[str] = {chunk.chunk_id for chunk in selected}
    while slack:
        progressed = False
        for group in groups.values():
            if slack == 0:
                break
            chosen_in_group = sum(1 for chunk in group[1:] if chunk.chunk_id in chosen)
            rest = [chunk for chunk in group[1:] if chunk.chunk_id not in chosen]
            if not rest:
                continue
            index = _strided_indices(len(rest), chosen_in_group + 1)[-1]
            selected.append(rest[index])
            chosen.add(rest[index].chunk_id)
            slack -= 1
            progressed = True
        if not progressed:
            break
    return sorted(selected, key=lambda chunk: position[chunk.chunk_id])


def build_paper_notes_prompt(
    paper_id: str,
    chunks: list[EvidenceChunk],
    *,
    paper_title: str | None = None,
) -> str:
    """Ask the model for grounded per-paper reading notes built only from supplied chunks.

    C2c: chunks are grouped by their top-level section; each claim's ``aspect``
    must be exactly one of these section titles — never a subsection name.
    """
    groups = _group_chunks_by_section(chunks, paper_title)
    sections = [
        {
            "section": aspect,
            "chunks": [
                {
                    "chunk_id": chunk.chunk_id,
                    "page_start": chunk.page_start,
                    "page_end": chunk.page_end,
                    "text": chunk.text,
                }
                for chunk in group
            ],
        }
        for aspect, group in groups.items()
    ]
    section_titles = list(groups)
    limitation_ids = sorted({chunk.chunk_id for chunk in detect_limitation_chunks(chunks)})
    valid_ids = sorted(chunk.chunk_id for chunk in chunks)
    return (
        "Summarize this single paper using only the supplied evidence chunks. "
        "Do not use outside knowledge and do not invent claims. "
        "Choose each claim's 'aspect' as exactly one of these section titles: "
        f"{json.dumps(section_titles)}. Cover every section with at least one claim. "
        "Never use a subsection name as the aspect, such as '1.1 Background'. "
        "If a chunk states limitations or future work, give its claims the matching section aspect. "
        f"Detected limitation cues: {json.dumps(limitation_ids)}. "
        "Return exactly one JSON object, without Markdown code fences or surrounding explanation, "
        "containing 'claims' and 'coverage_chunk_ids'. Each claim must contain 'text' of at least "
        "20 characters, 'chunk_ids' as a non-empty subset of the supplied chunk identifiers, and 'aspect'. "
        f"The complete set of valid chunk_ids is: {json.dumps(valid_ids)}. "
        "Never invent, guess, or modify chunk IDs. Only use the exact IDs from this set. "
        f"Paper ID: {paper_id}\n"
        f"Evidence chunks: {json.dumps(sections, ensure_ascii=False)}"
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
    *,
    paper_title: str | None = None,
) -> PaperSummary:
    """Summarize one paper into grounded reading notes through one bounded LLM call."""
    supplied = _bounded_chunks_for_llm(chunks, policy.llm_input_cap, paper_title=paper_title)
    if not supplied:
        raise SynthesisError(f"No evidence chunks available for paper {paper_id}.")
    note = _generate_validated(
        client, LlmPaperSummaryNote,
        build_paper_notes_prompt(paper_id, supplied, paper_title=paper_title),
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
    paper_assessments: list[PaperAssessment],
    paper_summaries: list[PaperSummary],
) -> str:
    """Ask the model for a fluent report whose facts cite only supplied claim ids.

    C2c design B: the model writes prose tagged with the claim-level ``[claim-N]``
    markers only; it never sees any chunk id, and the material source list is
    assembled programmatically (A5), so the prose needs no 材料來源清單 section.
    Claim ids are assigned in document order over the usable papers, mirroring
    ``build_claim_chunks`` exactly, so the markers resolve back to chunks
    deterministically (''per-paper notes → global claim-1..N'').
    """
    usable_ids = {assessment.paper_id for assessment in _filter_usable(paper_assessments)}
    assessment_summaries = [
        {
            "paper_id": assessment.paper_id,
            "utility_score": assessment.utility_score,
            "rationale": assessment.rationale,
        }
        for assessment in paper_assessments
        if assessment.paper_id in usable_ids
    ]
    notes: list[dict[str, object]] = []
    claim_counter = 0
    for note in paper_summaries:
        if note.paper_id not in usable_ids:
            continue
        claims: list[dict[str, object]] = []
        for claim in note.claims:
            claim_counter += 1
            claims.append(
                {
                    "claim_id": f"claim-{claim_counter}",
                    "text": claim.text,
                    "aspect": claim.aspect,
                }
            )
        notes.append({"paper_id": note.paper_id, "claims": claims})
    return (
        "Write a fluent literature-review synthesis report using only the supplied material. "
        "Do not use outside knowledge and do not invent claims. "
        "End every factual sentence with an inline [claim-N] marker copied exactly from the "
        "supplied claim identifiers. Never invent, guess, or modify claim ids. The material "
        "source list is assembled from local files programmatically, so do not include a "
        "材料來源清單 section in the report. Base limitations and future directions primarily on "
        "the stated-limitation and future-work material. Return exactly one JSON object, without "
        "Markdown code fences or surrounding explanation, shaped as {'report': <report string>, "
        "'future_directions': <array>}; each direction must contain 'title', 'rationale', "
        "'supporting_paper_ids', and 'supporting_claim_ids', where every id comes from the "
        "supplied sets. "
        f"Paper assessments: {json.dumps(assessment_summaries, ensure_ascii=False)}\n"
        f"Per-paper notes: {json.dumps(notes, ensure_ascii=False)}"
    )


def _allowed_marker_ids(
    paper_summaries: list[PaperSummary],
    usable_ids: set[str],
) -> set[str]:
    """Claim-level marker whitelist: the global ``claim-N`` tags of the usable notes.

    C2c design B: the report model may only cite claim ids, never chunk ids, so
    the allowed set is derived from ``build_claim_chunks`` over the usable notes
    (identical numbering to ``build_synthesis_prompt``).
    """
    usable_notes = [note for note in paper_summaries if note.paper_id in usable_ids]
    return set(build_claim_chunks(usable_notes))


def _repair_claim_markers(
    batch: LlmSynthesisBatch,
    client: JsonGenerationClient,
    allowed_ids: set[str],
) -> LlmSynthesisBatch:
    """Validate the report's ``[claim-N]`` markers, repairing unknown ids once.

    C2c design B: the report model may only cite claim ids from the supplied
    notes. A report with no markers at all is rejected outright; unknown markers
    trigger exactly one repair call that names the valid claim set (mirroring the
    notes-stage ``_check_unknown_ids`` repair, M5b lesson). Markers still outside
    the allowed set after the repair are rejected.
    """
    markers = _MARKER_REGEX.findall(batch.report)
    if not markers:
        raise SynthesisError("LLM report must contain at least one inline [claim-N] citation marker.")
    unknown_markers = sorted(set(markers) - allowed_ids)
    if not unknown_markers:
        return batch
    repaired = _generate_validated(
        client,
        LlmSynthesisBatch,
        _build_claim_repair_prompt(
            json.dumps(batch.model_dump()), unknown_markers, sorted(allowed_ids)
        ),
        LlmSynthesisBatch.model_json_schema(),
    )
    markers = _MARKER_REGEX.findall(repaired.report)
    unknown_markers = sorted(set(markers) - allowed_ids)
    if not markers or unknown_markers:
        raise SynthesisError(
            f"LLM report cites claim ids outside the supplied evidence: {', '.join(unknown_markers)}."
        )
    return repaired


@observe(name="synthesis_report")
def synthesize_report(
    evidence_assessment_response: EvidenceAssessmentResponse | None,
    coverage_packs: dict[str, list[EvidenceChunk]],
    paper_summaries: list[PaperSummary],
    client: JsonGenerationClient,
    *,
    paper_assessments: list[PaperAssessment] | None = None,
) -> SynthesisResponse:
    """Produce the final evidence-cited synthesis through one bounded LLM call.

    ``paper_assessments`` carries the C2b functional scoring path; when supplied it
    supersedes ``evidence_assessment_response`` (kept for the legacy RCS path).
    Local file paths are not part of the assessment contract, so paper sources are
    reported as unavailable here; the pipeline layer that owns real paths can replace them.
    """
    assessments = (
        _filter_usable(paper_assessments)
        if paper_assessments is not None
        else _usable_assessments(evidence_assessment_response)
    )
    if not assessments:
        raise SynthesisError("Synthesis needs at least one include paper assessment.")
    usable_ids = {assessment.paper_id for assessment in assessments}
    batch = _generate_validated(
        client,
        LlmSynthesisBatch,
        build_synthesis_prompt(assessments, paper_summaries),
        LlmSynthesisBatch.model_json_schema(),
    )
    usable_notes = [note for note in paper_summaries if note.paper_id in usable_ids]
    claim_chunks = build_claim_chunks(usable_notes)
    allowed_ids = _allowed_marker_ids(paper_summaries, usable_ids)
    batch = _repair_claim_markers(batch, client, allowed_ids)
    for direction in batch.future_directions:
        unknown_papers = sorted(set(direction.supporting_paper_ids) - usable_ids)
        if unknown_papers:
            raise SynthesisError(
                f"LLM direction cites papers outside the supplied evidence: {', '.join(unknown_papers)}."
            )
        unknown_claims = sorted(set(direction.supporting_claim_ids) - set(claim_chunks))
        if unknown_claims:
            raise SynthesisError(
                f"LLM direction cites claim ids outside the supplied evidence: {', '.join(unknown_claims)}."
            )
    excluded_count = sum(
        1
        for assessment in (paper_assessments or [])
        if assessment.recommendation != "include"
    )
    limitations = [
        "Section-sampled coverage, not a full-text reading.",
        "Every factual sentence carries an inline [claim-N] citation; this is not a whole-paper review.",
    ]
    if excluded_count:
        limitations.append(_EXCLUDED_SUMMARIES_LIMITATION.format(count=excluded_count))
    return SynthesisResponse(
        paper_sources=[
            PaperSource(paper_id=paper_id, source_path="unavailable")
            for paper_id in sorted(usable_ids & coverage_packs.keys())
        ],
        evidence_assessment_response=evidence_assessment_response,
        paper_assessments=assessments,
        paper_summaries=usable_notes,
        claim_chunks=claim_chunks,
        report=batch.report,
        future_directions=[
            FutureDirection(
                title=direction.title,
                rationale=direction.rationale,
                supporting_paper_ids=direction.supporting_paper_ids,
                supporting_claim_ids=direction.supporting_claim_ids,
            )
            for direction in batch.future_directions
        ],
        limitations=limitations,
        generated_by="llm",
    )


def render_materials_section(
    paper_sources: list[PaperSource],
    paper_summaries: list[PaperSummary],
    claim_chunks: dict[str, list[str]],
) -> str:
    """Assemble the 材料來源清單 programmatically (C2c A5: zero LLM involvement).

    The LLM report prose no longer lists sources; the pipeline layer appends
    this deterministic section after resolving real file paths. Claim numbering
    mirrors ``build_claim_chunks`` (document order over the usable papers), so
    the per-paper claim ids shown here match the ``[claim-N]`` markers in the
    prose. The section lists each paper's source path, its global claim ids, and
    the chunks each claim cites; no bracketed identifiers are emitted, so this
    text can never collide with the report's inline marker validation.
    """
    source_by_id = {source.paper_id: source.source_path for source in paper_sources}
    lines = ["## 材料來源清單"]
    claim_counter = 0
    for summary in paper_summaries:
        claim_ids: list[str] = []
        for _ in summary.claims:
            claim_counter += 1
            claim_ids.append(f"claim-{claim_counter}")
        chunks = sorted(
            {
                chunk_id
                for claim_id in claim_ids
                for chunk_id in claim_chunks.get(claim_id, [])
            }
        )
        lines.append(
            f"- {summary.paper_id}: source={source_by_id.get(summary.paper_id, 'unavailable')}; "
            f"claims={', '.join(claim_ids) or 'none'}; chunks={', '.join(chunks) or 'none'}"
        )
    return "\n".join(lines)
