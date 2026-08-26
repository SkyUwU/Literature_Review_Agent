"""Deterministic section-aware selection of bounded evidence chunks per paper."""

import re

from literature_review.models import CoveragePackPolicy, EvidenceChunk

_SECTION_HEADERS: tuple[tuple[str, int, re.Pattern[str]], ...] = (
    ("abstract", 1, re.compile(r"(?i)^[0-9]*\.?\s*abstract\b")),
    ("limitations", 2, re.compile(r"(?i)^[0-9]*\.?\s*limitations?\b")),
    ("future work", 3, re.compile(r"(?i)^[0-9]*\.?\s*future\s+work\b")),
    ("results", 4, re.compile(r"(?i)^[0-9]*\.?\s*results?\b")),
    ("experiments", 5, re.compile(r"(?i)^[0-9]*\.?\s*experiments?\b")),
    ("evaluation", 6, re.compile(r"(?i)^[0-9]*\.?\s*evaluation\b")),
    ("method", 7, re.compile(r"(?i)^[0-9]*\.?\s*method(?:ology|s)?\b")),
    ("approach", 8, re.compile(r"(?i)^[0-9]*\.?\s*approach\b")),
    ("introduction", 9, re.compile(r"(?i)^[0-9]*\.?\s*introduction\b")),
    ("conclusion", 10, re.compile(r"(?i)^[0-9]*\.?\s*conclusion\b")),
    ("related work", 11, re.compile(r"(?i)^[0-9]*\.?\s*related\s+work\b")),
    ("background", 11, re.compile(r"(?i)^[0-9]*\.?\s*background\b")),
    ("discussion", 11, re.compile(r"(?i)^[0-9]*\.?\s*discussion\b")),
)
_APPENDIX_REGEX = re.compile(r"(?i)^[0-9]*\.?\s*(?:appendix|supplementary\s+material)\b")
_LIMITATION_CUE_REGEX = re.compile(
    r"(?i)(?:limitation|future\s+work|fails?\s+to|does\s+not|"
    r"remains\s+(?:un)?(?:explor|solv|address)|lack\s+of)"
)
_OTHER_PRIORITY = 12
_APPENDIX_PRIORITY = 13


def classify_section(text: str) -> tuple[int, str]:
    """Return the section priority and name for chunk text, or the default.

    Checks each line of *text*. Lines >= 80 characters are skipped. The match
    is anchored at the start of the (stripped) line and supports an optional
    numbered prefix (e.g. ``1. Introduction``).
    """
    for line in text.splitlines():
        stripped = line.strip()
        if len(stripped) >= 80:
            continue
        for name, priority, pattern in _SECTION_HEADERS:
            if pattern.match(stripped):
                return priority, name
    return _OTHER_PRIORITY, "other"


def build_coverage_packs(
    all_chunks: list[EvidenceChunk],
    policy: CoveragePackPolicy,
) -> dict[str, list[EvidenceChunk]]:
    """Select a bounded, section-aware evidence pack per paper deterministically.

    Chunks carrying a recognized section header win by fixed section order;
    chunks after an appendix boundary rank last. Papers whose text has no
    section headers fall back to an even stride that keeps the first chunk.
    """
    chunks_by_paper: dict[str, list[EvidenceChunk]] = {}
    for chunk in all_chunks:
        chunks_by_paper.setdefault(chunk.paper_id, []).append(chunk)
    return {paper_id: _select_pack(chunks, policy) for paper_id, chunks in chunks_by_paper.items()}


def _has_appendix_header(text: str) -> bool:
    """Check whether *text* contains an appendix/supplementary header line < 80 chars."""
    for line in text.splitlines():
        stripped = line.strip()
        if len(stripped) < 80 and _APPENDIX_REGEX.match(stripped):
            return True
    return False


def _select_pack(chunks: list[EvidenceChunk], policy: CoveragePackPolicy) -> list[EvidenceChunk]:
    ordered = sorted(chunks, key=lambda item: (item.page_start, item.page_end, item.chunk_id))
    if not any(classify_section(item.text)[0] != _OTHER_PRIORITY for item in ordered):
        stride = max(1, len(ordered) // policy.max_chunks_per_paper)
        indices = list(range(0, len(ordered), stride))[: policy.max_chunks_per_paper]
        chosen = [(ordered[index].page_start, index) for index in indices]
    else:
        appendix_index = next(
            (i for i, item in enumerate(ordered) if _has_appendix_header(item.text)), None
        )
        priorities = [
            (
                classify_section(item.text)[0]
                if appendix_index is None or i <= appendix_index
                else _APPENDIX_PRIORITY,
                item.page_start,
                i,
            )
            for i, item in enumerate(ordered)
        ]
        priorities.sort()
        chosen = [
            (page_start, index)
            for _, page_start, index in priorities[: policy.max_chunks_per_paper]
        ]
    return [ordered[index] for _, index in sorted(chosen)]


def detect_limitation_chunks(paper_chunks: list[EvidenceChunk]) -> list[EvidenceChunk]:
    """Return every chunk whose text contains an explicit limitation cue."""
    return [chunk for chunk in paper_chunks if _LIMITATION_CUE_REGEX.search(chunk.text)]
