"""First-stage, no-API retrieval of relevant chunks from extracted full text."""

from literature_review.models import (
    EvidenceChunk,
    EvidenceRetrievalPolicy,
    EvidenceRetrievalResponse,
    RankedEvidenceChunk,
)
from literature_review.ranking import query_terms


def retrieve_evidence(
    chunks: list[EvidenceChunk],
    query: str,
    policy: EvidenceRetrievalPolicy,
) -> EvidenceRetrievalResponse:
    """Rank chunks with an inspectable lexical baseline before LLM re-ranking.

    A future embedding retriever will return this same response contract.
    """
    terms = query_terms(query)
    scored: list[tuple[EvidenceChunk, float, list[str]]] = []
    for chunk in chunks:
        text = chunk.text.lower()
        matched = sorted(term for term in terms if term in text)
        score = float(sum(min(text.count(term), 3) for term in terms))
        scored.append((chunk, score, matched))

    scored.sort(key=lambda item: (-item[1], item[0].paper_id, item[0].chunk_id))
    return EvidenceRetrievalResponse(
        query=query,
        policy=policy,
        ranked_chunks=[
            RankedEvidenceChunk(
                chunk=chunk,
                rank=index,
                score=score,
                matched_terms=matched,
                rationale=(
                    f"Matched {len(matched)} query terms; lexical retrieval baseline "
                    "before contextual LLM re-ranking."
                ),
            )
            for index, (chunk, score, matched) in enumerate(scored[: policy.top_k], start=1)
        ],
    )
