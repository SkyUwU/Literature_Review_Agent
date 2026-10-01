"""Shared query wording policy for LLM planning and gap follow-ups."""

SHORT_QUERY_GUIDANCE = (
    "Keyword Length (Strict): every search query must be a short keyword phrase "
    "of 2-4 whitespace-separated words, targeting one information need. "
    "Put detailed requirements in purpose, target_gap, or reason, not in the query. "
    'For example (good): "literature review citation evaluation"; '
    'For example (bad): "LLM scientific literature review citation entailment '
    'completeness provenance hallucinated references benchmark expert evaluation".'
    " Task anchoring: every query must retain the core task, research object, "
    "or domain from the original research question, or an unambiguous equivalent. "
    "Do not replace the requested task with a different task sharing similar words. Generic terms "
    "such as review, screening, routing, or clustering alone are insufficient. "
    "Disambiguate acronyms using a full name or task qualifier within the word budget; "
    "put the remaining explanation in purpose/target_gap/reason. "
    "Choose anchors from the actual input; the examples are illustrations, not required keywords. "
    'For example, for mixture-of-experts token assignment: "MoE token routing", '
    'not the ambiguous "routing optimization". '
    'For example, for scholarly survey generation: "survey generation citation accuracy", '
    'not the different task "manuscript peer review".'
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
