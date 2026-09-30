"""Functional (utility) scoring of evidence chunks for the research idea.

C2b replaces the relevance/quality dual dimensions with a single functional
score: how much a chunk contributes to the research idea behind the query —
independent of the sub-query wording. This module owns the LLM-facing prompt
and the batched scoring loop that resolves model-chosen indexes back to
trusted chunk ids (same anti-tamper design as ``summarize_and_rerank``).
"""

from langfuse import observe

from literature_review.coverage import drop_noise_sections
from literature_review.embedding_retriever import (
    Encoder,
    _cosine,
    encode_chunks,
    encode_query,
)
from literature_review.synthesis import top_level_section
from literature_review.llm_evidence import (
    JsonGenerationClient,
    LlmEvidenceError,
    LlmOutputSyntaxError,
    build_json_repair_prompt,
    strip_code_fence,
)
from literature_review.models import (
    EvidenceChunk,
    EvidenceCitation,
    FunctionalPaperScore,
    LlmFunctionalAssessment,
    LlmFunctionalAssessmentBatch,
    PaperAssessment,
)

FUNCTIONAL_BATCH_SIZE = 8


class FunctionalScoringError(LlmEvidenceError):
    """Raised for invalid functional-scoring model output.

    Subclasses ``LlmEvidenceError`` so the batched scoring loop can keep the
    single ``except LlmEvidenceError`` repair pattern used by the RCS stage.
    """


def build_functional_prompt(
    query: str,
    chunks: list[EvidenceChunk],
    paper_titles: dict[str, str] | None = None,
) -> str:
    """Ask the model for per-chunk utility scores without exposing provenance.

    ``query`` must be the *main* research query (the user's original idea),
    not a sub-query: utility is judged against the idea, so the wording of any
    sub-query is deliberately irrelevant (C2b Q7). Chunks are presented as
    ``## Chunk N`` with a title/section line followed by raw text only — no
    real chunk_id is sent, so the model can never copy or corrupt a trusted
    identifier; the caller resolves indexes back to ids.
    """
    blocks = []
    for index, chunk in enumerate(chunks, start=1):
        title = (paper_titles or {}).get(chunk.paper_id, "n/a")
        section = chunk.section or "n/a"
        blocks.append(
            f"## Chunk {index}\n"
            f"Paper: {title} | Section: {section}\n"
            f"{chunk.text}"
        )
    return (
        "Score each supplied evidence chunk only by its utility for the research idea described by the query. "
        "Do not use outside knowledge and do not invent claims. Return exactly one JSON object, "
        "without Markdown code fences or any surrounding explanation. "
        "The object must contain an 'assessments' array. Each item must include chunk_id "
        "(the chunk number), rationale, and utility_score (0-10). "
        "Write the rationale first, then assign the score consistent with it. "
        "The rationale must restate the chunk's core content in your own words and state "
        "how it advances the research idea. "
        "Before scoring, check whether the chunk provides at least one of the following "
        "kinds of concrete support — these are examples only, not an exhaustive list: "
        "① problem-definition or pain-point support — evidence that a problem exists and "
        "is not yet well solved; ② reusable technical mechanisms — concrete algorithms, "
        "architectures, or implementation details; ③ experimental and evaluation grounding "
        "— benchmark datasets, baselines, or evaluation metrics that can be compared "
        "directly. "
        "Utility measures contribution to the research idea — NOT how literally the chunk "
        "matches the query wording. A chunk can match the wording yet add nothing new, "
        "and a chunk that reuses methods or reports comparable data can be highly useful "
        "without sharing the query's terms. "
        "The 'Section' field shows the chunk's location in the paper (e.g. '2 Method', "
        "'Appendix'); use it to judge the evidence type — appendix or References chunks "
        "rarely contribute utility. "
        "Scoring guide. utility_score: 10 directly provides one of the three support kinds "
        "and fills a gap in the idea; 7-9 substantively advances a core facet; "
        "5-6 useful background that frames the idea (indirect support, including surveys "
        "or positioning); 3-4 tangentially related; 1-2 minimal contribution beyond noise; "
        "0 pure common knowledge, generic statements, or descriptions unrelated to the "
        "research idea. If you assign 0, state in the rationale why the chunk is common "
        "knowledge or unrelated. "
        f"Research idea: {query}\nEvidence chunks:\n" + "\n\n".join(blocks)
    )


def validate_functional_assessments(raw_output: str) -> LlmFunctionalAssessmentBatch:
    """Accept structured JSON while reporting schema failures without exposing model text."""
    normalized = strip_code_fence(raw_output)
    try:
        return LlmFunctionalAssessmentBatch.model_validate_json(normalized)
    except ValueError as error:
        details = "invalid JSON or schema mismatch"
        if hasattr(error, "errors"):
            issues = error.errors(include_url=False)
            if issues:
                location = ".".join(str(part) for part in issues[0]["loc"])
                details = f"{location}: {issues[0]['msg']}"
                if issues[0].get("type") == "json_invalid":
                    raise LlmOutputSyntaxError(
                        f"LLM output is malformed JSON ({details})."
                    ) from error
        raise FunctionalScoringError(
            f"LLM output failed functional-assessment validation ({details})."
        ) from error


