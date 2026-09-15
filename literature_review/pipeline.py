"""Opt-in paths from local PDFs to ranked LLM evidence and cited synthesis."""

import argparse
import glob
import json
import os
import sys
import time

try:
    from langfuse import get_client
except Exception:  # langfuse 未安裝或 import 失敗 — 不擋 pipeline
    get_client = None

from literature_review.assessment import aggregate_evidence_assessments
from literature_review.coverage import drop_noise_sections
from literature_review.evidence import chapter_chunk_document, chunk_document
from literature_review.embedding_retriever import Encoder, default_encoder, retrieve_evidence_embedding
from literature_review.extraction import PdfExtractionError, extract_pdf_text
from literature_review.functional import (
    aggregate_functional,
    sample_top_chunks_per_paper,
    score_chunks_functionally,
    select_quota_threshold,
)
from literature_review.llm_evidence import GeminiJsonClient, JsonGenerationClient, LlmEvidenceError, summarize_and_rerank
from literature_review.models import (
    ChunkPolicy,
    CoveragePackPolicy,
    EvidenceAggregationPolicy,
    EvidenceChunk,
    EvidenceRetrievalPolicy,
    EvidenceRetrievalResponse,
    EvidenceRerankResponse,
    FullTextDocument,
    FunctionalScoringPolicy,
    PaperSummary,
    SynthesisResponse,
)
from literature_review.synthesis import (
    SynthesisError,
    build_coverage_packs,
    render_materials_section,
    summarize_paper_notes,
    synthesize_report,
)


def _rcs_batch_size() -> int:
    raw = os.getenv("RCS_BATCH_SIZE", "4")
    try:
        value = int(raw)
    except ValueError:
        return 4
    return value if value >= 1 else 4


def _notes_pacing_seconds() -> float:
    """Seconds to sleep between consecutive per-paper note calls (key2 rate guard).

    Gemini free-tier is limited to 20 generate_content requests per minute per
    model; the notes stage calls the LLM once per paper in a tight loop, so a
    multi-paper run structurally exceeds that window (M5b Todo 6 real-run
    evidence: two runs both failed at ``summarize_paper_notes`` with a short
    429). This pacing spreads the calls across minutes. Zero disables it.
    """
    raw = os.getenv("NOTES_PACING_SECONDS", "4")
    try:
        value = float(raw)
    except ValueError:
        return 4.0
    return value if value >= 0 else 4.0


def run_evidence_pipeline(
    document: FullTextDocument,
    query: str,
    client: JsonGenerationClient,
    *,
    chunk_policy: ChunkPolicy,
    retrieval_policy: EvidenceRetrievalPolicy,
    encoder: Encoder | None = None,
) -> EvidenceRerankResponse:
    """Run chunking, first-stage retrieval, and LLM contextual re-ranking.

    ``encoder`` may be injected for tests; when omitted the real embedding model
    is built (``default_encoder``), so retrieve_evidence_embedding downloads the
    BGE weights on the first real run.
    """
    chunks = chunk_document(document, chunk_policy)
    if not chunks:
        raise ValueError("The extracted document did not produce usable evidence chunks.")
    retrieved = retrieve_evidence_embedding(chunks, query, retrieval_policy, encoder)
    return summarize_and_rerank(retrieved, client, batch_size=_rcs_batch_size())


def retrieve_from_pdf(
    pdf_path: str,
    paper_id: str,
    query: str,
    *,
    chunk_policy: ChunkPolicy,
    retrieval_policy: EvidenceRetrievalPolicy,
    encoder: Encoder | None = None,
) -> EvidenceRetrievalResponse:
    """Run only the local, no-key portion of the evidence workflow."""
    document = extract_pdf_text(pdf_path, paper_id)
    chunks = chunk_document(document, chunk_policy)
    if not chunks:
        raise ValueError("The extracted document did not produce usable evidence chunks.")
    return retrieve_evidence_embedding(chunks, query, retrieval_policy, encoder)


def expand_pdf_inputs(inputs: list[str]) -> list[str]:
    """Expand directories to sorted PDF lists, deduplicate, validate."""
    expanded: list[str] = []
    seen: set[str] = set()
    for item in inputs:
        candidates = sorted(glob.glob(os.path.join(item, "*.pdf"))) if os.path.isdir(item) else [item]
        for candidate in candidates:
            resolved = os.path.realpath(candidate)
            if resolved not in seen:
                seen.add(resolved)
                expanded.append(candidate)
    if not expanded:
        raise ValueError("No PDF files found in the provided inputs.")
    return expanded


