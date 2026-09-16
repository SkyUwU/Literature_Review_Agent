"""Tests for the M3C end-to-end entry: no real network, no API keys, no encoder build."""

import argparse
import contextlib
import io
import json
import math
import os
import re
import sys
import unittest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

import literature_review.main as main_module
from literature_review.embedding_retriever import QUERY_PREFIX
from literature_review.main import TOTAL_TARGET, run_end_to_end
from literature_review.models import (
    ChunkReference,
    FutureDirection,
    PaperSource,
    PaperSummary,
    PaperSummaryClaim,
    SynthesisResponse,
)
from literature_review.pdf_downloader import PdfDownloadError
from literature_review.planning import create_rule_based_plan

# ---------------------------------------------------------------------------
# Fake OpenAlex / PDF / LLM plumbing (mirrors tests/test_pipeline.py conventions)
# ---------------------------------------------------------------------------


def _inverted(text: str) -> dict[str, list[int]]:
    inverted: dict[str, list[int]] = {}
    for index, word in enumerate(text.split()):
        inverted.setdefault(word, []).append(index)
    return inverted


def record_for(paper_id: str, oa: bool = True) -> dict[str, object]:
    """One OpenAlex-style record that search.paper_from_openalex accepts."""
    return {
        "id": paper_id,
        "title": f"Towards {paper_id}: automated literature review agents",
        "abstract_inverted_index": _inverted(
            "This paper studies automated literature review generation with evidence selection."
        ),
        "publication_year": 2023,
        "authorships": [{"author": {"display_name": "A. Author"}}],
        "cited_by_count": 10,
        "primary_location": {"landing_page_url": f"https://example.org/landing/{paper_id}"},
        "best_oa_location": {"pdf_url": f"https://example.org/{paper_id}.pdf"} if oa else None,
    }


def results_payload(*records: dict[str, object]) -> dict[str, object]:
    return {"results": list(records)}


class FakeJsonFetcher:
    """Serves canned OpenAlex payloads in call order; records the request URLs."""

    def __init__(self, payloads: list[dict[str, object]]) -> None:
        self.payloads = list(payloads)
        self.calls: list[str] = []

    def __call__(self, url: str) -> dict[str, object]:
        self.calls.append(url)
        return self.payloads.pop(0) if self.payloads else {"results": []}


class FakePlanClient:
    """LLM plan client returning a validated SearchPlan with a chosen query count."""

    def __init__(self, query_count: int) -> None:
        self.query_count = query_count

    def generate_json(self, _prompt: str, schema: dict | None = None) -> str:
        return json.dumps(
            {
                "idea": "literature review agent",
                "queries": [
                    {
                        "query": f"literature review agent angle {index}",
                        "purpose": f"Covering aspect {index} of the requested review.",
                    }
                    for index in range(self.query_count)
                ],
                "perspectives": ["main"],
                "generated_by": "llm",
                "rationale": "LLM rationale justifying the planned search queries.",
            }
        )


class RaisePlanClient:
    """LLM plan client that always fails, exercising the rule-based fallback."""

    def generate_json(self, _prompt: str, schema: dict | None = None) -> str:
        raise RuntimeError("llm plan unavailable")


class FakeScreenClient:
    """Screening client: keeps every candidate listed in the prompt; one follow-up on first call."""

    def __init__(self, follow_up_query: str | None = None) -> None:
        self.follow_up_query = follow_up_query
        self.calls: list[str] = []

    def generate_json(self, prompt: str, schema: dict | None = None) -> str:
        self.calls.append(prompt)
        matches = re.findall(r"### \[(DOC_\d+)\] \d+ (.+)", prompt)
        first_call = len(self.calls) == 1
        follow_ups = []
        if first_call and self.follow_up_query:
            follow_ups = [
                {
                    "query": self.follow_up_query,
                    "target_gap": "missing benchmark family",
                    "reason": "fill the evaluation gap",
                }
            ]
        return json.dumps(
            {
                "decisions": [
                    {"doc_id": f"[{doc_id}]", "priority": "keep", "reason": "Directly relevant evidence."}
                    for doc_id, _ in matches
                ],
                "covered_areas": ["core topic"],
                "missing_pieces": ["benchmarks"] if follow_ups else [],
                "follow_up_queries": follow_ups,
            }
        )


