"""Semantic (embedding) top-k retrieval of relevant chunks from extracted full text.

This mirrors the response contract of ``evidence_ranking.retrieve_evidence`` so the
embedding path is directly comparable to the lexical baseline (scope: compare-only).
"""

from collections.abc import Callable, Sequence

from literature_review.models import (
    EvidenceChunk,
    EvidenceRetrievalPolicy,
    EvidenceRetrievalResponse,
    RankedEvidenceChunk,
)

# Official BGE retrieval prefix; documents are encoded without it for a fair comparison.
QUERY_PREFIX = "Represent this sentence for searching relevant passages: "

# Default embedding model used by the CLI / real runs (tests inject a fake encoder).
DEFAULT_MODEL_NAME = "BAAI/bge-small-en-v1.5"

# An encoder encodes a batch of strings into a list of float vectors.
Encoder = Callable[[Sequence[str]], list[list[float]]]


def default_encoder() -> Encoder:
    """Build the real sentence-transformers encoder for ``DEFAULT_MODEL_NAME``.

    The first call downloads the model weights (~130MB) from Hugging Face, so this is
    only constructed for real runs; tests inject a fake encoder and never download.
    ``normalize_embeddings=True`` makes dot-product equal cosine similarity.
    """

    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer(DEFAULT_MODEL_NAME)

    def encode(texts: Sequence[str]) -> list[list[float]]:
        return [vector.tolist() for vector in model.encode(list(texts), normalize_embeddings=True)]

    return encode


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine similarity between two already-normalized vectors (dot product)."""
    return float(sum(x * y for x, y in zip(a, b, strict=False)))


def retrieve_evidence_embedding(
    chunks: list[EvidenceChunk],
    query: str,
    policy: EvidenceRetrievalPolicy,
    encoder: Encoder | None = None,
) -> EvidenceRetrievalResponse:
    """Rank chunks by semantic similarity of the query to each chunk, capped at top_k.

    The query string is prefixed with the official BGE retrieval prefix; document chunk
    text is NOT prefixed. When ``encoder`` is omitted the real model is built via
    ``default_encoder()``.
    """
    encode = encoder if encoder is not None else default_encoder()
    query_vector = encode([QUERY_PREFIX + query])[0]

    chunk_texts = [chunk.text for chunk in chunks]
    chunk_vectors = encode(chunk_texts) if chunk_texts else []

    scored: list[tuple[EvidenceChunk, float]] = []
    for chunk, vector in zip(chunks, chunk_vectors, strict=True):
        scored.append((chunk, _cosine(query_vector, vector)))

    scored.sort(key=lambda item: (-item[1], item[0].paper_id, item[0].chunk_id))
    return EvidenceRetrievalResponse(
        query=query,
        policy=policy,
        ranked_chunks=[
            RankedEvidenceChunk(
                chunk=chunk,
                rank=index,
                score=score,
                matched_terms=[],
                rationale=(
                    "Semantic retrieval baseline using bge-small-en-v1.5 embeddings; "
                    "query is prefixed with the official BGE retrieval prefix."
                ),
            )
            for index, (chunk, score) in enumerate(scored[: policy.top_k], start=1)
        ],
    )