def _resolve_functional_indexes(
    batch: LlmFunctionalAssessmentBatch,
    index_map: dict[str, str],
) -> list[LlmFunctionalAssessment]:
    """Map model-chosen chunk indexes back to the trusted real chunk ids."""
    resolved = []
    for assessment in batch.assessments:
        real_id = index_map.get(assessment.chunk_id)
        if real_id is None:
            raise FunctionalScoringError(
                f"LLM output referenced an unknown chunk index {assessment.chunk_id!r}."
            )
        resolved.append(assessment.model_copy(update={"chunk_id": real_id}))
    return resolved


@observe(name="functional_scoring", capture_input=False, capture_output=False)
def score_chunks_functionally(
    query: str,
    chunks: list[EvidenceChunk],
    client: JsonGenerationClient,
    *,
    batch_size: int = FUNCTIONAL_BATCH_SIZE,
    paper_titles: dict[str, str] | None = None,
) -> list[LlmFunctionalAssessment]:
    """Score each chunk's utility to the research idea, one bounded batch at a time.

    Each batch is an independent call with at most one repair attempt: malformed
    JSON, wrong schema, an unknown index, or an incomplete index set all trigger
    one repair prompt that names the expected chunk indexes (M5b lesson). The
    merged result covers every supplied chunk exactly once.
    """
    all_assessments: list[LlmFunctionalAssessment] = []
    schema = LlmFunctionalAssessmentBatch.model_json_schema()

    for start in range(0, len(chunks), batch_size):
        batch_chunks = chunks[start : start + batch_size]
        batch_ids = [chunk.chunk_id for chunk in batch_chunks]
        index_map = {
            str(index): chunk_id for index, chunk_id in enumerate(batch_ids, start=1)
        }
        raw_output = client.generate_json(
            build_functional_prompt(query, batch_chunks, paper_titles=paper_titles),
            schema,
        )
        try:
            generated = validate_functional_assessments(raw_output)
            resolved = _resolve_functional_indexes(generated, index_map)
        except LlmEvidenceError:
            # One bounded repair per batch: local models may return valid JSON
            # with the wrong shape or a drifted index; name the expected set so
            # the model can complete the batch (M5b index-drift lesson).
            generated = validate_functional_assessments(
                client.generate_json(
                    build_json_repair_prompt(
                        raw_output, expected_chunk_ids=list(index_map)
                    ),
                    schema,
                )
            )
            resolved = _resolve_functional_indexes(generated, index_map)
        returned_ids = [item.chunk_id for item in resolved]
        complete = len(returned_ids) == len(set(returned_ids)) and set(returned_ids) == set(
            batch_ids
        )
        if not complete:
            generated = validate_functional_assessments(
                client.generate_json(
                    build_json_repair_prompt(
                        raw_output, expected_chunk_ids=list(index_map)
                    ),
                    schema,
                )
            )
            resolved = _resolve_functional_indexes(generated, index_map)
            returned_ids = [item.chunk_id for item in resolved]
            if len(returned_ids) != len(set(returned_ids)) or set(returned_ids) != set(
                batch_ids
            ):
                raise FunctionalScoringError(
                    "LLM output must assess every supplied chunk exactly once."
                )
        all_assessments.extend(resolved)

    all_assessments.sort(key=lambda item: item.chunk_id)
    return all_assessments


def sample_top_chunks_per_paper(
    paper_chunks: list[EvidenceChunk],
    query: str,
    *,
    top_n: int,
    encoder: Encoder,
    query_map: dict[str, str] | None = None,
    paper_titles: dict[str, str] | None = None,
) -> dict[str, list[EvidenceChunk]]:
    """Blacklist-filter every paper's chunks, then keep the top-``top_n`` per paper.

    Sampling is strictly per-paper: the input chunks are grouped by paper,
    noise sections are dropped inside each paper, and the remaining chunks
    compete only against the *same paper's* chunks by embedding similarity
    (never across papers, C2b). The ranking query is the paper's own
    sub-query when ``query_map`` says so (S2): the map binds a paper to the
    search query that downloaded it, so a paper found by a specific
    sub-query competes under that query's angle instead of the main query's;
    absent mapping or an empty string falls back to the main ``query``. The
    scoring stage still scores all sampled chunks against the main query.
    A paper left with no chunks after blacklisting is excluded from scoring
    — it simply has no entry in the returned dict.
    """
    by_paper: dict[str, list[EvidenceChunk]] = {}
    for chunk in paper_chunks:
        by_paper.setdefault(chunk.paper_id, []).append(chunk)

    sampled: dict[str, list[EvidenceChunk]] = {}
    for paper_id in sorted(by_paper):
        filtered = drop_noise_sections(by_paper[paper_id])
        if not filtered:
            continue  # blacklisted away -> paper not scored
        sampling_query = (query_map or {}).get(paper_id) or query
        query_vector = encode_query(sampling_query, encoder)
        _section_wrapped_text = lambda chunk: f"{top_level_section(chunk.section, paper_titles.get(chunk.paper_id) if paper_titles else None) or 'other'} | {chunk.text}"
        chunk_vectors = encode_chunks(filtered, encoder, text_for=_section_wrapped_text)
        ranked = sorted(
            zip(filtered, chunk_vectors),
            key=lambda item: (-_cosine(query_vector, item[1]), item[0].chunk_id),
        )
        sampled[paper_id] = [chunk for chunk, _ in ranked[:top_n]]
    return sampled


