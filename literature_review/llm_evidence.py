"""LLM-based contextual summaries for a bounded set of retrieved evidence chunks."""

import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Protocol, TypeVar

from pydantic import BaseModel

from literature_review.models import (
    EvidenceRerankResponse,
    EvidenceRetrievalResponse,
    EvidenceSummary,
    LlmEvidenceAssessment,
    LlmEvidenceAssessmentBatch,
)

from langfuse import observe

RCS_BATCH_SIZE = 4


class LlmEvidenceError(RuntimeError):
    """Raised for missing configuration or invalid model output."""


class LlmOutputSyntaxError(LlmEvidenceError):
    """Raised when a model response is not syntactically valid JSON."""


class JsonGenerationClient(Protocol):
    """Minimal provider interface; tests use a fake and Gemini is one implementation."""

    def generate_json(self, prompt: str, schema: dict | None = None) -> str:
        """Return exactly one JSON object encoded as text."""


class GeminiJsonClient:
    """Google Gemini implementation; the key comes from ``api_key`` or the environment."""

    def __init__(self, model: str = "gemini-3.6-flash", api_key: str | None = None) -> None:
        load_local_env()
        key = api_key or os.getenv("GEMINI_API_KEY")
        if not key:
            raise LlmEvidenceError("GEMINI_API_KEY is not set in the environment.")
        try:
            from google import genai
        except ImportError as error:
            raise LlmEvidenceError("Install dependencies with 'uv sync' before using Gemini.") from error
        self._client = genai.Client(api_key=key)
        self._model = model

    @observe(name="llm_call")
    def generate_json(self, prompt: str, schema: dict | None = None) -> str:
        interaction = self._client.interactions.create(
            model=self._model,
            input=prompt,
            response_format={
                "type": "text",
                "mime_type": "application/json",
                "schema": schema if schema is not None else LlmEvidenceAssessmentBatch.model_json_schema(),
            },
        )
        if not interaction.output_text:
            raise LlmEvidenceError("Gemini returned no text output.")
        return interaction.output_text


def load_local_env(path: str | Path = ".env") -> None:
    """Load simple KEY=VALUE entries only when that key is not already set."""
    env_path = Path(path)
    if not env_path.is_file():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", maxsplit=1)
        key = key.strip()
        if key and key not in os.environ:
            os.environ[key] = value.strip().strip('"').strip("'")


def build_evidence_prompt(
    response: EvidenceRetrievalResponse,
    chunk_ids: list[str] | None = None,
    paper_meta: dict[str, tuple[int | None, str | None]] | None = None,
) -> str:
    """Ask the model for grounded summaries without letting it alter provenance.

    Each chunk is presented as ``## Chunk N`` with a metadata line (citation
    count and venue from ``paper_meta``, ``n/a`` when unknown) followed by the
    raw text only: no chunk_id, paper_id, or page numbers are sent, so the
    model cannot copy or corrupt trusted identifiers (M5b RCS-input slimming).
    ``chunk_ids`` optionally narrows the prompt to a subset of the retrieved
    chunks (used by the batched RCS loop); ``None`` keeps the full corpus-wide
    prompt (the original single-call behavior).
    """
    selected = response.ranked_chunks
    if chunk_ids is not None:
        wanted = set(chunk_ids)
        selected = [item for item in response.ranked_chunks if item.chunk.chunk_id in wanted]
    blocks = []
    for index, item in enumerate(selected, start=1):
        citation, venue = (paper_meta or {}).get(item.chunk.paper_id, (None, None))
        citation_text = str(citation) if citation is not None else "n/a"
        venue_text = venue if venue else "n/a"
        blocks.append(
            f"## Chunk {index}\n"
            f"Citations: {citation_text} | Venue: {venue_text}\n"
            f"{item.chunk.text}"
        )
    return (
        "Assess each supplied evidence chunk only against the research query. "
        "Do not use outside knowledge and do not invent claims. Return exactly one JSON object, "
        "without Markdown code fences or any surrounding explanation. "
        "The object must contain an 'assessments' array. Each item must include chunk_id "
        "(the chunk number), summary, rationale_relevance, rationale_quality, "
        "relevance_score (1-10), and evidence_quality_score (1-10). "
        "Write the rationale first, then assign scores consistent with it. "
        "First state, in one sentence, the specific sub-question the query is asking. "
        "Then judge whether the chunk's main content — not just overlapping keywords — "
        "actually addresses that sub-question. "
        "A chunk with rich data, tables, or strong methodology is NOT automatically relevant. "
        "Score relevance solely on whether the chunk's core content addresses the query's specific question. "
        "Relevance and evidence quality are independent; a high-quality chunk must not inflate relevance. "
        "If a chunk mixes topics (e.g., a brief transitional sentence followed by unrelated content), "
        "base the relevance score on what the majority of the chunk substantively discusses, "
        "not on isolated overlapping phrases. "
        "Score the two dimensions independently: high relevance does not imply high evidence quality, and vice versa. "
        "Citation count and venue are secondary supporting signals only; "
        "never lower evidence quality solely for low citations when the text itself is concrete. "
        "Use them as context, not as a primary criterion. "
        "Scoring guide. relevance_score: 9-10 the chunk directly answers the query's core question "
        "(extremely rare; 9 = directly strong but not the very core); "
        "7-8 directly relevant and substantively discusses a core facet; "
        "5-6 related background that does not directly answer the core; "
        "3-4 indirectly related (adjacent topic or overlapping terms but different core); "
        "1-2 only a passing mention or marginal overlap. "
        "evidence_quality_score: 9-10 concrete methods, numbers, or conclusions directly backing the claim; "
        "7-8 has data or a clear method; "
        "5-6 clear argument but generic detail; "
        "3-4 vague with few details; "
        "1-2 almost no information (fragmented text). "
        "Do NOT penalize theory or framework papers just because they contain no numbers. "
        f"Research query: {response.query}\nEvidence chunks:\n" + "\n\n".join(blocks)
    )


