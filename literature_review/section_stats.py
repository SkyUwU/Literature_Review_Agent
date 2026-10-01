"""Machine-side section canonicalization and sampled-chunk distribution stats (SD).

Bilingual single source for the SD printable report and the gated B sampling
design. This is a read-only machine surface: the LLM side keeps the raw
``chunk.section`` path unchanged (see functional._section_wrapped_text)."""

from __future__ import annotations

import re
from collections import Counter

from literature_review.coverage import _classify_heading
from literature_review.models import EvidenceChunk

_SECTION_FAMILY_ALIASES: dict[str, str] = {
    "approach": "method",
    "our approach": "method",
    "proposed method": "method",
    "proposed approach": "method",
    "proposed framework": "method",
    "experimental setup": "experiments",
    "experimental results": "results",
    "empirical evaluation": "evaluation",
}

_POST_DROP_NAMES = frozenset({"references", "appendix", "acknowledgments"})
_CANONICAL_CATEGORIES = frozenset(
    {"context", "method", "evaluation_setup", "results", "limitations_future"}
)
_HEADING_PREFIX_REGEX = re.compile(
    r"(?i)^(?:\d+(?:\.\d+)*\.?\s*|[A-Z](?:\.\d+)*(?:[.)])?\s+)"
)
_APPENDIX_HEADING_REGEX = re.compile(r"(?i)^(?:appendix|supplementary\s+material)\b")
_CATEGORY_ALIASES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "context",
        re.compile(
            r"(?i)^(?:abstract|introduction|background|related\s+work|literature\s+review|"
            r"motivation|problem\s+statement)(?:\b|$)"
        ),
    ),
    (
        "method",
        re.compile(
            r"(?i)^(?:methods?|methodologies|approach|our\s+approach|proposed\s+(?:method|approach)|"
            r"framework|architecture|system|model|design)(?:\b|$)"
        ),
    ),
    (
        "evaluation_setup",
        re.compile(
            r"(?i)^(?:experimental\s+setup|evaluation\s+(?:setup|design|protocol)|datasets?|data|"
            r"benchmarks?|baselines?|metrics?|implementation\s+details)(?:\b|$)"
        ),
    ),
    (
        "limitations_future",
        re.compile(
            r"(?i)^(?:limitations?|threats?\s+to\s+validity|future\s+(?:work|directions?))(?:\b|$)"
        ),
    ),
    (
        "results",
        re.compile(
            r"(?i)^(?:(?:additional|supplementary)\s+)?(?:results?|findings?|experiments?|evaluation|ablations?|error\s+analysis|"
            r"discussion|conclusions?)(?:\b|$)"
        ),
    ),
)
_SETUP_CUE_REGEX = re.compile(
    r"(?i)\b(?:datasets?|baselines?|metrics?|protocol|hyperparameters?|implementation details|training setup)\b"
)


def canonical_section_name(top_heading: str | None) -> str:
    """Canonicalize a top-level section heading into a machine-side family name.

    Layer 1 uses the shared ``coverage`` heading table; layer 2 folds a small
    closed alias set into families (approach -> method, …). Headings outside
    both tables keep their cleaned raw text (保名成桶); ``None``/empty and the
    post-drop ``references``/``appendix`` heads collapse to ``other``.
    """
    if not top_heading or not top_heading.strip():
        return "other"
    heading = top_heading.strip().lower()
    name = _classify_heading(heading)[1]
    if name in _POST_DROP_NAMES:
        return "other"
    if name != "other":
        return _SECTION_FAMILY_ALIASES.get(name, name)
    return _fold_raw_alias(heading)


def _fold_raw_alias(heading: str) -> str:
    """Layer-2 alias folding on the cleaned raw heading, else keep the raw name."""
    return _SECTION_FAMILY_ALIASES.get(heading, heading)


