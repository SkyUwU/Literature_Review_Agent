"""M3 offline fixtures: bounded transports, valid PDFs, and persisted failures."""
import gzip
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from tests.pdf_fixtures import pdf_fixture
from tests.test_pdf_downloader import make_paper, rank
from literature_review.models import PapersOutput
from literature_review.pdf_fetch import FetchResult, PdfDownloadError, recover_pdf, sanitize_url
from literature_review.pdf_downloader import download_and_backfill


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dest = Path(self.tmp.name)
        self.paper = make_paper('W1', 'https://example.org/landing')
        self.good = pdf_fixture(self.paper.title)
        self.attempts = []
        self.calls = []

    def run_fetch(self, payloads, **kwargs):
        def fetch(url):
            self.calls.append(url)
            value = payloads[url] if isinstance(payloads, dict) else payloads(url)
            if isinstance(value, Exception):
                raise value
            return value
        return recover_pdf(self.paper, self.dest / 'W1.pdf', fetcher=fetch,
                           allow_recovery=True, on_attempt=self.attempts.append, **kwargs)

    def assert_failed(self, payloads, reason, **kwargs):
        with self.assertRaises(PdfDownloadError):
            self.run_fetch(payloads, **kwargs)
        self.assertFalse((self.dest / 'W1.pdf').exists())
        self.assertIn(reason, [a.reason_code for a in self.attempts])

    def test_direct_pdf_and_unknown_metadata(self):
        self.run_fetch({'https://example.org/landing': self.good})
        self.assertIsNone(self.attempts[0].http_status)
        self.assertIsNone(self.attempts[0].final_url)
        self.assertFalse(self.attempts[0].recovered)

    def test_content_type_is_only_hint(self):
        self.run_fetch({'https://example.org/landing': FetchResult(self.good, content_type='text/html')})
        self.assertEqual(self.attempts[0].identity, 'confirmed')

    def test_relative_html_pdf(self):
        self.run_fetch({'https://example.org/landing': b'<html><meta name="citation_pdf_url" content="/paper.pdf">',
                        'https://example.org/paper.pdf': self.good})
        self.assertTrue(self.attempts[-1].recovered)

    def test_gzip_html_and_redirect_base(self):
        self.run_fetch({'https://example.org/landing': FetchResult(
            gzip.compress(b'<html><a href="file.pdf">PDF</a>'), final_url='https://example.org/new/index'),
            'https://example.org/new/file.pdf': self.good})
        self.assertEqual(self.attempts[0].format, 'html')

    def test_corrupt_pdf(self):
        self.assert_failed({'https://example.org/landing': b'%PDF-1.4 fake'}, 'invalid_pdf')

    def test_false_signature(self):
        self.assert_failed({'https://example.org/landing': b'not a pdf'}, 'not_pdf')

    def test_unknown_identity(self):
        self.assert_failed({'https://example.org/landing': pdf_fixture('no identity available')}, 'unconfirmed')

    def test_wrong_identity(self):
        self.paper.doi = '10.1234/expected'
        self.assert_failed({'https://example.org/landing': pdf_fixture('10.1234/other')}, 'mismatch')

    def test_title_hyphenation(self):
        self.run_fetch({'https://example.org/landing': pdf_fixture('W1 title about litera-\nture reviews')})
        self.assertEqual(self.attempts[-1].identity, 'confirmed')

    def test_doi_confirms_without_title(self):
        self.paper.doi = '10.1234/expected'
        self.run_fetch({'https://example.org/landing': pdf_fixture('DOI: 10.1234/expected')})

    def test_missing_doi(self):
        self.assert_failed({'https://example.org/landing': b'<html>closed'}, 'skipped_no_doi')

    def test_missing_email(self):
        self.paper.doi = '10.1234/expected'
        self.assert_failed({'https://example.org/landing': b'<html>closed'}, 'skipped_no_email')

    def test_unpaywall_no_oa_and_versions(self):
        self.paper.open_access_pdf_url = None
        self.paper.doi = '10.1234/expected'
        payload = {'best_oa_location': {'url_for_pdf': 'https://example.org/submitted', 'version': 'submittedVersion'},
                   'oa_locations': [{'url_for_pdf': 'https://example.org/published', 'version': 'publishedVersion',
                                     'host_type': 'publisher', 'license': 'cc-by'}]}
        def fetch(url):
            return json.dumps(payload).encode() if 'api.unpaywall.org' in url else self.good
        self.run_fetch(fetch, unpaywall_email='private@example.org')
        self.assertEqual(self.calls[-1], 'https://example.org/published')
        self.assertEqual(self.attempts[-1].version, 'publishedVersion')
        self.assertNotIn('private', json.dumps([a.model_dump() for a in self.attempts]))
        self.assertEqual(len(self.attempts[-2].oa_locations), 2)

    def test_multiple_locations_first_invalid_second_valid(self):
        self.paper.open_access_pdf_url = None
        self.paper.doi = '10.1234/expected'
        locations = [{'url_for_pdf': 'https://example.org/first'}, {'url_for_pdf': 'https://example.org/second'}]
        self.run_fetch(lambda u: json.dumps({'oa_locations': locations}).encode() if 'unpaywall' in u
                       else b'bad' if u.endswith('first') else self.good, unpaywall_email='fake@example.org')
        self.assertEqual(self.calls[-1], 'https://example.org/second')

    def test_lookup_cached_once_per_doi(self):
        self.paper.open_access_pdf_url = None
        self.paper.doi = '10.1234/expected'
        cache = {}
        for _ in range(2):
            self.assert_failed(lambda u: b'{}', 'recovery_exhausted', unpaywall_email='fake@example.org', doi_cache=cache)
        self.assertEqual(len(self.calls), 1)

    def test_http_404_and_429(self):
        for status in (404, 429):
            self.attempts.clear()
            self.assert_failed({'https://example.org/landing': FetchResult(b'bad', http_status=status)}, 'http_error')
            self.assertEqual(self.attempts[0].http_status, status)

    def test_timeout(self):
        self.assert_failed({'https://example.org/landing': TimeoutError()}, 'timeout')

    def test_loop(self):
        self.assert_failed({'https://example.org/landing': b'<html><meta name="citation_pdf_url" content="/landing">'}, 'url_loop')
        self.assertEqual(len(self.calls), 1)

    def test_no_recursive_landing_page(self):
        self.assert_failed({'https://example.org/landing': b'<html><a href="first.pdf">PDF</a>',
                            'https://example.org/first.pdf': b'<html><a href="second.pdf">PDF</a>'}, 'recovery_exhausted')
        self.assertEqual(len(self.calls), 2)

    def test_extra_budget(self):
        self.assert_failed(lambda u: b'<html>' + b''.join(f'<a href="/{i}.pdf">PDF</a>'.encode() for i in range(10)), 'recovery_exhausted')
        self.assertEqual(len(self.calls), 4)

    def test_raw_and_gzip_size_limits(self):
        with patch('literature_review.pdf_fetch.MAX_BYTES', 1000):
            self.assert_failed({'https://example.org/landing': b'x' * 1001}, 'content_too_large')
            self.attempts.clear()
            self.assert_failed({'https://example.org/landing': gzip.compress(b'x' * 1001)}, 'decompressed_too_large')

    def test_invalid_gzip(self):
        self.assert_failed({'https://example.org/landing': b'\x1f\x8bgarbage'}, 'invalid_gzip')

    def test_dry_download_does_not_follow_html_or_unpaywall(self):
        self.paper.doi = '10.1234/expected'
        def fetch(u):
            self.calls.append(u)
            return b'<html><a href="file.pdf">PDF</a>'
        result = download_and_backfill([rank(self.paper, 1)], self.dest, 1, fetcher=fetch,
                                       unpaywall_email='fake@example.org')
        self.assertEqual(result.stats.downloaded, 0)
        self.assertEqual(len(self.calls), 1)

    def test_keep_then_maybe_backfill_records_failures(self):
        maybe = make_paper('W2', 'https://example.org/valid')
        result = download_and_backfill([], self.dest, 1,
            fetcher=lambda u: pdf_fixture(maybe.title) if u.endswith('valid') else b'bad',
            priority_groups={'keep': [rank(self.paper, 1)], 'maybe': [rank(maybe, 2)]})
        self.assertEqual(result.downloaded_paper_ids, ['W2'])
        self.assertEqual(result.stats.http_attempts, 2)
        self.assertEqual(result.stats.shortfall, 0)
        output = PapersOutput(run={}, download_attempts=result.download_attempts)
        self.assertIn('W1', output.model_dump_json())
        self.assertEqual(PapersOutput.model_validate_json('{"run":{},"papers":[]}').download_attempts, [])

    def test_sanitize_credentials(self):
        self.assertEqual(sanitize_url('https://u:p@example.org/file.pdf?email=a&api_key=b&X-Amz-Signature=c#secret'),
                         'https://example.org/file.pdf')

    def test_unpaywall_rate_limit_retains_status(self):
        self.paper.open_access_pdf_url = None
        self.paper.doi = '10.1234/expected'
        self.assert_failed(lambda u: FetchResult(b'{}', http_status=429), 'http_error',
                           unpaywall_email='fake@example.org')
        self.assertEqual(self.attempts[-2].http_status, 429)

    def test_redirect_limit(self):
        from literature_review.pdf_fetch import _Redirects
        from urllib.request import Request
        redirects = _Redirects()
        request = Request('https://example.org/start')
        request.timeout = 30
        for i in range(5):
            redirects.redirect_request(request, None, 302, '', {}, f'https://example.org/{i}')
        with self.assertRaisesRegex(PdfDownloadError, 'redirect_limit'):
            redirects.redirect_request(request, None, 302, '', {}, 'https://example.org/end')

    def test_acl_and_aaai_rules(self):
        from literature_review.pdf_fetch import html_links
        self.assertIn('https://aclanthology.org/2025.test.1.pdf', html_links(b'<html>', 'https://aclanthology.org/2025.test.1/'))
        self.assertIn('https://ojs.aaai.org/index.php/AAAI/article/download/1/2',
                      html_links(b'<a href="/index.php/AAAI/article/download/1/2">Download</a>',
                                 'https://ojs.aaai.org/index.php/AAAI/article/view/1'))

    def test_recovery_counts_lookup_and_pdf_requests(self):
        self.paper.open_access_pdf_url = None
        self.paper.doi = '10.1234/expected'
        result = download_and_backfill([rank(self.paper, 1)], self.dest, 1, allow_recovery=True,
            unpaywall_email='fake@example.org', fetcher=lambda u:
            b'{"best_oa_location":{"url_for_pdf":"https://example.org/file.pdf"}}' if 'unpaywall' in u else self.good)
        self.assertEqual(result.stats.http_attempts, 2)
        self.assertEqual(result.stats.recovered, 1)
        self.assertEqual(result.stats.downloaded, 1)

    def test_default_transport_limits_and_metadata(self):
        from literature_review.pdf_fetch import default_fetcher
        import io
        class Response(io.BytesIO):
            status = 200
            headers = {'Content-Type': 'application/pdf', 'Content-Encoding': 'identity'}
            def geturl(self):
                return 'https://example.org/final.pdf'
        class Opener:
            def open(inner, req, timeout):
                self.assertEqual(timeout, 30)
                return Response(self.good)
        with patch('literature_review.pdf_fetch.build_opener', return_value=Opener()):
            result = default_fetcher('https://example.org/start')
            self.assertEqual(result.final_url, 'https://example.org/final.pdf')
            self.assertEqual(result.content, self.good)
            with patch('literature_review.pdf_fetch.MAX_BYTES', 10):
                with self.assertRaisesRegex(PdfDownloadError, 'content_too_large'):
                    default_fetcher('https://example.org/start')

    def test_metadata_title_mismatch_and_encrypted_pdf(self):
        import pymupdf
        wrong = pdf_fixture('Unknown title in page text')
        with pymupdf.open(stream=wrong, filetype='pdf') as doc:
            doc.set_metadata({'title': 'Entirely different work'})
            self.assert_failed({'https://example.org/landing': doc.tobytes()}, 'mismatch')
        self.attempts.clear()
        with pymupdf.open(stream=self.good, filetype='pdf') as doc:
            encrypted = doc.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw='private')
        self.assert_failed({'https://example.org/landing': encrypted}, 'invalid_pdf')

    def test_atomic_write_failure_leaves_no_pdf_or_temp(self):
        with patch('pathlib.Path.replace', side_effect=OSError('disk full')):
            self.assert_failed({'https://example.org/landing': self.good}, 'storage_error')
        self.assertEqual(list(self.dest.iterdir()), [])

    def test_maybe_not_needed_records_target_satisfied(self):
        maybe = make_paper('W2', 'https://example.org/unused')
        result = download_and_backfill([], self.dest, 1, fetcher=lambda u: self.good,
            priority_groups={'keep': [rank(self.paper, 1)], 'maybe': [rank(maybe, 2)]})
        self.assertEqual(result.stats.http_attempts, 1)
        self.assertEqual(result.download_attempts[-1].reason_code, 'target_satisfied')

    def test_failed_duplicate_is_not_retried_or_satisfied(self):
        result = download_and_backfill([rank(self.paper, 1), rank(self.paper, 2)], self.dest, 1,
                                       fetcher=lambda u: b'bad')
        self.assertEqual(result.stats.http_attempts, 1)
        self.assertEqual(result.stats.shortfall, 1)
        self.assertEqual(result.stats.duplicate_reused, 0)


