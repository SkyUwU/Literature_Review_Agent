"""Split extracted paper text into page-traceable evidence chunks."""

from literature_review.models import ChunkPolicy, EvidenceChunk, FullTextDocument


def chunk_document(document: FullTextDocument, policy: ChunkPolicy) -> list[EvidenceChunk]:
    """Create overlapping, page-bounded word chunks from extracted paper text."""
    if policy.overlap_words >= policy.max_words:
        raise ValueError("overlap_words must be smaller than max_words")

    chunks: list[EvidenceChunk] = []
    for page in document.pages:
        words = page.text.split()
        step = policy.max_words - policy.overlap_words
        for start in range(0, len(words), step):
            chunk_words = words[start : start + policy.max_words]
            if len(chunk_words) < 4:
                continue
            chunks.append(
                EvidenceChunk(
                    chunk_id=f"{document.paper_id}-p{page.page_number}-c{len(chunks) + 1}",
                    paper_id=document.paper_id,
                    page_start=page.page_number,
                    page_end=page.page_number,
                    text=" ".join(chunk_words),
                )
            )
            if start + policy.max_words >= len(words):
                break
    return chunks
