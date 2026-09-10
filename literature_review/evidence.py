"""Split extracted paper text into page-traceable evidence chunks."""

import re

from langchain_text_splitters import MarkdownHeaderTextSplitter

from literature_review.models import ChunkPolicy, EvidenceChunk, FullTextDocument

#: Heading levels the section splitter understands, in hierarchy order.
_HEADER_KEYS = ("Header 1", "Section", "Subsection")

#: MarkdownHeaderTextSplitter normalizes blank-line paragraph breaks into hard
#: line breaks (two trailing spaces + newline); restore them for grouping.
_HARD_BREAK_REGEX = re.compile(r"[ \t]{2,}\n")


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
                section=None,
                page_start=chunk_words[0][0],
                page_end=chunk_words[-1][0],
                text=" ".join(word for _, word in chunk_words),
            )
        )
        if start + policy.max_words >= len(page_words):
            break
    return chunks


def chapter_chunk_document(
    document: FullTextDocument, policy: ChunkPolicy
) -> list[EvidenceChunk]:
    """Split markdown text into section-aware, non-overlapping chunks.

    The full markdown document is first divided by ``#/##/###`` headings
    (``MarkdownHeaderTextSplitter``); each heading region keeps its heading path
    in ``section`` (e.g. ``"Title > 1 Introduction > 1.1 Background"``). Within a
    section, paragraphs (blank-line separated) are grouped until they approach
    ``policy.max_words``; markdown table blocks (consecutive ``|`` lines) are
    kept whole, and only an unusually long plain paragraph gets a sliding-window
    fallback (the only overlap path). Chunk ids are ``{paper_id}-c{n}`` with a
    global file-order counter. Page bounds stay None because the page boundary
    is lost after merging the per-page markdown (no page-recovery engineering).
    """
    if policy.overlap_words >= policy.max_words:
        raise ValueError("overlap_words must be smaller than max_words")

    markdown_text = "\n\n".join(page.text for page in document.pages)
    splitter = MarkdownHeaderTextSplitter(
        headers_to_split_on=[("#", "Header 1"), ("##", "Section"), ("###", "Subsection")]
    )
    chunks: list[EvidenceChunk] = []
    for section_doc in splitter.split_text(markdown_text):
        section = _heading_path(section_doc.metadata)
        for text in _split_section_paragraphs(section_doc.page_content, policy):
            chunks.append(
                EvidenceChunk(
                    chunk_id=f"{document.paper_id}-c{len(chunks) + 1}",
                    paper_id=document.paper_id,
                    section=section,
                    text=text,
                )
            )
    return chunks


def _heading_path(metadata: dict[str, str]) -> str | None:
    """Compose a heading path from splitter metadata, skipping missing levels."""
    parts = [
        metadata[key].replace("**", "").strip()
        for key in _HEADER_KEYS
        if metadata.get(key)
    ]
    return " > ".join(parts) if parts else None


def _split_section_paragraphs(text: str, policy: ChunkPolicy) -> list[str]:
    """Split one section into chunks, preferring paragraph boundaries."""
    max_words = policy.max_words
    groups: list[str] = []
    current: list[str] = []
    current_words = 0

    def flush() -> None:
        nonlocal current, current_words
        if current:
            groups.append("\n\n".join(current))
            current = []
            current_words = 0

    paragraphs = [
        paragraph.strip()
        for paragraph in _HARD_BREAK_REGEX.sub("\n\n", text).split("\n\n")
        if paragraph.strip()
    ]
    for paragraph in paragraphs:
        words = len(paragraph.split())
        if words < 4:
            if current:
                current.append(paragraph)
                current_words += words
            else:
                current = [paragraph]
                current_words = words
            continue
        if _is_table_block(paragraph):
            if current_words and current_words + words > max_words:
                flush()
            current.append(paragraph)
            current_words += words
            continue
        if current_words + words <= max_words:
            current.append(paragraph)
            current_words += words
            continue
        flush()
        if words > max_words:
            groups.extend(_slide_split(paragraph, policy))
        else:
            current = [paragraph]
            current_words = words
    flush()
    return groups


def _is_table_block(paragraph: str) -> bool:
    """True when every non-empty line starts with ``|`` (a markdown table)."""
    lines = [line.strip() for line in paragraph.splitlines() if line.strip()]
    return len(lines) >= 2 and all(line.startswith("|") for line in lines)


def _slide_split(paragraph: str, policy: ChunkPolicy) -> list[str]:
    """Sliding-window fallback for an unusually long plain paragraph."""
    words = paragraph.split()
    step = policy.max_words - policy.overlap_words
    parts: list[str] = []
    for start in range(0, len(words), step):
        part_words = words[start : start + policy.max_words]
        if len(part_words) < 4 and len(parts):
            break
        parts.append(" ".join(part_words))
        if start + policy.max_words >= len(words):
            break
    return parts