def _prepare_documents(
    documents: list[FullTextDocument],
    chunk_policy: ChunkPolicy,
    min_words: int = 4,
) -> list[tuple[FullTextDocument, list[EvidenceChunk]]]:
    """Chunk every document (section-aware), dropping noise regions, rejecting duplicates.

    C2b (A10): the legacy word-overlap ``chunk_document`` is replaced by the
    section-aware ``chapter_chunk_document`` (chunk ids ``{paper_id}-c{n}`` with a
    ``section`` heading path); ``drop_noise_sections`` removes
    references/acknowledgments (and appendix) chunks so notes and functional
    scoring never see citation noise. Chunks shorter than ``min_words`` words are
    dropped (default 4, mirroring both the legacy splitter minimum and
    ``FunctionalScoringPolicy.min_words``), so a near-empty document is skipped
    with the stderr note below.
    """
    seen_ids: set[str] = set()
    prepared: list[tuple[FullTextDocument, list[EvidenceChunk]]] = []
    for document in documents:
        if document.paper_id in seen_ids:
            raise ValueError(f"Duplicate paper_id {document.paper_id!r} in the supplied documents.")
        seen_ids.add(document.paper_id)
        chunks = drop_noise_sections(chapter_chunk_document(document, chunk_policy))
        if min_words > 0:
            chunks = [chunk for chunk in chunks if len(chunk.text.split()) >= min_words]
        if chunks:
            prepared.append((document, chunks))
        else:
            print(f"Skipping {document.paper_id}: extracted text produced no evidence chunks.", file=sys.stderr)
    if not prepared:
        raise ValueError("None of the supplied documents produced usable evidence chunks.")
    return prepared


def run_synthesis_pipeline(
    documents: list[FullTextDocument],
    query: str,
    client: JsonGenerationClient,
    *,
    client_rcs: JsonGenerationClient | None = None,
    client_report: JsonGenerationClient | None = None,
    paper_meta: dict[str, tuple[int | None, str | None]] | None = None,
    chunk_policy: ChunkPolicy = ChunkPolicy(),
    retrieval_policy: EvidenceRetrievalPolicy = EvidenceRetrievalPolicy(),
    coverage_policy: CoveragePackPolicy = CoveragePackPolicy(),
    aggregation_policy: EvidenceAggregationPolicy = EvidenceAggregationPolicy(),
    functional_policy: FunctionalScoringPolicy | None = None,
    encoder: Encoder | None = None,
    notes_pacing_seconds: float | None = None,
    paper_titles: dict[str, str] | None = None,
    paper_queries: dict[str, str] | None = None,
    follow_up_queries: set[str] | None = None,
) -> SynthesisResponse:
    """Run section-aware chunking, per-paper functional scoring, notes, and cited synthesis.

    C2b: the corpus-wide RCS stage (``summarize_and_rerank`` then
    ``aggregate_evidence_assessments``) is retired from this path. ``retrieval_policy``
    and ``aggregation_policy`` are kept only for CLI/legacy signature compatibility
    and are ignored (per-paper sampling is driven by
    ``FunctionalScoringPolicy.top_chunks_per_paper``).
    ``client_rcs`` is now the *functional-scoring* client: each paper's top sampled
    chunks are scored against the main research ``query`` (batched utility scores);
    when omitted, ``client`` serves scoring too. ``paper_titles`` feeds the scoring
    prompt's paper-title line; ``paper_queries`` maps each paper to the query that
    downloaded it — the sub-query also drives per-paper *sampling* (S2: the top
    chunks of each paper are ranked against that paper's own query), while scoring
    still uses the main ``query`` — and ``follow_up_queries`` marks the gap
    follow-up group, so ``select_quota_threshold`` can apply the per-query
    quota ∩ threshold rule.
    ``notes_pacing_seconds`` spaces consecutive per-paper note calls (defaults to
    the ``NOTES_PACING_SECONDS`` env value, ~4s; see ``_notes_pacing_seconds``);
    passing 0 disables the pacing for tests.
    ``client_report`` (C2c, key3) generates the final synthesis report; when
    omitted, ``client`` serves the report too, and the programmatic 材料來源清單
    section (A5) is appended to the report by this layer after the real source
    paths are resolved.
    """
    effective_functional_policy = functional_policy or FunctionalScoringPolicy()
    prepared = _prepare_documents(
        documents,
        chunk_policy,
        min_words=effective_functional_policy.min_words,
    )
    all_chunks = [chunk for _, chunks in prepared for chunk in chunks]
    scoring_client = client_rcs or client
    effective_encoder = encoder if encoder is not None else default_encoder()

    sampled = sample_top_chunks_per_paper(
        all_chunks,
        query,
        top_n=effective_functional_policy.top_chunks_per_paper,
        encoder=effective_encoder,
        query_map=paper_queries,
    )
    sampled_flat = [chunk for chunks in sampled.values() for chunk in chunks]
    functional_assessments = score_chunks_functionally(
        query,
        sampled_flat,
        scoring_client,
        batch_size=effective_functional_policy.batch_size,
        paper_titles=paper_titles,
    )
    functional_scores = aggregate_functional(
        functional_assessments,
        sampled,
        max_weight=effective_functional_policy.max_weight,
    )

    if paper_queries is None:
        # 缺省:全部歸一組、配額 = 組內篇數(僅閾值把關),相容舊測試
        effective_paper_queries = {paper_id: "" for paper_id in functional_scores}
        effective_follow_ups: set[str] = set()
        quota = max(1, len(effective_paper_queries))
        assessments = select_quota_threshold(
            functional_scores,
            effective_paper_queries,
            effective_follow_ups,
            n_first_round=quota,
            n_follow_up=quota,
            threshold=effective_functional_policy.threshold,
        )
    else:
        assessments = select_quota_threshold(
            functional_scores,
            paper_queries,
            follow_up_queries or set(),
            n_first_round=effective_functional_policy.n_first_round,
            n_follow_up=effective_functional_policy.n_follow_up,
            threshold=effective_functional_policy.threshold,
        )

    usable_ids = {
        assessment.paper_id
        for assessment in assessments
        if assessment.recommendation == "include"
    }
    coverage_packs = build_coverage_packs(all_chunks, coverage_policy)
    pacing = (
        _notes_pacing_seconds() if notes_pacing_seconds is None else notes_pacing_seconds
    )
    paper_summaries: list[PaperSummary] = []
    for position, (document, chunks) in enumerate(
        (item for item in prepared if item[0].paper_id in usable_ids)
    ):
        if position > 0 and pacing > 0:
            time.sleep(pacing)
        paper_summaries.append(
            summarize_paper_notes(
                document.paper_id,
                chunks,
                client,
                coverage_policy,
                paper_title=(paper_titles or {}).get(document.paper_id),
            )
        )
    result = synthesize_report(
        None, coverage_packs, paper_summaries, client_report or client, paper_assessments=assessments, query=query
    )
    source_paths = {document.paper_id: document.source_path for document, _ in prepared}
    resolved_sources = [
        source.model_copy(update={"source_path": source_paths[source.paper_id]})
        for source in result.paper_sources
    ]
    report_text = result.report
    if result.generated_by == "llm":
        # C2c A5: the LLM report prose is a pure claim-cited narrative; the
        # deterministic baseline already embeds its own 材料來源清單 block, so the
        # programmatic section is appended only on the LLM path.
        materials = render_materials_section(
            resolved_sources, result.paper_summaries, result.claim_chunks
        )
        report_text = report_text.rstrip() + "\n\n" + materials
    return result.model_copy(
        update={
            "paper_sources": resolved_sources,
            "report": report_text,
        }
    )


