"""Compare lexical vs embedding retrieval quality using an LLM judge.

This is a compare-only evaluation: it measures which retrieval method finds more
relevant top-k chunks, without changing any existing retrieval/pipeline code.
"""

import argparse
import json
import os
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from literature_review.embedding_retriever import default_encoder, retrieve_evidence_embedding
from literature_review.evidence import chunk_document
from literature_review.evidence_ranking import retrieve_evidence
from literature_review.extraction import PdfExtractionError, extract_pdf_text
from literature_review.llm_evidence import (
    GeminiJsonClient,
    JsonGenerationClient,
    LlmEvidenceError,
    LlmOutputSyntaxError,
    build_evidence_prompt,
    build_json_repair_prompt,
    validate_evidence_assessments,
)
from literature_review.models import (
    ChunkPolicy,
    EvidenceChunk,
    EvidenceRetrievalPolicy,
    EvidenceRetrievalResponse,
    RankedEvidenceChunk,
)

VERDICT_THRESHOLD = 0.5

Encoder = Callable[[Sequence[str]], list[list[float]]]


def _ids(response: EvidenceRetrievalResponse) -> list[str]:
    return [item.chunk.chunk_id for item in response.ranked_chunks]


def _judge_group(
    chunks: list[EvidenceChunk],
    query: str,
    client: JsonGenerationClient,
) -> dict[str, int]:
    """Score one non-empty group of chunks, returning {chunk_id: relevance_score}.

    Reuses the existing evidence-assessment prompt/validation flow so each chunk gets
    an LLM relevance score under the same contract as the pipeline.
    """
    synthetic = EvidenceRetrievalResponse(
        query=query,
        policy=EvidenceRetrievalPolicy(top_k=len(chunks)),
        ranked_chunks=[
            RankedEvidenceChunk(
                chunk=chunk,
                rank=index,
                score=0.0,
                matched_terms=[],
                rationale="Synthetic grouping for retrieval comparison.",
            )
            for index, chunk in enumerate(chunks, start=1)
        ],
    )
    raw = client.generate_json(build_evidence_prompt(synthetic), None)
    try:
        generated = validate_evidence_assessments(raw)
    except LlmOutputSyntaxError:
        generated = validate_evidence_assessments(
            client.generate_json(build_json_repair_prompt(raw), None)
        )
    index_map = {str(index): chunk.chunk_id for index, chunk in enumerate(chunks, start=1)}
    return {
        index_map.get(assessment.chunk_id, assessment.chunk_id): assessment.relevance_score
        for assessment in generated.assessments
    }


def compare_retrieval(
    chunks: list[EvidenceChunk],
    query: str,
    retrieval_policy: EvidenceRetrievalPolicy,
    client: JsonGenerationClient,
    encoder: Encoder | None = None,
) -> dict[str, object]:
    """Run lexical and embedding top-k, judge with separate LLM calls, return a report.

    Each method's unique chunks and the shared overlap are scored in SEPARATE LLM calls
    to avoid order bias; any empty group is skipped with no LLM call.
    """
    lexical = retrieve_evidence(chunks, query, retrieval_policy)
    embedding = retrieve_evidence_embedding(chunks, query, retrieval_policy, encoder=encoder)

    lexical_ids = _ids(lexical)
    embedding_ids = _ids(embedding)

    shared = sorted(set(lexical_ids) & set(embedding_ids))
    only_lexical = sorted(set(lexical_ids) - set(embedding_ids))
    only_embedding = sorted(set(embedding_ids) - set(lexical_ids))

    by_id = {chunk.chunk_id: chunk for chunk in chunks}
    scores: dict[str, int] = {}
    skipped_groups: list[str] = []

    def judge(ids: list[str], group_name: str) -> None:
        if not ids:
            skipped_groups.append(group_name)
            return
        group_chunks = [by_id[chunk_id] for chunk_id in ids if chunk_id in by_id]
        if not group_chunks:
            skipped_groups.append(group_name)
            return
        scores.update(_judge_group(group_chunks, query, client))

    judge(only_lexical, "only_lexical")
    judge(only_embedding, "only_embedding")
    judge(shared, "overlap")

    lexical_scored = [score for chunk_id, score in scores.items() if chunk_id in lexical_ids]
    embedding_scored = [score for chunk_id, score in scores.items() if chunk_id in embedding_ids]

    avg_lexical = sum(lexical_scored) / len(lexical_scored) if lexical_scored else None
    avg_embedding = sum(embedding_scored) / len(embedding_scored) if embedding_scored else None

    verdict = _compute_verdict(avg_lexical, avg_embedding)

    return {
        "query": query,
        "top_k": retrieval_policy.top_k,
        "overlap": shared,
        "only_lexical": only_lexical,
        "only_embedding": only_embedding,
        "avg_relevance_lexical": avg_lexical,
        "avg_relevance_embedding": avg_embedding,
        "verdict": verdict,
        "skipped_groups": skipped_groups,
        "chunk_scores": {k: scores[k] for k in sorted(scores)},
    }


def _compute_verdict(
    avg_lexical: float | None,
    avg_embedding: float | None,
) -> str:
    """Explicit verdict rule: diff >= 0.5 -> winner; < 0.5 -> comparable.

    A method with no scored chunks is treated as missing and flagged in the verdict.
    """
    if avg_lexical is None and avg_embedding is None:
        return "comparable (no scored chunks)"
    if avg_lexical is None:
        return "embedding better (lexical had no scored chunks)"
    if avg_embedding is None:
        return "lexical better (embedding had no scored chunks)"

    difference = avg_embedding - avg_lexical
    if difference >= VERDICT_THRESHOLD:
        return "embedding better"
    if difference <= -VERDICT_THRESHOLD:
        return "lexical better"
    return "comparable"


def expand_pdf_inputs(inputs: list[str]) -> list[str]:
    """Expand folders to sorted PDF paths (mirrors pipeline's helper)."""
    import glob

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


def _default_paper_id(pdf_path: str) -> str:
    return pdf_path.rsplit("/", maxsplit=1)[-1].rsplit("\\", maxsplit=1)[-1]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compare lexical vs embedding retrieval quality over local PDFs."
    )
    parser.add_argument("inputs", nargs="+", help="Path(s) to research-paper PDFs or folders of PDFs")
    parser.add_argument("query", help="Research question used to retrieve evidence")
    parser.add_argument("--top-k", type=int, default=8, help="Number of chunks retrieved per method")
    parser.add_argument(
        "--judge-model",
        default="gemini-3.6-flash",
        help="Gemini model used to judge chunk relevance (LLM judge).",
    )
    arguments = parser.parse_args()

    chunk_policy = ChunkPolicy()
    retrieval_policy = EvidenceRetrievalPolicy(top_k=arguments.top_k)

    try:
        pdf_paths = expand_pdf_inputs(arguments.inputs)
        all_chunks: list[EvidenceChunk] = []
        for path in pdf_paths:
            document = extract_pdf_text(path, _default_paper_id(path))
            all_chunks.extend(chunk_document(document, chunk_policy))
        if not all_chunks:
            raise ValueError("No usable evidence chunks were produced from the supplied PDFs.")

        client = GeminiJsonClient(model=arguments.judge_model)
        report = compare_retrieval(
            all_chunks,
            arguments.query,
            retrieval_policy,
            client,
            encoder=default_encoder(),
        )
        print(json.dumps(report, ensure_ascii=True, indent=2))
    except (PdfExtractionError, LlmEvidenceError, ValueError) as error:
        print(f"Retrieval eval failed: {error}", file=sys.stderr)
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