class SpecialAlignedEncoder:
    """Fake encoder aligning the BGE-prefixed query with the W-special paper."""

    def __call__(self, texts: list[str]) -> list[list[float]]:
        return [
            [1.0, 0.0] if text.startswith(QUERY_PREFIX) or "W-special" in text else [0.0, 1.0]
            for text in texts
        ]


def assessment_payload(chunk_id: str) -> dict[str, object]:
    return {
        "chunk_id": chunk_id,
        "rationale": f"The chunk {chunk_id} supplies concrete evidence that directly advances the literature review agent research idea.",
        "utility_score": 10,
    }


def note_payload(paper_id: str) -> dict[str, object]:
    return {
        "claims": [
            {
                "text": f"The study in {paper_id} reports evidence selection results for review agents.",
                "chunk_ids": [f"{paper_id}-c1"],
                "aspect": "contribution",
            }
        ],
    }


def direction_payload(paper_id: str, claim_id: str) -> dict[str, object]:
    return {
        "title": "Harden multilingual evaluation coverage",
        "rationale": "Both studies state evaluation restrictions that motivate broader multilingual benchmarks.",
        "supporting_claim_ids": [claim_id],
    }


class SynthesisFakeClient:
    """Answers the three prompt kinds produced by the synthesis workflow."""

    def __init__(self, paper_ids: tuple[str, ...]) -> None:
        self.paper_ids = paper_ids
        self.prompts: list[str] = []
        self.cited_claim_ids: list[str] = []
        self.note_paper_ids: list[str] = []

    def generate_json(self, prompt: str, schema: dict | None = None) -> str:
        self.prompts.append(prompt)
        if prompt.startswith("Summarize this single paper"):
            for paper_id in self.paper_ids:
                if f"Paper ID: {paper_id}\n" in prompt:
                    self.note_paper_ids.append(paper_id)
                    return json.dumps(note_payload(paper_id))
        if prompt.startswith("Plan a thematic outline"):
            real_claim_ids = re.findall(r'"claim_id": "(claim-\d+)"', prompt)
            return json.dumps(
                {
                    "sections": [
                        {
                            "title": "Evidence and scope",
                            "purpose": "groups the supplied claims about evidence-cited review agents.",
                            "supporting_claim_ids": real_claim_ids,
                        }
                    ]
                }
            )
        if prompt.startswith("Write a fluent literature-review"):
            real_claim_ids = re.findall(r'"claim_id": "(claim-\d+)"', prompt)
            self.cited_claim_ids.extend(real_claim_ids)
            report = (
                "# Evidence-cited synthesis\n"
                + "".join(
                    f"The reviewed study supplies retrieved evidence for its claims "
                    f"in [{claim_id}].\n"
                    for claim_id in real_claim_ids
                )
            )
            return json.dumps({"report": report})
        if prompt.startswith("Propose future research directions"):
            cited = self.paper_ids[-1]
            claim_id = f"claim-{self.note_paper_ids.index(cited) + 1}"
            return json.dumps({"future_directions": [direction_payload(cited, claim_id)]})
        indexes = re.findall(r"## Chunk (\d+)", prompt)
        return json.dumps({"assessments": [assessment_payload(index) for index in indexes]})


PAGE_TEXT = " ".join(["literature", "review", "agent", "evidence", "selection"] * 12)


class FakePdfPage:
    def __init__(self, text: str) -> None:
        self.text = text

    def extract_text(self) -> str:
        return self.text


class FakePdfReader:
    is_encrypted = False

    def __init__(self, _path: Path) -> None:
        self.pages = [FakePdfPage(PAGE_TEXT)]


class FakeEncoder:
    def __call__(self, texts: list[str]) -> list[list[float]]:
        return [[0.1, 0.1 * len(text), 0.2] for text in texts]


def pdf_bytes(url: str) -> bytes:
    return b"%PDF-1.4 fake download"


class MainEntryTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.dest = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    # -- scenario 1: shared set deduplicates across queries ------------------

    def test_cross_query_duplicates_downloaded_once(self) -> None:
        payloads = [
            results_payload(record_for("W1"), record_for("W2")),
            results_payload(record_for("W1"), record_for("W2")),
        ]
        result = run_end_to_end(
            "literature review agent",
            dest_dir=self.dest,
            client_plan=FakePlanClient(2),
            use_llm_plan=True,
            dry_run=True,
            json_fetcher=FakeJsonFetcher(payloads),
            pdf_fetcher=pdf_bytes,
        )

        self.assertEqual(len(result["downloads"]), 2)
        self.assertEqual({entry["paper_id"] for entry in result["downloads"]}, {"W1", "W2"})
        self.assertEqual(result["stats_per_query"][0]["downloaded"], 2)
        self.assertEqual(result["stats_per_query"][1]["duplicate_reused"], 2)
        pdf_files = sorted(self.dest.glob("*.pdf"))
        self.assertEqual(len(pdf_files), 2)

    # -- scenario 2: a failed download backfills the next candidate ----------

    def test_download_failure_backfills_next_candidate(self) -> None:
        payloads = [
            results_payload(record_for("W1"), record_for("W2")),
            results_payload(record_for("W3")),
        ]

        def flaky_fetcher(url: str) -> bytes:
            if url.endswith("/W1.pdf"):
                raise PdfDownloadError("timeout while downloading the first paper")
            return b"%PDF-1.4 fake"

        result = run_end_to_end(
            "literature review agent",
            dest_dir=self.dest,
            client_plan=FakePlanClient(2),
            use_llm_plan=True,
            dry_run=True,
            json_fetcher=FakeJsonFetcher(payloads),
            pdf_fetcher=flaky_fetcher,
        )

        self.assertEqual(
            result["downloads"],
            [
                {"paper_id": "W2", "path": str(self.dest / "W2.pdf")},
                {"paper_id": "W3", "path": str(self.dest / "W3.pdf")},
            ],
        )
        self.assertEqual(result["stats_per_query"][0]["failed_network"], 1)
        self.assertEqual(result["stats_per_query"][0]["downloaded"], 1)
        self.assertEqual(result["stats_per_query"][1]["downloaded"], 1)

    # -- scenario 3: dry-run never calls the LLM or builds an encoder --------

    def test_dry_run_skips_llm_and_encoder(self) -> None:
        payloads = [
            results_payload(record_for("W1")),
            results_payload(record_for("W2")),
        ]
        synth_client = SynthesisFakeClient(("W1", "W2"))
        with mock.patch(
            "literature_review.embedding_retriever.default_encoder",
            side_effect=AssertionError("the encoder must not be built in dry-run mode"),
        ):
            result = run_end_to_end(
                "literature review agent",
                dest_dir=self.dest,
                client_plan=FakePlanClient(2),
                client_synth=synth_client,
                use_llm_plan=True,
                dry_run=True,
                json_fetcher=FakeJsonFetcher(payloads),
                pdf_fetcher=pdf_bytes,
            )

        self.assertIs(result["dry_run"], True)
        self.assertEqual(synth_client.prompts, [])
        self.assertNotIn("report", result)

    # -- scenario 4: full run produces a synthesis report --------------------

    def test_full_run_produces_report(self) -> None:
        payloads = [
            results_payload(record_for("W1")),
            results_payload(record_for("W2")),
        ]
        synth_client = SynthesisFakeClient(("W1", "W2"))
        with mock.patch("literature_review.extraction.PdfReader", FakePdfReader):
            with mock.patch(
            "literature_review.embedding_retriever.default_encoder", return_value=FakeEncoder()
        ):
                result = run_end_to_end(
                    "literature review agent",
                    dest_dir=self.dest,
                    client_plan=FakePlanClient(2),
                    client_synth=synth_client,
                    use_llm_plan=True,
                    dry_run=False,
                    json_fetcher=FakeJsonFetcher(payloads),
                    pdf_fetcher=pdf_bytes,
                )

        self.assertIs(result["dry_run"], False)
        self.assertEqual(result["failed_extractions"], [])
        report = result["report"]
        self.assertIsInstance(report, SynthesisResponse)
        self.assertEqual(len(report.paper_sources), 2)
        self.assertEqual(
            {source.paper_id for source in report.paper_sources},
            {"W1", "W2"},
        )
        self.assertTrue(
            all(source.claim_ids for source in report.paper_sources),
            "every resolved paper source carries claim-N tags",
        )
        self.assertEqual(
            {claim for source in report.paper_sources for claim in source.claim_ids},
            set(report.claim_chunks),
        )
        self.assertTrue(len(synth_client.prompts) >= 2)
        self.assertIn("[claim-", report.report)
        self.assertNotIn("## 材料來源清單", report.report)

    # -- scenario 5: target_n follows ceil(TOTAL_TARGET / query count) ------

    def test_target_n_follows_ceil_formula(self) -> None:
        # Pairs must both divide TOTAL_TARGET and supply >= target_n candidates
        # per query so downloads can actually reach TOTAL_TARGET.
        for query_count, papers_per_query in ((4, 5), (5, 4)):
            expected = math.ceil(TOTAL_TARGET / query_count)
            payloads = [
                results_payload(
                    *[
                        record_for(f"Q{query_count}q{query_index}W{index}")
                        for index in range(papers_per_query)
                    ]
                )
                for query_index in range(query_count)
            ]
            with self.subTest(query_count=query_count):
                result = run_end_to_end(
                    "literature review agent",
                    dest_dir=self.dest,
                    client_plan=FakePlanClient(query_count),
                    use_llm_plan=True,
                    dry_run=True,
                    json_fetcher=FakeJsonFetcher(payloads),
                    pdf_fetcher=pdf_bytes,
                )
                self.assertEqual(result["stats_per_query"][0]["requested"], expected)
                self.assertEqual(len(result["downloads"]), TOTAL_TARGET)

    # -- scenario 6: LLM plan failure falls back to the rule-based plan ------

    def test_llm_plan_failure_falls_back_to_rules(self) -> None:
        payloads = [results_payload() for _ in range(4)]  # rule-based plan has 4 queries
        result = run_end_to_end(
            "literature review agent",
            dest_dir=self.dest,
            client_plan=RaisePlanClient(),
            use_llm_plan=True,
            dry_run=True,
            json_fetcher=FakeJsonFetcher(payloads),
            pdf_fetcher=pdf_bytes,
        )

        self.assertEqual(result["plan"].generated_by, "rule_based")
        self.assertEqual(len(result["plan"].queries), 4)
        self.assertEqual(result["downloads"], [])

    # -- K milestone: injected encoder is honored (and skipped in dry-run) ---

    def test_dry_run_ignores_injected_encoder(self) -> None:
        payloads = [
            results_payload(record_for("W1")),
            results_payload(record_for("W2")),
        ]

        class RaiseOnCallEncoder:
            """Sentinel encoder that fails loudly if the dry-run path calls it."""

            def __call__(self, texts: list[str]) -> list[list[float]]:
                raise AssertionError("encoder must not be called in dry-run mode")

        result = run_end_to_end(
            "literature review agent",
            dest_dir=self.dest,
            client_plan=FakePlanClient(2),
            use_llm_plan=True,
            dry_run=True,
            json_fetcher=FakeJsonFetcher(payloads),
            pdf_fetcher=pdf_bytes,
            encoder=RaiseOnCallEncoder(),
        )

        self.assertIs(result["dry_run"], True)
        self.assertNotIn("report", result)

    def test_embedding_encoder_selects_otherwise_ranked_out_paper(self) -> None:
        # Six candidates per query: five ordinary papers plus W-special last.
        # Lexical ranking ties on every query term (same title pattern, same
        # abstract, same citations and year), so the stable sort keeps input
        # order and W-special is cut from the top-5 selection. The injected
        # encoder keys on the BGE query prefix (query call) and on "W-special"
        # in paper text, lifting W-special to rank 1. The embedding run must be
        # a real run because dry-run deliberately ignores the injected encoder.
        special = record_for("W-special")
        records = [record_for(f"W{index}") for index in range(1, 6)] + [special]
        payloads = [results_payload(*records) for _ in range(4)]

        with mock.patch("literature_review.extraction.PdfReader", FakePdfReader):
            with mock.patch(
                "literature_review.embedding_retriever.default_encoder",
                return_value=FakeEncoder(),
            ):
                embedding = run_end_to_end(
                    "literature review agent",
                    dest_dir=self.dest,
                    client_plan=FakePlanClient(4),
                    client_synth=SynthesisFakeClient(
                        ("W1", "W2", "W3", "W4", "W5", "W-special")
                    ),
                    use_llm_plan=True,
                    dry_run=False,
                    json_fetcher=FakeJsonFetcher(list(payloads)),
                    pdf_fetcher=pdf_bytes,
                    encoder=SpecialAlignedEncoder(),
                )
        lexical = run_end_to_end(
            "literature review agent",
            dest_dir=self.dest,
            client_plan=FakePlanClient(4),
            use_llm_plan=True,
            dry_run=True,
            json_fetcher=FakeJsonFetcher(list(payloads)),
            pdf_fetcher=pdf_bytes,
        )

        embedding_ids = {entry["paper_id"] for entry in embedding["downloads"]}
        lexical_ids = {entry["paper_id"] for entry in lexical["downloads"]}
        self.assertIn("W-special", embedding_ids)
        self.assertNotIn("W-special", lexical_ids)
        self.assertEqual(len(embedding_ids), 5)
        self.assertEqual(len(lexical_ids), 5)

        embedding_ids = {entry["paper_id"] for entry in embedding["downloads"]}
        lexical_ids = {entry["paper_id"] for entry in lexical["downloads"]}
        self.assertIn("W-special", embedding_ids)
        self.assertNotIn("W-special", lexical_ids)
        self.assertEqual(len(embedding_ids), 5)
        self.assertEqual(len(lexical_ids), 5)

    # -- M5e: screening client drives a merged keep/maybe download -------------

    def test_dry_run_with_screen_client_still_skips_screening(self) -> None:
        payloads = [
            results_payload(record_for("W1"), record_for("W2")),
            results_payload(record_for("W3")),
        ]
        screen_client = FakeScreenClient(follow_up_query="literature review agent benchmark")
        result = run_end_to_end(
            "literature review agent",
            dest_dir=self.dest,
            client_plan=FakePlanClient(2),
            client_screen=screen_client,
            use_llm_plan=True,
            dry_run=True,
            json_fetcher=FakeJsonFetcher(payloads),
            pdf_fetcher=pdf_bytes,
        )

        self.assertEqual(screen_client.calls, [])
        self.assertNotIn("screening", result)
        self.assertEqual(len(result["stats_per_query"]), 2)  # legacy per-query path
        self.assertEqual(len(result["downloads"]), 3)

    def test_no_screen_client_keeps_legacy_per_query_merging(self) -> None:
        payloads = [
            results_payload(record_for("W1"), record_for("W2")),
            results_payload(record_for("W3")),
        ]
        result = run_end_to_end(
            "literature review agent",
            dest_dir=self.dest,
            client_plan=FakePlanClient(2),
            use_llm_plan=True,
            dry_run=True,
            json_fetcher=FakeJsonFetcher(payloads),
            pdf_fetcher=pdf_bytes,
        )

        self.assertNotIn("screening", result)
        self.assertEqual(len(result["stats_per_query"]), 2)

    def test_screen_client_full_run_with_follow_up(self) -> None:
        screen_client = FakeScreenClient(follow_up_query="literature review agent benchmark")
        payloads = [
            results_payload(record_for("W1"), record_for("W2")),
            results_payload(record_for("W3")),
            results_payload(record_for("W4")),  # answered for the follow-up query
        ]
        synth_client = SynthesisFakeClient(("W1", "W2", "W3", "W4"))
        with mock.patch("literature_review.extraction.PdfReader", FakePdfReader):
            with mock.patch(
                "literature_review.embedding_retriever.default_encoder",
                return_value=FakeEncoder(),
            ):
                result = run_end_to_end(
                    "literature review agent",
                    dest_dir=self.dest,
                    client_plan=FakePlanClient(2),
                    client_synth=synth_client,
                    client_screen=screen_client,
                    use_llm_plan=True,
                    dry_run=False,
                    json_fetcher=FakeJsonFetcher(payloads),
                    pdf_fetcher=pdf_bytes,
                )

        self.assertEqual(len(screen_client.calls), 2)  # one screening + one follow-up round
        self.assertEqual(len(result["stats_per_query"]), 1)  # single merged download pass
        self.assertEqual(
            {entry["paper_id"] for entry in result["downloads"]},
            {"W1", "W2", "W3", "W4"},
        )
        self.assertEqual(
            result["follow_ups"],
            [
                {
                    "query": "literature review agent benchmark",
                    "target_gap": "missing benchmark family",
                    "reason": "fill the evaluation gap",
                }
            ],
        )
        self.assertIsNotNone(result["screening"])
        self.assertEqual(len(result["report"].paper_sources), 4)

    # -- failure path: every bad PDF aborts cleanly --------------------------

    def test_all_bad_pdfs_fail_cleanly_with_value_error(self) -> None:
        payloads = [
            results_payload(record_for("W1")),
            results_payload(record_for("W2")),
        ]
        with self.assertRaisesRegex(ValueError, "failed text extraction"):
            run_end_to_end(
                "literature review agent",
                dest_dir=self.dest,
                client_plan=FakePlanClient(2),
                client_synth=SynthesisFakeClient(("W1", "W2")),
                use_llm_plan=True,
                dry_run=False,
                json_fetcher=FakeJsonFetcher(payloads),
                pdf_fetcher=lambda url: b"not a pdf",
            )

    # -- main(): EOF input and missing key2 both exit cleanly ----------------

    def test_main_handles_eof_input_with_exit_1(self) -> None:
        with mock.patch("builtins.input", side_effect=EOFError):
            with mock.patch.object(sys, "argv", ["literature_review.main", "--dry-run"]):
                with self.assertRaises(SystemExit) as ctx:
                    main_module.main()
        self.assertEqual(ctx.exception.code, 1)

    def test_main_missing_key2_exits_before_synthesis(self) -> None:
        with mock.patch("builtins.input", return_value="literature review agent"):
            with mock.patch.object(sys, "argv", ["literature_review.main"]):
                with mock.patch.dict(os.environ, {"GEMINI_API_KEY_2": ""}, clear=False):
                    with self.assertRaises(SystemExit) as ctx:
                        main_module.main()
        self.assertEqual(ctx.exception.code, 1)

    # -- Amendment 1: LLM planner is the default; rule-based is the escape hatch --

    def test_run_end_to_end_default_planner_is_llm(self) -> None:
        payloads = [results_payload(record_for("W1")), results_payload(record_for("W2"))]
        result = run_end_to_end(
            "literature review agent",
            dest_dir=self.dest,
            client_plan=FakePlanClient(2),
            dry_run=True,
            json_fetcher=FakeJsonFetcher(payloads),
            pdf_fetcher=pdf_bytes,
        )
        self.assertEqual(result["plan"].generated_by, "llm")

    def test_build_clients_default_full_run_builds_plan_client(self) -> None:
        with mock.patch.dict(
            os.environ, {"GEMINI_API_KEY": "AIza000", "GEMINI_API_KEY_2": "AIza000", "GEMINI_API_KEY_3": "AIza000", "OLLAMA_BASE_URL": ""}, clear=False
        ):
            with mock.patch("literature_review.main.GeminiJsonClient") as client_cls:
                client_plan, client_synth, client_rcs, client_report = main_module._build_clients(
                    argparse.Namespace(rule_based=False, dry_run=False)
                )
        self.assertEqual(client_cls.call_count, 3)
        self.assertIsNotNone(client_plan)
        self.assertIsNotNone(client_synth)
        self.assertIsNone(client_rcs)  # OLLAMA_BASE_URL 未設定 → No fallback client
        self.assertIsNotNone(client_report)  # C2c: key3 專屬報告 client

    def test_build_clients_rule_based_skips_plan_client(self) -> None:
        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": "AIza000", "GEMINI_API_KEY_2": "AIza000", "GEMINI_API_KEY_3": "AIza000", "OLLAMA_BASE_URL": ""}, clear=False):
            with mock.patch("literature_review.main.GeminiJsonClient") as client_cls:
                client_plan, client_synth, client_rcs, client_report = main_module._build_clients(
                    argparse.Namespace(rule_based=True, dry_run=False)
                )
        self.assertEqual(client_cls.call_count, 2)
        self.assertIsNone(client_plan)
        self.assertIsNotNone(client_synth)
        self.assertIsNone(client_rcs)
        self.assertIsNotNone(client_report)

    def test_build_clients_missing_key1_falls_back_without_exit(self) -> None:
        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": "", "GEMINI_API_KEY_2": "AIza000", "GEMINI_API_KEY_3": "AIza000", "OLLAMA_BASE_URL": ""}, clear=False):
            with mock.patch("literature_review.main.GeminiJsonClient") as client_cls:
                client_plan, client_synth, client_rcs, client_report = main_module._build_clients(
                    argparse.Namespace(rule_based=False, dry_run=False)
                )
        self.assertIsNone(client_plan)
        self.assertIsNotNone(client_synth)
        self.assertIsNone(client_rcs)
        self.assertIsNotNone(client_report)

    def test_build_clients_dry_run_bypasses_all_keys(self) -> None:
        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": "", "GEMINI_API_KEY_2": "", "GEMINI_API_KEY_3": "", "OLLAMA_BASE_URL": ""}, clear=False):
            client_plan, client_synth, client_rcs, client_report = main_module._build_clients(
                argparse.Namespace(rule_based=False, dry_run=True)
            )
        self.assertIsNone(client_plan)
        self.assertIsNone(client_synth)
        self.assertIsNone(client_rcs)
        self.assertIsNone(client_report)

    def test_build_clients_ollama_configured_builds_rcs_client(self) -> None:
        """OLLAMA_BASE_URL 設定存在 → 第三 client 用 Ollama(RCS)，Gemini 仍只建 plan + synth + report 三個。"""
        with mock.patch.dict(
            os.environ,
            {
                "GEMINI_API_KEY": "AIza000",
                "GEMINI_API_KEY_2": "AIza000",
                "GEMINI_API_KEY_3": "AIza000",
                "OLLAMA_BASE_URL": "http://ollama:11434/v1",
            },
            clear=False,
        ):
            with mock.patch("literature_review.main.GeminiJsonClient") as client_cls:
                with mock.patch("literature_review.main.OllamaJsonClient") as ollama_cls:
                    client_plan, client_synth, client_rcs, client_report = main_module._build_clients(
                        argparse.Namespace(rule_based=False, dry_run=False)
                    )
        self.assertEqual(client_cls.call_count, 3)
        self.assertEqual(ollama_cls.call_count, 1)
        self.assertIsNotNone(client_plan)
        self.assertIsNotNone(client_synth)
        self.assertIsNotNone(client_rcs)
        self.assertIsNotNone(client_report)

    def test_build_clients_missing_key3_exits(self) -> None:
        with mock.patch.dict(
            os.environ,
            {"GEMINI_API_KEY": "", "GEMINI_API_KEY_2": "AIza000", "GEMINI_API_KEY_3": "", "OLLAMA_BASE_URL": ""},
            clear=False,
        ):
            with mock.patch("literature_review.main.GeminiJsonClient") as client_cls:
                with self.assertRaises(SystemExit) as ctx:
                    main_module._build_clients(
                        argparse.Namespace(rule_based=False, dry_run=False)
                    )
        self.assertEqual(ctx.exception.code, 1)
        self.assertEqual(client_cls.call_count, 1)  # 只建了 key2 synth，就因缺 key3 提早退出

    def test_main_dry_run_forces_rule_based(self) -> None:
        plan = create_rule_based_plan("literature review agent")
        fake_result = {"plan": plan, "downloads": [], "stats_per_query": [], "dry_run": True}
        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": "", "GEMINI_API_KEY_2": "", "OLLAMA_BASE_URL": ""}, clear=False):
            with mock.patch("builtins.input", return_value="literature review agent"):
                with mock.patch.object(sys, "argv", ["literature_review.main", "--dry-run"]):
                    with mock.patch("literature_review.main.run_end_to_end", return_value=fake_result) as m_run:
                        main_module.main()
        kwargs = m_run.call_args.kwargs
        self.assertIs(kwargs["use_llm_plan"], False)
        self.assertIsNone(kwargs["client_plan"])
        self.assertIsNone(kwargs["client_synth"])

    def test_main_rule_based_flag_forces_rule_based(self) -> None:
        plan = create_rule_based_plan("literature review agent")
        fake_result = {"plan": plan, "downloads": [], "stats_per_query": [], "dry_run": True}
        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": "", "GEMINI_API_KEY_2": "", "OLLAMA_BASE_URL": ""}, clear=False):
            with mock.patch("builtins.input", return_value="literature review agent"):
                with mock.patch.object(sys, "argv", ["literature_review.main", "--rule-based", "--dry-run"]):
                    with mock.patch("literature_review.main.run_end_to_end", return_value=fake_result) as m_run:
                        main_module.main()
        kwargs = m_run.call_args.kwargs
        self.assertIs(kwargs["use_llm_plan"], False)
        self.assertIsNone(kwargs["client_plan"])

    def test_main_full_run_saves_report_json(self) -> None:
        plan = create_rule_based_plan("literature review agent")
        report = fake_report()
        fake_result = {
            "plan": plan,
            "downloads": [{"paper_id": "W1", "path": str(self.dest / "W1.pdf")}],
            "stats_per_query": [],
            "failed_extractions": [],
            "report": report,
            "screening": None,
            "follow_ups": [],
            "dry_run": False,
        }
        real_save = main_module.save_report_output
        saved_path: str | None = None

        def save_to_tmp(rpt: object) -> str | None:
            nonlocal saved_path
            saved_path = real_save(rpt, output_dir=self.dest)
            return saved_path

        with mock.patch.dict(
            os.environ,
            {"GEMINI_API_KEY": "AIza000", "GEMINI_API_KEY_2": "AIza000", "GEMINI_API_KEY_3": "AIza000", "OLLAMA_BASE_URL": ""},
            clear=False,
        ):
            with mock.patch("builtins.input", return_value="literature review agent"):
                with mock.patch.object(sys, "argv", ["literature_review.main"]):
                    with mock.patch("literature_review.main.run_end_to_end", return_value=fake_result) as m_run:
                        with mock.patch("literature_review.main.save_report_output", side_effect=save_to_tmp):
                            with contextlib.redirect_stdout(io.StringIO()) as captured:
                                main_module.main()

        self.assertIsNotNone(saved_path)
        path = Path(saved_path)
        self.assertTrue(path.exists())
        with open(path, encoding="utf-8") as f:
            payload = json.load(f)
        self.assertEqual(payload["generated_by"], "llm")
        self.assertEqual(len(payload["paper_sources"]), 1)
        self.assertIn(f"Report saved to: {saved_path}", captured.getvalue())