def classify_chunk_category(
    chunk: EvidenceChunk,
    *,
    paper_title: str | None = None,
) -> str:
    """Map a chunk to one of five evidence categories, or ``other``.

    The most specific recognized heading in its Markdown path wins. For broad
    Experiments/Evaluation headings, explicit setup cues in the chunk text map
    the chunk to ``evaluation_setup``; otherwise those headings mean ``results``.
    ``paper_title`` is accepted to keep this helper easy to use with extracted
    documents; matching relies on anchored heading aliases, so title text is not
    treated as a category unless it is itself an exact heading-like prefix.
    """
    headings = [
        _clean_category_heading(part)
        for part in (chunk.section or "").split(" > ")
        if part.strip()
    ]
    if headings and paper_title:
        first_key = re.sub(r"[^a-z0-9]", "", headings[0].lower())
        title_key = re.sub(r"[^a-z0-9]", "", paper_title.lower())
        if first_key and title_key and (
            first_key == title_key
            or title_key.startswith(first_key)
            or first_key.startswith(title_key)
        ):
            headings = headings[1:]
    if not headings:
        _, legacy_name = _classify_heading_from_text(chunk.text)
        headings = [legacy_name]

    for heading in reversed(headings):
        if _APPENDIX_HEADING_REGEX.match(heading):
            continue
        for category, pattern in _CATEGORY_ALIASES:
            if pattern.match(heading):
                if category == "results" and heading.lower().startswith(("experiments", "evaluation")):
                    if _SETUP_CUE_REGEX.search(chunk.text):
                        return "evaluation_setup"
                return category
        # Keep compatibility with the shared Layer-1 heading recognizer for
        # common variants (for example singular/plural section labels).
        shared_name = _classify_heading(heading)[1]
        mapped = {
            "abstract": "context",
            "introduction": "context",
            "background": "context",
            "related work": "context",
            "method": "method",
            "approach": "method",
            "experiments": "results",
            "results": "results",
            "evaluation": "results",
            "discussion": "results",
            "conclusion": "results",
            "limitations": "limitations_future",
            "future work": "limitations_future",
        }.get(shared_name)
        if mapped:
            if mapped == "results" and shared_name in {"experiments", "evaluation"}:
                if _SETUP_CUE_REGEX.search(chunk.text):
                    return "evaluation_setup"
            return mapped
    return "other"


def is_abstract_chunk(chunk: EvidenceChunk) -> bool:
    """Whether a chunk belongs to an explicitly titled Abstract section."""
    return any(
        re.match(r"(?i)^abstract(?:\b|$)", _clean_category_heading(part))
        for part in (chunk.section or "").split(" > ")
    )


def is_appendix_chunk(chunk: EvidenceChunk) -> bool:
    """Whether any heading in a chunk's path marks an appendix boundary."""
    return any(
        _APPENDIX_HEADING_REGEX.match(_clean_category_heading(part))
        for part in (chunk.section or "").split(" > ")
    )


def _clean_category_heading(heading: str) -> str:
    cleaned = re.sub(r"\*\*", "", heading).strip()
    return _HEADING_PREFIX_REGEX.sub("", cleaned).strip()


def _classify_heading_from_text(text: str) -> tuple[int, str]:
    """Use the existing line-anchored classifier for chunks without a path."""
    from literature_review.coverage import classify_section

    return classify_section(text)


def section_distribution(
    sampled: dict[str, list[EvidenceChunk]],
    paper_titles: dict[str, str] | None = None,
) -> dict[str, dict[str, int]]:
    """Count evidence categories per paper over sampled chunks.

    Returns ``{paper_id: {canonical_name: count}}``. ``paper_titles`` maps paper
    ids to titles used to ignore a leading title-like heading; missing entries
    leave the section path untouched.
    """
    paper_titles = paper_titles or {}
    distribution: dict[str, dict[str, int]] = {}
    for paper_id, chunks in sampled.items():
        counts: Counter[str] = Counter()
        for chunk in chunks:
            counts[classify_chunk_category(chunk, paper_title=paper_titles.get(chunk.paper_id))] += 1
        distribution[paper_id] = dict(counts)
    return distribution


def _render_paper_line(
    paper_id: str,
    counts: dict[str, int],
    paper_titles: dict[str, str] | None,
) -> str:
    title = (paper_titles or {}).get(paper_id)
    label = f"{paper_id} ({title})" if title else paper_id
    items = " ".join(f"{name}:{n}" for name, n in sorted(counts.items()))
    return f"{label}: {items if items else 'other:0'}"


def render_section_distribution(
    distribution: dict[str, dict[str, int]],
    paper_titles: dict[str, str] | None = None,
) -> str:
    """Render per-paper one-liners plus an aggregate table (compact ASCII)."""
    lines = []
    total_chunks = 0
    aggregate: Counter[str] = Counter()
    papers_with_section: Counter[str] = Counter()
    for paper_id, counts in distribution.items():
        for name, n in counts.items():
            aggregate[name] += n
            papers_with_section[name] += 1
        total_chunks += sum(counts.values())
        lines.append(_render_paper_line(paper_id, counts, paper_titles))
    lines.append("")
    lines.append(f"Section distribution across {total_chunks} sampled chunks (evidence categories)")
    for name, n in sorted(aggregate.items()):
        lines.append(f"  {name:<18} {n:>4}  (papers: {papers_with_section[name]})")
    return "\n".join(lines)


def print_section_distribution(
    sampled: dict[str, list[EvidenceChunk]],
    paper_titles: dict[str, str] | None = None,
) -> None:
    """Print the SD report for *sampled* chunks to stdout."""
    print(render_section_distribution(section_distribution(sampled, paper_titles), paper_titles))
