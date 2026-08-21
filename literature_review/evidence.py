"""Split extracted paper text into page-traceable evidence chunks."""

from literature_review.models import ChunkPolicy, EvidenceChunk, FullTextDocument


def chunk_document(document: FullTextDocument, policy: ChunkPolicy) -> list[EvidenceChunk]:
    """Create overlapping chunks while retaining the inclusive page range."""
    if policy.overlap_words >= policy.max_words:
        raise ValueError("overlap_words must be smaller than max_words")

    page_words = [
        (page.page_number, word)
        for page in document.pages
        for word in page.text.split()
    ]
    chunks: list[EvidenceChunk] = []
    step = policy.max_words - policy.overlap_words
    for start in range(0, len(page_words), step):
        chunk_words = page_words[start : start + policy.max_words]
        if len(chunk_words) < 4:
            break
        chunks.append(
            EvidenceChunk(
                chunk_id=(
                    f"{document.paper_id}-p{chunk_words[0][0]}-{chunk_words[-1][0]}"
                    f"-c{len(chunks) + 1}"
                ),
                paper_id=document.paper_id,
                page_start=chunk_words[0][0],
                page_end=chunk_words[-1][0],
                text=" ".join(word for _, word in chunk_words),
            )
        )
        if start + policy.max_words >= len(page_words):
            break
    return chunks
