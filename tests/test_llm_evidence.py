import contextlib
import io
import json
import os
import re
import unittest
from unittest import mock

from literature_review.llm_evidence import (
    DailyQuotaExhausted,
    LlmEvidenceError,
    LlmServiceError,
    _RateTracker,
    build_evidence_prompt,
    classify_provider_error,
    summarize_and_rerank,
    validate_evidence_assessments,
)
from literature_review.models import (
    EvidenceChunk,
    EvidenceRetrievalPolicy,
    EvidenceRetrievalResponse,
    RankedEvidenceChunk,
)


class FakeClock:
    """Monotonic clock that only advances when something sleeps."""

    def __init__(self) -> None:
        self.now_value = 0.0
        self.slept: list[float] = []

    def now(self) -> float:
        return self.now_value

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now_value += seconds


class FakeProviderError(Exception):
    """Provider error carrying the HTTP status the way ``google.genai`` does."""

    def __init__(self, message: str, code: int | None = None) -> None:
        super().__init__(message)
        self.code = code


class FakeInteractions:
    """Stands in for ``client.interactions.create`` with a scripted reply list."""

    def __init__(self, script: list[str | Exception]) -> None:
        self.script = list(script)
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        reply = self.script.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return type("Interaction", (), {"output_text": reply})()


class FakeGeminiApi:
    def __init__(self, script: list[str | Exception]) -> None:
        self.interactions = FakeInteractions(script)


def gemini_client(script: list[str | Exception], clock: FakeClock, **tracker_kwargs):
    """Build a ``GeminiJsonClient`` around fakes, bypassing the real SDK client."""
    from literature_review.llm_evidence import GeminiJsonClient

    client = object.__new__(GeminiJsonClient)
    client._client = FakeGeminiApi(script)
    client._model = "test-model"
    client._label = "test"
    client._key_id = "slot0000"
    client._tracker = _RateTracker(
        clock=clock.now, sleeper=clock.sleep, **tracker_kwargs
    )
    return client


class RateTrackerTest(unittest.TestCase):
    def test_default_interval_matches_the_free_tier_ceiling(self) -> None:
        # 5 requests per minute needs a 12s gap; the default adds 1s of slack.
        self.assertEqual(_RateTracker().interval_seconds, 13.0)

    def test_first_call_never_waits(self) -> None:
        clock = FakeClock()
        tracker = _RateTracker(clock=clock.now, sleeper=clock.sleep)
        self.assertEqual(tracker.wait_for_slot(), 0.0)
        tracker.record()
        self.assertEqual(clock.slept, [])

    def test_consecutive_calls_are_spaced_by_the_interval(self) -> None:
        clock = FakeClock()
        tracker = _RateTracker(clock=clock.now, sleeper=clock.sleep)
        tracker.record()
        self.assertEqual(tracker.wait_for_slot(), 13.0)
        tracker.record()
        # a call that already cost time only waits out the remainder
        clock.now_value += 10.0
        self.assertAlmostEqual(tracker.wait_for_slot(), 3.0)
        self.assertEqual(tracker.calls, 2)

    def test_zero_per_minute_disables_pacing(self) -> None:
        clock = FakeClock()
        tracker = _RateTracker(requests_per_minute=0, clock=clock.now, sleeper=clock.sleep)
        tracker.record()
        self.assertEqual(tracker.wait_for_slot(), 0.0)
        self.assertEqual(clock.slept, [])

    def test_daily_budget_blocks_the_call_that_would_exceed_it(self) -> None:
        clock = FakeClock()
        tracker = _RateTracker(
            requests_per_day=2, clock=clock.now, sleeper=clock.sleep
        )
        self.assertTrue(tracker.can_call())
        tracker.record()
        tracker.record()
        self.assertFalse(tracker.can_call())
        with self.assertRaises(DailyQuotaExhausted):
            tracker.require_slot()

    def test_daily_budget_of_zero_disables_the_guard(self) -> None:
        tracker = _RateTracker(requests_per_day=0)
        tracker.record()
        self.assertTrue(tracker.can_call())
        tracker.require_slot()


