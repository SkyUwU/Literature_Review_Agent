"""R1 offline contracts: identity is not revision, and availability is not relevance."""
import json
import socket
import ssl
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.error import HTTPError, URLError
from unittest.mock import patch

from literature_review.models import Paper, SourceRecoveryPolicy, VersionTarget, PapersOutput
from literature_review.source_resolution import (SourceLookupContext, metadata_sources, derived_locations,
    source_locations, attach_lookup, arxiv_identifier, identifier_from_url, classify_error, retry_delay)
from literature_review.pdf_fetch import FetchResult, PdfDownloadError, recover_pdf, inspect_fulltext
from literature_review.pdf_downloader import download_and_backfill
from literature_review.ss_search import paper_from_ss
from literature_review.search import paper_from_openalex
from tests.pdf_fixtures import pdf_fixture
from tests.test_pdf_downloader import make_paper, rank


def ss_record(arxiv='2401.12345v2', doi='10.1234/published'):
    return dict(paperId='S1', title='A careful study of language models', authors=[{'name': 'Alice Smith'}],
                abstract='This study evaluates language models under controlled conditions.', year=2025,
                externalIds={'DOI': doi, 'ArXiv': arxiv}, url='https://example.org/paper')


def oa_record(paper, urls=()):
    return dict(id='https://openalex.org/W1', doi='https://doi.org/' + paper.doi, title=paper.title,
                authorships=[{'author': {'display_name': paper.authors[0]}}],
                publication_year=2025, abstract_inverted_index={'Controlled': [0], 'study': [1], 'of': [2], 'language': [3], 'models': [4]},
                locations=[dict(pdf_url=url, version='publishedVersion') for url in urls])


class SourceMetadataTests(unittest.TestCase):
    def test_ss_retains_arxiv_and_original_doi(self):
        p = paper_from_ss(ss_record())
        arxiv = next(x for x in p.identifiers if x.scheme == 'arxiv')
        self.assertEqual((arxiv.value, arxiv.revision), ('2401.12345', 'v2'))
        self.assertEqual(p.doi, '10.1234/published')
        self.assertEqual(p.version_target.revision, 'v2')
        self.assertEqual(p.version_relations[0].status, 'confirmed')

    def test_openalex_retains_all_locations(self):
        p = paper_from_ss(ss_record())
        p2 = paper_from_openalex(oa_record(p, ['https://a.example/p.pdf', 'https://b.example/p.pdf']))
        self.assertEqual(len(p2.fulltext_locations), 2)
        self.assertIsNone(p2.open_access_pdf_url)

    def test_lookup_preserves_original_bibliography(self):
        p = paper_from_ss(ss_record(arxiv=None))
        before = (p.doi, p.year, p.title, p.venue, p.open_access_pdf_url)
        rec = oa_record(p, ['https://arxiv.org/pdf/2401.12345v3'])
        rec['publication_year'] = 2020
        self.assertTrue(attach_lookup(p, rec))
        self.assertEqual(before, (p.doi, p.year, p.title, p.venue, p.open_access_pdf_url))
        self.assertTrue(p.version_relations)

    def test_lookup_conflicting_identity_does_not_attach(self):
        for field in ('doi', 'title', 'authorships'):
            p = paper_from_ss(ss_record(arxiv=None))
            rec = oa_record(p, ['https://other.example/p.pdf'])
            rec[field] = [] if field == 'authorships' else 'different'
            self.assertFalse(attach_lookup(p, rec))
            self.assertFalse(p.fulltext_locations)

    def test_arxiv_id_formats_and_invalid_strings(self):
        for value in ['2401.12345v2', 'hep-th/9901001v1', '10.48550/arXiv.2401.12345']:
            self.assertIsNotNone(arxiv_identifier(value))
        for value in ['Title arXiv 2401.12345', '2401.12345junk', None, '2401.12345v0']:
            self.assertIsNone(arxiv_identifier(value))

    def test_official_urls_only(self):
        self.assertIsNone(identifier_from_url('https://fake.example/pdf/2401.12345v2', 'x', 'url'))
        self.assertIsNone(identifier_from_url('https://arxiv.org.evil.example/pdf/2401.12345', 'x', 'url'))
        self.assertEqual(identifier_from_url('https://aclanthology.org/2025.acl-long.1.pdf', 'x', 'url').value, '2025.acl-long.1')
        self.assertEqual(identifier_from_url('https://openreview.net/pdf?id=abcXYZ', 'x', 'url').scheme, 'openreview')

    def test_derived_arxiv_doi(self):
        p = make_paper('W1', None)
        p.doi = '10.48550/arxiv.2401.12345'
        self.assertEqual(derived_locations(p)[0].pdf_url, 'https://arxiv.org/pdf/2401.12345')

    def test_legacy_json_new_fields_default(self):
        p = make_paper('W1', None)
        values = p.model_dump()
        for field in ('identifiers', 'version_target', 'version_relations', 'fulltext_locations'):
            values.pop(field)
        self.assertFalse(Paper.model_validate(values).identifiers)
        self.assertEqual(PapersOutput.model_validate({'run': {}, 'papers': []}).candidate_events, [])

    def test_malformed_optional_metadata_does_not_remove_valid_paper(self):
        rec = ss_record(); rec['externalIds'] = []; rec['openAccessPdf'] = []
        self.assertIsNotNone(paper_from_ss(rec))
        self.assertFalse(paper_from_ss(rec).fulltext_locations)
        from literature_review.main import backfill_abstracts
        from literature_review.ss_search import ABSTRACT_PLACEHOLDER
        p = paper_from_ss(ss_record(arxiv=None)); p.abstract = ABSTRACT_PLACEHOLDER
        backfill_abstracts([p], json_fetcher=lambda _: {'abstract_inverted_index': ['wrong shape']})
        self.assertEqual(p.abstract, ABSTRACT_PLACEHOLDER)

    def test_duplicate_url_preserves_provenance_and_conflicting_versions(self):
        p = paper_from_ss(ss_record(arxiv=None))
        rec = oa_record(p, ['https://x.example/p.pdf'] * 3)
        rec['locations'][1]['version'] = 'submittedVersion'
        attach_lookup(p, rec)
        locs = source_locations(p)
        self.assertEqual(len(locs), 1)
        self.assertTrue(locs[0].version_conflicting)
        self.assertIsNone(locs[0].version)
        self.assertEqual(len(locs[0].provenance), 3)