def fake_report() -> SynthesisResponse:
    return SynthesisResponse(
        paper_sources=[PaperSource(paper_id="W1", source_path="data/papers/W1.pdf")],
        paper_summaries=[
            PaperSummary(
                paper_id="W1",
                claims=[
                    PaperSummaryClaim(
                        claim_id="claim-1",
                        text="W1 reports evidence selection results for review agents.",
                        aspect="contribution",
                        evidence=[
                            ChunkReference(
                                chunk_id="W1-c1",
                                paper_id="W1",
                                quote="Evidence selection results for review agents.",
                            )
                        ],
                    )
                ],
                coverage_chunk_ids=["W1-c1"],
            )
        ],
        claim_chunks={"claim-1": ["W1-c1"]},
        report=(
            "W1 is retained because its evidence directly advances the literature review "
            "agent research idea: retrieved chunks supply page-level provenance that "
            "supports evidence-cited synthesis."
        ),
        future_directions=[
            FutureDirection(
                title="Harden multilingual evaluation coverage",
                rationale="The retained study states evaluation restrictions that motivate broader multilingual benchmarks.",
                supporting_paper_ids=["W1"],
                supporting_claim_ids=["claim-1"],
            )
        ],
        limitations=[],
        generated_by="llm",
    )


class SaveReportOutputTests(unittest.TestCase):
    """Unit tests for main.save_report_output: file creation and failure fallback."""

    def test_save_report_output_creates_file(self) -> None:
        report = fake_report()
        ts = datetime(2026, 9, 16, 12, 0, 0, 123456)
        with TemporaryDirectory() as tmp:
            saved = main_module.save_report_output(
                report, output_dir=Path(tmp), timestamp=ts
            )

            self.assertIsNotNone(saved)
            self.assertTrue(saved.startswith(str(Path(tmp))))
            path = Path(saved)
            self.assertTrue(path.exists())
            with open(path, encoding="utf-8") as f:
                payload = json.load(f)
            self.assertEqual(payload, report.model_dump(mode="json"))
            self.assertEqual(path.name, "report_20260916_120000_123456.json")

    def test_save_report_output_returns_none_on_error(self) -> None:
        report = fake_report()
        with TemporaryDirectory() as tmp:
            # A file occupying the intended output directory path forces OSError
            blocker = Path(tmp) / "not-a-directory"
            blocker.write_text("", encoding="utf-8")
            with mock.patch("sys.stderr") as stderr:
                saved = main_module.save_report_output(
                    report,
                    output_dir=blocker,
                    timestamp=datetime(2026, 9, 16, 12, 0, 0, 123456),
                )
            written = "".join(call.args[0] for call in stderr.write.call_args_list)
            self.assertIn("Failed to save report JSON", written)

        self.assertIsNone(saved)


if __name__ == "__main__":
    unittest.main()
