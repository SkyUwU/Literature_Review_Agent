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
    LlmEvidenceAssessmentBatch,
)

from langfuse import observe


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

    def __init__(self, model: str = "gemini-2.5-flash", api_key: str | None = None) -> None:
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


def build_evidence_prompt(response: EvidenceRetrievalResponse) -> str:
    """Ask the model for grounded summaries without allowing it to alter provenance."""
    chunks = [
        {
            "chunk_id": item.chunk.chunk_id,
            "paper_id": item.chunk.paper_id,
            "page_start": item.chunk.page_start,
            "page_end": item.chunk.page_end,
            "text": item.chunk.text,
        }
        for item in response.ranked_chunks
    ]
    return (
        "Assess each supplied evidence chunk only against the research query. "
        "Do not use outside knowledge and do not invent claims. Return exactly one JSON object, "
        "without Markdown code fences or any surrounding explanation. "
        "The object must contain an 'assessments' array. Each item must include chunk_id, summary, relevance_score "
        "(1-5), evidence_quality_score (1-5), recommendation "
        "(include, consider, exclude, or insufficient_evidence), and rationale. "
        f"Research query: {response.query}\nEvidence chunks: {json.dumps(chunks, ensure_ascii=False)}"
    )


def build_json_repair_prompt(raw_output: str) -> str:
    """Request one bounded repair attempt when structured output was malformed."""
    return (
        "The previous response was malformed JSON. Return a repaired version as exactly one JSON object, "
        "without Markdown or explanation. Preserve the intended assessments and follow the response schema. "
        f"Previous response:\n{raw_output}"
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
) -> EvidenceRerankResponse:
    """Validate LLM assessments and enrich them only with trusted chunk provenance."""
    raw_output = client.generate_json(build_evidence_prompt(response), LlmEvidenceAssessmentBatch.model_json_schema())
    try:
        generated = validate_evidence_assessments(raw_output)
    except LlmOutputSyntaxError:
        generated = validate_evidence_assessments(client.generate_json(
            build_json_repair_prompt(raw_output), LlmEvidenceAssessmentBatch.model_json_schema()
        ))

    by_chunk_id = {item.chunk.chunk_id: item.chunk for item in response.ranked_chunks}
    returned_ids = [item.chunk_id for item in generated.assessments]
    if len(returned_ids) != len(set(returned_ids)) or set(returned_ids) != set(by_chunk_id):
        raise LlmEvidenceError("LLM output must assess every retrieved chunk exactly once.")

    summaries = [
        EvidenceSummary(
            **assessment.model_dump(),
            paper_id=by_chunk_id[assessment.chunk_id].paper_id,
            page_start=by_chunk_id[assessment.chunk_id].page_start,
            page_end=by_chunk_id[assessment.chunk_id].page_end,
        )
        for assessment in generated.assessments
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
