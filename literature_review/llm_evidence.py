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


def build_evidence_prompt(response: EvidenceRetrievalResponse, chunk_ids: list[str] | None = None) -> str:
    """Ask the model for grounded summaries without allowing it to alter provenance.

    ``chunk_ids`` optionally narrows the prompt to a subset of the retrieved
    chunks (used by the batched RCS loop); ``None`` keeps the full corpus-wide
    prompt (the original single-call behavior).
    """
    selected = response.ranked_chunks
    if chunk_ids is not None:
        wanted = set(chunk_ids)
        selected = [item for item in response.ranked_chunks if item.chunk.chunk_id in wanted]
    chunks = [
        {
            "chunk_id": item.chunk.chunk_id,
            "paper_id": item.chunk.paper_id,
            "page_start": item.chunk.page_start,
            "page_end": item.chunk.page_end,
            "text": item.chunk.text,
        }
        for item in selected
    ]
    return (
        "Assess each supplied evidence chunk only against the research query. "
        "Do not use outside knowledge and do not invent claims. Return exactly one JSON object, "
        "without Markdown code fences or any surrounding explanation. "
        "The object must contain an 'assessments' array. Each item must include chunk_id, summary, "
        "relevance_score (1-10), evidence_quality_score (1-10), and rationale. "
        "Write the rationale first as the reason, then assign scores consistent with it. "
        "Scoring guide: relevance_score 10 = the chunk directly answers the query's core question; "
        "5 = related but only touches the topic; 1 = only a passing mention. "
        "evidence_quality_score 10 = concrete methods, numbers, or conclusions directly backing the claim; "
        "5 = specific but ordinary detail; 1 = vague with little information; "
        "do NOT penalize theory or framework papers just because they contain no numbers. "
        f"Research query: {response.query}\nEvidence chunks: {json.dumps(chunks, ensure_ascii=False)}"
    )


def build_json_repair_prompt(raw_output: str, expected_chunk_ids: list[str] | None = None) -> str:
    """Request one bounded repair attempt when structured output was malformed.

    When ``expected_chunk_ids`` is supplied (a batch's full chunk set), the
    repair prompt additionally demands exactly one assessment for every listed
    chunk_id, so a model that silently dropped chunks gets one chance to
    complete the batch (Todo 3: single-repair budget per batch).
    """
    coverage_clause = ""
    if expected_chunk_ids is not None:
        coverage_clause = (
            " The repaired response must assess exactly these chunk_ids, each once: "
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


@observe(name="rcs")
def summarize_and_rerank(
    response: EvidenceRetrievalResponse,
    client: JsonGenerationClient,
    *,
    batch_size: int = RCS_BATCH_SIZE,
) -> EvidenceRerankResponse:
    """Validate LLM assessments per bounded batch and enrich them only with trusted provenance.

    The corpus-wide chunk set is split into batches of at most ``batch_size``
    chunks; each batch is an independent call with at most one repair attempt.
    The per-batch repair prompt also names the expected chunk_ids, so a model
    that silently drops chunks gets one chance to complete the batch. The merged
    result keeps the existing every-chunk-exactly-once invariant.
    """
    ranked_chunks = response.ranked_chunks
    by_chunk_id = {item.chunk.chunk_id: item.chunk for item in ranked_chunks}
    all_assessments: list[LlmEvidenceAssessment] = []
    schema = LlmEvidenceAssessmentBatch.model_json_schema()

    for start in range(0, len(ranked_chunks), batch_size):
        batch_ids = [item.chunk.chunk_id for item in ranked_chunks[start : start + batch_size]]
        raw_output = client.generate_json(build_evidence_prompt(response, chunk_ids=batch_ids), schema)
        try:
            generated = validate_evidence_assessments(raw_output)
        except LlmEvidenceError:
            # Local models (Ollama) may return valid JSON with the wrong shape
            # (e.g. a missing/renamed 'assessments' key); give each batch one
            # bounded repair, exactly like the syntactic-failure repair below.
            generated = validate_evidence_assessments(
                client.generate_json(build_json_repair_prompt(raw_output), schema)
            )
        returned_ids = [item.chunk_id for item in generated.assessments]
        complete = len(returned_ids) == len(set(returned_ids)) and set(returned_ids) == set(batch_ids)
        if not complete:
            generated = validate_evidence_assessments(
                client.generate_json(build_json_repair_prompt(raw_output, expected_chunk_ids=batch_ids), schema)
            )
            returned_ids = [item.chunk_id for item in generated.assessments]
            if len(returned_ids) != len(set(returned_ids)) or set(returned_ids) != set(batch_ids):
                raise LlmEvidenceError("LLM output must assess every retrieved chunk exactly once.")
        all_assessments.extend(generated.assessments)

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
