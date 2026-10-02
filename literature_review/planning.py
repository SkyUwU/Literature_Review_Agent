"""Create an inspectable search plan before sending queries to paper providers."""

import logging
import re

from literature_review.llm_evidence import (
    JsonGenerationClient,
    generate_validated,
    strip_code_fence,
)
from literature_review.models import PlannedQuery, SearchPlan, TaskInterpretation
from literature_review.query_policy import (
    SHORT_QUERY_GUIDANCE, TASK_INTERPRETATION_GUIDANCE, validate_short_query,
)


class _LlmSearchPlan(SearchPlan):
    """New generations require interpretation; saved legacy plans remain readable."""

    task_interpretation: TaskInterpretation

logger = logging.getLogger(__name__)

_QUERY_TOKEN_RE = re.compile(r"[^a-z0-9]+")
# Safety net for lexical near-duplicate reuse: 0.5 fires under evident keyword
# overlap; 0.4 would fire too often and 0.55 is almost unreachable for the
# Jaccard value range of sensible keyword phrases.
QUERY_OVERLAP_THRESHOLD = 0.5


class PlanningError(RuntimeError):
    """Raised when an LLM-produced search plan cannot be parsed or validated."""


def query_overlap(a: str, b: str) -> float:
    """Return the Jaccard overlap of the lowercase alphanumeric tokens of two queries.

    The overlap is defined as the size of the token intersection divided by the
    size of the token union. It is a literal, deterministic measure of keyword
    reuse: ``literature review agent`` vs ``systematic review automation`` scores
    low (only ``review`` is shared) even though the two are semantically related,
    which is exactly the synonym-rewriting behavior the planner should reward.
    """
    tokens_a = {token for token in _QUERY_TOKEN_RE.split(a.lower()) if token}
    tokens_b = {token for token in _QUERY_TOKEN_RE.split(b.lower()) if token}
    if not tokens_a or not tokens_b:
        return 0.0
    return len(tokens_a & tokens_b) / len(tokens_a | tokens_b)


def max_query_overlap(queries: list[str]) -> float:
    """Return the highest pairwise query overlap in a list of query strings."""
    highest = 0.0
    for index, first in enumerate(queries):
        for second in queries[index + 1 :]:
            highest = max(highest, query_overlap(first, second))
    return highest


def create_rule_based_plan(query: str, max_queries: int = 5) -> SearchPlan:
    """Create a small fallback plan from a bare query without an LLM or external API.

    The input is a query string (the LLM planner ``create_llm_plan`` shares this
    query-only contract), so ``SearchPlan.idea`` holds the query string to preserve
    the input provenance.
    """
    if max_queries < 3:
        raise ValueError("max_queries must be >= 3 to satisfy the SearchPlan lower bound.")
    candidates = [
        (query, "Find papers on the core topic supplied by the researcher."),
        (f"{query} literature review", "Find surveys and prior literature reviews for the topic."),
        (f"{query} survey", "Find survey and review articles covering the topic."),
        (f"{query} comparison", "Find comparative and evaluative studies of the topic."),
    ]
    queries: list[PlannedQuery] = []
    seen_queries: set[str] = set()
    for candidate_query, purpose in candidates:
        normalized = " ".join(candidate_query.split()).lower()
        if len(normalized) < 3 or normalized in seen_queries:
            continue
        seen_queries.add(normalized)
        queries.append(PlannedQuery(query=candidate_query, purpose=purpose))
        if len(queries) == max_queries:
            break

    return SearchPlan(
        idea=query,
        queries=queries,
        perspectives=["core topic", "prior surveys", "survey articles", "comparative studies"],
        generated_by="rule_based",
        rationale=(
            "The fallback plan expands the query string into a small set of inspectable "
            "search intents when no LLM planner is available."
        ),
    )


