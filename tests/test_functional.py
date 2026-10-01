import json
import re
import unittest

from literature_review.llm_evidence import LlmEvidenceError
from literature_review.functional import (
    FUNCTIONAL_BATCH_SIZE,
    FunctionalScoringError,
    aggregate_functional,
    build_functional_prompt,
    sample_top_chunks_per_paper,
    sample_formal_chunks_per_paper,
    score_chunks_functionally,
    select_quota_threshold,
    validate_functional_assessments,
)
from literature_review.models import (
    EvidenceChunk,
    FunctionalPaperScore,
    LlmFunctionalAssessment,
    PaperAssessment,
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


def evidence_chunks(chunk_count: int = 2) -> list[EvidenceChunk]:
    return [
        EvidenceChunk(
            chunk_id=f"paper-1-c{index}",
            paper_id="paper-1",
            section="Method",
            page_start=index + 1,
            page_end=index + 2,
            text=(
                f"Chunk {index} describes content with sufficient detail for "
                "functional scoring."
            ),
        )
        for index in range(1, chunk_count + 1)
    ]


def _prompt_chunk_indexes(prompt: str) -> list[str] | None:
    """Extract the chunk indexes shown inside a built functional prompt.

    Returns ``None`` for repair prompts, which have no inline chunk list.
    """
    marker = "Evidence chunks:\n"
    if marker not in prompt:
        return None
    payload = prompt.split(marker, maxsplit=1)[1]
    return re.findall(r"## Chunk (\d+)", payload)


def functional_payload(index: str) -> dict[str, object]:
    return {
        "chunk_id": index,
        "rationale": (
            f"Functional rationale for chunk {index} with sufficient detail for validation."
        ),
        "utility_score": 8,
    }


class SubsetFakeClient:
    """Assess exactly the chunk indexes present in each request prompt (batched).

    ``drop_on_first`` omits an index only on the very first call (a repair call
    then completes its set); ``drop_always`` omits it on every call, so the
    repair also fails and ``score_chunks_functionally`` raises.
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
        return json.dumps({"assessments": [functional_payload(index) for index in selected]})


class IndexDriftFakeClient:
    """Return one drifted index per first batch call, corrected on repair.

    Mirrors the M5b lesson: local models sometimes echo a wrong index (e.g.
    'A1') for a batch; the repair prompt must name the expected indexes so the
    drift is fixable within the one bounded repair budget.
    """

    def __init__(self, drifted_index: str = "A1") -> None:
        self.prompts: list[str] = []
        self.drifted_index = drifted_index

    def generate_json(self, prompt: str, schema: dict | None = None) -> str:
        self.prompts.append(prompt)
        if len(self.prompts) == 1:
            return json.dumps({"assessments": [functional_payload(self.drifted_index)]})
        return json.dumps(
            {
                "assessments": [
                    functional_payload(str(index)) for index in range(1, 5)
                ]
            }
        )


class FunctionalPromptTests(unittest.TestCase):
    def test_prompt_wraps_title_and_section_without_real_ids(self) -> None:
        chunks = evidence_chunks(2)
        prompt = build_functional_prompt(
            "How should a literature review agent rank evidence?",
            chunks,
            paper_titles={"paper-1": "A Concrete Study"},
        )

        self.assertIn("Research idea", prompt)
        self.assertIn("How should a literature review agent rank evidence?", prompt)
        self.assertIn("## Chunk 1", prompt)
        self.assertIn("## Chunk 2", prompt)
        self.assertIn("Paper: A Concrete Study | Section: Method", prompt)
        self.assertNotIn("paper-1-c1", prompt)  # no real chunk id is sent
        self.assertNotIn("paper-1-c2", prompt)

    def test_prompt_instructs_functional_utility_not_query_literal(self) -> None:
        prompt = build_functional_prompt("Rank evidence.", evidence_chunks(1))
        self.assertIn("rationale first", prompt)
        self.assertIn("(0-10)", prompt)  # S4: 0 分錨點放寬下限
        self.assertIn("NOT how literally", prompt)
        self.assertIn("The 'Section' field", prompt)  # LLM Input Hygiene §3
        self.assertIn("10 directly provides one of the three support kinds", prompt)
        self.assertIn("5-6 useful background", prompt)

    def test_prompt_lists_three_support_kinds_as_examples(self) -> None:
        # S4: 三類支持檢查清單(舉例性質,不限於此)
        prompt = build_functional_prompt("Rank evidence.", evidence_chunks(1))
        self.assertIn("examples only, not an exhaustive list", prompt)
        self.assertIn("problem-definition or pain-point support", prompt)
        self.assertIn("reusable technical mechanisms", prompt)
        self.assertIn("experimental and evaluation grounding", prompt)

    def test_prompt_anchors_zero_score_with_rationale_requirement(self) -> None:
        # S4: 0 分錨點(純常識/泛泛而談/無關)+ 給 0 分必須說明理由
        prompt = build_functional_prompt("Rank evidence.", evidence_chunks(1))
        self.assertIn("0 pure common knowledge", prompt)
        self.assertIn("If you assign 0, state in the rationale", prompt)

    def test_missing_titles_and_sections_fall_back_to_na(self) -> None:
        prompt = build_functional_prompt(
            "Rank evidence.",
            [EvidenceChunk(chunk_id="x-1", paper_id="x", text=("A chunk with enough text to satisfy the minimum length requirement."))],
        )
        self.assertIn("Paper: n/a | Section: n/a", prompt)


class FunctionalValidationTests(unittest.TestCase):
    def test_accepts_json_wrapped_in_a_markdown_fence(self) -> None:
        batch = validate_functional_assessments(
            "```json\n"
            '{"assessments": [{"chunk_id": "1", '
            '"rationale": "This rationale is long enough to satisfy the validation requirement.", '
            '"utility_score": 8}]}\n'
            "```"
        )
        self.assertEqual(batch.assessments[0].chunk_id, "1")
        self.assertEqual(batch.assessments[0].utility_score, 8)

    def test_accepts_utility_score_zero(self) -> None:
        # S4:utility_score 下限放寬到 0(純常識/泛泛而談/無關 → 0 分)
        batch = validate_functional_assessments(
            '{"assessments": [{"chunk_id": "1", '
            '"rationale": "Common knowledge stated without specific detail, unrelated to the research idea.", '
            '"utility_score": 0}]}'
        )
        self.assertEqual(batch.assessments[0].utility_score, 0)

    def test_reports_first_schema_failure_without_echoing_model_output(self) -> None:
        with self.assertRaisesRegex(FunctionalScoringError, "rationale"):
            validate_functional_assessments(
                '{"assessments": [{"chunk_id": "1", "utility_score": 8}]}'
            )

    def test_rejects_utility_score_outside_range(self) -> None:
        with self.assertRaisesRegex(FunctionalScoringError, "utility_score"):
            validate_functional_assessments(
                '{"assessments": [{"chunk_id": "1", "rationale": "A rationale that is definitely long enough to pass the minimum.", "utility_score": 11}]}'
            )

    def test_functional_scoring_error_is_an_llm_evidence_error(self) -> None:
        self.assertTrue(issubclass(FunctionalScoringError, LlmEvidenceError))


class FunctionalScoringTests(unittest.TestCase):
    def test_scores_and_resolves_functional_assessments(self) -> None:
        client = FakeClient(
            {
                "assessments": [
                    {
                        "chunk_id": "2",
                        "rationale": "Chunk two reports a reusable evaluation protocol for ranking systems.",
                        "utility_score": 9,
                    },
                    {
                        "chunk_id": "1",
                        "rationale": "Chunk one introduces a retrieval method that directly advances ranking.",
                        "utility_score": 7,
                    },
                ]
            }
        )

        result = score_chunks_functionally(
            "How should a literature review agent rank evidence?",
            evidence_chunks(2),
            client,
            batch_size=2,
            paper_titles={"paper-1": "A Concrete Study"},
        )

        self.assertIn("Utility measures contribution", client.prompt)
        self.assertIn("A Concrete Study", client.prompt)
        self.assertNotIn("paper-1-c1", client.prompt)
        self.assertEqual([item.chunk_id for item in result], ["paper-1-c1", "paper-1-c2"])
        self.assertEqual(result[0].utility_score, 7)
        self.assertEqual(result[1].utility_score, 9)

    def test_rejects_unknown_chunk_index(self) -> None:
        client = FakeClient(
            {
                "assessments": [
                    {
                        "chunk_id": "unknown",
                        "rationale": "An apparently valid assessment for an index that does not exist in the batch.",
                        "utility_score": 8,
                    }
                ]
            }
        )

        with self.assertRaises(FunctionalScoringError):
            score_chunks_functionally(
                "How should evidence be ranked?",
                evidence_chunks(2),
                client,
                batch_size=2,
            )

    def test_retries_once_when_the_first_response_is_malformed_json(self) -> None:
        client = RetryClient(
            {
                "assessments": [
                    {
                        "chunk_id": "1",
                        "rationale": "This valid repaired rationale is sufficiently long for validation.",
                        "utility_score": 9,
                    },
                    {
                        "chunk_id": "2",
                        "rationale": "This second valid rationale is sufficiently long for validation.",
                        "utility_score": 7,
                    },
                ]
            }
        )

        result = score_chunks_functionally(
            "How should evidence be ranked?",
            evidence_chunks(2),
            client,
            batch_size=2,
        )

        self.assertEqual(len(client.prompts), 2)
        self.assertIn("malformed JSON", client.prompts[1])
        self.assertEqual(len(result), 2)

    def test_batches_non_multiple_chunk_sets_into_bounded_calls(self) -> None:
        client = SubsetFakeClient()
        result = score_chunks_functionally(
            "How should evidence be ranked?",
            evidence_chunks(9),
            client,
            batch_size=4,
        )

        self.assertEqual(len(client.prompts), 3)  # ceil(9 / 4) = 3 calls
        self.assertEqual(len(_prompt_chunk_indexes(client.prompts[0])), 4)
        self.assertEqual(len(_prompt_chunk_indexes(client.prompts[1])), 4)
        self.assertEqual(len(_prompt_chunk_indexes(client.prompts[2])), 1)
        self.assertEqual(len(result), 9)
        self.assertEqual(
            {item.chunk_id for item in result},
            {f"paper-1-c{index}" for index in range(1, 10)},
        )

    def test_default_batch_size_groups_chunks_into_single_call(self) -> None:
        client = SubsetFakeClient()
        result = score_chunks_functionally(
            "How should evidence be ranked?",
            evidence_chunks(3),
            client,
        )

        self.assertEqual(len(client.prompts), 1)  # FUNCTIONAL_BATCH_SIZE defaults to 8
        self.assertEqual(len(result), 3)

    def test_batch_boundary_exact_multiple_single_batch(self) -> None:
        client = SubsetFakeClient()
        result = score_chunks_functionally(
            "How should evidence be ranked?",
            evidence_chunks(FUNCTIONAL_BATCH_SIZE),
            client,
            batch_size=FUNCTIONAL_BATCH_SIZE,
        )

        self.assertEqual(len(client.prompts), 1)
        self.assertEqual(len(result), FUNCTIONAL_BATCH_SIZE)

    def test_missing_chunk_is_repaired_once_then_completes(self) -> None:
        client = SubsetFakeClient(drop_on_first={"1"})
        result = score_chunks_functionally(
            "How should evidence be ranked?",
            evidence_chunks(4),
            client,
            batch_size=4,
        )

        self.assertEqual(len(client.prompts), 2)  # original batch + one repair
        self.assertIn("each once", client.prompts[1])
        self.assertIn('["1", "2", "3", "4"]', client.prompts[1])
        self.assertEqual(len(result), 4)

    def test_drifted_index_is_repaired_once_with_expected_ids(self) -> None:
        client = IndexDriftFakeClient(drifted_index="A1")
        result = score_chunks_functionally(
            "How should evidence be ranked?",
            evidence_chunks(4),
            client,
            batch_size=4,
        )

        self.assertEqual(len(client.prompts), 2)  # original batch + one repair
        self.assertIn("each once", client.prompts[1])
        self.assertIn('["1", "2", "3", "4"]', client.prompts[1])
        self.assertEqual(
            [item.chunk_id for item in result],
            ["paper-1-c1", "paper-1-c2", "paper-1-c3", "paper-1-c4"],
        )

    def test_missing_chunk_still_missing_after_repair_raises(self) -> None:
        client = SubsetFakeClient(drop_always={"1"})

        with self.assertRaises(FunctionalScoringError):
            score_chunks_functionally(
                "How should evidence be ranked?",
                evidence_chunks(4),
                client,
                batch_size=4,
            )

        self.assertEqual(len(client.prompts), 2)  # no further retries after one repair


class KeywordEncoder:
    """Deterministic fake encoder: 1.0 when the text contains the keyword, else 0.0.

    The BGE query prefix does not contain the keyword, so the encoded query and
    any matching chunk share cosine 1.0; non-matching chunks score 0.0, making
    per-paper top-n sampling fully deterministic in tests.
    """

    def __init__(self, keyword: str) -> None:
        self.keyword = keyword

    def __call__(self, texts: list[str]) -> list[list[float]]:
        return [[1.0 if self.keyword in text else 0.0] for text in texts]


class MultiKeywordEncoder:
    """Deterministic fake encoder with one dimension per keyword (S2 tests).

    Lets a sub-query vector differ from the main query vector, so a paper
    sampled under its own sub-query ranks its chunks differently than under
    the main query.
    """

    def __init__(self, keywords: list[str]) -> None:
        self.keywords = keywords

    def __call__(self, texts: list[str]) -> list[list[float]]:
        return [
            [1.0 if keyword in text else 0.0 for keyword in self.keywords]
            for text in texts
        ]


def keyword_chunks(
    paper_id: str,
    count: int,
    keyword: str,
    *,
    section: str | None = "Method",
) -> list[EvidenceChunk]:
    return [
        EvidenceChunk(
            chunk_id=f"{paper_id}-c{index}",
            paper_id=paper_id,
            section=section,
            page_start=index,
            page_end=index + 1,
            text=(
                f"Chunk {index} reports {keyword} results with enough detail to "
                "support functional scoring in tests."
            ),
        )
        for index in range(1, count + 1)
    ]


class FunctionalSamplingTests(unittest.TestCase):
    def test_formal_sampler_selects_method_results_and_bounded_notes(self) -> None:
        chunks = [
            EvidenceChunk(
                chunk_id=f"p1-c{i}", paper_id="p1", section=section,
                page_start=i, page_end=i,
                text=f"{term} evidence with sufficient detail for this sampling test.",
            )
            for i, (section, term) in enumerate([
                ("Abstract", "method"), ("Method", "method"), ("Method", "method"),
                ("Evaluation Setup", "method"), ("Evaluation Setup", "method"),
                ("Results", "method"), ("Results", "method"),
                ("Limitations", "method"), ("Limitations", "method"),
                ("References", "method"),
            ], start=1)
        ]
        scoring, notes = sample_formal_chunks_per_paper(
            chunks, "method", encoder=KeywordEncoder("method")
        )
        self.assertEqual([chunk.section for chunk in scoring["p1"]], ["Method", "Results"])
        self.assertEqual(len(notes["p1"]), 9)
        self.assertEqual(notes["p1"][0].section, "Abstract")
        self.assertEqual(len({chunk.chunk_id for chunk in notes["p1"]}), 9)
        self.assertNotIn("References", [chunk.section for chunk in notes["p1"]])

    def test_formal_sampler_embeds_each_eligible_chunk_once(self) -> None:
        encoded: list[str] = []

        def encoder(texts: list[str]) -> list[list[float]]:
            encoded.extend(texts)
            return [[1.0] for _ in texts]

        chunks = keyword_chunks("p1", 2, "method", section="Method")
        chunks += keyword_chunks("p1", 1, "method", section="Results")
        sample_formal_chunks_per_paper(chunks, "q", encoder=encoder)
        self.assertEqual(len(encoded), 4)  # one query plus three chunks in one pass

    def test_blacklisted_sections_are_dropped_before_sampling(self) -> None:
        chunks = keyword_chunks("paper-1", 3, "method")
        chunks.append(
            EvidenceChunk(
                chunk_id="paper-1-c4",
                paper_id="paper-1",
                section="References",
                page_start=5,
                page_end=5,
                text="References section content that must never be sampled.",
            )
        )
        chunks.append(
            EvidenceChunk(
                chunk_id="paper-1-c5",
                paper_id="paper-1",
                section="Acknowledgments",
                page_start=6,
                page_end=6,
                text="Acknowledgments section content that must never be sampled.",
            )
        )

        sampled = sample_top_chunks_per_paper(
            chunks, "method", top_n=2, encoder=KeywordEncoder("method")
        )

        self.assertEqual(
            [item.chunk_id for item in sampled["paper-1"]],
            ["paper-1-c1", "paper-1-c2"],
        )

    def test_appendix_is_dropped_by_default(self) -> None:
        chunks = keyword_chunks("paper-1", 1, "method")
        chunks.append(
            EvidenceChunk(
                chunk_id="paper-1-c2",
                paper_id="paper-1",
                section="Appendix",
                page_start=9,
                page_end=9,
                text="Appendix material that is dropped with the default setting.",
            )
        )

        sampled = sample_top_chunks_per_paper(
            chunks, "method", top_n=2, encoder=KeywordEncoder("method")
        )

        self.assertEqual([item.chunk_id for item in sampled["paper-1"]], ["paper-1-c1"])

    def test_per_paper_top_two_never_crosses_papers(self) -> None:
        # Paper A never mentions the query term (low global rank), while every
        # Paper B chunk matches. Per-paper sampling must still give A its own
        # two best chunks and never let B's chunks crowd them out.
        chunks = keyword_chunks("paper-a", 2, "noise", section="Method")
        chunks += keyword_chunks("paper-b", 3, "method", section="Results")

        sampled = sample_top_chunks_per_paper(
            chunks, "method", top_n=2, encoder=KeywordEncoder("method")
        )

        self.assertEqual(
            [item.chunk_id for item in sampled["paper-a"]],
            ["paper-a-c1", "paper-a-c2"],
        )
        self.assertEqual(
            [item.chunk_id for item in sampled["paper-b"]],
            ["paper-b-c1", "paper-b-c2"],
        )

    def test_fewer_chunks_than_top_n_returns_actual_count(self) -> None:
        sampled = sample_top_chunks_per_paper(
            keyword_chunks("paper-1", 1, "method"),
            "method",
            top_n=2,
            encoder=KeywordEncoder("method"),
        )

        self.assertEqual([item.chunk_id for item in sampled["paper-1"]], ["paper-1-c1"])

    def test_paper_with_no_chunks_after_blacklist_is_excluded(self) -> None:
        chunks = [
            EvidenceChunk(
                chunk_id="paper-1-c1",
                paper_id="paper-1",
                section="References",
                page_start=1,
                page_end=1,
                text="Only a references section exists, so nothing can be sampled.",
            )
        ]

        sampled = sample_top_chunks_per_paper(
            chunks, "method", top_n=2, encoder=KeywordEncoder("method")
        )

        self.assertEqual(sampled, {})

    def test_empty_input_returns_empty_mapping(self) -> None:
        sampled = sample_top_chunks_per_paper(
            [], "method", top_n=2, encoder=KeywordEncoder("method")
        )
        self.assertEqual(sampled, {})

    def test_query_map_subsamples_paper_under_its_own_subquery(self) -> None:
        # paper-b 的 c1 對主 query "method" 相似、c2 對其 sub-query "baseline" 相似
        # (S2):有 map → paper-b 用 sub-query 取樣,取樣結果與無 map 不同
        encoder = MultiKeywordEncoder(["method", "baseline"])
        chunks = [
            EvidenceChunk(
                chunk_id="paper-b-c1",
                paper_id="paper-b",
                section="Results",
                page_start=1,
                page_end=1,
                text="method results with enough detail for functional scoring in tests.",
            ),
            EvidenceChunk(
                chunk_id="paper-b-c2",
                paper_id="paper-b",
                section="Results",
                page_start=2,
                page_end=2,
                text="baseline results with enough detail for functional scoring in tests.",
            ),
        ]

        without_map = sample_top_chunks_per_paper(
            chunks, "method", top_n=2, encoder=encoder
        )
        with_map = sample_top_chunks_per_paper(
            chunks,
            "method",
            top_n=2,
            encoder=encoder,
            query_map={"paper-b": "baseline"},
        )

        self.assertEqual(
            [item.chunk_id for item in without_map["paper-b"]],
            ["paper-b-c1", "paper-b-c2"],
        )
        self.assertEqual(
            [item.chunk_id for item in with_map["paper-b"]],
            ["paper-b-c2", "paper-b-c1"],
        )

    def test_query_map_absent_and_empty_entries_fall_back_to_main_query(self) -> None:
        # (S2/P2):不在 map 內或值為空字串 → 回退主 query "method"
        encoder = MultiKeywordEncoder(["method", "baseline"])
        chunks = [
            EvidenceChunk(
                chunk_id="paper-b-c1",
                paper_id="paper-b",
                section="Results",
                page_start=1,
                page_end=1,
                text="method results with enough detail for functional scoring in tests.",
            ),
            EvidenceChunk(
                chunk_id="paper-b-c2",
                paper_id="paper-b",
                section="Results",
                page_start=2,
                page_end=2,
                text="baseline results with enough detail for functional scoring in tests.",
            ),
        ]

        absent = sample_top_chunks_per_paper(
            chunks,
            "method",
            top_n=2,
            encoder=encoder,
            query_map={"paper-x": "baseline"},
        )
        empty = sample_top_chunks_per_paper(
            chunks,
            "method",
            top_n=2,
            encoder=encoder,
            query_map={"paper-b": ""},
        )

        self.assertEqual(
            [item.chunk_id for item in absent["paper-b"]],
            ["paper-b-c1", "paper-b-c2"],
        )
        self.assertEqual(
            [item.chunk_id for item in empty["paper-b"]],
            ["paper-b-c1", "paper-b-c2"],
        )

    def test_sample_wraps_text_with_top_level_section_prefix(self) -> None:
        """T1: encode_chunks receives '2 Method | <text>' when paper_titles resolves."""
        recorded_texts: list[str] = []

        def recording_encoder(texts: list[str]) -> list[list[float]]:
            recorded_texts.extend(texts)
            return [[1.0] for _ in texts]

        chunks = [
            EvidenceChunk(
                chunk_id="p1-c1", paper_id="p1", section="2 Method",
                page_start=1, page_end=1,
                text="Method detail chunk with enough words for validation.",
            ),
        ]
        sample_top_chunks_per_paper(
            chunks, "q", top_n=1, encoder=recording_encoder,
            paper_titles={"p1": "A Title"},
        )
        # encode_query adds a prefixed query string before encode_chunks, so last entry is the chunk.
        self.assertTrue(recorded_texts[-1].startswith("2 Method | "), recorded_texts[-1])

    def test_sample_prefixes_other_when_no_paper_titles(self) -> None:
        """T1: without paper_titles, section=None yields 'other | <text>'."""
        recorded_texts: list[str] = []

        def recording_encoder(texts: list[str]) -> list[list[float]]:
            recorded_texts.extend(texts)
            return [[1.0] for _ in texts]

        chunks = [
            EvidenceChunk(
                chunk_id="p1-c1", paper_id="p1", section=None,
                page_start=1, page_end=1,
                text="Anonymous section chunk with enough words for validation.",
            ),
        ]
        sample_top_chunks_per_paper(
            chunks, "q", top_n=1, encoder=recording_encoder,
        )
        self.assertTrue(recorded_texts[-1].startswith("other | "), recorded_texts[-1])


class FunctionalAggregationTests(unittest.TestCase):
    def _assessments(
        self, pairs: list[tuple[str, int]], rationale_prefix: str = "This rationale restates the chunk and how it advances the research idea."
    ) -> list[LlmFunctionalAssessment]:
        return [
            LlmFunctionalAssessment(
                chunk_id=chunk_id,
                rationale=f"{rationale_prefix} {chunk_id}",
                utility_score=score,
            )
            for chunk_id, score in pairs
        ]

    def _paper_chunks(self) -> dict[str, list[EvidenceChunk]]:
        return {
            "paper-1": [
                EvidenceChunk(
                    chunk_id="paper-1-c1",
                    paper_id="paper-1",
                    section="Method",
                    page_start=1,
                    page_end=2,
                    text=("Chunk one of paper one with sufficient detail for testing."),
                ),
                EvidenceChunk(
                    chunk_id="paper-1-c2",
                    paper_id="paper-1",
                    section="Results",
                    page_start=3,
                    page_end=4,
                    text=("Chunk two of paper one with sufficient detail for testing."),
                ),
            ]
        }

    def test_weighted_blend_is_rounded_to_one_decimal_with_sample_size(self) -> None:
        # 0.7*8 + 0.3*7.5 = 7.85 → round → 7.8 (S3)
        assessments = self._assessments([("paper-1-c1", 8), ("paper-1-c2", 7)])
        scores = aggregate_functional(assessments, self._paper_chunks())

        score = scores["paper-1"]
        self.assertEqual(score.utility_score, 7.8)
        self.assertEqual(score.n_samples, 2)

    def test_weighted_blend_max_dominant_case(self) -> None:
        # 0.7*9 + 0.3*8.5 = 8.85 → round → 8.8 (S3)
        assessments = self._assessments([("paper-1-c1", 8), ("paper-1-c2", 9)])
        score = aggregate_functional(assessments, self._paper_chunks())["paper-1"]
        self.assertEqual(score.utility_score, 8.8)

    def test_weighted_blend_weak_chunk_case(self) -> None:
        # 0.7*8 + 0.3*5.5 = 7.25 → round → 7.2 (S3: 高分 chunk 主導、弱 chunk 壓制)
        assessments = self._assessments([("paper-1-c1", 8), ("paper-1-c2", 3)])
        score = aggregate_functional(assessments, self._paper_chunks())["paper-1"]
        self.assertEqual(score.utility_score, 7.2)

    def test_max_weight_is_adjustable_via_policy_value(self) -> None:
        # max_weight=0 → 純 mean (5.5);max_weight=1 → 純 max (8.0)
        assessments = self._assessments([("paper-1-c1", 8), ("paper-1-c2", 3)])
        chunks = self._paper_chunks()
        self.assertEqual(
            aggregate_functional(assessments, chunks, max_weight=0.0)["paper-1"].utility_score,
            5.5,
        )
        self.assertEqual(
            aggregate_functional(assessments, chunks, max_weight=1.0)["paper-1"].utility_score,
            8.0,
        )

    def test_single_chunk_collapses_regardless_of_weight(self) -> None:
        # n=1 時 max=mean=該值,權重不影響 (S3)
        assessments = self._assessments([("paper-1-c1", 8)])
        score = aggregate_functional(
            assessments, self._paper_chunks(), max_weight=1.0
        )["paper-1"]
        self.assertEqual(score.utility_score, 8.0)
        self.assertEqual(
            aggregate_functional(assessments, self._paper_chunks())["paper-1"].utility_score,
            8.0,
        )

    def test_evidence_citations_carry_chunk_provenance_and_scores(self) -> None:
        assessments = self._assessments([("paper-1-c2", 9), ("paper-1-c1", 7)])
        score = aggregate_functional(assessments, self._paper_chunks())["paper-1"]

        self.assertEqual(
            [item.chunk_id for item in score.evidence],
            ["paper-1-c1", "paper-1-c2"],
        )
        self.assertEqual(score.evidence[0].page_start, 1)
        self.assertEqual(score.evidence[0].page_end, 2)
        self.assertEqual(score.evidence[0].utility_score, 7)
        self.assertEqual(score.evidence[1].page_start, 3)
        self.assertEqual(score.evidence[1].utility_score, 9)
        self.assertIn("paper-1-c1", score.evidence[0].rationale)

    def test_multiple_papers_aggregate_independently(self) -> None:
        chunks = self._paper_chunks()
        chunks["paper-2"] = [
            EvidenceChunk(
                chunk_id="paper-2-c1",
                paper_id="paper-2",
                section="Method",
                page_start=1,
                page_end=1,
                text=("Single chunk of paper two with enough detail for testing."),
            )
        ]
        assessments = [
            *self._assessments([("paper-1-c1", 6), ("paper-1-c2", 6)]),
            *self._assessments([("paper-2-c1", 10)]),
        ]

        scores = aggregate_functional(assessments, chunks)

        self.assertEqual(scores["paper-1"].utility_score, 6.0)
        self.assertEqual(scores["paper-1"].n_samples, 2)
        self.assertEqual(scores["paper-2"].utility_score, 10.0)
        self.assertEqual(scores["paper-2"].n_samples, 1)

    def test_assessment_for_unknown_chunk_raises(self) -> None:
        assessments = self._assessments([("ghost-c1", 8)])
        with self.assertRaises(FunctionalScoringError):
            aggregate_functional(assessments, self._paper_chunks())

    def test_empty_assessments_return_empty_scores(self) -> None:
        self.assertEqual(aggregate_functional([], self._paper_chunks()), {})


class FunctionalQuotaThresholdTests(unittest.TestCase):
    def _score(self, paper_id: str, utility: float) -> FunctionalPaperScore:
        return FunctionalPaperScore(
            paper_id=paper_id,
            utility_score=utility,
            n_samples=2,
        )

    def _included(self, result: list[PaperAssessment]) -> set[str]:
        return {item.paper_id for item in result if item.recommendation == "include"}

    def test_groups_by_query_and_keeps_quota_per_group(self) -> None:
        scores = {
            "p1": self._score("p1", 9.0),
            "p2": self._score("p2", 8.0),
            "p3": self._score("p3", 7.0),
            "q1p1": self._score("q1p1", 6.5),
        }
        paper_queries = {
            "p1": "query-a",
            "p2": "query-a",
            "p3": "query-a",
            "q1p1": "query-b",
        }

        result = select_quota_threshold(
            scores,
            paper_queries,
            set(),
            n_first_round=2,
            n_follow_up=1,
            threshold=6.0,
        )

        # p3 clears the threshold but falls outside query-a's top-2 quota.
        self.assertEqual(self._included(result), {"p1", "p2", "q1p1"})
        self.assertEqual(
            {item.paper_id: item.recommendation for item in result}["p3"],
            "exclude",
        )

    def test_threshold_filters_within_quota(self) -> None:
        scores = {
            "p1": self._score("p1", 8.5),
            "p2": self._score("p2", 5.5),
        }
        paper_queries = {"p1": "query-a", "p2": "query-a"}

        result = select_quota_threshold(
            scores,
            paper_queries,
            set(),
            n_first_round=2,
            n_follow_up=1,
            threshold=6.0,
        )

        self.assertEqual(self._included(result), {"p1"})
        self.assertEqual(
            {item.paper_id: item.recommendation for item in result}["p2"],
            "exclude",
        )

    def test_follow_up_query_quota_is_one(self) -> None:
        scores = {
            "p1": self._score("p1", 9.0),
            "p2": self._score("p2", 8.0),
            "f1": self._score("f1", 7.0),
            "f2": self._score("f2", 6.5),
        }
        paper_queries = {
            "p1": "query-a",
            "p2": "query-a",
            "f1": "follow-up-1",
            "f2": "follow-up-1",
        }

        result = select_quota_threshold(
            scores,
            paper_queries,
            {"follow-up-1"},
            n_first_round=2,
            n_follow_up=1,
            threshold=6.0,
        )

        # follow-up queries keep only the single best paper.
        self.assertEqual(self._included(result), {"p1", "p2", "f1"})

    def test_shortfall_is_kept_small_and_not_backfilled(self) -> None:
        scores = {
            "p1": self._score("p1", 6.0),
            "p2": self._score("p2", 5.0),
        }
        paper_queries = {"p1": "query-a", "p2": "query-a"}

        result = select_quota_threshold(
            scores,
            paper_queries,
            set(),
            n_first_round=2,
            n_follow_up=1,
            threshold=6.0,
        )

        # p2 fails the threshold, so the group yields a shortfall (no fill-in).
        self.assertEqual(self._included(result), {"p1"})
        self.assertEqual(len(result), 2)

    def test_equal_scores_tie_break_by_paper_id(self) -> None:
        scores = {
            "pa": self._score("pa", 8.0),
            "pb": self._score("pb", 8.0),
            "pc": self._score("pc", 8.0),
        }
        paper_queries = {"pa": "query-a", "pb": "query-a", "pc": "query-a"}

        result = select_quota_threshold(
            scores,
            paper_queries,
            set(),
            n_first_round=2,
            n_follow_up=1,
            threshold=6.0,
        )

        self.assertEqual(self._included(result), {"pa", "pb"})

    def test_blacklisted_paper_is_excluded_with_reason(self) -> None:
        scores = {"p1": self._score("p1", 7.0)}
        paper_queries = {"p1": "query-a", "p2": "query-a"}  # p2 never scored

        result = select_quota_threshold(
            scores,
            paper_queries,
            set(),
            n_first_round=2,
            n_follow_up=1,
            threshold=6.0,
        )

        self.assertEqual(self._included(result), {"p1"})
        p2 = {item.paper_id: item for item in result}["p2"]
        self.assertEqual(p2.recommendation, "exclude")
        self.assertIn("no chunks", p2.rationale)

    def test_rationale_records_mean_samples_query_and_decision(self) -> None:
        scores = {"p1": self._score("p1", 7.5)}
        paper_queries = {"p1": "query-a"}

        result = select_quota_threshold(
            scores,
            paper_queries,
            set(),
            n_first_round=2,
            n_follow_up=1,
            threshold=6.0,
        )

        rationale = result[0].rationale
        self.assertIn("7.5", rationale)
        self.assertIn("2 sampled chunk(s)", rationale)
        self.assertIn("query-a", rationale)
        self.assertIn("quota", rationale)

    def test_result_sorted_by_utility_descending(self) -> None:
        scores = {
            "p1": self._score("p1", 7.0),
            "p2": self._score("p2", 9.0),
            "p3": self._score("p3", 8.0),
        }
        paper_queries = {"p1": "query-a", "p2": "query-a", "p3": "query-a"}

        result = select_quota_threshold(
            scores,
            paper_queries,
            set(),
            n_first_round=2,
            n_follow_up=1,
            threshold=6.0,
        )

        self.assertEqual(
            [item.paper_id for item in result],
            ["p2", "p3", "p1"],
        )


if __name__ == "__main__":
    unittest.main()
