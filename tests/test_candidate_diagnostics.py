"""R1 early-stage snapshots and provenance flow; no real services or workspace output."""
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from literature_review.main import run_end_to_end, _search_and_rank
from literature_review.diagnostics import CandidateDiagnostics, safe_paper
from literature_review.models import PapersOutput, FilterPolicy
from literature_review.ranking import filter_papers
from literature_review.source_resolution import SourceLookupContext
from tests.test_main import FakePlanClient, FakeJsonFetcher, FakeScreenClient, QuotaScreenClient, record_for, results_payload
from tests.test_pipeline import FakeEncoder, PAPER_TEXT, make_document
from tests.test_dispositions import StableFakeClient
from tests.pdf_fixtures import pdf_fixture
from literature_review.llm_evidence import DailyQuotaExhausted
from tests.test_pdf_downloader import make_paper


class CandidateDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def saved(self):
        paths = list((self.root / 'out').glob('papers_*.json'))
        self.assertEqual(len(paths), 1)
        return PapersOutput.model_validate_json(paths[0].read_text(encoding='utf-8'))

    def run_flow(self, **kwargs):
        values = dict(dest_dir=self.root / 'pdfs', client_plan=FakePlanClient(3), client_screen=FakeScreenClient(),
                      client_synth=StableFakeClient(), encoder=FakeEncoder(),
                      json_fetcher=FakeJsonFetcher([results_payload(record_for('W1'))]),
                      pdf_fetcher=lambda _: pdf_fixture('Towards W1: automated literature review agents'),
                      diagnostics_output_dir=self.root / 'out')
        values.update(kwargs)
        with patch('literature_review.main.extract_pdf_text', side_effect=lambda path, pid: make_document(pid, PAPER_TEXT)), \
             patch('literature_review.pipeline._notes_pacing_seconds', return_value=0):
            return run_end_to_end('evidence selection', **values)

    def test_search_failure_saved_before_any_download(self):
        def fail(_):
            raise RuntimeError('secret key details')
        with self.assertRaises(RuntimeError):
            self.run_flow(json_fetcher=fail)
        saved = self.saved()
        self.assertEqual(saved.run['failed_stage'], 'search')
        self.assertEqual(saved.run['status'], 'failed')
        self.assertEqual(saved.papers, [])
        self.assertIsNone(saved.stage_summaries[0].counts['received'])
        self.assertNotIn('secret key', saved.model_dump_json())

    def test_screening_failure_retains_candidates_without_downloading(self):
        calls = []
        with self.assertRaises(DailyQuotaExhausted):
            self.run_flow(client_screen=QuotaScreenClient(DailyQuotaExhausted('quota')), pdf_fetcher=lambda u: calls.append(u))
        saved = self.saved()
        self.assertEqual(saved.run['failed_stage'], 'screening')
        self.assertTrue(saved.candidate_events)
        self.assertTrue(saved.candidate_records)
        self.assertFalse(calls)
        self.assertEqual(saved.paper_dispositions, [])

    def test_actual_source_reaches_report_and_same_snapshot(self):
        result = self.run_flow()
        saved = self.saved()
        self.assertEqual(saved.run['status'], 'completed')
        self.assertEqual(saved.run['run_id'], result['papers'].run['run_id'])
        self.assertEqual(saved.papers[0].fulltext.sha256, result['report'].paper_sources[0].fulltext.sha256)
        self.assertEqual(saved.papers[0].fulltext.version_match, 'unknown')
        self.assertTrue(saved.candidate_events)
        self.assertEqual(saved.stage_summaries[0].counts['download_valid'], 1)

    def test_follow_up_has_separate_instance_ids(self):
        fetch = FakeJsonFetcher([results_payload(record_for('W1')), results_payload(), results_payload(), results_payload(record_for('W2'))])
        result = self.run_flow(client_screen=FakeScreenClient('benchmark evaluation'), json_fetcher=fetch,
                              pdf_fetcher=lambda url: pdf_fixture('Towards W2: automated literature review agents' if 'W2' in url else 'Towards W1: automated literature review agents'))
        saved = self.saved()
        self.assertEqual([s.round for s in saved.stage_summaries], ['initial', 'initial', 'initial', 'follow_up'])
        self.assertEqual(len({s.query_id for s in saved.stage_summaries}), 4)
        self.assertEqual(len(result['downloads']), 2)

    def test_normalization_and_year_venue_reasons(self):
        old = record_for('old'); old['publication_year'] = 2020
        other = record_for('other')
        good = record_for('good'); good['primary_location']['source'] = {'display_name': 'ICLR', 'type': 'conference'}
        missing = {'id': 'missing', 'title': 'missing abstract'}
        collector = CandidateDiagnostics('topic')
        _search_and_rank('topic', json_fetcher=lambda _: results_payload(old, other, good, missing),
            encoder=FakeEncoder(), paper_meta={}, year_from=2024, venues=('iclr',), diagnostics=collector,
            source_context=SourceLookupContext(sleeper=lambda _: None))
        reasons = {reason for event in collector.output.candidate_events for reason in event.reason_codes}
        self.assertTrue({'year_before_min', 'venue_not_allowed', 'missing_abstract'} <= reasons)
        self.assertEqual(collector.current.counts['received'], 4)
        self.assertEqual(collector.current.counts['after_venue'], 1)

    def test_invalid_record_id_keeps_ordinal_without_fake_paper_id(self):
        collector = CandidateDiagnostics('topic')
        _search_and_rank('topic', json_fetcher=lambda _: results_payload({'id': {'wrong': 'shape'}}, 7),
                        encoder=FakeEncoder(), paper_meta={}, diagnostics=collector)
        self.assertEqual(collector.current.counts['received'], 2)
        self.assertEqual(collector.current.counts['normalized'], 0)
        self.assertTrue(all(e.paper_id is None for e in collector.output.candidate_events))
        self.assertEqual(len(collector.output.candidate_records), 2)

    def test_duplicate_title_policy_is_not_version_relation(self):
        a, b = make_paper('a'), make_paper('b')
        a.title = b.title = 'The same title'
        a.doi, b.doi = '10.1234/a', '10.1234/b'
        b.citation_count = 10
        c = CandidateDiagnostics('topic'); c.begin('topic')
        result = filter_papers([a, b], FilterPolicy(), observer=c.event)
        self.assertEqual(result[0].paper_id, 'b')
        self.assertIn('duplicate_title_policy', [r for e in c.output.candidate_events for r in e.reason_codes])
        self.assertFalse(result[0].version_relations)

    def test_year_and_venue_diagnostics_match_actual_ranked_representative(self):
        a, b = record_for('a'), record_for('b')
        a['title'] = b['title'] = 'The same candidate title'
        a['cited_by_count'] = 100
        b['primary_location']['source'] = {'display_name': 'ICLR', 'type': 'conference'}
        c = CandidateDiagnostics('topic')
        ranked = _search_and_rank('topic', json_fetcher=lambda _: results_payload(a, b),
            encoder=FakeEncoder(), paper_meta={}, year_from=2024, venues=('iclr',), diagnostics=c)
        self.assertEqual([i.paper.paper_id for i in ranked], ['b'])
        self.assertEqual(c.current.counts['after_year'], 2)
        self.assertEqual(c.current.counts['after_venue'], 1)
        self.assertFalse(any(e.paper_id == 'b' and e.action == 'excluded' for e in c.output.candidate_events))

    def test_source_lookup_does_not_change_screening_or_relevance(self):
        rec = record_for('W1', oa=False); rec['doi'] = 'https://doi.org/10.1234/work'
        lookup = {**rec, 'locations': [{'pdf_url': 'https://example.org/recovered.pdf'}]}
        calls = []
        def fetch(url):
            calls.append(url)
            return lookup if '/works/https' in url else results_payload(rec)
        screen = FakeScreenClient()
        result = self.run_flow(json_fetcher=fetch, client_screen=screen)
        self.assertEqual(len(screen.calls), 1)
        self.assertEqual(sum('/works/https' in u for u in calls), 1)
        self.assertEqual(result['papers'].run['source_counters']['new_source_lookups'], 1)
        self.assertIsNone(result['papers'].papers[0].paper.open_access_pdf_url)
        self.assertTrue(result['papers'].papers[0].fulltext)

    def test_library_opt_in_and_dry_run_do_not_save(self):
        result = self.run_flow(dry_run=True, client_plan=None, client_screen=None, client_synth=None,
                              use_llm_plan=False)
        self.assertFalse((self.root / 'out').exists())
        self.assertFalse(result['papers'].source_lookup_attempts)

    def test_candidate_registry_redacts_source_urls(self):
        rec = record_for('W1')
        rec['best_oa_location']['pdf_url'] += '?token=private'
        rec['locations'] = [{'pdf_url': 'https://example.org/p.pdf?email=private'}]
        result = self.run_flow(json_fetcher=FakeJsonFetcher([results_payload(rec)]))
        self.assertNotIn('private', self.saved().model_dump_json())
        self.assertNotIn('private', result['papers'].model_dump_json())

    def test_snapshot_storage_failure_is_explicit(self):
        with patch('literature_review.main.save_papers_output', side_effect=OSError('secret detail')), \
             patch('literature_review.diagnostics.sys.stderr') as stderr:
            result = self.run_flow()
        self.assertTrue(result['downloads'])
        text = ''.join(str(call.args[0]) for call in stderr.write.call_args_list)
        self.assertIn('Failed to save', text)
        self.assertNotIn('secret detail', text)

    def test_downstream_failure_keeps_early_and_download_evidence(self):
        def fail(*args, **kwargs):
            kwargs['diagnostics'].enter('synthesis')
            raise RuntimeError('private provider details')
        with patch('literature_review.main.pipeline.run_synthesis_pipeline', side_effect=fail):
            with self.assertRaises(RuntimeError):
                self.run_flow()
        saved = self.saved()
        self.assertEqual(saved.run['failed_stage'], 'synthesis')
        self.assertTrue(saved.candidate_events)
        self.assertTrue(saved.papers[0].fulltext)
        self.assertTrue(saved.paper_dispositions)
        self.assertNotIn('private provider', saved.model_dump_json())


if __name__ == '__main__':
    unittest.main()
