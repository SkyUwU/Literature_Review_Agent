"""Deterministic section-aware selection of bounded evidence chunks per paper."""

import re

from literature_review.models import CoveragePackPolicy, EvidenceChunk

_ACKNOWLEDGMENTS_REGEX = re.compile(r"(?i)^[0-9]*\.?\s*acknowledg(?:ements?|ments?)\b")
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
    ("acknowledgments", 14, _ACKNOWLEDGMENTS_REGEX),
)
_APPENDIX_REGEX = re.compile(r"(?i)^[0-9]*\.?\s*(?:appendix|supplementary\s+material)\b")
_LIMITATION_CUE_REGEX = re.compile(
    r"(?i)(?:limitation|future\s+work|fails?\s+to|does\s+not|"
    r"remains\s+(?:un)?(?:explor|solv|address)|lack\s+of)"
)
_OTHER_PRIORITY = 12
_APPENDIX_PRIORITY = 13
_REFERENCES_REGEX = re.compile(r"(?i)^[0-9]*\.?\s*(?:references|bibliography)\b")
_REFERENCES_PRIORITY = 14  # lowest; lower than appendix(13)
_NOISE_CLASSES = frozenset({"references", "bibliography", "acknowledgments"})


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


def classify_chunk(chunk: EvidenceChunk) -> tuple[int, str]:
    """Classify a chunk by its section heading first, then by raw text.

    Section-aware chunks match the *last* heading level (bold markers stripped,
    optional numeric prefix tolerated) against the section table; appendix is
    recognised separately. Legacy chunks without a section keep the historical
    ``classify_section`` text search unchanged.
    """
    if chunk.section:
        return _classify_heading(_last_heading_name(chunk.section) or "")
    return classify_section(chunk.text)


def _classify_heading(heading: str) -> tuple[int, str]:
    """Match one heading string against the section table, then reference/appendix."""
    for name, priority, pattern in _SECTION_HEADERS:
        if pattern.match(heading):
            return priority, name
    if _REFERENCES_REGEX.match(heading):
        return _REFERENCES_PRIORITY, "references"
    if _APPENDIX_REGEX.match(heading):
        return _APPENDIX_PRIORITY, "appendix"
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


def _has_references_header(text: str) -> bool:
    """Check whether *text* contains a references/bibliography header line < 80 chars."""
    for line in text.splitlines():
        stripped = line.strip()
        if len(stripped) < 80 and _REFERENCES_REGEX.match(stripped):
            return True
    return False


def _select_pack(chunks: list[EvidenceChunk], policy: CoveragePackPolicy) -> list[EvidenceChunk]:
    ordered = sorted(
        chunks,
        key=lambda item: (item.page_start or 0, item.page_end or 0, item.chunk_id),
    )
    refs_index = next(
        (i for i, item in enumerate(ordered) if _is_noise_section(item.text, item.section)),
        None,
    )
    if not any(classify_chunk(item)[0] != _OTHER_PRIORITY for item in ordered):
        candidates = list(range(len(ordered)))
        if refs_index is not None:
            candidates = [i for i in candidates if i < refs_index] or [0]
        stride = max(1, len(candidates) // policy.max_chunks_per_paper)
        indices = candidates[::stride][: policy.max_chunks_per_paper]
        chosen = [((ordered[index].page_start or 0), index) for index in indices]
    else:
        appendix_index = next(
            (i for i, item in enumerate(ordered) if _has_appendix_header(item.text, item.section)),
            None,
        )
        priorities = [
            _region_priority(item, i, refs_index, appendix_index)
            for i, item in enumerate(ordered)
        ]
        priorities.sort()
        chosen = [
            (page_start, index)
            for _, page_start, index in priorities[: policy.max_chunks_per_paper]
        ]
    return [ordered[index] for _, index in sorted(chosen)]


def _last_heading_name(section: str | None) -> str | None:
    """Return the lowest heading level of a section path, bold markers stripped."""
    if not section:
        return None
    return section.split(" > ")[-1].replace("**", "").strip()


def _is_noise_section(text: str, section: str | None) -> bool:
    """True when a references/acknowledgments boundary is present.

    The section heading takes priority; text scanning is the legacy fallback
    for chunks without a section (keeps C1-C4 behaviour unchanged).
    """
    if section:
        return _classify_heading(_last_heading_name(section) or "")[1] in _NOISE_CLASSES
    return _has_references_header(text)


def _has_appendix_header(text: str, section: str | None) -> bool:
    """Check whether *text*/*section* contains an appendix boundary."""
    if section:
        last = _last_heading_name(section) or ""
        return bool(_APPENDIX_REGEX.match(last))
    for line in text.splitlines():
        stripped = line.strip()
        if len(stripped) < 80 and _APPENDIX_REGEX.match(stripped):
            return True
    return False


def _region_priority(
    item: EvidenceChunk,
    index: int,
    refs_index: int | None,
    appendix_index: int | None,
) -> tuple[int, int, int]:
    classified = classify_chunk(item)[0]
    if refs_index is None:
        # C1: no references header — unchanged original logic (appendix 13, body 1-12).
        priority = classified if appendix_index is None or index <= appendix_index else _APPENDIX_PRIORITY
    elif appendix_index is None:
        # C2: references present, no appendix.
        priority = classified if index < refs_index else _REFERENCES_PRIORITY
    elif refs_index < appendix_index:
        # C3: references before appendix.
        if index < refs_index:
            priority = classified
        elif index < appendix_index:
            priority = _REFERENCES_PRIORITY
        else:
            priority = _APPENDIX_PRIORITY
    else:
        # C4: appendix at or before references.
        if index < appendix_index:
            priority = classified
        elif index < refs_index:
            priority = _APPENDIX_PRIORITY
        else:
            priority = _REFERENCES_PRIORITY
    return (priority, item.page_start or 0, index)


def detect_limitation_chunks(paper_chunks: list[EvidenceChunk]) -> list[EvidenceChunk]:
    """Return every chunk whose text contains an explicit limitation cue."""
    return [chunk for chunk in paper_chunks if _LIMITATION_CUE_REGEX.search(chunk.text)]


def drop_noise_sections(
    chunks: list[EvidenceChunk],
    *,
    drop_appendix: bool = True,
) -> list[EvidenceChunk]:
    """Drop chunks whose classified section is a noise region.

    Only references/acknowledgments/bibliography are dropped (shared
    ``classify_chunk`` single source); the appendix is dropped only when
    *drop_appendix* is set. Everything else — including chunks without a
    section — is kept, so odd per-paper headings are never misfiled.
    """
    kept = []
    for chunk in chunks:
        name = classify_chunk(chunk)[1]
        if name in _NOISE_CLASSES:
            continue
        if drop_appendix and name == "appendix":
            continue
        kept.append(chunk)
    return kept
