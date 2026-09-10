import json
import re
import unittest

from literature_review.llm_evidence import (
    LlmEvidenceError,
    build_evidence_prompt,
    summarize_and_rerank,
    validate_evidence_assessments,
)
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


def _prompt_chunk_indexes(prompt: str) -> list[str] | None:
    """Extract the chunk indexes shown inside a built evidence prompt.

    Returns ``None`` for repair prompts, which have no inline chunk list.
    """
    marker = "Evidence chunks:\n"
    if marker not in prompt:
        return None
    payload = prompt.split(marker, maxsplit=1)[1]
    return re.findall(r"## Chunk (\d+)", payload)


def assessment_payload(index: str) -> dict[str, object]:
    return {
        "chunk_id": index,
        "summary": f"Summary for chunk {index} with sufficient detail for validation.",
        "rationale_relevance": (
            f"Relevance rationale for chunk {index} with sufficient detail for validation."
        ),
        "rationale_quality": (
            f"Quality rationale for chunk {index} with sufficient detail for validation."
        ),
        "relevance_score": 8,
        "evidence_quality_score": 7,
    }


class SubsetFakeClient:
    """Assess exactly the chunk indexes present in each request prompt (batched RCS).

    ``drop_on_first`` omits an index only on the very first call (a repair call
    then completes its set); ``drop_always`` omits it on every call, so the
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
        self._last_batch_indexes: list[str] = []

    def generate_json(self, prompt: str, schema: dict | None = None) -> str:
        self.prompts.append(prompt)
        self._call_count += 1
        indexes = _prompt_chunk_indexes(prompt)
        if indexes is None:
            indexes = self._last_batch_indexes
        else:
            self._last_batch_indexes = indexes
        first_call = self._call_count == 1
        drop = self.drop_always | (self.drop_on_first if first_call else set())
        selected = [index for index in indexes if index not in drop]
        return json.dumps({"assessments": [assessment_payload(index) for index in selected]})


class IndexDriftFakeClient:
    """Return one drifted index per first batch call, corrected on repair.

    Mirrors the M5b B=1 probe finding: qwen3:8b sometimes echoes a wrong
    index (e.g. '2'/'A1'/'C1') for a single-chunk batch; the repair prompt
    must name the expected indexes so the drift is fixable within the one
    bounded repair budget.
    """

    def __init__(self, drifted_index: str = "A1") -> None:
        self.prompts: list[str] = []
        self.drifted_index = drifted_index

    def generate_json(self, prompt: str, schema: dict | None = None) -> str:
        self.prompts.append(prompt)
        if len(self.prompts) == 1:
            return json.dumps(
                {
                    "assessments": [
                        assessment_payload(self.drifted_index),
                    ]
                }
            )
        return json.dumps(
            {
                "assessments": [
                    assessment_payload("1"),
                    assessment_payload("2"),
                    assessment_payload("3"),
                    assessment_payload("4"),
                ]
            }
        )


class LlmEvidenceTests(unittest.TestCase):
    def test_validates_and_enriches_llm_assessments(self) -> None:
        client = FakeClient(
            {
                "assessments": [
                    {
                        "chunk_id": "2",
                        "summary": "The chunk reports an evaluation result relevant to evidence ranking.",
                        "rationale_relevance": "It directly reports an evaluation result for the requested topic.",
                        "rationale_quality": "The evaluation result is concrete and directly backs the claim.",
                        "relevance_score": 9,
                        "evidence_quality_score": 8,
                    },
                    {
                        "chunk_id": "1",
                        "summary": "The chunk describes a retrieval method relevant to evidence ranking.",
                        "rationale_relevance": "It describes a method that directly addresses the requested topic.",
                        "rationale_quality": "The method description is specific and directly supports the claim.",
                        "relevance_score": 9,
                        "evidence_quality_score": 9,
                    },
                ]
            }
        )

        result = summarize_and_rerank(retrieval_response(), client, batch_size=2)

        self.assertIn("Research query", client.prompt)
        self.assertIn("(1-10)", client.prompt)
        self.assertIn("rationale first", client.prompt)
        self.assertIn("Do NOT penalize", client.prompt)
        self.assertNotIn("recommendation", client.prompt)
        self.assertIn("## Chunk 1", client.prompt)
        self.assertIn("Citations:", client.prompt)
        self.assertIn("Venue:", client.prompt)
        self.assertNotIn('"chunk_id": "chunk-', client.prompt)
        self.assertNotIn("page_start", client.prompt)
        self.assertEqual([item.chunk_id for item in result.summaries], ["chunk-1", "chunk-2"])
        self.assertEqual(result.summaries[0].page_end, 3)

    def test_evidence_prompt_contains_m5b1_score_guidance(self) -> None:
        prompt = build_evidence_prompt(retrieval_response())
        self.assertIn("sub-question", prompt)
        self.assertIn("NOT automatically relevant", prompt)
        self.assertIn("majority of the chunk", prompt)
        self.assertIn("independently", prompt)
        self.assertIn("secondary supporting signals", prompt)

    def test_rejects_unmatched_or_missing_chunk_ids(self) -> None:
        client = FakeClient(
            {
                "assessments": [
                    {
                        "chunk_id": "unknown",
                        "summary": "An apparently valid summary that cannot be matched to a source chunk.",
                        "rationale_relevance": "The alleged source identity cannot be verified against retrieved evidence.",
                        "rationale_quality": "No source chunk exists to judge the quality of this summary.",
                        "relevance_score": 8,
                        "evidence_quality_score": 7,
                    }
                ]
            }
        )

        with self.assertRaises(LlmEvidenceError):
            summarize_and_rerank(retrieval_response(), client, batch_size=2)

    def test_accepts_json_wrapped_in_a_markdown_fence(self) -> None:
        result = validate_evidence_assessments(
            "```json\n"
            '{"assessments": [{"chunk_id": "chunk-1", "summary": "This summary is long enough to satisfy the validation requirement.", '
            '"relevance_score": 8, "evidence_quality_score": 7, '
            '"rationale_relevance": "This relevance rationale is long enough to satisfy the requirement.", '
            '"rationale_quality": "This quality rationale is long enough to satisfy the requirement."}]}\n'
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
                        "chunk_id": "1",
                        "summary": "This valid repaired summary is sufficiently long for Pydantic validation.",
                        "rationale_relevance": "This valid repaired relevance rationale is sufficiently long for validation.",
                        "rationale_quality": "This valid repaired quality rationale is sufficiently long for validation.",
                        "relevance_score": 9,
                        "evidence_quality_score": 8,
                    },
                    {
                        "chunk_id": "2",
                        "summary": "This second valid summary is sufficiently long for Pydantic validation.",
                        "rationale_relevance": "This second valid relevance rationale is sufficiently long for validation.",
                        "rationale_quality": "This second valid quality rationale is sufficiently long for validation.",
                        "relevance_score": 9,
                        "evidence_quality_score": 7,
                    },
                ]
            }
        )

        result = summarize_and_rerank(retrieval_response(), client, batch_size=2)

        self.assertEqual(len(client.prompts), 2)
        self.assertIn("malformed JSON", client.prompts[1])
        self.assertEqual(len(result.summaries), 2)

    def test_batches_non_multiple_chunk_sets_into_bounded_calls(self) -> None:
        client = SubsetFakeClient()
        result = summarize_and_rerank(retrieval_response(9), client, batch_size=4)

        self.assertEqual(len(client.prompts), 3)  # ceil(9 / 4) = 3 calls
        self.assertEqual(len(_prompt_chunk_indexes(client.prompts[0])), 4)  # first call holds a full batch
        self.assertEqual(len(_prompt_chunk_indexes(client.prompts[1])), 4)  # second call holds a full batch
        self.assertEqual(len(_prompt_chunk_indexes(client.prompts[2])), 1)  # last call holds the remainder
        self.assertEqual(len(result.summaries), 9)
        self.assertEqual(
            {item.chunk_id for item in result.summaries},
            {f"chunk-{index}" for index in range(1, 10)},
        )

    def test_default_batch_size_groups_chunks_into_single_call(self) -> None:
        client = SubsetFakeClient()
        result = summarize_and_rerank(retrieval_response(3), client)

        self.assertEqual(len(client.prompts), 1)  # RCS_BATCH_SIZE defaults to 4
        self.assertEqual(len(result.summaries), 3)

    def test_batch_boundary_exact_multiple_single_batch(self) -> None:
        client = SubsetFakeClient()
        result = summarize_and_rerank(retrieval_response(4), client, batch_size=4)

        self.assertEqual(len(client.prompts), 1)  # 4 chunks fit in exactly one batch
        self.assertEqual(len(result.summaries), 4)

    def test_missing_chunk_is_repaired_once_then_completes(self) -> None:
        client = SubsetFakeClient(drop_on_first={"1"})
        result = summarize_and_rerank(retrieval_response(4), client, batch_size=4)

        self.assertEqual(len(client.prompts), 2)  # original batch + one repair
        self.assertIn("each once", client.prompts[1])  # repair prompt names the expected chunk indexes
        self.assertIn('["1", "2", "3", "4"]', client.prompts[1])
        self.assertEqual(len(result.summaries), 4)  # repaired batch completes the set

    def test_drifted_index_is_repaired_once_with_expected_ids(self) -> None:
        client = IndexDriftFakeClient(drifted_index="A1")
        result = summarize_and_rerank(retrieval_response(4), client, batch_size=4)

        self.assertEqual(len(client.prompts), 2)  # original batch + one repair
        self.assertIn("each once", client.prompts[1])
        self.assertIn('["1", "2", "3", "4"]', client.prompts[1])
        self.assertEqual([item.chunk_id for item in result.summaries], ["chunk-1", "chunk-2", "chunk-3", "chunk-4"])

    def test_missing_chunk_still_missing_after_repair_raises(self) -> None:
        client = SubsetFakeClient(drop_always={"1"})

        with self.assertRaises(LlmEvidenceError):
            summarize_and_rerank(retrieval_response(4), client, batch_size=4)

        self.assertEqual(len(client.prompts), 2)  # no further retries after one repair


if __name__ == "__main__":
    unittest.main()
