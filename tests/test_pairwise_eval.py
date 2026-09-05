"""TDD tests for the pair-wise lexical-vs-embedding comparison core."""

import contextlib
import io
import json
import os
import re
import tempfile
import unittest
from unittest import mock

from literature_review.embedding_retriever import (
    QUERY_PREFIX,
    encode_chunks,
    encode_query,
    retrieve_evidence_embedding,
    retrieve_evidence_embedding_cached,
)
from literature_review.models import (
    EvidenceChunk,
    EvidenceRetrievalPolicy,
    EvidenceRetrievalResponse,
    RankedEvidenceChunk,
)
from literature_review.pairwise_eval import (
    BUILTIN_QUERIES,
    FIXED_SEED,
    MAX_BATCH,
    PairwiseVerdict,
    _generate_json_with_retry,
    aggregate_overall,
    aggregate_query,
    assign_display_orders,
    build_batch_prompt,
    build_pairwise_prompt,
    build_parser,
    build_report,
    evaluate_query,
    judge_batch,
    judge_pair,
    main,
    pair_ranked,
)
from literature_review.llm_evidence import GeminiJsonClient, LlmEvidenceError
from google.genai._gaos.lib.compat_errors import (
    InternalServerError as GaosInternalServerError,
)
from google.genai._gaos.lib.compat_errors import RateLimitError as GaosRateLimitError


def chunk(chunk_id: str, text: str, paper_id: str = "paper-1") -> EvidenceChunk:
    padded = text if len(text) >= 20 else f"{text} {text} padded evidence text"
    return EvidenceChunk(
        chunk_id=chunk_id,
        paper_id=paper_id,
        page_start=1,
        page_end=1,
        text=padded,
    )


def ranked(chunk_id: str, rank: int, text: str, paper_id: str = "paper-1") -> RankedEvidenceChunk:
    return RankedEvidenceChunk(
        chunk=chunk(chunk_id, text, paper_id),
        rank=rank,
        score=float(rank),
        matched_terms=[],
        rationale="test ranking",
    )


def response(query: str, items: list[RankedEvidenceChunk]) -> EvidenceRetrievalResponse:
    if len(query) < 3:
        query = f"{query} query"
    return EvidenceRetrievalResponse(
        query=query,
        policy=EvidenceRetrievalPolicy(top_k=len(items)),
        ranked_chunks=items,
    )


def make_pairs(lexical_ids: list[str], embedding_ids: list[str]) -> list:
    lexical = response("q", [ranked(cid, idx, f"lexical text {cid}") for idx, cid in enumerate(lexical_ids, start=1)])
    embedding = response("q", [ranked(cid, idx, f"embedding text {cid}") for idx, cid in enumerate(embedding_ids, start=1)])
    return pair_ranked(lexical, embedding)


class PairwiseFakeClient:
    """Fake judge returning a scripted winner per pair_id, recording prompts."""

    def __init__(self, winners: dict[str, str]) -> None:
        self._winners = winners
        self.calls: list[str] = []

    def generate_json(self, prompt: str, schema: dict | None = None) -> str:
        self.calls.append(prompt)
        match = re.search(r"Pair id: (\S+)", prompt)
        pair_id = match.group(1) if match else "rank-1"
        winner = self._winners.get(pair_id, "tie")
        return json.dumps({"pair_id": pair_id, "winner": winner, "rationale": "scripted"})


class RepairOnceFakeClient:
    """Fake judge that returns malformed JSON once, then repairs on the second call."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self._pair_id: str | None = None

    def generate_json(self, prompt: str, schema: dict | None = None) -> str:
        self.calls.append(prompt)
        if self._pair_id is None:
            match = re.search(r"Pair id: (\S+)", prompt)
            self._pair_id = match.group(1) if match else "rank-1"
            return "{this is not valid json"
        return json.dumps(
            {"pair_id": self._pair_id, "winner": "A", "rationale": "repaired"}
        )


class AlwaysMalformedFakeClient:
    """Fake judge that never returns valid JSON (repair path must propagate error)."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self._pair_id: str | None = None

    def generate_json(self, prompt: str, schema: dict | None = None) -> str:
        self.calls.append(prompt)
        if self._pair_id is None:
            match = re.search(r"Pair id: (\S+)", prompt)
            self._pair_id = match.group(1) if match else "rank-1"
        return "{still not valid"


def _batch_payload(prompt: str, winners: dict[str, str]) -> str:
    """Build a positional BatchVerdictList JSON payload.

    One verdict per input pair, aligned by occurrence order, with no pair_id
    field (positional decode design).
    """
    verdicts = []
    for match in re.finditer(r"Pair id: (\S+)", prompt):
        pair_id = match.group(1)
        winner = winners.get(pair_id, "tie")
        verdicts.append({"winner": winner, "rationale": "scripted"})
    return json.dumps({"verdicts": verdicts})


class BatchPairwiseFakeClient:
    """Fake batch judge returning a BatchVerdictList, recording prompts."""

    def __init__(self, winners: dict[str, str]) -> None:
        self._winners = winners
        self.calls: list[str] = []

    def generate_json(self, prompt: str, schema: dict | None = None) -> str:
        self.calls.append(prompt)
        return _batch_payload(prompt, self._winners)