def build_json_repair_prompt(raw_output: str, expected_chunk_ids: list[str] | None = None) -> str:
    """Request one bounded repair attempt when structured output was malformed.

    When ``expected_chunk_ids`` is supplied (a batch's full chunk-index set),
    the repair prompt additionally demands exactly one assessment for every
    listed index, so a model that silently dropped or renamed chunks gets one
    chance to complete the batch (M5b: single-repair budget per batch).
    """
    coverage_clause = ""
    if expected_chunk_ids is not None:
        coverage_clause = (
            " The repaired response must assess exactly these chunk indexes, each once: "
            f"{json.dumps(expected_chunk_ids)}."
        )
    return (
        "The previous response was malformed JSON. Return a repaired version as exactly one JSON object, "
        "without Markdown or explanation. Preserve the intended assessments and follow the response schema."
        f"{coverage_clause}\nPrevious response:\n{raw_output}"
    )


def validate_evidence_assessments(raw_output: str) -> LlmEvidenceAssessmentBatch:
    """Accept structured JSON while reporting schema failures without exposing model text."""
    normalized = raw_output.strip()
    if normalized.startswith("```") and normalized.endswith("```"):
        lines = normalized.splitlines()
        normalized = "\n".join(lines[1:-1]).strip()
    try:
        return LlmEvidenceAssessmentBatch.model_validate_json(normalized)
    except ValueError as error:
        details = "invalid JSON or schema mismatch"
        if hasattr(error, "errors"):
            issues = error.errors(include_url=False)
            if issues:
                location = ".".join(str(part) for part in issues[0]["loc"])
                details = f"{location}: {issues[0]['msg']}"
                if issues[0].get("type") == "json_invalid":
                    raise LlmOutputSyntaxError(f"LLM output is malformed JSON ({details}).") from error
        raise LlmEvidenceError(f"LLM output failed evidence-assessment validation ({details}).") from error


_MODEL_T = TypeVar("_MODEL_T", bound=BaseModel)


def strip_code_fence(raw_output: str) -> str:
    """Remove Markdown code fences and any trailing text after the last JSON brace."""
    normalized = raw_output.strip()
    if normalized.startswith("```") and normalized.endswith("```"):
        lines = normalized.splitlines()
        normalized = "\n".join(lines[1:-1]).strip()
    last_brace = normalized.rfind("}")
    if last_brace != -1:
        normalized = normalized[: last_brace + 1]
    return normalized


def generate_validated(
    client: JsonGenerationClient,
    model: type[_MODEL_T],
    prompt: str,
    schema: dict | None,
    *,
    parse: Callable[[str], _MODEL_T],
    repair_prompt: Callable[[str, BaseException], str],
) -> _MODEL_T:
    """Call the client once, validate, and repair exactly once on a parse failure.

    ``parse`` raises the caller's domain error type on validation failure and
    ``repair_prompt`` builds one repair request from the raw output and that error.
    The retry budget is exactly one repair: if the repaired output also fails to
    parse, the second ``parse`` call propagates the error (no further retries).
    """
    raw_output = client.generate_json(prompt, schema)
    try:
        return parse(raw_output)
    except Exception as exc:
        return parse(client.generate_json(repair_prompt(raw_output, exc), schema))


