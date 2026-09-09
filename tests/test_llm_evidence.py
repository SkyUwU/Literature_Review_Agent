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


def retrieval_response(chunk_count: int = 2) -> EvidenceRetrievalResponse:
    chunks = [
        EvidenceChunk(
            chunk_id=f"chunk-{index}",
            paper_id=f"paper-{index}",
            page_start=index + 1,
            page_end=index + 2,
            text=(
                f"Evidence chunk {index} describes content in sufficient detail for testing."
            ),
        )
        for index in range(1, chunk_count + 1)
    ]
    return EvidenceRetrievalResponse(
        query="How should literature review evidence be ranked?",
        policy=EvidenceRetrievalPolicy(top_k=chunk_count),
        ranked_chunks=[
            RankedEvidenceChunk(chunk=chunk, rank=rank, score=3, matched_terms=["evidence"], rationale="test")
            for rank, chunk in enumerate(chunks, start=1)
        ],
    )


def _prompt_chunk_ids(prompt: str) -> list[str] | None:
    """Extract the chunk_ids named inside a built evidence prompt.

    Returns ``None`` for repair prompts, which have no inline chunk list.
    """
    marker = "Evidence chunks: "
    if marker not in prompt:
        return None
    payload = prompt.split(marker, maxsplit=1)[1]
    chunks = json.loads(payload)
    return [item["chunk_id"] for item in chunks]


def assessment_payload(chunk_id: str) -> dict[str, object]:
    return {
        "chunk_id": chunk_id,
        "summary": f"Summary for {chunk_id} with sufficient detail for validation.",
        "relevance_score": 8,
        "evidence_quality_score": 7,
        "rationale": f"Rationale for {chunk_id} with sufficient detail for validation.",
    }


class SubsetFakeClient:
    """Assess exactly the chunk_ids present in each request prompt (batched RCS).

    ``drop_on_first`` omits chunks only on the very first call (a repair call
    then completes them); ``drop_always`` omits them on every call, so the
    repair also fails and ``summarize_and_rerank`` raises.
    """

    def __init__(
        self,
        *,
        drop_on_first: set[str] | None = None,
        drop_always: set[str] | None = None,
    ) -> None:
        self.prompts: list[str] = []
        self.drop_on_first = set(drop_on_first or set())
        self.drop_always = set(drop_always or set())
        self._call_count = 0
        self._last_batch_ids: list[str] = []

    def generate_json(self, prompt: str, schema: dict | None = None) -> str:
        self.prompts.append(prompt)
        self._call_count += 1
        chunk_ids = _prompt_chunk_ids(prompt)
        if chunk_ids is None:
            chunk_ids = self._last_batch_ids
        else:
            self._last_batch_ids = chunk_ids
        first_call = self._call_count == 1
        drop = self.drop_always | (self.drop_on_first if first_call else set())
        selected = [chunk_id for chunk_id in chunk_ids if chunk_id not in drop]
        return json.dumps({"assessments": [assessment_payload(chunk_id) for chunk_id in selected]})


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

    def test_batches_non_multiple_chunk_sets_into_bounded_calls(self) -> None:
        client = SubsetFakeClient()
        result = summarize_and_rerank(retrieval_response(9), client)

        self.assertEqual(len(client.prompts), 3)  # ceil(9 / 4) = 3 calls
        self.assertEqual(len(_prompt_chunk_ids(client.prompts[0])), 4)  # first call holds a full batch
        self.assertEqual(len(_prompt_chunk_ids(client.prompts[1])), 4)  # second call holds a full batch
        self.assertEqual(len(_prompt_chunk_ids(client.prompts[2])), 1)  # last call holds the remainder
        self.assertEqual(len(result.summaries), 9)
        self.assertEqual(
            {item.chunk_id for item in result.summaries},
            {f"chunk-{index}" for index in range(1, 10)},
        )

    def test_batch_boundary_exact_multiple_single_batch(self) -> None:
        client = SubsetFakeClient()
        result = summarize_and_rerank(retrieval_response(4), client)

        self.assertEqual(len(client.prompts), 1)  # 4 chunks fit in exactly one batch
        self.assertEqual(len(result.summaries), 4)

    def test_missing_chunk_is_repaired_once_then_completes(self) -> None:
        client = SubsetFakeClient(drop_on_first={"chunk-1"})
        result = summarize_and_rerank(retrieval_response(4), client)

        self.assertEqual(len(client.prompts), 2)  # original batch + one repair
        self.assertIn("each once", client.prompts[1])  # repair prompt names the expected chunk_ids
        self.assertEqual(len(result.summaries), 4)  # repaired batch completes the set

    def test_missing_chunk_still_missing_after_repair_raises(self) -> None:
        client = SubsetFakeClient(drop_always={"chunk-1"})

        with self.assertRaises(LlmEvidenceError):
            summarize_and_rerank(retrieval_response(4), client)

        self.assertEqual(len(client.prompts), 2)  # no further retries after one repair


if __name__ == "__main__":
    unittest.main()
