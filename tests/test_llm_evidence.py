import json
import unittest

from literature_review.llm_evidence import LlmEvidenceError, summarize_and_rerank, validate_evidence_assessments
from literature_review.models import (
    EvidenceChunk,
    EvidenceRetrievalPolicy,
    EvidenceRetrievalResponse,
    RankedEvidenceChunk,
)


class FakeClient:
    def __init__(self, response: dict[str, object]) -> None:
        self.response = response
        self.prompt = ""

    def generate_json(self, prompt: str, schema: dict | None = None) -> str:
        self.prompt = prompt
        return json.dumps(self.response)


class RetryClient:
    def __init__(self, valid_response: dict[str, object]) -> None:
        self.responses = ['{"assessments": [', json.dumps(valid_response)]
        self.prompts: list[str] = []

    def generate_json(self, prompt: str, schema: dict | None = None) -> str:
        self.prompts.append(prompt)
        return self.responses.pop(0)


def retrieval_response() -> EvidenceRetrievalResponse:
    chunks = [
        EvidenceChunk(
            chunk_id="chunk-1",
            paper_id="paper-1",
            page_start=2,
            page_end=3,
            text="This evidence chunk describes a retrieval method in sufficient detail for testing.",
        ),
        EvidenceChunk(
            chunk_id="chunk-2",
            paper_id="paper-2",
            page_start=5,
            page_end=5,
            text="This evidence chunk describes evaluation results in sufficient detail for testing.",
        ),
    ]
    return EvidenceRetrievalResponse(
        query="How should literature review evidence be ranked?",
        policy=EvidenceRetrievalPolicy(top_k=2),
        ranked_chunks=[
            RankedEvidenceChunk(chunk=chunks[0], rank=1, score=3, matched_terms=["evidence"], rationale="test"),
            RankedEvidenceChunk(chunk=chunks[1], rank=2, score=2, matched_terms=["review"], rationale="test"),
        ],
    )


class LlmEvidenceTests(unittest.TestCase):
    def test_validates_and_enriches_llm_assessments(self) -> None:
        client = FakeClient(
            {
                "assessments": [
                    {
                        "chunk_id": "chunk-2",
                        "summary": "The chunk reports an evaluation result relevant to evidence ranking.",
                        "relevance_score": 9,
                        "evidence_quality_score": 8,
                        "rationale": "It directly reports an evaluation result for the requested topic.",
                    },
                    {
                        "chunk_id": "chunk-1",
                        "summary": "The chunk describes a retrieval method relevant to evidence ranking.",
                        "relevance_score": 9,
                        "evidence_quality_score": 9,
                        "rationale": "It describes a method that directly addresses the requested topic.",
                    },
                ]
            }
        )

        result = summarize_and_rerank(retrieval_response(), client)

        self.assertIn("Research query", client.prompt)
        self.assertIn("(1-10)", client.prompt)
        self.assertIn("rationale first", client.prompt)
        self.assertIn("do NOT penalize", client.prompt)
        self.assertNotIn("recommendation", client.prompt)
        self.assertEqual([item.chunk_id for item in result.summaries], ["chunk-1", "chunk-2"])
        self.assertEqual(result.summaries[0].page_end, 3)

    def test_rejects_unmatched_or_missing_chunk_ids(self) -> None:
        client = FakeClient(
            {
                "assessments": [
                    {
                        "chunk_id": "unknown",
                        "summary": "An apparently valid summary that cannot be matched to a source chunk.",
                        "relevance_score": 8,
                        "evidence_quality_score": 7,
                        "rationale": "The alleged source identity cannot be verified against retrieved evidence.",
                    }
                ]
            }
        )

        with self.assertRaises(LlmEvidenceError):
            summarize_and_rerank(retrieval_response(), client)

    def test_accepts_json_wrapped_in_a_markdown_fence(self) -> None:
        result = validate_evidence_assessments(
            "```json\n"
            '{"assessments": [{"chunk_id": "chunk-1", "summary": "This summary is long enough to satisfy the validation requirement.", '
            '"relevance_score": 8, "evidence_quality_score": 7, '
            '"rationale": "This rationale is long enough to satisfy the validation requirement."}]}\n'
            "```"
        )

        self.assertEqual(result.assessments[0].chunk_id, "chunk-1")

    def test_reports_the_first_schema_failure_without_echoing_model_output(self) -> None:
        with self.assertRaisesRegex(LlmEvidenceError, "summary"):
            validate_evidence_assessments('{"assessments": [{"chunk_id": "chunk-1"}]}')

    def test_retries_once_when_the_first_response_is_malformed_json(self) -> None:
        client = RetryClient(
            {
                "assessments": [
                    {
                        "chunk_id": "chunk-1",
                        "summary": "This valid repaired summary is sufficiently long for Pydantic validation.",
                        "relevance_score": 9,
                        "evidence_quality_score": 8,
                        "rationale": "This valid repaired rationale is sufficiently long for Pydantic validation.",
                    },
                    {
                        "chunk_id": "chunk-2",
                        "summary": "This second valid summary is sufficiently long for Pydantic validation.",
                        "relevance_score": 9,
                        "evidence_quality_score": 7,
                        "rationale": "This second valid rationale is sufficiently long for Pydantic validation.",
                    },
                ]
            }
        )

        result = summarize_and_rerank(retrieval_response(), client)

        self.assertEqual(len(client.prompts), 2)
        self.assertIn("malformed JSON", client.prompts[1])
        self.assertEqual(len(result.summaries), 2)


if __name__ == "__main__":
    unittest.main()
