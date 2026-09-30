"""Tests for the M5e LLM screening layer: no real network, no API keys.

Covers the single-call screening contract (doc-id resolution, global gap
analysis in the same call, unknown/hallucinated doc ids dropped with a warning),
the A/B/C diversity bucket sampling, the one-repair retry budget, and the
follow-up cap of three queries.
"""

import contextlib
import io
import json
import unittest
from datetime import datetime

from pydantic import HttpUrl

from literature_review.models import Paper, RankedPaper
from literature_review.screening import (
    SampledCandidates,
    ScreeningError,
    build_screening_prompt,
    sample_candidates,
    screen_candidates,
)


def _paper(
    paper_id: str,
    *,
    year: int = 2023,
    title: str | None = None,
    citations: int = 10,
) -> Paper:
    return Paper(
        paper_id=paper_id,
        title=title or f"Towards {paper_id}: automated literature review agents",
        authors=["A. Author"],
        year=year,
        abstract=(
            "This paper studies automated literature review generation with "
            "evidence selection and evaluation."
        ),
        url=HttpUrl(f"https://example.org/landing/{paper_id}"),
        venue="Test Venue",
        citation_count=citations,
        open_access_pdf_url=HttpUrl(f"https://example.org/{paper_id}.pdf"),
    )


def _ranked(
    paper_id: str,
    *,
    year: int = 2023,
    title: str | None = None,
    citations: int = 10,
    rank: int = 1,
) -> RankedPaper:
    return RankedPaper(
        paper=_paper(paper_id, year=year, title=title, citations=citations),
        rank=rank,
        score=2.0,
        matched_terms=["literature", "review"],
        rationale=f"Test ranking fixture for {paper_id}.",
    )


def _sampled(*items: RankedPaper) -> SampledCandidates:
    return SampledCandidates(papers=list(items), buckets={})


def _screening_payload(
    decisions: list[tuple[str, str]],
    *,
    covered: list[str] | None = None,
    missing: list[str] | None = None,
    follow_ups: list[dict[str, str]] | None = None,
) -> dict[str, object]:
    return {
        "decisions": [
            {"doc_id": doc_id, "priority": priority, "reason": "Relevant evidence for the topic."}
            for doc_id, priority in decisions
        ],
        "covered_areas": covered or ["core topic"],
        "missing_pieces": missing or [],
        "follow_up_queries": follow_ups or [],
    }


class FakeClipboardClient:
    """Screening client returning one canned raw JSON string per call."""

    def __init__(self, raw_outputs: list[str]) -> None:
        self.raw_outputs = list(raw_outputs)
        self.prompts: list[str] = []
        self.schemas: list[dict | None] = []

    def generate_json(self, prompt: str, schema: dict | None = None) -> str:
        self.prompts.append(prompt)
        self.schemas.append(schema)
        if not self.raw_outputs:
            raise AssertionError("no canned screening output left")
        return self.raw_outputs.pop(0)


class BucketSamplingTests(unittest.TestCase):
    def _pool(self, count: int) -> list[RankedPaper]:
        return [
            _ranked(
                f"W{index:02d}",
                year=2020 + (index % 5),
                citations=100 - index,
                rank=index + 1,
            )
            for index in range(count)
        ]

    def test_small_pool_below_target_returns_everything_unbucketed(self) -> None:
        pool = self._pool(5)
        sampled = sample_candidates(pool, per_query_target=24)
        self.assertEqual(len(sampled.papers), 5)
        self.assertEqual(
            [item.paper.paper_id for item in sampled.papers],
            [f"W{i:02d}" for i in range(5)],
        )
        self.assertEqual(sampled.buckets, {})

    def test_large_pool_samples_bounded_diverse_window_with_bucket_labels(self) -> None:
        pool = self._pool(40)
        sampled = sample_candidates(pool, per_query_target=24)
        ids = [item.paper.paper_id for item in sampled.papers]
        # Bucket A keeps the authority heads (rank <= 20%, citation <= 30%);
        # bucket B adds frontier papers (rank <= 50%, year >= 2023). In this
        # synthetic pool citations drop with rank, so bucket C (rank 40-70% AND
        # citation top 30%) is legitimately empty. The window must stay bounded,
        # unique, and sampled beyond the strict top of the rank order.
        self.assertLessEqual(len(sampled.papers), 24)
        self.assertEqual(len(set(ids)), len(ids))
        self.assertIn("W00", ids)
        self.assertIn("W19", ids)  # B bucket: rank 20 with year 2024
        self.assertGreater(len(ids), 6)
        # every emitted paper carries exactly one bucket label, aligned to the list
        self.assertEqual(set(sampled.buckets), set(ids))
        self.assertTrue(all(label in ("a", "b", "c") for label in sampled.buckets.values()))

    def test_sampling_is_deterministic(self) -> None:
        pool = self._pool(40)
        first = [item.paper.paper_id for item in sample_candidates(pool, per_query_target=24).papers]
        second = [item.paper.paper_id for item in sample_candidates(pool, per_query_target=24).papers]
        self.assertEqual(first, second)


