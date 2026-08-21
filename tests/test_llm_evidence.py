import json
import unittest

from literature_review.llm_evidence import LlmEvidenceError, summarize_and_rerank
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

    def generate_json(self, prompt: str) -> str:
        self.prompt = prompt
        return json.dumps(self.response)


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
                        "relevance_score": 4,
                        "evidence_quality_score": 4,
                        "recommendation": "include",
                        "rationale": "It directly reports an evaluation result for the requested topic.",
                    },
                    {
                        "chunk_id": "chunk-1",
                        "summary": "The chunk describes a retrieval method relevant to evidence ranking.",
                        "relevance_score": 5,
                        "evidence_quality_score": 3,
                        "recommendation": "include",
                        "rationale": "It describes a method that directly addresses the requested topic.",
                    },
                ]
            }
        )

        result = summarize_and_rerank(retrieval_response(), client)

        self.assertIn("Research query", client.prompt)
        self.assertEqual([item.chunk_id for item in result.summaries], ["chunk-1", "chunk-2"])
        self.assertEqual(result.summaries[0].page_end, 3)

    def test_rejects_unmatched_or_missing_chunk_ids(self) -> None:
        client = FakeClient(
            {
                "assessments": [
                    {
                        "chunk_id": "unknown",
                        "summary": "An apparently valid summary that cannot be matched to a source chunk.",
                        "relevance_score": 3,
                        "evidence_quality_score": 3,
                        "recommendation": "consider",
                        "rationale": "The alleged source identity cannot be verified against retrieved evidence.",
                    }
                ]
            }
        )

        with self.assertRaises(LlmEvidenceError):
            summarize_and_rerank(retrieval_response(), client)


if __name__ == "__main__":
    unittest.main()
