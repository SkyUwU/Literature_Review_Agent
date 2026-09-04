"""Create an inspectable search plan before sending queries to paper providers."""

from literature_review.llm_evidence import (
    JsonGenerationClient,
    generate_validated,
    strip_code_fence,
)
from literature_review.models import PlannedQuery, SearchPlan


class PlanningError(RuntimeError):
    """Raised when an LLM-produced search plan cannot be parsed or validated."""


def create_rule_based_plan(query: str, max_queries: int = 5) -> SearchPlan:
    """Create a small fallback plan from a bare query without an LLM or external API.

    The input is a query string (the LLM planner ``create_llm_plan`` shares this
    query-only contract), so ``SearchPlan.idea`` holds the query string to preserve
    the input provenance.
    """
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


def build_llm_plan_prompt(query: str, max_queries: int = 5) -> str:
    """Build the prompt that asks the model for one structured, query-only search plan."""
    return (
        "You are a scholarly search planner. Given one researcher query, produce a "
        "structured search plan as exactly one JSON object matching the provided schema.\n\n"
        f"Researcher query: {query}\n\n"
        "Return a JSON object with these fields only:\n"
        f'- "queries": 1 to {max_queries} items, each with a "query" string (at least 3 '
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
        return SearchPlan.model_validate_json(strip_code_fence(raw_output))
    except ValueError as error:
        raise PlanningError(f"LLM plan failed validation ({_format_plan_error(error)}).") from error


def _build_plan_repair_prompt(raw_output: str, error: BaseException) -> str:
    """Ask the model to repair one malformed or schema-invalid plan output."""
    return (
        "The previous response was not a valid search plan. Return a repaired version as "
        "exactly one JSON object matching the schema, without Markdown or explanation. "
        f"Validation error: {_format_plan_error(error)}\n"
        f"Previous response:\n{raw_output}"
    )


def create_llm_plan(
    query: str, client: JsonGenerationClient, *, max_queries: int = 5
) -> SearchPlan:
    """Use an LLM to turn a bare query into a validated ``SearchPlan`` (``generated_by="llm"``).

    The search-plan schema is sent as the JSON-schema second argument so the provider can
    constrain its structured output, and the returned text is still validated with
    ``model_validate_json``. Exactly one schema-repair retry is allowed. ``idea`` is set
    to the query string to preserve the input provenance, matching ``create_rule_based_plan``.
    """
    prompt = build_llm_plan_prompt(query, max_queries)
    schema = SearchPlan.model_json_schema()
    plan = generate_validated(
        client,
        SearchPlan,
        prompt,
        schema,
        parse=_parse_plan,
        repair_prompt=_build_plan_repair_prompt,
    )
    return plan.model_copy(update={"generated_by": "llm", "idea": query})