class RecoveryFlowTests(unittest.TestCase):
    def test_recovered_pdf_enters_same_pipeline_and_snapshots(self):
        from tests.test_main import FakePlanClient, FakeJsonFetcher, record_for, results_payload, FakeScreenClient
        from tests.test_dispositions import StableFakeClient
        from tests.test_pipeline import FakeEncoder, PAPER_TEXT, make_document
        from literature_review.main import run_end_to_end
        # Main's screening fake admits W1; no API clients or encoder downloads.
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            calls = []
            def fetch(url):
                calls.append(url)
                if url.endswith('W1.pdf'):
                    return b'<html><meta name="citation_pdf_url" content="/recovered.pdf">'
                return pdf_fixture('Towards W1: automated literature review agents')
            with patch('literature_review.main.extract_pdf_text', side_effect=lambda path, pid: make_document(pid, PAPER_TEXT)), \
                 patch('literature_review.pipeline._notes_pacing_seconds', return_value=0):
                result = run_end_to_end('evidence selection', dest_dir=root / 'pdfs',
                    client_plan=FakePlanClient(3), client_screen=FakeScreenClient(),
                    client_synth=StableFakeClient(), encoder=FakeEncoder(),
                    json_fetcher=FakeJsonFetcher([results_payload(record_for('W1'))]),
                    pdf_fetcher=fetch, diagnostics_output_dir=root / 'outputs')
            self.assertEqual(len(result['downloads']), 1)
            saved = PapersOutput.model_validate_json(next((root / 'outputs').glob('papers_*.json')).read_text(encoding='utf-8'))
            self.assertTrue(any(a.recovered for a in saved.download_attempts))
            self.assertEqual(saved.run['status'], 'completed')
            self.assertEqual(str(saved.papers[0].paper.open_access_pdf_url), 'https://example.org/W1.pdf')

    def test_all_failed_candidates_persist_without_dispositions(self):
        from tests.test_main import FakePlanClient, FakeJsonFetcher, record_for, results_payload
        from tests.test_pipeline import FakeEncoder
        from literature_review.main import run_end_to_end
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaises(ValueError):
                run_end_to_end('evidence selection', dest_dir=root / 'pdfs', client_plan=FakePlanClient(3),
                    encoder=FakeEncoder(), client_synth=object(), pdf_fetcher=lambda u: b'bad',
                    json_fetcher=FakeJsonFetcher([results_payload(record_for('W1'))]),
                    diagnostics_output_dir=root / 'outputs')
            saved = PapersOutput.model_validate_json(next((root / 'outputs').glob('papers_*.json')).read_text(encoding='utf-8'))
            self.assertEqual(saved.papers, [])
            self.assertEqual(saved.paper_dispositions, [])
            self.assertTrue(saved.download_attempts)


if __name__ == '__main__':
    unittest.main()
