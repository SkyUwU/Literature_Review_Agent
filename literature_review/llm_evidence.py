"""LLM-based contextual summaries for a bounded set of retrieved evidence chunks."""

import json
import os
from pathlib import Path
from typing import Protocol

from literature_review.models import (
    EvidenceRerankResponse,
    EvidenceRetrievalResponse,
    EvidenceSummary,
    LlmEvidenceAssessmentBatch,
)


class LlmEvidenceError(RuntimeError):
    """Raised for missing configuration or invalid model output."""


class JsonGenerationClient(Protocol):
    """Minimal provider interface; tests use a fake and Gemini is one implementation."""

    def generate_json(self, prompt: str) -> str:
        """Return exactly one JSON object encoded as text."""


class GeminiJsonClient:
    """Google Gemini implementation that reads its key only from the environment."""

    def __init__(self, model: str = "gemini-2.5-flash") -> None:
        load_local_env()
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise LlmEvidenceError("GEMINI_API_KEY is not set in the environment.")
        try:
            from google import genai
        except ImportError as error:
            raise LlmEvidenceError("Install dependencies with 'uv sync' before using Gemini.") from error
        self._client = genai.Client(api_key=api_key)
        self._model = model

    def generate_json(self, prompt: str) -> str:
        interaction = self._client.interactions.create(
            model=self._model,
            input=prompt,
            response_format={
                "type": "text",
                "mime_type": "application/json",
                "schema": LlmEvidenceAssessmentBatch.model_json_schema(),
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
        "Do not use outside knowledge and do not invent claims. Return exactly one JSON object "
        "with an 'assessments' array. Each item must include chunk_id, summary, relevance_score "
        "(1-5), evidence_quality_score (1-5), recommendation "
        "(include, consider, exclude, or insufficient_evidence), and rationale. "
        f"Research query: {response.query}\nEvidence chunks: {json.dumps(chunks, ensure_ascii=False)}"
    )


def summarize_and_rerank(
    response: EvidenceRetrievalResponse,
    client: JsonGenerationClient,
) -> EvidenceRerankResponse:
    """Validate LLM assessments and enrich them only with trusted chunk provenance."""
    try:
        generated = LlmEvidenceAssessmentBatch.model_validate_json(client.generate_json(build_evidence_prompt(response)))
    except ValueError as error:
        raise LlmEvidenceError("LLM output is not valid evidence-assessment JSON.") from error

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
