"""Opt-in paths from local PDFs to ranked LLM evidence and cited synthesis."""

import argparse
import glob
import json
import os
import sys

from literature_review.assessment import aggregate_evidence_assessments
from literature_review.evidence import chunk_document
from literature_review.evidence_ranking import retrieve_evidence
from literature_review.extraction import PdfExtractionError, extract_pdf_text
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
    SynthesisResponse,
)
from literature_review.synthesis import SynthesisError, build_coverage_packs, summarize_paper_notes, synthesize_report


def run_evidence_pipeline(
    document: FullTextDocument,
    query: str,
    client: JsonGenerationClient,
    *,
    chunk_policy: ChunkPolicy,
    retrieval_policy: EvidenceRetrievalPolicy,
) -> EvidenceRerankResponse:
    """Run chunking, first-stage retrieval, and LLM contextual re-ranking."""
    chunks = chunk_document(document, chunk_policy)
    if not chunks:
        raise ValueError("The extracted document did not produce usable evidence chunks.")
    retrieved = retrieve_evidence(chunks, query, retrieval_policy)
    return summarize_and_rerank(retrieved, client)


def retrieve_from_pdf(
    pdf_path: str,
    paper_id: str,
    query: str,
    *,
    chunk_policy: ChunkPolicy,
    retrieval_policy: EvidenceRetrievalPolicy,
) -> EvidenceRetrievalResponse:
    """Run only the local, no-key portion of the evidence workflow."""
    document = extract_pdf_text(pdf_path, paper_id)
    chunks = chunk_document(document, chunk_policy)
    if not chunks:
        raise ValueError("The extracted document did not produce usable evidence chunks.")
    return retrieve_evidence(chunks, query, retrieval_policy)


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
) -> list[tuple[FullTextDocument, list[EvidenceChunk]]]:
    """Chunk every document, rejecting duplicates and skipping chunkless ones."""
    seen_ids: set[str] = set()
    prepared: list[tuple[FullTextDocument, list[EvidenceChunk]]] = []
    for document in documents:
        if document.paper_id in seen_ids:
            raise ValueError(f"Duplicate paper_id {document.paper_id!r} in the supplied documents.")
        seen_ids.add(document.paper_id)
        chunks = chunk_document(document, chunk_policy)
        if chunks:
            prepared.append((document, chunks))
        else:
            print(f"Skipping {document.paper_id}: extracted text produced no evidence chunks.", file=sys.stderr)
    if not prepared:
        raise ValueError("None of the supplied documents produced usable evidence chunks.")
    return prepared


def _merge_rerank_responses(responses: list[EvidenceRerankResponse]) -> EvidenceRerankResponse:
    """Combine per-document rerank results into one corpus-level response."""
    first_retrieval = responses[0].retrieval_response
    return EvidenceRerankResponse(
        retrieval_response=EvidenceRetrievalResponse(
            query=first_retrieval.query,
            policy=first_retrieval.policy,
            ranked_chunks=[
                item for response in responses for item in response.retrieval_response.ranked_chunks
            ],
        ),
        summaries=[summary for response in responses for summary in response.summaries],
        limitations=list(
            dict.fromkeys(limitation for response in responses for limitation in response.limitations)
        ),
    )


def run_synthesis_pipeline(
    documents: list[FullTextDocument],
    query: str,
    client: JsonGenerationClient,
    *,
    chunk_policy: ChunkPolicy = ChunkPolicy(),
    retrieval_policy: EvidenceRetrievalPolicy = EvidenceRetrievalPolicy(),
    coverage_policy: CoveragePackPolicy = CoveragePackPolicy(),
    aggregation_policy: EvidenceAggregationPolicy = EvidenceAggregationPolicy(),
) -> SynthesisResponse:
    """Run retrieval, LLM re-ranking, aggregation, notes, and cited synthesis."""
    prepared = _prepare_documents(documents, chunk_policy)
    rerank_response = _merge_rerank_responses(
        [
            summarize_and_rerank(retrieve_evidence(chunks, query, retrieval_policy), client)
            for _, chunks in prepared
        ]
    )
    assessment_response = aggregate_evidence_assessments(rerank_response, aggregation_policy)
    usable_ids = {
        assessment.paper_id
        for assessment in assessment_response.assessments
        if assessment.recommendation in {"include", "consider"}
    }
    coverage_packs = build_coverage_packs(
        [chunk for _, chunks in prepared for chunk in chunks], coverage_policy
    )
    paper_summaries = [
        summarize_paper_notes(document.paper_id, chunks, client, coverage_policy)
        for document, chunks in prepared
        if document.paper_id in usable_ids
    ]
    result = synthesize_report(assessment_response, coverage_packs, paper_summaries, client)
    source_paths = {document.paper_id: document.source_path for document, _ in prepared}
    resolved_sources = [
        source.model_copy(update={"source_path": source_paths[source.paper_id]})
        for source in result.paper_sources
    ]
    return result.model_copy(update={"paper_sources": resolved_sources})


def _default_paper_id(pdf_path: str) -> str:
    return pdf_path.rsplit("/", maxsplit=1)[-1].rsplit("\\", maxsplit=1)[-1]


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract, retrieve, and optionally synthesize PDF evidence.")
    parser.add_argument("inputs", nargs="+", help="Path(s) to research-paper PDFs or folders of PDFs")
    parser.add_argument("query", help="Research question used to retrieve evidence")
    parser.add_argument("--paper-id", help="Stable local identifier; only allowed with a single PDF input")
    parser.add_argument("--top-k", type=int, default=3, help="Number of chunks sent to the LLM")
    parser.add_argument("--dry-run", action="store_true", help="Stop after local chunk retrieval; no key or API call")
    parser.add_argument("--model", default="gemini-2.5-flash", help="Gemini model for the LLM stage")
    arguments = parser.parse_args()
    chunk_policy = ChunkPolicy()
    retrieval_policy = EvidenceRetrievalPolicy(top_k=arguments.top_k)
    coverage_policy = CoveragePackPolicy()
    aggregation_policy = EvidenceAggregationPolicy()

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
            retrieved = retrieve_evidence(
                [chunk for _, document_chunks in prepared for chunk in document_chunks],
                arguments.query,
                retrieval_policy,
            )
            print(json.dumps(retrieved.model_dump(mode="json"), ensure_ascii=True, indent=2))
            return
        result = run_synthesis_pipeline(
            documents,
            arguments.query,
            GeminiJsonClient(model=arguments.model),
            chunk_policy=chunk_policy,
            retrieval_policy=retrieval_policy,
            coverage_policy=coverage_policy,
            aggregation_policy=aggregation_policy,
        )
        print(json.dumps(result.model_dump(mode="json"), ensure_ascii=True, indent=2))
    except (PdfExtractionError, LlmEvidenceError, SynthesisError, ValueError) as error:
        print(f"Pipeline failed: {error}", file=sys.stderr)
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
