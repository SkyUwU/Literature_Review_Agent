"""Shared task interpretation and query wording guidance."""

import json

from literature_review.models import TaskInterpretation


TASK_DISAMBIGUATION_GUIDANCE = (
    "Task disambiguation: identify the task, research object, and expected output from the "
    "original input before judging relevance. Shared words or similar methods do not make "
    "two tasks identical. The original input is authoritative; an interpretation is only "
    "an aid and must not override it or invent requirements. "
    "The following examples are illustrations, not required keywords, fixed domains, "
    "or a blacklist; apply the same comparison to the actual input.\n"
    "Example 1 — Original idea: LLM-based automated literature review → Ambiguous query: "
    "LLM review evaluation → Clear query: literature review evaluation → Reason: synthesizing "
    "multiple papers differs from manuscript peer review for acceptance decisions.\n"
    "Example 2 — Original idea: mixture-of-experts token assignment → Ambiguous query: "
    "routing optimization → Clear query: MoE token routing → Reason: assigning tokens to "
    "experts differs from network packet routing.\n"
)

TASK_INTERPRETATION_GUIDANCE = (
    "In this same response, first output task_interpretation, then queries, perspectives, "
    "and rationale. task_interpretation is a concise interpretation of the user's input, "
    "not a detailed chain of thought: task describes what is to be done; research_object "
    "describes what is studied or processed; expected_output describes the intended result; "
    "scope_boundaries lists only relevant distinctions justified by the input. "
    "Use 'unspecified' for information the input does not establish and [] for absent "
    "boundaries. Do not invent requirements. Generate queries from this interpretation "
    "while preserving the original input as authoritative.\n"
)

TASK_TRANSFER_GUIDANCE = (
    "Distinguish findings about the paper's original task from transferable design "
    "implications for the requested task. A different task's empirical result is not "
    "validated performance on the requested task. When proposing a transfer, state the "
    "concrete mechanism, applicable stage, assumptions or limitations, and need for "
    "validation; do not merely rename the original task.\n"
)

NOTES_TASK_GUIDANCE = (
    "Preserve the paper's own task, research object, experimental conditions, and limits "
    "in each substantive claim. Do not rename its task to fit another research topic. "
    "Website controls, citation exports, author lists, and copyright boilerplate are not "
    "research findings; omit them rather than inventing substantive claims. Only sections "
    "with substantive research evidence require claims.\n"
)


def task_context(query: str | None, interpretation: TaskInterpretation | None = None) -> str:
    """Keep the original input alongside an optional, subordinate interpretation."""
    context = f"Original research idea (authoritative): {query or '(not provided)'}\n"
    if interpretation is not None:
        context += "Task interpretation (aid only): " + json.dumps(
            interpretation.model_dump(mode="json"), ensure_ascii=False
        ) + "\n"
    return context

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
    " Task anchors may repeat across queries; diversify methods and evaluation facets "
    "instead. Semantic clarity takes priority over vocabulary diversity.\n"
    + TASK_DISAMBIGUATION_GUIDANCE
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