def aggregate_functional(
    assessments: list[LlmFunctionalAssessment],
    per_paper_chunks: dict[str, list[EvidenceChunk]],
    *,
    max_weight: float = 0.7,
) -> dict[str, FunctionalPaperScore]:
    """Blend max and mean functional scores into per-paper score containers.

    Each paper's score is ``max_weight * max + (1 - max_weight) * mean`` of
    its sampled chunks' utility scores, rounded to one decimal (S3): the max
    keeps a single strong chunk from being diluted, the mean keeps one outlier
    from dominating. ``max_weight`` mirrors ``FunctionalScoringPolicy.max_weight``
    and defaults to 0.7; a single-chunk paper collapses to that chunk's score
    regardless of the weight. Sample size and functional citations are
    preserved. No recommendation is set here (C2b Q1 fix): the container only
    carries the evidence; the include/exclude decision belongs to the
    quota/threshold selector (Todo 3).
    """
    chunk_by_id: dict[str, EvidenceChunk] = {}
    for chunks in per_paper_chunks.values():
        for chunk in chunks:
            chunk_by_id[chunk.chunk_id] = chunk

    by_paper: dict[str, list[LlmFunctionalAssessment]] = {}
    for assessment in assessments:
        chunk = chunk_by_id.get(assessment.chunk_id)
        if chunk is None:
            raise FunctionalScoringError(
                f"Assessment references a chunk not in the sampled set: {assessment.chunk_id!r}."
            )
        by_paper.setdefault(chunk.paper_id, []).append(assessment)

    scores: dict[str, FunctionalPaperScore] = {}
    for paper_id, paper_assessments in by_paper.items():
        values = [item.utility_score for item in paper_assessments]
        max_score = max(values)
        mean_score = sum(values) / len(values)
        combined = round(max_weight * max_score + (1 - max_weight) * mean_score, 1)
        evidence = [
            EvidenceCitation(
                chunk_id=item.chunk_id,
                page_start=chunk_by_id[item.chunk_id].page_start,
                page_end=chunk_by_id[item.chunk_id].page_end,
                rationale=item.rationale,
                utility_score=item.utility_score,
            )
            for item in sorted(paper_assessments, key=lambda item: item.chunk_id)
        ]
        scores[paper_id] = FunctionalPaperScore(
            paper_id=paper_id,
            utility_score=combined,
            n_samples=len(paper_assessments),
            evidence=evidence,
        )
    return scores


def select_quota_threshold(
    scores: dict[str, FunctionalPaperScore],
    paper_queries: dict[str, str],
    follow_up_queries: set[str],
    *,
    n_first_round: int,
    n_follow_up: int,
    threshold: float,
) -> list[PaperAssessment]:
    """Turn per-paper functional scores into the final include/exclude list.

    Papers are grouped by the query that downloaded them. Inside each group the
    top ``n_first_round`` (or ``n_follow_up`` for follow-up queries) papers by
    utility score are the quota; among them only papers with a mean
    utility_score >= *threshold* are included. Papers that were never scored —
    blacklisted to zero chunks — are recorded here as excluded with their
    reason, so the returned list covers every downloaded paper exactly once.
    """
    by_query: dict[str, list[FunctionalPaperScore]] = {}
    for paper_id, query in paper_queries.items():
        score = scores.get(paper_id)
        if score is not None:
            by_query.setdefault(query, []).append(score)

    assessments: list[PaperAssessment] = []
    for query in sorted(by_query):
        quota = n_follow_up if query in follow_up_queries else n_first_round
        group = sorted(
            by_query[query],
            key=lambda item: (-item.utility_score, item.paper_id),
        )
        for rank, score in enumerate(group, start=1):
            included = rank <= quota and score.utility_score >= threshold
            rationale = (
                f"Mean functional utility {score.utility_score:.1f} over "
                f"{score.n_samples} sampled chunk(s) from query {query!r} "
                + ("meets the threshold within quota." if included else "misses the quota/threshold bar.")
            )
            assessments.append(
                PaperAssessment(
                    paper_id=score.paper_id,
                    utility_score=score.utility_score,
                    recommendation="include" if included else "exclude",
                    rationale=rationale,
                    evidence=score.evidence,
                )
            )

    for paper_id in sorted(set(paper_queries) - set(scores)):
        assessments.append(
            PaperAssessment(
                paper_id=paper_id,
                utility_score=1.0,
                recommendation="exclude",
                rationale=(
                    f"Paper {paper_id} was not scored: blacklisting left no "
                    "chunks, so it cannot enter the quota."
                ),
            )
        )

    assessments.sort(key=lambda item: (-item.utility_score, item.paper_id))
    return assessments