def _default_paper_id(pdf_path: str) -> str:
    return pdf_path.rsplit("/", maxsplit=1)[-1].rsplit("\\", maxsplit=1)[-1]


def _flush_langfuse() -> None:
    """Best-effort flush Langfuse telemetry; observability must never block the pipeline."""
    if get_client is None:
        return
    try:
        get_client().flush()
    except Exception:
        pass  # observability must not fail the pipeline


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract, retrieve, and optionally synthesize PDF evidence.")
    parser.add_argument("inputs", nargs="+", help="Path(s) to research-paper PDFs or folders of PDFs")
    parser.add_argument("query", help="Research question used to retrieve evidence")
    parser.add_argument("--paper-id", help="Stable local identifier; only allowed with a single PDF input")
    parser.add_argument("--top-k", type=int, default=3, help="Number of chunks retrieved corpus-wide across all papers and sent to the LLM")
    parser.add_argument("--dry-run", action="store_true", help="Stop after local chunk retrieval; no key or API call")
    parser.add_argument("--model", default="gemini-3.6-flash", help="Gemini model for the LLM stage")
    arguments = parser.parse_args()
    chunk_policy = ChunkPolicy()
    coverage_policy = CoveragePackPolicy()

    try:
        pdf_paths = expand_pdf_inputs(arguments.inputs)
        if arguments.paper_id and len(pdf_paths) != 1:
            parser.error("--paper-id is only allowed when the inputs expand to exactly one PDF.")
        documents = [
            extract_pdf_text(path, arguments.paper_id or _default_paper_id(path))
            for path in pdf_paths
        ]
        if arguments.dry_run:
            prepared = _prepare_documents(documents, chunk_policy)
            retrieved = retrieve_evidence_embedding(
                [chunk for _, document_chunks in prepared for chunk in document_chunks],
                arguments.query,
                EvidenceRetrievalPolicy(top_k=arguments.top_k),
            )
            print(json.dumps(retrieved.model_dump(mode="json"), ensure_ascii=True, indent=2))
            _flush_langfuse()
            return
        result = run_synthesis_pipeline(
            documents,
            arguments.query,
            GeminiJsonClient(model=arguments.model),
            chunk_policy=chunk_policy,
            coverage_policy=coverage_policy,
        )
        print(json.dumps(result.model_dump(mode="json"), ensure_ascii=True, indent=2))
        _flush_langfuse()
    except (PdfExtractionError, LlmEvidenceError, SynthesisError, ValueError) as error:
        print(f"Pipeline failed: {error}", file=sys.stderr)
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
