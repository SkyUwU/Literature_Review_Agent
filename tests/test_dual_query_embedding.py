import math
import unittest

from literature_review.embedding_retriever import EmbeddingContext, QUERY_PREFIX
from literature_review.functional import sample_formal_chunks_per_paper
from literature_review.models import EvidenceChunk, Paper, RankedPaper
from literature_review.ranking import rank_papers_embedding


class VectorEncoder:
    def __init__(self):
        self.calls = []

    def __call__(self, texts):
        self.calls.extend(texts)
        return [[1.0, 0.0] if 'idea' in t else
                [0.6, 0.8] if 'core' in t else [0.0, 1.0] for t in texts]


def paper(name):
    return Paper(paper_id=name, title=name, abstract=name+' evidence description for testing', year=2025,
                 authors=['fixture'], url='https://example.org/' + name)


class DualQueryTests(unittest.TestCase):
    def test_dual_query_promotes_core_without_rejection(self):
        papers = [paper('transfer'), paper('core')]
        self.assertEqual(rank_papers_embedding(papers, 'sub', VectorEncoder())[0].paper.paper_id, 'transfer')
        ranked = rank_papers_embedding(papers, 'sub', VectorEncoder(), main_idea='idea')
        self.assertEqual([r.paper.paper_id for r in ranked], ['core', 'transfer'])
        c = ranked[0].score_components
        self.assertAlmostEqual(c.semantic_score, 0.7)
        self.assertAlmostEqual(ranked[0].score, round(c.semantic_score+c.citation_score+c.recency_score, 3))
        self.assertEqual((ranked[0].main_idea, ranked[0].source_query), ('idea', 'sub'))

    def test_weight_endpoints_and_same_query(self):
        papers = [paper('transfer'), paper('core')]
        old = rank_papers_embedding(papers, 'sub', VectorEncoder())
        zero = rank_papers_embedding(papers, 'sub', VectorEncoder(), main_idea='idea', idea_weight=0)
        same = rank_papers_embedding(papers, 'sub', VectorEncoder(), main_idea='sub')
        self.assertEqual([r.score for r in old], [r.score for r in zero])
        self.assertEqual([r.score for r in old], [r.score for r in same])
        one = rank_papers_embedding(papers, 'sub', VectorEncoder(), main_idea='idea', idea_weight=1)
        self.assertEqual(one[0].paper.paper_id, 'core')

    def test_run_cache_reuses_documents_and_queries(self):
        encoder = VectorEncoder()
        context = EmbeddingContext(encoder)
        for q in ['sub', 'other', 'idea']:
            rank_papers_embedding([paper('core')], q, encoder, main_idea='idea', embedding_context=context)
        self.assertEqual(encoder.calls.count(QUERY_PREFIX+'idea'), 1)
        self.assertEqual(encoder.calls.count('core\ncore evidence description for testing'), 1)

    def test_formal_sampling_dual_query_and_caps(self):
        chunks = [EvidenceChunk(chunk_id=f'{p}-{i}', paper_id=p, text=t+' evidence description for testing',
                                section='Method', source_path='fixture.pdf')
                  for p in ['p1', 'p2'] for i, t in enumerate(['transfer', 'core', 'transfer2'])]
        encoder = VectorEncoder()
        scoring, notes = sample_formal_chunks_per_paper(chunks, 'idea', encoder=encoder,
                                                       query_map={'p1':'sub','p2':'sub'})
        for p in scoring:
            self.assertTrue(scoring[p][0].text.startswith('core'))
            self.assertLessEqual(len(scoring[p]), 2)
            self.assertLessEqual(len(notes[p]), 9)
        self.assertEqual(encoder.calls.count(QUERY_PREFIX+'idea'), 1)
        self.assertEqual(encoder.calls.count(QUERY_PREFIX+'sub'), 1)
        self.assertEqual(len([t for t in encoder.calls if not t.startswith(QUERY_PREFIX)]), 6)

    def test_invalid_weights_and_old_json(self):
        for weight in [-1, 2, math.nan]:
            with self.assertRaises(ValueError):
                rank_papers_embedding([], 'sub', VectorEncoder(), main_idea='idea', idea_weight=weight)
        old = RankedPaper(paper=paper('core'), rank=1, score=1, matched_terms=[], rationale='old')
        self.assertIsNone(old.score_components)