def build_llm_plan_prompt(query: str) -> str:
    """Build the prompt that asks the model for one structured, query-only search plan.

    Sub-queries must be 3-4 short keyword phrases (2-4 keywords each): the schema
    enforces the count band and the ``Keyword Length (Strict)`` rule constrains
    phrasing, because scholarly search APIs match short keyword phrases more
    precisely than verbose sentences.
    """
    return (
        "You are a scholarly search planner. Given one researcher query, produce a "
        "structured search plan as exactly one JSON object matching the provided schema.\n\n"
        f"Researcher query: {query}\n\n"
        f"{TASK_INTERPRETATION_GUIDANCE}\n"
        "Query design rules:\n"
        f"- {SHORT_QUERY_GUIDANCE}\n"
        "- First decompose the idea into 3 complementary research dimensions: "
        "(1) the core task name, (2) key methodology, and (3) evaluation benchmarks "
        "and mainstream comparisons. Generate a short keyword pool for each dimension "
        "(2-4 word keywords for scholarly search APIs, not long sentences).\n"
        "- Keyword Length (Strict): every sub-query must be a short keyword phrase of 2-4 "
        "keywords - scholarly search APIs match short keyword phrases more precisely than "
        'verbose sentences. For example (good): "multi-agent retrieval planning"; '
        'For example (bad): "how can we build a literature review agent that uses retrieval '
        'augmentation to write a survey for users".\n'
        "- Compose this plan's sub-queries from those keyword pools: each sub-query "
        "must combine terms taken from different dimensions so the resulting queries "
        "cover distinct angles of the idea rather than recombining the same words.\n"
        "- Each sub-query must target a distinct facet of the researcher query; avoid "
        "near-duplicate queries that only rephrase the same angle.\n"
        "- Keep task anchors even when they repeat. Vary method and evaluation terms; "
        "prefer unambiguous synonyms, hyponyms, and alternative phrasings so "
        "a literal search engine retrieves different papers for each facet.\n"
        "- Stay on-topic: every sub-query must remain a reasonable sub-facet of the "
        "original researcher query, not a tangential topic.\n\n"
        "Return a JSON object with these fields only:\n"
        '- "task_interpretation": task, research_object, expected_output, and scope_boundaries;\n'
        '- "queries": exactly 3 to 4 items, each with a "query" string (at least 3 '
        'characters) and a "purpose" string (at least 10 characters explaining the '
        "information need that query covers);\n"
        '- "perspectives": at least 1 short label naming the search angles;\n'
        '- "rationale": at least 20 characters explaining the overall strategy;\n'
        '- "generated_by": "llm".\n\n'
        'Do not include an "idea" field. Return exactly one JSON object, without Markdown '
        "code fences or surrounding explanation."
    )


def _format_plan_error(error: BaseException) -> str:
    """Compactly describe a schema failure without echoing the model's text back."""
    details = "invalid JSON or schema mismatch"
    if hasattr(error, "errors"):
        issues = error.errors(include_url=False)
        if issues:
            location = ".".join(str(part) for part in issues[0]["loc"])
            details = f"{location}: {issues[0]['msg']}"
    return details


def _parse_plan(raw_output: str) -> SearchPlan:
    """Validate one candidate plan object, raising PlanningError on failure."""
    try:
        plan = _LlmSearchPlan.model_validate_json(strip_code_fence(raw_output))
        for planned in plan.queries:
            planned.query = validate_short_query(planned.query)
        return plan
    except ValueError as error:
        raise PlanningError(f"LLM plan failed validation ({_format_plan_error(error)}).") from error


def _build_plan_repair_prompt(raw_output: str, error: BaseException, query: str | None = None) -> str:
    """Ask the model to repair one malformed or schema-invalid plan output."""
    return (
        "The previous response was not a valid search plan. Return a repaired version as "
        "exactly one JSON object matching the schema, without Markdown or explanation. "
        f"Validation error: {_format_plan_error(error)}\n"
        f"{SHORT_QUERY_GUIDANCE}\n"
        f"{TASK_INTERPRETATION_GUIDANCE}\n"
        f"Original research question: {query or '(not provided)'}\n"
        f"Previous response:\n{raw_output}"
    )


def _build_overlap_repair_prompt(queries: list[str], query: str) -> str:
    """Ask the model to rewrite a plan whose queries overlap too much lexically."""
    query_list = "\n".join(f"- {item}" for item in queries)
    return (
        "The previous search plan had queries that were too similar in wording. "
        "Rewrite each sub-query so it targets a distinct facet of the researcher "
        "query and uses different keywords, synonyms, or alternative phrasings, "
        "while staying on topic.\n"
        f"{SHORT_QUERY_GUIDANCE}\n"
        f"{TASK_INTERPRETATION_GUIDANCE}\n"
        f"Researcher query: {query}\n"
        f"Previous queries:\n{query_list}\n"
        "Return exactly one JSON object matching the schema, without Markdown or "
        "explanation."
    )