class ProviderErrorClassificationTest(unittest.TestCase):
    def test_daily_ceiling_is_not_retryable(self) -> None:
        error = FakeProviderError(
            "429 RESOURCE_EXHAUSTED: Limit: 20 requests per day", code=429
        )
        self.assertEqual(classify_provider_error(error), ("daily_quota", 0.0))

    def test_per_minute_limit_carries_a_retry_hint(self) -> None:
        error = FakeProviderError(
            "429 RESOURCE_EXHAUSTED: Limit: 5 requests per minute. Please retry in 37s",
            code=429,
        )
        self.assertEqual(classify_provider_error(error), ("retry_after", 37.0))

    def test_429_without_a_retry_hint_is_treated_as_quota(self) -> None:
        self.assertEqual(
            classify_provider_error(FakeProviderError("429 nope", code=429)),
            ("daily_quota", 0.0),
        )

    def test_503_and_overload_messages_are_service_errors(self) -> None:
        for error in (
            FakeProviderError("503 Service Unavailable", code=503),
            FakeProviderError("The model is overloaded. Please try again later."),
        ):
            self.assertEqual(classify_provider_error(error), ("service", 0.0))

    def test_unrelated_errors_stay_unclassified(self) -> None:
        error = FakeProviderError("401 UNAUTHENTICATED: bad key", code=401)
        self.assertEqual(classify_provider_error(error), ("other", 0.0))


class GeminiGenerateJsonTest(unittest.TestCase):
    def _generate(self, client, prompt: str = "p") -> str:
        stream = io.StringIO()
        with contextlib.redirect_stderr(stream):
            text = client.generate_json(prompt, {"type": "object"})
        return text

    def test_successful_call_logs_the_key_slot_and_call_count(self) -> None:
        clock = FakeClock()
        client = gemini_client(['{"ok": 1}', '{"ok": 2}'], clock)
        self.assertEqual(self._generate(client), '{"ok": 1}')
        stream = io.StringIO()
        with contextlib.redirect_stderr(stream):
            client.generate_json("p", None)
        log = stream.getvalue()
        self.assertIn("label=test", log)
        self.assertIn("key=slot0000", log)
        self.assertIn("calls=2/20", log)
        self.assertIn("status=ok", log)

    def test_retry_after_429_waits_then_succeeds(self) -> None:
        clock = FakeClock()
        client = gemini_client(
            [
                FakeProviderError(
                    "429 Limit: 5 requests per minute. Please retry in 3s", code=429
                ),
                '{"ok": 2}',
            ],
            clock,
        )
        with mock.patch.dict(os.environ, {"GEMINI_429_RETRIES": "1"}, clear=False):
            self.assertEqual(self._generate(client), '{"ok": 2}')
        self.assertEqual(len(client._client.interactions.calls), 2)
        # the provider's 3s hint, then the remainder of the 13s pacing interval
        self.assertEqual(clock.slept, [3.0, 10.0])

    def test_retry_budget_of_zero_surfaces_the_429(self) -> None:
        clock = FakeClock()
        client = gemini_client(
            [FakeProviderError("429 Limit: 5 requests per minute. retry in 3s", code=429)],
            clock,
        )
        with mock.patch.dict(os.environ, {"GEMINI_429_RETRIES": "0"}, clear=False):
            with self.assertRaises(DailyQuotaExhausted):
                self._generate(client)
        self.assertEqual(len(client._client.interactions.calls), 1)

    def test_daily_429_is_never_retried(self) -> None:
        clock = FakeClock()
        client = gemini_client(
            [FakeProviderError("429 Limit: 20 requests per day", code=429)], clock
        )
        with mock.patch.dict(os.environ, {"GEMINI_429_RETRIES": "5"}, clear=False):
            with self.assertRaises(DailyQuotaExhausted):
                self._generate(client)
        self.assertEqual(len(client._client.interactions.calls), 1)

    def test_503_stops_after_three_retries(self) -> None:
        clock = FakeClock()
        client = gemini_client(
            [FakeProviderError("503 The model is overloaded", code=503)] * 4, clock
        )
        with mock.patch("literature_review.llm_evidence.random.uniform", return_value=0):
            with self.assertRaises(LlmServiceError):
                self._generate(client)
        self.assertEqual(len(client._client.interactions.calls), 4)
        self.assertEqual(clock.slept, [15.0, 30.0, 60.0])
        self.assertEqual({call["model"] for call in client._client.interactions.calls}, {"test-model"})

    def test_exhausted_local_budget_blocks_before_calling(self) -> None:
        clock = FakeClock()
        client = gemini_client(['{"ok": 1}'], clock, requests_per_day=1)
        self._generate(client)
        with self.assertRaises(DailyQuotaExhausted):
            self._generate(client)
        self.assertEqual(len(client._client.interactions.calls), 1)

    def test_empty_output_is_reported(self) -> None:
        clock = FakeClock()
        client = gemini_client([""], clock)
        with self.assertRaises(LlmEvidenceError):
            self._generate(client)



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
