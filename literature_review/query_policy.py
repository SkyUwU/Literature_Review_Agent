"""Shared query wording policy for LLM planning and gap follow-ups."""

SHORT_QUERY_GUIDANCE = (
    "Keyword Length (Strict): every search query must be a short keyword phrase "
    "of 2-4 whitespace-separated words, targeting one information need. "
    "Put detailed requirements in purpose, target_gap, or reason, not in the query. "
    'For example (good): "literature review citation evaluation"; '
    'For example (bad): "LLM scientific literature review citation entailment '
    'completeness provenance hallucinated references benchmark expert evaluation".'
)


def validate_short_query(query: str) -> str:
    """Reject verbose queries without truncating their intended information need."""
    normalized = " ".join(query.split())
    if not 2 <= len(normalized.split()) <= 4:
        raise ValueError(
            "Search query must contain 2-4 whitespace-separated words; "
            "rewrite it around one information need and put details in purpose/target_gap/reason."
        )
    return normalized