class _BudgetedClient:
    """Wrap a planning client so the whole plan stage costs at most ``budget`` calls.

    ``generate_validated`` retries once on parse failure, so when a third provider
    call would be attempted the budget guard raises ``PlanningError``; the caller
    treats that as "keep the plan we already have" rather than a hard failure.
    """

    def __init__(self, client: JsonGenerationClient, budget: int) -> None:
        self.client = client
        self.budget = budget
        self.calls = 0

    def generate_json(self, prompt: str, schema: dict | None = None) -> str:
        if self.calls >= self.budget:
            raise PlanningError("Planning LLM call budget exhausted after 2 calls.")
        self.calls += 1
        return self.client.generate_json(prompt, schema)


def create_llm_plan(
    query: str, client: JsonGenerationClient, *, enable_overlap_repair: bool = True
) -> SearchPlan:
    """Use an LLM to turn a bare query into a validated ``SearchPlan`` (``generated_by="llm"``).

    The search-plan schema is sent as the JSON-schema second argument so the provider can
    constrain its structured output, and the returned text is still validated with
    ``model_validate_json``. Exactly one repair is allowed for the whole stage — schema and
    overlap repairs share the budget — so planning always costs at most two provider calls.
    ``idea`` is set to the query string to preserve the input provenance, matching
    ``create_rule_based_plan``.

    After schema validation the plan's queries are checked for lexical overlap. When any
    pair exceeds ``QUERY_OVERLAP_THRESHOLD`` and ``enable_overlap_repair`` is set, one
    diversification rewrite is requested (unless the repair budget is already spent); the
    rewritten plan is accepted when it no longer exceeds the threshold, or when it strictly
    improves on the original overlap; otherwise the first (original) plan is returned and a
    warning is logged. Overlap is a quality signal, not a failure, so planning never
    degrades to the rule-based fallback for this reason.
    """
    prompt = build_llm_plan_prompt(query)
    schema = _LlmSearchPlan.model_json_schema()
    # Provider schema follows the requested generation order; the trusted input
    # is assigned locally rather than asking the model to reproduce an idea field.
    properties = schema["properties"]
    schema["properties"] = {
        name: properties[name]
        for name in ("task_interpretation", "queries", "perspectives", "rationale", "generated_by")
    }
    schema.get("$defs", {}).pop("ResearchIdea", None)
    budgeted = _BudgetedClient(client, 2)

    def _normalized_plan(plan: SearchPlan) -> SearchPlan:
        return SearchPlan.model_validate({**plan.model_dump(), "generated_by": "llm", "idea": query})

    first = _normalized_plan(
        generate_validated(
            budgeted,
            _LlmSearchPlan,
            prompt,
            schema,
            parse=_parse_plan,
            repair_prompt=lambda raw, error: _build_plan_repair_prompt(raw, error, query),
        )
    )
    if (
        max_query_overlap([item.query for item in first.queries]) <= QUERY_OVERLAP_THRESHOLD
        or budgeted.calls >= 2
        or not enable_overlap_repair
    ):
        return first

    try:
        repaired = _normalized_plan(
            generate_validated(
                budgeted,
                _LlmSearchPlan,
                _build_overlap_repair_prompt([item.query for item in first.queries], query),
                schema,
                parse=_parse_plan,
                repair_prompt=lambda raw, error: _build_plan_repair_prompt(raw, error, query),
            )
        )
    except PlanningError as error:
        logger.warning("Overlap repair failed (%s); keeping the original plan.", error)
        return first
    first_overlap = max_query_overlap([item.query for item in first.queries])
    repaired_overlap = max_query_overlap([item.query for item in repaired.queries])
    if repaired_overlap <= QUERY_OVERLAP_THRESHOLD:
        return repaired
    if repaired_overlap < first_overlap:
        return repaired
    logger.warning(
        "Plan queries still overlap after repair (max %.2f); keeping the original plan.",
        repaired_overlap,
    )
    return first
