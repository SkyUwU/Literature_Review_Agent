import json
import unittest

from literature_review.models import ChunkPolicy, EvidenceRetrievalPolicy, FullTextDocument, PageText
from literature_review.pipeline import run_evidence_pipeline


class FakeClient:
    def generate_json(self, _prompt: str) -> str:
        return json.dumps(
            {
                "assessments": [
                    {
                        "chunk_id": "paper-1-p1-1-c1",
                        "summary": "This chunk provides evidence relevant to the literature review agent query.",
                        "relevance_score": 5,
                        "evidence_quality_score": 3,
                        "recommendation": "include",
                        "rationale": "The chunk directly discusses the requested literature review agent topic.",
                    }
                ]
            }
        )


class PipelineTests(unittest.TestCase):
    def test_runs_full_flow_with_fake_client(self) -> None:
        document = FullTextDocument(
            paper_id="paper-1",
            source_path="data/papers/paper-1.pdf",
            extraction_method="test",
            pages=[
                PageText(
                    page_number=1,
                    text=" ".join(["literature", "review", "agent", "evidence"] * 20),
                )
            ],
        )

        result = run_evidence_pipeline(
            document,
            "literature review agent",
            FakeClient(),
            chunk_policy=ChunkPolicy(max_words=50, overlap_words=10),
            retrieval_policy=EvidenceRetrievalPolicy(top_k=1),
        )

        self.assertEqual(result.summaries[0].paper_id, "paper-1")
        self.assertEqual(result.summaries[0].page_start, 1)