class ScreeningContractTests(unittest.TestCase):
    def test_screen_resolves_doc_ids_to_papers_and_keeps_gap(self) -> None:
        candidates = {
            "literature review agent": _sampled(_ranked("W1"), _ranked("W2", rank=2)),
        }
        payload = _screening_payload(
            [
                ("[DOC_1]", "keep"),
                ("[DOC_2]", "reject"),
            ],
            covered=["core topic"],
            missing=["evaluation benchmarks"],
            follow_ups=[
                {
                    "query": "literature review agent benchmark",
                    "target_gap": "no benchmarks",
                    "reason": "fill evaluation gap",
                }
            ],
        )
        client = FakeClipboardClient([json.dumps(payload)])
        result = screen_candidates(candidates, client)

        self.assertEqual(
            {d.paper_id: d.priority for d in result.decisions["literature review agent"]},
            {"W1": "keep", "W2": "reject"},
        )
        self.assertEqual(
            {d.paper_title for d in result.decisions["literature review agent"]},
            {
                "Towards W1: automated literature review agents",
                "Towards W2: automated literature review agents",
            },
        )
        self.assertEqual(result.gap.covered_areas, ["core topic"])
        self.assertEqual(result.gap.missing_pieces, ["evaluation benchmarks"])
        self.assertEqual(len(result.gap.follow_up_queries), 1)
        self.assertEqual(
            result.gap.follow_up_queries[0].query, "literature review agent benchmark"
        )
        self.assertIsInstance(result.screened_at, datetime)

    def test_screen_requires_every_candidate_and_repairs_once(self) -> None:
        first = _screening_payload([("[DOC_1]", "keep")])
        del first["decisions"][0]  # drop one candidate -> resolution error
        repair = _screening_payload(
            [
                ("[DOC_1]", "keep"),
                ("[DOC_2]", "reject"),
            ]
        )
        client = FakeClipboardClient([json.dumps(first), json.dumps(repair)])
        candidates = {
            "literature review agent": _sampled(_ranked("W1"), _ranked("W2", rank=2)),
        }
        result = screen_candidates(candidates, client)

        self.assertEqual(len(client.prompts), 2)
        self.assertIn("repaired JSON", client.prompts[1])
        self.assertEqual(
            {d.paper_id: d.priority for d in result.decisions["literature review agent"]},
            {"W1": "keep", "W2": "reject"},
        )

    def test_screen_raises_when_repair_still_misses_candidates(self) -> None:
        incomplete = _screening_payload([("[DOC_1]", "keep")])
        also_incomplete = _screening_payload(
            [("[DOC_1]", "keep")]
        )
        client = FakeClipboardClient([json.dumps(incomplete), json.dumps(also_incomplete)])
        candidates = {
            "literature review agent": _sampled(_ranked("W1"), _ranked("W2", rank=2)),
        }
        with self.assertRaises(ScreeningError):
            screen_candidates(candidates, client)

    def test_screen_drops_unknown_doc_id_with_warning_and_keeps_known(self) -> None:
        candidates = {"literature review agent": _sampled(_ranked("W1"))}
        payload = _screening_payload(
            [
                ("[DOC_1]", "keep"),
                ("[DOC_99]", "keep"),  # hallucinated paper outside the list
            ]
        )
        client = FakeClipboardClient([json.dumps(payload)])
        buffer = io.StringIO()
        with contextlib.redirect_stderr(buffer):
            result = screen_candidates(candidates, client)
        self.assertEqual(
            {d.paper_id: d.priority for d in result.decisions["literature review agent"]},
            {"W1": "keep"},
        )
        self.assertIn("unknown document", buffer.getvalue())

    def test_screen_unknown_doc_id_cannot_replace_a_missing_candidate(self) -> None:
        candidates = {"literature review agent": _sampled(_ranked("W1"))}
        only_unknown = _screening_payload([("[DOC_9]", "keep")])
        client = FakeClipboardClient([json.dumps(only_unknown), json.dumps(only_unknown)])
        with self.assertRaises(ScreeningError):
            screen_candidates(candidates, client)

    def test_build_prompt_lists_doc_ids_and_hides_rank_and_score(self) -> None:
        candidates = {
            "literature review agent": _sampled(
                _ranked("W1", year=2024, citations=85, title="Alpha paper on review agents", rank=7)
            )
        }
        prompt = build_screening_prompt(candidates, main_query="How should evidence be mapped for a lit review agent?")
        self.assertIn("Alpha paper on review agents", prompt)
        self.assertIn('- [DOC_1] "Alpha paper on review agents" (2024, citations: 85)', prompt)
        self.assertIn("[DOC_1]", prompt)
        self.assertIn("## Query 1: literature review agent", prompt)
        self.assertIn("## Research topic", prompt)
        self.assertIn("How should evidence be mapped for a lit review agent?", prompt)
        self.assertIn("doc_id", prompt)
        self.assertIn("never output a document id not shown above", prompt)
        self.assertNotIn("rank", prompt.lower())
        self.assertNotIn("score", prompt.lower())

    def test_prompt_renders_citation_with_na_fallback(self) -> None:
        candidates = {
            "q1": _sampled(
                _ranked("W1", year=2024, citations=None),
                _ranked("W2", year=2023, citations=3),
            )
        }
        prompt = build_screening_prompt(candidates, main_query="main")
        self.assertIn("(2024, citations: N/A)", prompt)
        self.assertIn("(2023, citations: 3)", prompt)

    def test_prompt_groups_bucketed_candidates_under_category_headers(self) -> None:
        a = _ranked("W1", year=2020, citations=200, rank=1)
        b = _ranked("W2", year=2025, citations=2, rank=10)
        sampled = SampledCandidates(
            papers=[a, b],
            buckets={"W1": "a", "W2": "b"},
        )
        prompt = build_screening_prompt({"q": sampled}, main_query="main")
        self.assertIn("### Category A: Foundational (High-Impact / Baseline)", prompt)
        self.assertIn("### Category B: Frontier (Recent Frontier)", prompt)
        self.assertNotIn("Category C:", prompt)
        self.assertLess(prompt.index("Category A"), prompt.index("Category B"))

    def test_prompt_marks_unbucketed_and_empty_retrieval_queries(self) -> None:
        unbucketed = {"q1": _sampled(_ranked("W1"))}
        empty = {"q1": _sampled(_ranked("W1")), "q2": SampledCandidates(papers=[], buckets={})}
        prompt_u = build_screening_prompt(unbucketed)
        self.assertIn("### Un-bucketed", prompt_u)
        self.assertNotIn("Category A:", prompt_u)
        prompt_e = build_screening_prompt(empty)
        self.assertIn("### Empty retrieval", prompt_e)
        self.assertIn("propose a follow-up search", prompt_e)

    def test_prompt_doc_ids_are_global_across_queries_and_buckets(self) -> None:
        candidates = {
            "q1": SampledCandidates(papers=[_ranked("W1")], buckets={}),
            "q2": SampledCandidates(
                papers=[_ranked("W2"), _ranked("W3")],
                buckets={"W2": "a", "W3": "b"},
            ),
            "q3": SampledCandidates(papers=[], buckets={}),
        }
        prompt = build_screening_prompt(candidates, main_query="main")
        self.assertIn("[DOC_1]", prompt)
        self.assertIn("[DOC_2]", prompt)
        self.assertIn("[DOC_3]", prompt)
        self.assertNotIn("[DOC_4]", prompt)

    def test_prompt_global_guidance_and_output_instructions_last(self) -> None:
        candidates = {"q1": _sampled(_ranked("W1"))}
        prompt = build_screening_prompt(candidates, main_query="main")
        self.assertIn("Global review first", prompt)
        self.assertIn("avoid repeating the initial sub-queries", prompt)
        last_query = prompt.rfind("## Query")
        json_instruction = prompt.rfind("No Markdown, no explanation, no preamble")
        self.assertGreater(json_instruction, last_query)

    def test_prompt_omits_research_topic_when_main_query_none(self) -> None:
        candidates = {"q1": _sampled(_ranked("W1"))}
        self.assertNotIn("## Research topic", build_screening_prompt(candidates))
        self.assertIn("## Output", build_screening_prompt(candidates))

    def test_follow_up_cap_of_three_queries(self) -> None:
        candidates = {"q": _sampled(_ranked("W1"))}
        too_many = _screening_payload(
            [("[DOC_1]", "keep")],
            follow_ups=[
                {"query": f"follow up {i}", "target_gap": f"gap {i}", "reason": f"reason {i}"}
                for i in range(4)
            ],
        )
        repaired = _screening_payload(
            [("[DOC_1]", "keep")],
            follow_ups=[
                {"query": f"follow up {i}", "target_gap": f"gap {i}", "reason": f"reason {i}"}
                for i in range(3)
            ],
        )
        client = FakeClipboardClient([json.dumps(too_many), json.dumps(repaired)])
        result = screen_candidates(candidates, client)
        self.assertEqual(len(client.prompts), 2)  # schema cap -> one repair
        self.assertEqual(len(result.gap.follow_up_queries), 3)

    def test_every_candidate_must_receive_exactly_one_decision(self) -> None:
        candidates = {
            "literature review agent": _sampled(_ranked("W1"), _ranked("W2", rank=2)),
        }
        duplicated = _screening_payload(
            [
                ("[DOC_1]", "keep"),
                ("[DOC_1]", "keep"),
                ("[DOC_2]", "keep"),
            ]
        )
        client = FakeClipboardClient([json.dumps(duplicated), json.dumps(duplicated)])
        with self.assertRaises(ScreeningError):
            screen_candidates(candidates, client)


if __name__ == "__main__":
    unittest.main()