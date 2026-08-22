"""A small opt-in path from one local PDF to ranked LLM evidence summaries."""

import argparse
import json
import sys

from literature_review.evidence import chunk_document
from literature_review.evidence_ranking import retrieve_evidence
from literature_review.extraction import PdfExtractionError, extract_pdf_text
from literature_review.llm_evidence import GeminiJsonClient, JsonGenerationClient, LlmEvidenceError, summarize_and_rerank
from literature_review.models import ChunkPolicy, EvidenceRetrievalPolicy, EvidenceRerankResponse, EvidenceRetrievalResponse, FullTextDocument


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


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract, retrieve, and optionally summarize PDF evidence.")
    parser.add_argument("pdf_path", help="Path to one local research-paper PDF")
    parser.add_argument("query", help="Research question used to retrieve evidence")
    parser.add_argument("--paper-id", help="Stable local identifier; defaults to the PDF filename")
    parser.add_argument("--top-k", type=int, default=3, help="Number of chunks sent to the LLM")
    parser.add_argument("--dry-run", action="store_true", help="Stop after local chunk retrieval; no key or API call")
    parser.add_argument("--model", default="gemini-2.5-flash", help="Gemini model for the LLM stage")
    arguments = parser.parse_args()
    paper_id = arguments.paper_id or arguments.pdf_path.rsplit("/", maxsplit=1)[-1].rsplit("\\", maxsplit=1)[-1]
    chunk_policy = ChunkPolicy()
    retrieval_policy = EvidenceRetrievalPolicy(top_k=arguments.top_k)

    try:
        retrieved = retrieve_from_pdf(
            arguments.pdf_path,
            paper_id,
            arguments.query,
            chunk_policy=chunk_policy,
            retrieval_policy=retrieval_policy,
        )
        if arguments.dry_run:
            print(json.dumps(retrieved.model_dump(mode="json"), ensure_ascii=True, indent=2))
            return
        result = summarize_and_rerank(retrieved, GeminiJsonClient(model=arguments.model))
        print(json.dumps(result.model_dump(mode="json"), ensure_ascii=True, indent=2))
    except (PdfExtractionError, LlmEvidenceError, ValueError) as error:
        print(f"Pipeline failed: {error}", file=sys.stderr)
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