class BatchRepairOnceFakeClient:
    """Fake batch judge returning malformed JSON once, then a valid batch."""

    def __init__(self, winners: dict[str, str] | None = None) -> None:
        self._winners = winners or {}
        self.calls: list[str] = []
        self._repaired_payload: str | None = None

    def generate_json(self, prompt: str, schema: dict | None = None) -> str:
        self.calls.append(prompt)
        if self._repaired_payload is None:
            self._repaired_payload = _batch_payload(prompt, self._winners)
            return "{this is not valid json"
        return self._repaired_payload


class BatchAlwaysMalformedFakeClient:
    """Fake batch judge that never returns valid JSON."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def generate_json(self, prompt: str, schema: dict | None = None) -> str:
        self.calls.append(prompt)
        return "{still not valid"


class ScriptedClient:
    """Fake client whose generate_json returns a scripted sequence of outcomes."""

    def __init__(self, outcomes: list) -> None:
        self._outcomes = list(outcomes)
        self.calls = 0

    def generate_json(self, prompt: str, schema: dict | None = None) -> str:
        self.calls += 1
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class _FakeRateLimit(GaosRateLimitError):
    def __init__(self, message: str = "rate limited") -> None:
        self.message = message


class _FakeInternalServer(GaosInternalServerError):
    def __init__(self, message: str = "server error") -> None:
        self.message = message


def _minimal_report() -> dict[str, object]:
    return {"top_k": 16, "query_set": [], "queries": [], "overall_verdict": "comparable overall"}


class PairRankedTests(unittest.TestCase):
    def test_same_chunk_rank_is_recorded_tie(self) -> None:
        pairs = make_pairs(["c1", "c2"], ["c1", "c2"])
        self.assertEqual([p.kind for p in pairs], ["same_chunk", "same_chunk"])
        self.assertEqual(pairs[0].rank, 1)
        self.assertEqual(pairs[0].lexical_chunk.chunk_id, "c1")
        self.assertEqual(pairs[0].embedding_chunk.chunk_id, "c1")

    def test_differing_rank_is_valid_pair(self) -> None:
        pairs = make_pairs(["c1"], ["c9"])
        self.assertEqual(pairs[0].kind, "valid")
        self.assertNotEqual(pairs[0].lexical_chunk.chunk_id, pairs[0].embedding_chunk.chunk_id)

    def test_shorter_rank_list_counts_unpaired(self) -> None:
        pairs = make_pairs(["c1", "c2"], ["c9", "c8", "c3", "c4"])
        kinds = [p.kind for p in pairs]
        self.assertEqual(len(pairs), 4)
        self.assertEqual(kinds, ["valid", "valid", "unpaired", "unpaired"])
        self.assertIsNone(pairs[2].lexical_chunk)
        self.assertEqual(pairs[2].embedding_chunk.chunk_id, "c3")


class JudgeCallTests(unittest.TestCase):
    def test_same_chunk_rank_causes_no_llm_call(self) -> None:
        lexical = response("q", [ranked("c1", 1, "shared text")])
        embedding = response("q", [ranked("c1", 1, "shared text")])
        client = PairwiseFakeClient({})
        report = evaluate_query(lexical, embedding, "q", client)
        self.assertEqual(client.calls, [])
        self.assertEqual(report["same_chunk_ties"], 1)
        self.assertEqual(report["valid_pairs"], 0)

    def test_differing_rank_causes_exactly_one_judge_call(self) -> None:
        lexical = response("q", [ranked("c1", 1, "lex text")])
        embedding = response("q", [ranked("c9", 1, "emb text")])
        client = BatchPairwiseFakeClient({"rank-1": "A"})
        evaluate_query(lexical, embedding, "q", client)
        self.assertEqual(len(client.calls), 1)

    def test_judge_pair_uses_schema_and_validates(self) -> None:
        lexical = response("q", [ranked("c1", 1, "lex text")])
        embedding = response("q", [ranked("c9", 1, "emb text")])
        client = PairwiseFakeClient({"rank-1": "B"})
        prompt = build_pairwise_prompt("q", lexical.ranked_chunks[0].chunk, embedding.ranked_chunks[0].chunk, "rank-1")
        verdict = judge_pair(client, prompt, "rank-1")
        self.assertIsInstance(verdict, PairwiseVerdict)
        self.assertEqual(verdict.winner, "B")

    def test_winner_mapping_respects_display_order(self) -> None:
        lexical = response("q", [ranked("c1", 1, "lex text")])
        embedding = response("q", [ranked("c9", 1, "emb text")])
        pairs = assign_display_orders(pair_ranked(lexical, embedding))
        displayed_as_a = pairs[0].display_as_a

        for winner in ("A", "B"):
            client = BatchPairwiseFakeClient({"pair-1": winner})
            report = evaluate_query(lexical, embedding, "q", client)
            expected = displayed_as_a if winner == "A" else ("embedding" if displayed_as_a == "lexical" else "lexical")
            self.assertEqual(report["pairs"][0]["winner"], expected)


class DeterminismTests(unittest.TestCase):
    def test_display_order_is_deterministic_for_same_seed(self) -> None:
        pairs = make_pairs(["c1", "c2", "c3", "c4"], ["c9", "c8", "c7", "c6"])
        first = assign_display_orders(pairs)
        second = assign_display_orders(pairs)
        self.assertEqual(
            [p.display_as_a for p in first if p.kind == "valid"],
            [p.display_as_a for p in second if p.kind == "valid"],
        )

    def test_seed_is_an_int_constant(self) -> None:
        self.assertIsInstance(FIXED_SEED, int)

    def test_prompt_contains_order_is_random_statement(self) -> None:
        prompt = build_pairwise_prompt("q", chunk("c1", "lex text"), chunk("c2", "emb text"), "rank-1")
        self.assertIn("randomized", prompt)
        self.assertIn("NO meaning", prompt)
        self.assertIn("Pair id: rank-1", prompt)

    def test_prompt_records_both_chunk_contexts(self) -> None:
        prompt = build_pairwise_prompt("q", chunk("c1", "lex text"), chunk("c2", "emb text"), "rank-1")
        self.assertIn("c1", prompt)
        self.assertIn("c2", prompt)
        self.assertIn("paper-1", prompt)
        self.assertIn('"page_start": 1', prompt)
        self.assertIn('"page_end": 1', prompt)


class AggregateQueryTests(unittest.TestCase):
    def test_fewer_than_three_valid_pairs_is_insufficient(self) -> None:
        pairs = make_pairs(["c1", "c2"], ["c9", "c8"])
        report = aggregate_query(pairs, {"rank-1": "embedding", "rank-2": "embedding"})
        self.assertEqual(report["verdict"], "insufficient")
        self.assertEqual(report["win_rate_embedding"], 1.0)

    def test_win_rate_ge_0_6_is_embedding_better(self) -> None:
        pairs = make_pairs(["c1", "c2", "c3"], ["c9", "c8", "c7"])
        report = aggregate_query(
            pairs, {"rank-1": "embedding", "rank-2": "embedding", "rank-3": "embedding"}
        )
        self.assertEqual(report["verdict"], "embedding better")
        self.assertEqual(report["win_rate_embedding"], 1.0)

    def test_win_rate_le_0_4_is_lexical_better(self) -> None:
        pairs = make_pairs(["c1", "c2", "c3"], ["c9", "c8", "c7"])
        # 2 embedding wins / 5 decided = 0.4 -> lexical better at the boundary.
        report = aggregate_query(
            pairs,
            {
                "rank-1": "lexical",
                "rank-2": "lexical",
                "rank-3": "lexical",
            },
        )
        self.assertEqual(report["verdict"], "lexical better")
        self.assertEqual(report["win_rate_embedding"], 0.0)

    def test_middle_win_rate_is_comparable(self) -> None:
        pairs = make_pairs(["c1", "c2", "c3"], ["c9", "c8", "c7"])
        report = aggregate_query(
            pairs, {"rank-1": "embedding", "rank-2": "lexical", "rank-3": "tie"}
        )
        self.assertEqual(report["verdict"], "comparable")
        self.assertEqual(report["win_rate_embedding"], 0.5)
        self.assertEqual(report["judge_ties"], 1)

    def test_judge_tie_excluded_from_denominator_but_counted(self) -> None:
        pairs = make_pairs(["c1", "c2", "c3"], ["c9", "c8", "c7"])
        report = aggregate_query(
            pairs, {"rank-1": "embedding", "rank-2": "lexical", "rank-3": "tie"}
        )
        # decided pairs = 2 (ties excluded); embedding = 1 -> win_rate 0.5.
        self.assertEqual(report["embedding_wins"], 1)
        self.assertEqual(report["lexical_wins"], 1)
        self.assertEqual(report["judge_ties"], 1)
        self.assertEqual(report["win_rate_embedding"], 0.5)

    def test_exact_0_6_boundary_is_embedding_better(self) -> None:
        pairs = make_pairs(["c1", "c2", "c3", "c4", "c5"], ["c9", "c8", "c7", "c6", "c5b"])
        report = aggregate_query(
            pairs,
            {
                "rank-1": "embedding",
                "rank-2": "embedding",
                "rank-3": "embedding",
                "rank-4": "lexical",
                "rank-5": "lexical",
            },
        )
        self.assertEqual(report["win_rate_embedding"], 0.6)
        self.assertEqual(report["verdict"], "embedding better")


class AggregateOverallTests(unittest.TestCase):
    def test_majority_of_decisive_verdicts_wins(self) -> None:
        reports = [
            {"verdict": "embedding better"},
            {"verdict": "embedding better"},
            {"verdict": "lexical better"},
            {"verdict": "comparable"},
        ]
        self.assertEqual(aggregate_overall(reports), "embedding better")

    def test_no_decisive_majority_is_comparable(self) -> None:
        reports = [{"verdict": "embedding better"}, {"verdict": "lexical better"}]
        self.assertEqual(aggregate_overall(reports), "comparable overall")

    def test_all_non_decisive_is_comparable(self) -> None:
        reports = [{"verdict": "comparable"}, {"verdict": "insufficient"}]
        self.assertEqual(aggregate_overall(reports), "comparable overall")


class RepairPathTests(unittest.TestCase):
    def test_malformed_json_repaired_exactly_once(self) -> None:
        lexical = response("q", [ranked("c1", 1, "lex text")])
        embedding = response("q", [ranked("c9", 1, "emb text")])
        client = BatchRepairOnceFakeClient({"pair-1": "A"})
        report = evaluate_query(lexical, embedding, "q", client)
        self.assertEqual(len(client.calls), 2)
        self.assertEqual(report["pairs"][0]["winner"], report["pairs"][0]["display_as_a"])


class MalformedForeverTests(unittest.TestCase):
    def test_persistent_malformed_json_propagates_error(self) -> None:
        lexical = response("q", [ranked("c1", 1, "lex text")])
        embedding = response("q", [ranked("c9", 1, "emb text")])
        client = BatchAlwaysMalformedFakeClient()
        with self.assertRaises(ValueError):
            evaluate_query(lexical, embedding, "q", client)


BUILTIN_QUERY_EXPECTED = (
    "literature review agent",
    "automatic tool that summarizes research papers",
    "writing a survey with help from AI",
    "an AI agent that writes and improves code",
    "agent",
    "software engineering automation with language models",
)


class DummyEncoder:
    """Deterministic fake encoder mirrored from tests/test_retrieval_eval.py."""

    def __call__(self, texts: list[str]) -> list[list[float]]:
        return [[0.0, float(len(text)), 0.0] for text in texts]


def cli_test_chunks() -> list[EvidenceChunk]:
    return [
        chunk("c1", "literature review agent evidence " * 10),
        chunk("c2", "token matching terms " * 8),
        chunk("c3", "survey writing assistance " * 7),
        chunk("c4", "code agent automation " * 6),
        chunk("c5", "software engineering aid " * 9),
        chunk("c6", "semantic retrieval queries " * 5),
    ]


class CliReportTests(unittest.TestCase):
    def test_parser_defaults(self) -> None:
        arguments = build_parser().parse_args(["data/papers"])
        self.assertEqual(arguments.top_k, 16)
        self.assertEqual(arguments.judge_model, "gemini-3.6-flash")
        self.assertIsNone(arguments.query)
        self.assertEqual(arguments.inputs, ["data/papers"])

    def test_parser_query_override(self) -> None:
        arguments = build_parser().parse_args(
            ["data/papers", "--query", "agent", "--query", "literature review agent"]
        )
        self.assertEqual(arguments.query, ["agent", "literature review agent"])

    def test_builtin_queries_exact(self) -> None:
        self.assertEqual(BUILTIN_QUERIES, BUILTIN_QUERY_EXPECTED)

    def test_build_report_with_fake_client_and_encoder(self) -> None:
        policy = EvidenceRetrievalPolicy(top_k=3)
        client = BatchPairwiseFakeClient({"pair-1": "A", "pair-2": "A", "pair-3": "A"})
        queries = ["literature review agent", "an AI agent that writes and improves code"]
        report = build_report(cli_test_chunks(), queries, policy, client, DummyEncoder())

        self.assertEqual(report["top_k"], 3)
        self.assertEqual(report["query_set"], queries)
        self.assertEqual(len(report["queries"]), 2)
        for per_query in report["queries"]:
            self.assertIn(per_query["verdict"], {"embedding better", "lexical better", "comparable", "insufficient"})
            self.assertIn("pairs", per_query)
            self.assertIn("valid_pairs", per_query)
            self.assertIn("win_rate_embedding", per_query)
        self.assertIn(report["overall_verdict"], {"embedding better", "lexical better", "comparable overall"})
        self.assertGreater(len(client.calls), 0)

    def test_main_missing_pdf_exits_1(self) -> None:
        with self.assertRaises(SystemExit) as context:
            main(["definitely-not-a-real.pdf"])
        self.assertEqual(context.exception.code, 1)


class PacingTests(unittest.TestCase):
    """Tests for the request_delay_seconds pacing parameter."""

    @mock.patch("literature_review.pairwise_eval.time.sleep")
    def test_judge_pair_no_sleep_by_default(self, mock_sleep: mock.MagicMock) -> None:
        client = PairwiseFakeClient({"rank-1": "A"})
        prompt = build_pairwise_prompt("q", chunk("c1", "lex"), chunk("c9", "emb"), "rank-1")
        judge_pair(client, prompt, "rank-1", request_delay_seconds=0)
        mock_sleep.assert_not_called()

    @mock.patch("literature_review.pairwise_eval.time.sleep")
    def test_judge_pair_sleeps_before_real_call(self, mock_sleep: mock.MagicMock) -> None:
        client = PairwiseFakeClient({"rank-1": "A"})
        prompt = build_pairwise_prompt("q", chunk("c1", "lex"), chunk("c9", "emb"), "rank-1")
        judge_pair(client, prompt, "rank-1", request_delay_seconds=3.5)
        mock_sleep.assert_called_once_with(3.5)

    @mock.patch("literature_review.pairwise_eval.time.sleep")
    def test_repair_path_sleeps_too(self, mock_sleep: mock.MagicMock) -> None:
        client = RepairOnceFakeClient()
        prompt = build_pairwise_prompt("q", chunk("c1", "lex"), chunk("c9", "emb"), "pair-1")
        judge_pair(client, prompt, "pair-1", request_delay_seconds=2.0)
        # Two real generate_json calls (first + repair), each preceded by sleep
        self.assertEqual(mock_sleep.call_count, 2)
        mock_sleep.assert_called_with(2.0)

    def test_main_reads_delay_env(self) -> None:
        with mock.patch.dict(os.environ, {"PAIRWISE_JUDGE_DELAY_S": "3.5"}):
            with mock.patch("literature_review.pairwise_eval.build_report") as mock_build:
                mock_build.return_value = {"top_k": 16, "query_set": [], "queries": [], "overall_verdict": "comparable overall"}
                # Need a valid PDF path that expand_pdf_inputs can resolve
                with mock.patch("literature_review.pairwise_eval.expand_pdf_inputs", return_value=["dummy.pdf"]):
                    with mock.patch("literature_review.pairwise_eval.extract_pdf_text") as mock_extract:
                        mock_extract.return_value = mock.MagicMock()
                        with mock.patch("literature_review.pairwise_eval.chunk_document", return_value=[chunk("c1", "text")]):
                            main(["dummy.pdf"])
                mock_build.assert_called_once()
                _, kwargs = mock_build.call_args
                self.assertEqual(kwargs["request_delay_seconds"], 3.5)

        # Invalid value → SystemExit(1)
        with mock.patch.dict(os.environ, {"PAIRWISE_JUDGE_DELAY_S": "abc"}):
            with self.assertRaises(SystemExit) as ctx:
                main(["dummy.pdf"])
            self.assertEqual(ctx.exception.code, 1)

    def test_cli_flag_sets_delay(self) -> None:
        with mock.patch("literature_review.pairwise_eval.build_report") as mock_build:
            mock_build.return_value = {"top_k": 16, "query_set": [], "queries": [], "overall_verdict": "comparable overall"}
            with mock.patch("literature_review.pairwise_eval.expand_pdf_inputs", return_value=["dummy.pdf"]):
                with mock.patch("literature_review.pairwise_eval.extract_pdf_text") as mock_extract:
                    mock_extract.return_value = mock.MagicMock()
                    with mock.patch("literature_review.pairwise_eval.chunk_document", return_value=[chunk("c1", "text")]):
                        main(["--judge-delay-s", "3.5", "dummy.pdf"])
            mock_build.assert_called_once()
            _, kwargs = mock_build.call_args
            self.assertEqual(kwargs["request_delay_seconds"], 3.5)

    def test_cli_flag_overrides_env(self) -> None:
        with mock.patch.dict(os.environ, {"PAIRWISE_JUDGE_DELAY_S": "1.0"}):
            with mock.patch("literature_review.pairwise_eval.build_report") as mock_build:
                mock_build.return_value = {"top_k": 16, "query_set": [], "queries": [], "overall_verdict": "comparable overall"}
                with mock.patch("literature_review.pairwise_eval.expand_pdf_inputs", return_value=["dummy.pdf"]):
                    with mock.patch("literature_review.pairwise_eval.extract_pdf_text") as mock_extract:
                        mock_extract.return_value = mock.MagicMock()
                        with mock.patch("literature_review.pairwise_eval.chunk_document", return_value=[chunk("c1", "text")]):
                            main(["--judge-delay-s", "2.0", "dummy.pdf"])
                mock_build.assert_called_once()
                _, kwargs = mock_build.call_args
                self.assertEqual(kwargs["request_delay_seconds"], 2.0)

    def test_rate_limit_error_caught_cleanly(self) -> None:
        class FakeRateLimit(GaosRateLimitError):
            def __init__(self, message: str) -> None:
                self.message = message

        import io
        from contextlib import redirect_stderr

        err = io.StringIO()
        with mock.patch("literature_review.pairwise_eval.build_report", side_effect=FakeRateLimit("boom")):
            with mock.patch("literature_review.pairwise_eval.expand_pdf_inputs", return_value=["dummy.pdf"]):
                with mock.patch("literature_review.pairwise_eval.extract_pdf_text") as mock_extract:
                    mock_extract.return_value = mock.MagicMock()
                    with mock.patch("literature_review.pairwise_eval.chunk_document", return_value=[chunk("c1", "text")]):
                        with redirect_stderr(err):
                            with self.assertRaises(SystemExit) as ctx:
                                main(["dummy.pdf"])
                self.assertEqual(ctx.exception.code, 1)
        captured: str = err.getvalue()
        self.assertIn("Pairwise eval failed:", captured)
        self.assertNotIn("Traceback", captured)


class BatchJudgeTests(unittest.TestCase):
    """Tests for the batched positional judge (judge_batch / build_batch_prompt)."""

    def test_build_batch_prompt_indexed(self) -> None:
        prompts = ["prompt A", "prompt B", "prompt C"]
        prompt = build_batch_prompt(prompts)
        self.assertIn("=== PAIRS ===", prompt)
        self.assertIn("INDEX 0", prompt)
        self.assertIn("INDEX 1", prompt)
        self.assertIn("INDEX 2", prompt)
        self.assertNotIn("pair-", prompt)
        self.assertLess(prompt.index("INDEX 0"), prompt.index("INDEX 1"))
        self.assertLess(prompt.index("INDEX 1"), prompt.index("INDEX 2"))

    def test_judge_batch_positional_alignment(self) -> None:
        client = BatchPairwiseFakeClient({"pair-a": "A", "pair-b": "tie"})
        prompts = [
            build_pairwise_prompt("q", chunk("c1", "lex"), chunk("c2", "emb"), "pair-a"),
            build_pairwise_prompt("q", chunk("c3", "lex"), chunk("c4", "emb"), "pair-b"),
        ]
        verdicts = judge_batch(client, prompts)
        self.assertEqual(len(verdicts), 2)
        self.assertEqual(verdicts[0].winner, "A")
        self.assertEqual(verdicts[1].winner, "tie")

    def test_judge_batch_repair(self) -> None:
        client = BatchRepairOnceFakeClient({"pair-a": "B"})
        prompts = [build_pairwise_prompt("q", chunk("c1", "lex"), chunk("c2", "emb"), "pair-a")]
        verdicts = judge_batch(client, prompts)
        self.assertEqual(len(client.calls), 2)
        self.assertEqual(verdicts[0].winner, "B")

    def test_judge_batch_count_mismatch_raises(self) -> None:
        class OneVerdictClient(BatchPairwiseFakeClient):
            def generate_json(self, prompt: str, schema: dict | None = None) -> str:
                self.calls.append(prompt)
                return json.dumps({"verdicts": [{"winner": "A", "rationale": "only one"}]})

        client = OneVerdictClient({})
        with self.assertRaises(LlmEvidenceError):
            judge_batch(client, ["prompt-a", "prompt-b"])

    def test_evaluate_query_batched_positional(self) -> None:
        class Verdict:
            def __init__(self, winner: str) -> None:
                self.winner = winner
                self.rationale = "r"

        lexical = response("q", [ranked("c1", 1, "lex text")])
        embedding = response("q", [ranked("c9", 1, "emb text")])
        with mock.patch("literature_review.pairwise_eval.judge_batch") as mock_judge:
            mock_judge.return_value = [Verdict("A")]
            report = evaluate_query(lexical, embedding, "q", object())
            mock_judge.assert_called_once()
            _, kwargs = mock_judge.call_args
            self.assertEqual(kwargs["request_delay_seconds"], 0.0)
            pair = report["pairs"][0]
            self.assertEqual(pair["winner"], pair["display_as_a"])


class ResumeCheckpointTests(unittest.TestCase):
    """Tests for the accumulating resume checkpoint (--checkpoint / --resume)."""

    def _write_prior(
        self,
        path: str,
        queries: list[str],
        completed: list[str],
        blocks: list[dict[str, object]],
    ) -> None:
        payload = {
            "top_k": 3,
            "query_set": list(queries),
            "completed": list(completed),
            "queries": blocks,
            "partial": len(completed) < len(queries),
        }
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)

    def test_build_report_writes_checkpoint(self) -> None:
        policy = EvidenceRetrievalPolicy(top_k=3)
        queries = ["q-one", "q-two"]
        client = BatchPairwiseFakeClient({"pair-1": "A", "pair-2": "A", "pair-3": "A"})
        with tempfile.TemporaryDirectory() as tmp:
            ckpt = os.path.join(tmp, "ckpt.json")
            build_report(cli_test_chunks(), queries, policy, client, DummyEncoder(), checkpoint_path=ckpt)
            self.assertTrue(os.path.exists(ckpt))
            with open(ckpt, encoding="utf-8") as fh:
                data = json.load(fh)
            self.assertEqual(data["completed"], ["q-one", "q-two"])
            self.assertFalse(data["partial"])
            self.assertEqual(len(data["queries"]), 2)
            self.assertFalse(os.path.exists(ckpt + ".tmp"))

    def test_checkpoint_contains_only_completed(self) -> None:
        class Interrupt(BatchPairwiseFakeClient):
            def __init__(self, winners: dict[str, str]) -> None:
                super().__init__(winners)
                self.calls_made = 0

            def generate_json(self, prompt: str, schema: dict | None = None) -> str:
                self.calls_made += 1
                if self.calls_made > 1:
                    raise RuntimeError("simulated stop after first query")
                return _batch_payload(prompt, self._winners)

        policy = EvidenceRetrievalPolicy(top_k=3)
        queries = ["q-one", "q-two"]
        client = Interrupt({"pair-1": "A", "pair-2": "A", "pair-3": "A"})
        with tempfile.TemporaryDirectory() as tmp:
            ckpt = os.path.join(tmp, "ckpt.json")
            with self.assertRaises(RuntimeError):
                build_report(cli_test_chunks(), queries, policy, client, DummyEncoder(), checkpoint_path=ckpt)
            with open(ckpt, encoding="utf-8") as fh:
                data = json.load(fh)
            self.assertEqual(data["completed"], ["q-one"])
            self.assertTrue(data["partial"])
            self.assertEqual(len(data["queries"]), 1)

    def test_main_resume_skips_completed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ckpt = os.path.join(tmp, "ckpt.json")
            prior_block = {"query": "q1", "verdict": "embedding better", "pairs": []}
            self._write_prior(ckpt, ["q1", "q2"], ["q1"], [prior_block])
            with mock.patch("literature_review.pairwise_eval.build_report") as mock_build:
                mock_build.return_value = {
                    "top_k": 3,
                    "query_set": ["q2"],
                    "queries": [{"query": "q2", "verdict": "comparable", "pairs": []}],
                    "overall_verdict": "comparable overall",
                }
                with mock.patch("literature_review.pairwise_eval.default_encoder", return_value=DummyEncoder()):
                    with mock.patch("literature_review.pairwise_eval.expand_pdf_inputs", return_value=["dummy.pdf"]):
                        with mock.patch("literature_review.pairwise_eval.extract_pdf_text") as mock_extract:
                            mock_extract.return_value = mock.MagicMock()
                            with mock.patch("literature_review.pairwise_eval.chunk_document", return_value=[chunk("c1", "text")]):
                                main(["--checkpoint", ckpt, "--resume", "--query", "q1", "--query", "q2", "dummy.pdf"])
                mock_build.assert_called_once()
                self.assertEqual(mock_build.call_args.args[1], ["q2"])

    def test_main_resume_requires_checkpoint(self) -> None:
        with mock.patch("literature_review.pairwise_eval.expand_pdf_inputs", return_value=["dummy.pdf"]):
            with mock.patch("literature_review.pairwise_eval.extract_pdf_text") as mock_extract:
                mock_extract.return_value = mock.MagicMock()
                with mock.patch("literature_review.pairwise_eval.chunk_document", return_value=[chunk("c1", "text")]):
                    with self.assertRaises(SystemExit) as ctx:
                        main(["--resume", "dummy.pdf"])
        self.assertEqual(ctx.exception.code, 1)

    def test_main_resume_all_done_exits_0(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ckpt = os.path.join(tmp, "ckpt.json")
            q1 = {"query": "q1", "verdict": "embedding better", "pairs": []}
            q2 = {"query": "q2", "verdict": "comparable", "pairs": []}
            self._write_prior(ckpt, ["q1", "q2"], ["q1", "q2"], [q1, q2])
            with mock.patch("literature_review.pairwise_eval.build_report") as mock_build:
                with mock.patch("literature_review.pairwise_eval.expand_pdf_inputs", return_value=["dummy.pdf"]):
                    with mock.patch("literature_review.pairwise_eval.extract_pdf_text") as mock_extract:
                        mock_extract.return_value = mock.MagicMock()
                        with mock.patch("literature_review.pairwise_eval.chunk_document", return_value=[chunk("c1", "text")]):
                            with self.assertRaises(SystemExit) as ctx:
                                main(["--checkpoint", ckpt, "--resume", "--query", "q1", "--query", "q2", "dummy.pdf"])
            self.assertEqual(ctx.exception.code, 0)
            mock_build.assert_not_called()

    def test_non_resume_removes_stale_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ckpt = os.path.join(tmp, "ckpt.json")
            self._write_prior(
                ckpt, ["q1", "q2"], ["q1"], [{"query": "q1", "verdict": "embedding better", "pairs": []}]
            )
            with mock.patch("literature_review.pairwise_eval.build_report") as mock_build:
                mock_build.return_value = {"top_k": 3, "query_set": [], "queries": [], "overall_verdict": "comparable overall"}
                with mock.patch("literature_review.pairwise_eval.default_encoder", return_value=DummyEncoder()):
                    with mock.patch("literature_review.pairwise_eval.expand_pdf_inputs", return_value=["dummy.pdf"]):
                        with mock.patch("literature_review.pairwise_eval.extract_pdf_text") as mock_extract:
                            mock_extract.return_value = mock.MagicMock()
                            with mock.patch("literature_review.pairwise_eval.chunk_document", return_value=[chunk("c1", "text")]):
                                main(["--checkpoint", ckpt, "dummy.pdf"])
            self.assertFalse(os.path.exists(ckpt))


class EmbeddingCacheTests(unittest.TestCase):
    def test_cached_embedding_reuses_precomputed_vectors(self) -> None:
        class CountingEncoder:
            def __init__(self) -> None:
                self.batch_sizes: list[int] = []

            def __call__(self, texts: list[str]) -> list[list[float]]:
                self.batch_sizes.append(len(texts))
                return [[0.0, float(len(t)), 0.0] for t in texts]

        encoder = CountingEncoder()
        policy = EvidenceRetrievalPolicy(top_k=3)
        chunks = cli_test_chunks()
        client = BatchPairwiseFakeClient({"pair-1": "A", "pair-2": "A", "pair-3": "A"})
        build_report(chunks, ["lit query", "agent query"], policy, client, encoder)
        full_batch_calls = encoder.batch_sizes.count(len(chunks))
        self.assertEqual(full_batch_calls, 1)

    def test_cached_retrieval_identical_to_non_cached(self) -> None:
        chunks = cli_test_chunks()
        query = "literature review agent"
        policy = EvidenceRetrievalPolicy(top_k=3)
        encoder = DummyEncoder()
        non_cached = retrieve_evidence_embedding(chunks, query, policy, encoder=encoder)
        query_vector = encode_query(query, encoder)
        chunk_vectors = encode_chunks(chunks, encoder)
        cached = retrieve_evidence_embedding_cached(chunks, query, policy, query_vector, chunk_vectors)
        self.assertEqual(
            [(r.chunk.chunk_id, r.score) for r in non_cached.ranked_chunks],
            [(r.chunk.chunk_id, r.score) for r in cached.ranked_chunks],
        )

    def test_encode_query_uses_prefix(self) -> None:
        class CaptureEncoder:
            def __call__(self, texts: list[str]) -> list[list[float]]:
                self.seen = list(texts)
                return [[0.0, float(len(t)), 0.0] for t in texts]

        encoder = CaptureEncoder()
        query = "literature review"
        encode_query(query, encoder)
        self.assertEqual(encoder.seen, [QUERY_PREFIX + query])

    def test_build_report_prints_progress(self) -> None:
        policy = EvidenceRetrievalPolicy(top_k=3)
        client = BatchPairwiseFakeClient({"pair-1": "A", "pair-2": "A", "pair-3": "A"})
        buffer = io.StringIO()
        with contextlib.redirect_stderr(buffer):
            build_report(cli_test_chunks(), ["lit query", "agent query"], policy, client, DummyEncoder())
        text = buffer.getvalue()
        self.assertIn("query 1/2: lit query", text)
        self.assertIn("query 1/2 done", text)
        self.assertIn("query 2/2: agent query", text)
        self.assertIn("query 2/2 done", text)


class RetryTests(unittest.TestCase):
    def test_retry_succeeds_after_transient_error(self) -> None:
        class FlakyBatch(BatchPairwiseFakeClient):
            def __init__(self, failures, error):
                super().__init__({"pair-1": "A"})
                self._failures = failures
                self._error = error
                self.failures_seen = 0

            def generate_json(self, prompt, schema=None):
                if self.failures_seen < self._failures:
                    self.failures_seen += 1
                    raise self._error
                return _batch_payload(prompt, self._winners)

        prompts = [build_pairwise_prompt("q", chunk("c1", "lex"), chunk("c2", "emb"), "pair-a")]
        client = FlakyBatch(2, _FakeRateLimit())
        with mock.patch("literature_review.pairwise_eval.time.sleep"):
            verdicts = judge_batch(client, prompts, request_delay_seconds=0.0)
        self.assertEqual(len(verdicts), 1)

    def test_retry_exhausts_then_raises(self) -> None:
        class Always500(BatchPairwiseFakeClient):
            def __init__(self):
                super().__init__({})
                self.calls = 0

            def generate_json(self, prompt, schema=None):
                self.calls += 1
                raise _FakeInternalServer()

        client = Always500()
        with mock.patch("literature_review.pairwise_eval.time.sleep"):
            with self.assertRaises(GaosInternalServerError):
                judge_batch(client, ["prompt"], request_delay_seconds=0.0)
        self.assertEqual(client.calls, 3)

    def test_retry_backoff_increases(self) -> None:
        client = ScriptedClient([_FakeRateLimit(), _FakeRateLimit(), '{"ok": true}'])
        with mock.patch("literature_review.pairwise_eval.time.sleep") as mock_sleep:
            result = _generate_json_with_retry(client, "p", None, request_delay_seconds=0.0)
        self.assertEqual(result, '{"ok": true}')
        self.assertEqual(mock_sleep.call_args_list, [mock.call(5.0), mock.call(10.0)])

    def test_success_path_no_retry_sleep(self) -> None:
        client = ScriptedClient(['{"ok": true}'])
        with mock.patch("literature_review.pairwise_eval.time.sleep") as mock_sleep:
            result = _generate_json_with_retry(client, "p", None, request_delay_seconds=0.0)
        self.assertEqual(result, '{"ok": true}')
        mock_sleep.assert_not_called()

    def test_main_internal_server_error_clean_exit(self) -> None:
        err = io.StringIO()
        with mock.patch(
            "literature_review.pairwise_eval.build_report", side_effect=_FakeInternalServer()
        ):
            with mock.patch("literature_review.pairwise_eval.GeminiJsonClient"):
                with mock.patch("literature_review.pairwise_eval.expand_pdf_inputs", return_value=["dummy.pdf"]):
                    with mock.patch("literature_review.pairwise_eval.extract_pdf_text") as mock_extract:
                        mock_extract.return_value = mock.MagicMock()
                        with mock.patch("literature_review.pairwise_eval.chunk_document", return_value=[chunk("c1", "text")]):
                            with contextlib.redirect_stderr(err):
                                with self.assertRaises(SystemExit) as ctx:
                                    main(["dummy.pdf"])
        self.assertEqual(ctx.exception.code, 1)
        self.assertIn("Pairwise eval failed:", err.getvalue())


class ApiKeySuffixTests(unittest.TestCase):
    def test_suffix_selects_correct_env_var(self) -> None:
        with mock.patch.dict(os.environ, {"GEMINI_API_KEY_2": "fake2"}):
            with mock.patch("literature_review.pairwise_eval.GeminiJsonClient") as mock_client:
                with mock.patch("literature_review.pairwise_eval.build_report", return_value=_minimal_report()):
                    with mock.patch("literature_review.pairwise_eval.expand_pdf_inputs", return_value=["dummy.pdf"]):
                        with mock.patch("literature_review.pairwise_eval.extract_pdf_text") as mock_extract:
                            mock_extract.return_value = mock.MagicMock()
                            with mock.patch("literature_review.pairwise_eval.chunk_document", return_value=[chunk("c1", "text")]):
                                main(["--api-key-suffix", "2", "dummy.pdf"])
        mock_client.assert_called_once()
        self.assertEqual(mock_client.call_args.kwargs["api_key"], "fake2")

    def test_no_suffix_uses_default_key(self) -> None:
        with mock.patch("literature_review.pairwise_eval.GeminiJsonClient") as mock_client:
            with mock.patch("literature_review.pairwise_eval.build_report", return_value=_minimal_report()):
                with mock.patch("literature_review.pairwise_eval.expand_pdf_inputs", return_value=["dummy.pdf"]):
                    with mock.patch("literature_review.pairwise_eval.extract_pdf_text") as mock_extract:
                        mock_extract.return_value = mock.MagicMock()
                        with mock.patch("literature_review.pairwise_eval.chunk_document", return_value=[chunk("c1", "text")]):
                            main(["dummy.pdf"])
        mock_client.assert_called_once()
        self.assertEqual(mock_client.call_args.kwargs["api_key"], None)

    def test_missing_suffix_raises(self) -> None:
        with mock.patch.dict(os.environ, {"GEMINI_API_KEY_2": ""}):
            with mock.patch("literature_review.pairwise_eval.expand_pdf_inputs", return_value=["dummy.pdf"]):
                with mock.patch("literature_review.pairwise_eval.extract_pdf_text") as mock_extract:
                    mock_extract.return_value = mock.MagicMock()
                    with mock.patch("literature_review.pairwise_eval.chunk_document", return_value=[chunk("c1", "text")]):
                        with self.assertRaises(SystemExit) as ctx:
                            main(["--api-key-suffix", "2", "dummy.pdf"])
        self.assertEqual(ctx.exception.code, 1)


if __name__ == "__main__":
    unittest.main()