class LookupBudgetTests(unittest.TestCase):
    def setUp(self):
        self.p = paper_from_ss(ss_record(arxiv=None))
        self.sleeps = []
        self.context = SourceLookupContext(sleeper=self.sleeps.append)

    def test_abstract_lookup_reused_for_source(self):
        calls = []
        def fetch(url):
            calls.append(url)
            return oa_record(self.p)
        self.context.lookup_openalex(self.p, fetch, phase='abstract_backfill')
        self.context.lookup_openalex(self.p, fetch)
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.context.new_lookups, 0)
        self.assertEqual(self.context.abstract_transports, 1)
        self.assertEqual(self.context.recovery_transports, 0)
        self.assertTrue(self.context.lookup_attempts[-1].cache_hit)

    def test_negative_404_cache_not_retried(self):
        calls = []
        def fetch(url):
            calls.append(url)
            raise HTTPError(url, 404, 'not found', {}, None)
        self.assertIsNone(self.context.lookup_openalex(self.p, fetch))
        self.assertIsNone(self.context.lookup_openalex(self.p, fetch))
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.context.lookup_attempts[-1].reason_code, 'lookup_not_found')
        self.assertEqual(self.sleeps, [])

    def test_logical_lookup_boundary(self):
        self.context.policy.new_source_lookups_per_run = 1
        self.context.lookup_openalex(self.p, lambda _: oa_record(self.p))
        other = self.p.model_copy(update={'doi': '10.1234/other', 'paper_id': 'S2'})
        self.assertIsNone(self.context.lookup_openalex(other, lambda _: self.fail('budget bypass')))
        self.assertEqual(self.context.new_lookups, 1)
        self.assertEqual(self.context.lookup_attempts[-1].budget_scope, 'new_source_lookups')

    def test_retry_last_slot_does_not_sleep_or_call(self):
        self.context.policy.recovery_transports_per_run = 1
        attempts = []
        def fail(_):
            raise TimeoutError()
        with self.assertRaises(PdfDownloadError):
            self.context.request('https://x.example', fail, recovery=True, on_attempt=attempts.append)
        self.assertEqual(self.context.recovery_transports, 1)
        self.assertEqual(attempts[-1]['reason_code'], 'budget_exhausted')
        self.assertEqual(self.sleeps, [])

    def test_timeout_retry_attempt_indexes_and_elapsed(self):
        calls = []
        attempts = []
        def fetch(_):
            calls.append(1)
            if len(calls) < 3:
                raise TimeoutError()
            return b'ok'
        self.assertEqual(self.context.request('https://x.example', fetch, recovery=True, on_attempt=attempts.append), b'ok')
        self.assertEqual([a['attempt_index'] for a in attempts], [1, 2, 3])
        self.assertEqual(self.sleeps, [1, 2])
        self.assertEqual(self.context.recovery_transports, 3)
        self.assertTrue(all(a['elapsed_ms'] >= 0 for a in attempts))

    def test_http_retry_policy(self):
        for status, expected in [(403, 1), (404, 1), (429, 3), (500, 3), (501, 1), (502, 3), (503, 3), (504, 3)]:
            ctx = SourceLookupContext(sleeper=lambda _: None)
            calls = []
            def fetch(_):
                calls.append(1)
                return FetchResult(b'', http_status=status)
            with self.assertRaises(PdfDownloadError):
                ctx.request('https://x.example', fetch, recovery=True, on_attempt=lambda _: None)
            self.assertEqual(len(calls), expected, status)

    def test_retry_after_too_long_deferred(self):
        attempts = []
        with self.assertRaises(PdfDownloadError):
            self.context.request('https://x.example', lambda _: FetchResult(b'', http_status=429, retry_after='60'),
                                 recovery=True, on_attempt=attempts.append)
        self.assertEqual(self.context.recovery_transports, 1)
        self.assertEqual(attempts[-1]['reason_code'], 'retry_deferred')
        self.assertFalse(self.sleeps)

    def test_network_classifications(self):
        for error, kind, retry in [
            (URLError(socket.gaierror(socket.EAI_AGAIN, 'temporary')), 'dns', True),
            (URLError(socket.gaierror(socket.EAI_NONAME, 'permanent')), 'dns', False),
            (URLError(ssl.SSLCertVerificationError('private detail')), 'tls_certificate', False),
            (URLError(ssl.SSLError('private detail')), 'tls_transport', True),
            (URLError(TimeoutError()), 'timeout', True), (ConnectionResetError(), 'connection', True),
            (URLError('unknown secret'), 'unknown', False),
        ]:
            self.assertEqual((classify_error(error)[0], classify_error(error)[2]), (kind, retry))

    def test_date_retry_after(self):
        from datetime import datetime, timedelta, timezone
        from email.utils import format_datetime
        value = format_datetime(datetime.now(timezone.utc) + timedelta(seconds=10))
        self.assertGreater(retry_delay(value, 0), 8)
        self.assertLessEqual(retry_delay(value, 0), 10)

    def test_unknown_failure_cached_without_retries(self):
        calls = []
        def fetch(_):
            calls.append(1)
            raise URLError('secret details')
        self.context.lookup_openalex(self.p, fetch)
        self.context.lookup_openalex(self.p, fetch)
        self.assertEqual(len(calls), 1)
        self.assertNotIn('secret', json.dumps([a.model_dump() for a in self.context.lookup_attempts]))

    def test_transport_budget_exhaustion_saved_before_first_fetch(self):
        self.context.policy.recovery_transports_per_run = 0
        self.assertIsNone(self.context.lookup_openalex(self.p, lambda _: self.fail('unexpected transport')))
        self.assertEqual(self.context.recovery_transports, 0)
        self.assertTrue(any(a.budget_scope == 'recovery_transports' for a in self.context.lookup_attempts))

    def test_default_fetcher_dns_and_tls_classification(self):
        from literature_review.pdf_fetch import default_fetcher
        for cause, kind in [(socket.gaierror(socket.EAI_AGAIN, 'private'), 'dns'),
                            (ssl.SSLCertVerificationError('private'), 'tls_certificate')]:
            with patch('literature_review.pdf_fetch.build_opener') as build:
                build.return_value.open.side_effect = URLError(cause)
                with self.assertRaises(PdfDownloadError) as caught:
                    default_fetcher('https://example.org/p.pdf')
                self.assertEqual(caught.exception.error_kind, kind)
                self.assertNotIn('private', str(caught.exception))


class VersionDownloadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'paper.pdf'
        self.p = paper_from_ss(ss_record())
        self.attempts = []
        self.calls = []
        self.context = SourceLookupContext(sleeper=lambda _: None)

    def pdf(self, revision='v2', title=None, author='Alice Smith', doi=None, base='2401.12345'):
        return pdf_fixture(f'{title or self.p.title}\n{author}\narXiv:{base}{revision}\n' + (f'DOI: {doi}' if doi else ''))

    def run_download(self, fetch):
        def transport(url):
            self.calls.append(url)
            return fetch(url)
        return recover_pdf(self.p, self.path, fetcher=transport, allow_recovery=True,
                           source_context=self.context, on_attempt=self.attempts.append)

    def test_exact_revision_before_unversioned_baseline(self):
        self.p.open_access_pdf_url = 'https://example.org/unversioned.pdf'
        self.run_download(lambda _: self.pdf())
        self.assertEqual(self.calls, ['https://arxiv.org/pdf/2401.12345v2'])
        info = self.attempts[-1].fulltext
        self.assertEqual(info.version_match, 'exact')
        self.assertEqual(info.revision, 'v2')
        self.assertEqual(len(info.sha256), 64)
        self.assertEqual(info.version_label, '指定版本已確認')

    def test_alternative_revision_is_labelled(self):
        self.run_download(lambda _: self.pdf(revision='v3'))
        info = self.attempts[-1].fulltext
        self.assertEqual(info.version_match, 'alternative')
        self.assertEqual(info.target.revision, 'v2')
        self.assertEqual(info.revision, 'v3')
        self.assertIn('v3', info.version_label)

    def test_unversioned_search_does_not_claim_exact(self):
        self.p = paper_from_ss(ss_record(arxiv='2401.12345', doi='10.48550/arxiv.2401.12345'))
        self.run_download(lambda _: self.pdf())
        self.assertEqual(self.attempts[-1].fulltext.version_match, 'unknown')
        self.assertEqual(self.attempts[-1].fulltext.revision, 'v2')

    def test_same_research_preprint_for_published_doi(self):
        self.p = paper_from_ss(ss_record(arxiv='2401.12345'))
        self.run_download(lambda _: self.pdf())
        self.assertEqual(self.attempts[-1].fulltext.version_match, 'alternative')
        self.assertEqual(self.p.doi, '10.1234/published')

    def test_wrong_doi_cannot_be_saved_by_title(self):
        with self.assertRaises(PdfDownloadError):
            self.run_download(lambda _: self.pdf(doi='10.1234/other'))
        self.assertFalse(self.path.exists())
        self.assertIn('mismatch', [a.reason_code for a in self.attempts])

    def test_wrong_arxiv_cannot_be_saved_by_title(self):
        with self.assertRaises(PdfDownloadError):
            self.run_download(lambda _: self.pdf(base='2401.99999'))
        self.assertFalse(self.path.exists())

    def test_wrong_arxiv_not_overridden_by_related_doi(self):
        with self.assertRaises(PdfDownloadError):
            self.run_download(lambda _: self.pdf(base='2401.99999', doi='10.48550/arxiv.2401.12345'))
        self.assertFalse(self.path.exists())

    def test_explicit_author_conflict_rejected(self):
        with self.assertRaises(PdfDownloadError):
            self.run_download(lambda _: self.pdf(author='Authors: Someone Else'))
        self.assertFalse(self.path.exists())

    def test_openreview_direct_link_works_without_api_lookup(self):
        rec = ss_record(arxiv=None)
        rec['openAccessPdf'] = {'url': 'https://openreview.net/pdf?id=abcXYZ'}
        self.p = paper_from_ss(rec)
        self.run_download(lambda _: pdf_fixture(self.p.title))
        self.assertEqual(self.calls, ['https://openreview.net/pdf?id=abcXYZ'])
        self.assertEqual(self.attempts[-1].fulltext.version_match, 'unknown')

    def test_first_page_reference_is_not_identity(self):
        self.p.doi = '10.1234/expected'
        data = pdf_fixture('An unrelated short document\nWe refer to 10.1234/expected')
        self.assertNotEqual(inspect_fulltext(data, self.p, 'https://example.org')[1], 'confirmed')

    def test_provider_version_label_does_not_confirm_exact(self):
        self.p = paper_from_ss(ss_record(arxiv=None))
        attach_lookup(self.p, oa_record(self.p, ['https://example.org/published.pdf']))
        self.run_download(lambda _: pdf_fixture(self.p.title + '\nAlice Smith\nDOI: ' + self.p.doi))
        self.assertEqual(self.attempts[-1].fulltext.version_match, 'unknown')
        self.assertEqual(self.attempts[-1].fulltext.version_evidence, 'provider_reported')

    def test_missing_revision_is_unknown(self):
        self.run_download(lambda _: pdf_fixture(self.p.title + '\nAlice Smith'))
        self.assertIsNone(self.attempts[-1].fulltext.revision)
        self.assertEqual(self.attempts[-1].fulltext.version_match, 'unknown')

    def test_arxiv_doi_never_unpaywall(self):
        self.p = paper_from_ss(ss_record(arxiv=None, doi='10.48550/arxiv.2401.12345'))
        with self.assertRaises(PdfDownloadError):
            recover_pdf(self.p, self.path, fetcher=lambda url: self.calls.append(url) or b'bad',
                allow_recovery=True, source_context=self.context, unpaywall_email='secret@example.org', on_attempt=self.attempts.append)
        self.assertFalse(any('unpaywall' in url for url in self.calls))
        self.assertIn('skipped_arxiv_doi', [a.reason_code for a in self.attempts])

    def test_missing_oa_uses_multiple_locations(self):
        self.p = paper_from_ss(ss_record(arxiv=None))
        attach_lookup(self.p, oa_record(self.p, ['https://example.org/bad.pdf', 'https://example.org/good.pdf']))
        self.run_download(lambda url: b'bad' if 'bad' in url else pdf_fixture(self.p.title))
        self.assertEqual(self.calls[-1], 'https://example.org/good.pdf')
        self.assertTrue(self.attempts[-1].recovered)

    def test_secret_urls_not_persisted(self):
        self.p = paper_from_ss(ss_record(arxiv=None))
        self.p.open_access_pdf_url = 'https://example.org/p.pdf?token=secret'
        self.run_download(lambda _: FetchResult(pdf_fixture(self.p.title), final_url='https://example.org/p.pdf?key=secret'))
        self.assertNotIn('secret', json.dumps([a.model_dump() for a in self.attempts]))

    def test_dry_legacy_ignores_derived_sources(self):
        with self.assertRaises(PdfDownloadError):
            recover_pdf(self.p, self.path, fetcher=lambda _: self.fail('unscreened derived request'))
        self.assertFalse(self.path.exists())

    def test_unique_stats_and_actual_source_mapping(self):
        result = download_and_backfill([rank(self.p, 1)], Path(self.tmp.name), 1,
            fetcher=lambda _: self.pdf(), allow_recovery=True, source_context=self.context)
        self.assertEqual(result.stats.provider_oa_metadata_coverage, 0)
        self.assertEqual(result.stats.resolved_pdf_location_coverage, 1)
        self.assertEqual(result.stats.validated_pdf_success_rate, 1)
        self.assertEqual(result.fulltext_sources['S1'].version_match, 'exact')

    def test_extra_urls_and_run_budget_both_enforced(self):
        self.p = paper_from_ss(ss_record(arxiv=None))
        attach_lookup(self.p, oa_record(self.p, [f'https://x.example/{i}.pdf' for i in range(5)]))
        with self.assertRaises(PdfDownloadError):
            self.run_download(lambda _: b'bad')
        self.assertEqual(len(self.calls), 3)
        self.assertTrue(any(a.budget_scope == 'extra_document_urls' for a in self.attempts))

    def test_same_file_hash_stable_different_file_hash_not_version_proof(self):
        data = self.pdf()
        self.run_download(lambda _: data)
        original = self.attempts[-1].fulltext
        self.calls.clear(); self.attempts.clear()
        self.run_download(lambda _: data)
        self.assertEqual(original.sha256, self.attempts[-1].fulltext.sha256)
        self.calls.clear(); self.attempts.clear()
        self.run_download(lambda _: self.pdf())
        self.assertEqual(self.attempts[-1].fulltext.version_match, 'exact')

    def test_canonical_arxiv_doi_matches_confirmed_preprint_relation(self):
        self.p = paper_from_ss(ss_record(arxiv='2401.12345'))
        self.run_download(lambda _: self.pdf(doi='10.48550/arxiv.2401.12345'))
        self.assertEqual(self.attempts[-1].identity, 'confirmed')
        self.assertEqual(self.attempts[-1].fulltext.version_match, 'alternative')

    def test_different_doi_relation_requires_title_and_author(self):
        from literature_review.models import PaperIdentifier, VersionRelation
        self.p = paper_from_ss(ss_record(arxiv=None))
        ids = [PaperIdentifier(scheme='doi', value=value, provider='official', metadata_path='relation')
               for value in [self.p.doi, '10.1234/preprint']]
        self.p.version_relations = [VersionRelation(identifiers=ids, status='confirmed', source='official',
            metadata_path='relation', title_match=True, author_match=True)]
        self.p.open_access_pdf_url = 'https://example.org/p.pdf'
        self.run_download(lambda _: pdf_fixture(self.p.title + '\nAlice Smith\nDOI: 10.1234/preprint'))
        self.assertEqual(self.attempts[-1].fulltext.version_match, 'alternative')
        self.assertTrue(self.attempts[-1].fulltext.relation_evidence)
        with self.assertRaises(PdfDownloadError):
            self.run_download(lambda _: pdf_fixture(self.p.title + '\nDOI: 10.1234/preprint'))

    def test_official_acl_target_exact(self):
        rec = ss_record(arxiv=None); rec['externalIds']['ACL'] = '2025.acl-long.1'
        self.p = paper_from_ss(rec)
        self.run_download(lambda _: pdf_fixture(self.p.title + '\nAlice Smith\nDOI: ' + self.p.doi))
        self.assertEqual(self.calls[0], 'https://aclanthology.org/2025.acl-long.1.pdf')
        self.assertEqual(self.attempts[-1].fulltext.version_match, 'exact')

    def test_unpaywall_relationship_requires_matching_metadata(self):
        self.p = paper_from_ss(ss_record(arxiv=None))
        payload = {'doi': self.p.doi, 'title': self.p.title,
                   'z_authors': [{'given': 'Alice', 'family': 'Smith'}],
                   'oa_locations': [{'url_for_pdf': 'https://arxiv.org/pdf/2401.12345v2', 'version': 'submittedVersion'}]}
        for title, expected in [(self.p.title, True), ('Different research', False)]:
            self.p.version_relations = []
            payload['title'] = title
            self.attempts.clear()
            def fetch(url):
                return json.dumps(payload).encode() if 'unpaywall' in url else self.pdf()
            if expected:
                recover_pdf(self.p, self.path, fetcher=fetch, allow_recovery=True,
                    unpaywall_email='private@example.org', source_context=self.context, on_attempt=self.attempts.append)
                self.assertEqual(self.attempts[-1].fulltext.version_match, 'alternative')
                self.assertEqual(self.attempts[-1].fulltext.relation_evidence[0].source, 'unpaywall')
            else:
                with self.assertRaises(PdfDownloadError):
                    recover_pdf(self.p, self.path, fetcher=fetch, allow_recovery=True,
                        unpaywall_email='private@example.org', source_context=self.context)


if __name__ == '__main__':
    unittest.main()
