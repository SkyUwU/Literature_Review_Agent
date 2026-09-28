"""Machine-side section canonicalization and sampled-chunk distribution stats (SD).

Bilingual single source for the SD printable report and the gated B sampling
design. This is a read-only machine surface: the LLM side keeps the raw
``chunk.section`` path unchanged (see functional._section_wrapped_text)."""

from __future__ import annotations

from collections import Counter

from literature_review.coverage import _classify_heading
from literature_review.models import EvidenceChunk
from literature_review.synthesis import top_level_section

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


def section_distribution(
    sampled: dict[str, list[EvidenceChunk]],
    paper_titles: dict[str, str] | None = None,
) -> dict[str, dict[str, int]]:
    """Count canonicalized top-level sections per paper over sampled chunks.

    Returns ``{paper_id: {canonical_name: count}}``. ``paper_titles`` maps paper
    ids to titles for the top-level title-drop heuristic; missing entries pass
    ``None`` to ``top_level_section``.
    """
    paper_titles = paper_titles or {}
    distribution: dict[str, dict[str, int]] = {}
    for paper_id, chunks in sampled.items():
        counts: Counter[str] = Counter()
        for chunk in chunks:
            top = top_level_section(chunk.section, paper_titles.get(chunk.paper_id))
            counts[canonical_section_name(top)] += 1
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
    lines.append(f"Section distribution across {total_chunks} sampled chunks (top-level, canonicalized)")
    for name, n in sorted(aggregate.items()):
        lines.append(f"  {name:<18} {n:>4}  (papers: {papers_with_section[name]})")
    return "\n".join(lines)


def print_section_distribution(
    sampled: dict[str, list[EvidenceChunk]],
    paper_titles: dict[str, str] | None = None,
) -> None:
    """Print the SD report for *sampled* chunks to stdout."""
    print(render_section_distribution(section_distribution(sampled, paper_titles), paper_titles))