def _resolve_indexes(
    batch: LlmEvidenceAssessmentBatch,
    index_map: dict[str, str],
) -> list[LlmEvidenceAssessment]:
    resolved = []
    for assessment in batch.assessments:
        real_id = index_map.get(assessment.chunk_id)
        if real_id is None:
            raise LlmEvidenceError(
                f"LLM output referenced an unknown chunk index {assessment.chunk_id!r}."
            )
        resolved.append(assessment.model_copy(update={"chunk_id": real_id}))
    return resolved


@observe(name="rcs", capture_input=False, capture_output=False)
def summarize_and_rerank(
    response: EvidenceRetrievalResponse,
    client: JsonGenerationClient,
    *,
    batch_size: int = RCS_BATCH_SIZE,
    paper_meta: dict[str, tuple[int | None, str | None]] | None = None,
) -> EvidenceRerankResponse:
    """Validate LLM assessments per bounded batch and enrich them only with trusted provenance.

    The corpus-wide chunk set is split into batches of at most ``batch_size``
    chunks; each batch is an independent call with at most one repair attempt.
    Chunks are presented to the model by index only (``## Chunk N``), so the
    model can never copy or corrupt a real chunk id; the per-batch repair
    prompt names the expected chunk indexes, giving a model that silently
    dropped or renamed chunks one chance to complete the batch. The merged
    result keeps the existing every-chunk-exactly-once invariant.
    """
    ranked_chunks = response.ranked_chunks
    by_chunk_id = {item.chunk.chunk_id: item.chunk for item in ranked_chunks}
    all_assessments: list[LlmEvidenceAssessment] = []
    schema = LlmEvidenceAssessmentBatch.model_json_schema()

    for start in range(0, len(ranked_chunks), batch_size):
        batch_ids = [item.chunk.chunk_id for item in ranked_chunks[start : start + batch_size]]
        index_map = {str(index): chunk_id for index, chunk_id in enumerate(batch_ids, start=1)}
        raw_output = client.generate_json(
            build_evidence_prompt(response, chunk_ids=batch_ids, paper_meta=paper_meta), schema
        )
        try:
            generated = validate_evidence_assessments(raw_output)
            resolved = _resolve_indexes(generated, index_map)
        except LlmEvidenceError:
            # Local models (Ollama) may return valid JSON with the wrong shape
            # (e.g. a missing/renamed 'assessments' key) or an unknown chunk
            # index; give each batch one bounded repair that names the exact
            # expected chunk indexes so an index drift (M5b B=1 probe: qwen
            # echoed '2'/'A1'/'C1' for a single-chunk batch) can be repaired,
            # exactly like the completeness repair below.
            generated = validate_evidence_assessments(
                client.generate_json(
                    build_json_repair_prompt(raw_output, expected_chunk_ids=list(index_map)),
                    schema,
                )
            )
            resolved = _resolve_indexes(generated, index_map)
        returned_ids = [item.chunk_id for item in resolved]
        complete = len(returned_ids) == len(set(returned_ids)) and set(returned_ids) == set(batch_ids)
        if not complete:
            generated = validate_evidence_assessments(
                client.generate_json(
                    build_json_repair_prompt(raw_output, expected_chunk_ids=list(index_map)),
                    schema,
                )
            )
            resolved = _resolve_indexes(generated, index_map)
            returned_ids = [item.chunk_id for item in resolved]
            if len(returned_ids) != len(set(returned_ids)) or set(returned_ids) != set(batch_ids):
                raise LlmEvidenceError("LLM output must assess every retrieved chunk exactly once.")
        all_assessments.extend(resolved)

    try:
        from langfuse import langfuse_context

        langfuse_context.update_current_observation(
            input={
                "query": response.query,
                "chunk_ids": [item.chunk.chunk_id for item in ranked_chunks],
            }
        )
    except Exception:
        pass  # Observability must never break the RCS stage.

    summaries = [
        EvidenceSummary(
            **assessment.model_dump(),
            paper_id=by_chunk_id[assessment.chunk_id].paper_id,
            page_start=by_chunk_id[assessment.chunk_id].page_start,
            page_end=by_chunk_id[assessment.chunk_id].page_end,
        )
        for assessment in all_assessments
    ]
    summaries.sort(key=lambda item: (-item.relevance_score, -item.evidence_quality_score, item.chunk_id))
    return EvidenceRerankResponse(
        retrieval_response=response,
        summaries=summaries,
        limitations=[
            "The LLM assessment is limited to the supplied chunks and is not a full-paper review.",
            "Summaries should be checked against their cited source pages before final synthesis.",
        ],
    )
