import json
import re
import unittest

from literature_review.models import (
    ChunkPolicy,
    EvidenceChunk,
    EvidenceRetrievalPolicy,
    FullTextDocument,
    PageText,
)
from literature_review.retrieval_eval import _compute_verdict, compare_retrieval


def chunk(chunk_id: str, text: str) -> EvidenceChunk:
    return EvidenceChunk(
        chunk_id=chunk_id,
        paper_id="paper-1",
        page_start=1,
        page_end=1,
        text=text,
    )


def assessment(chunk_id: str, relevance: int) -> dict[str, object]:
    return {
        "chunk_id": chunk_id,
        "summary": f"Chunk {chunk_id} provides relevant evidence for the review query.",
        "relevance_score": relevance,
        "evidence_quality_score": 3,
        "rationale": "The chunk directly discusses the requested literature review agent topic.",
    }


class ScoredFakeClient:
    """Fake LLM judge: returns a fixed relevance score per chunk id seen in a prompt."""

    def __init__(self, scores: dict[str, int]) -> None:
        self._scores = scores
        self.calls: list[str] = []

    def generate_json(self, prompt: str, schema: dict | None = None) -> str:
        self.calls.append(prompt)
        ids = re.findall(r'"chunk_id": "([^"]+)"', prompt)
        return json.dumps(
            {"assessments": [assessment(chunk_id, self._scores.get(chunk_id, 3)) for chunk_id in ids]}
        )


class DummyEncoder:
    """Deterministic fake encoder: longer text -> larger vector magnitude."""

    def __call__(self, texts: list[str]) -> list[list[float]]:
        return [[0.0, float(len(text)), 0.0] for text in texts]


def tests_chunks() -> list[EvidenceChunk]:
    return [
        chunk("c1", "word " * 30),
        chunk("c2", "term " * 25),
        chunk("c3", "phrase " * 20),
        chunk("c4", "sentence " * 15),
    ]


class CompareRetrievalTests(unittest.TestCase):
    def test_separate_calls_and_report_keys(self) -> None:
        chunks = tests_chunks()
        policy = EvidenceRetrievalPolicy(top_k=3)
        client = ScoredFakeClient({"c1": 5, "c2": 4, "c3": 3, "c4": 2})

        report = compare_retrieval(chunks, "literature review agent", policy, client, DummyEncoder())

        self.assertEqual(len(client.calls), 3)  # overlap + unique-lexical + unique-embedding
        self.assertIn("overlap", report)
        self.assertIn("only_lexical", report)
        self.assertIn("only_embedding", report)
        self.assertIn("avg_relevance_lexical", report)
        self.assertIn("avg_relevance_embedding", report)
        self.assertIn("verdict", report)

    def test_overlap_scored_once(self) -> None:
        chunks = tests_chunks()
        policy = EvidenceRetrievalPolicy(top_k=3)
        client = ScoredFakeClient({"c1": 5, "c2": 4, "c3": 3, "c4": 2})

        report = compare_retrieval(chunks, "literature review agent", policy, client, DummyEncoder())

        # Count total chunk assessments across all calls equals number of unique scored chunks.
        total_assessed = sum(
            len(re.findall(r'"chunk_id": "([^"]+)"', prompt)) for prompt in client.calls
        )
        self.assertEqual(total_assessed, len(report["overlap"]) + len(report["only_lexical"]) + len(report["only_embedding"]))

    def test_empty_group_skipped(self) -> None:
        # Force identical lexical and embedding top-k so there are no unique chunks.
        chunks = [chunk("c1", "literature review agent evidence " * 10)]
        policy = EvidenceRetrievalPolicy(top_k=1)
        client = ScoredFakeClient({"c1": 4})

        report = compare_retrieval(chunks, "literature review agent", policy, client, DummyEncoder())

        self.assertEqual(report["only_lexical"], [])
        self.assertEqual(report["only_embedding"], [])
        self.assertEqual(len(client.calls), 1)  # only the overlap group is judged
        # Both empty groups recorded as skipped.
        self.assertIn("only_lexical", report["skipped_groups"])
        self.assertIn("only_embedding", report["skipped_groups"])


class VerdictRuleTests(unittest.TestCase):
    def test_embedding_better_when_diff_ge_threshold(self) -> None:
        self.assertEqual(_compute_verdict(3.0, 4.0), "embedding better")  # diff = 1.0

    def test_lexical_better_when_diff_ge_threshold(self) -> None:
        self.assertEqual(_compute_verdict(4.0, 3.0), "lexical better")  # diff = -1.0

    def test_comparable_when_diff_below_threshold(self) -> None:
        self.assertEqual(_compute_verdict(3.0, 3.4), "comparable")  # diff = 0.4

    def test_exact_threshold_counts_as_better(self) -> None:
        self.assertEqual(_compute_verdict(3.0, 3.5), "embedding better")  # diff = 0.5

    def test_missing_method_is_flagged(self) -> None:
        self.assertIn("embedding better", _compute_verdict(None, 4.0))
        self.assertIn("lexical better", _compute_verdict(4.0, None))


if __name__ == "__main__":
    unittest.main()
