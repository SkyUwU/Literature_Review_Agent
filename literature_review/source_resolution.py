"""Run-scoped source metadata, exact lookups and bounded transport retries.

Availability never changes research relevance or provider bibliographic metadata.
Execution URLs live in memory; callers sanitize them when persisting snapshots.
"""
from __future__ import annotations

import errno
import hashlib
import re
import socket
import ssl
import time
import uuid
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit, urlunsplit, parse_qsl, urlencode

from literature_review.models import (
    FullTextLocation, Paper, PaperIdentifier, SourceLookupAttempt,
    SourceRecoveryPolicy, VersionRelation, VersionTarget,
)

ARXIV = re.compile(r"(?P<base>(?:\d{4}\.\d{4,5}|[a-z][a-z.-]*(?:\.[A-Z]{2})?/\d{7}))(?P<revision>v[1-9]\d*)?", re.I)


def normal(text: str) -> str:
    import unicodedata
    return ''.join(c for c in unicodedata.normalize('NFKC', text).casefold() if c.isalnum())


def arxiv_identifier(value: object) -> tuple[str, str | None] | None:
    if not isinstance(value, str):
        return None
    value = value.strip().removeprefix('arXiv:')
    if value.lower().startswith('10.48550/arxiv.'):
        value = value[len('10.48550/arxiv.'):]
    match = ARXIV.fullmatch(value)
    if match:
        date_part = match['base'].rsplit('/', 1)[-1][:4]
        if not 1 <= int(date_part[2:4]) <= 12:
            return None
    return (match['base'], match['revision']) if match else None


def identifier_from_url(url: object, provider: str, path: str) -> PaperIdentifier | None:
    if not isinstance(url, str):
        return None
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    if parts.scheme not in {'http', 'https'} or parts.username:
        return None
    if parts.hostname in {'arxiv.org', 'www.arxiv.org'}:
        match = re.fullmatch(r'/(?:abs|pdf)/(.+?)(?:\.pdf)?/?', parts.path)
        parsed = arxiv_identifier(match[1]) if match else None
        if parsed:
            return PaperIdentifier(scheme='arxiv', value=parsed[0], revision=parsed[1],
                                   provider=provider, metadata_path=path)
    if parts.hostname == 'aclanthology.org':
        value = parts.path.strip('/').removesuffix('.pdf')
        if re.fullmatch(r'(?:\d{4}\.[a-z0-9-]+\.\d+|[A-Z]\d{2}-\d{4})', value):
            return PaperIdentifier(scheme='acl', value=value, provider=provider, metadata_path=path)
    if parts.hostname == 'openreview.net' and parts.path in {'/forum', '/pdf'}:
        ids = [v for k, v in parse_qsl(parts.query) if k == 'id']
        if len(ids) == 1 and re.fullmatch(r'[A-Za-z0-9_-]{1,128}', ids[0]):
            return PaperIdentifier(scheme='openreview', value=ids[0], provider=provider, metadata_path=path)
    return None


def canonical_url(url: str) -> str:
    try:
        parts = urlsplit(url)
        port = parts.port
        host = (parts.hostname or '').lower()
        if port and not (parts.scheme == 'https' and port == 443 or parts.scheme == 'http' and port == 80):
            host += f':{port}'
        path = parts.path
        if host in {'arxiv.org', 'www.arxiv.org'} and path.startswith('/pdf/'):
            host = 'arxiv.org'
            path = path.removesuffix('.pdf')
        return urlunsplit((parts.scheme.lower(), host, path, urlencode(sorted(parse_qsl(parts.query, keep_blank_values=True))), ''))
    except ValueError:
        return url


def metadata_sources(record: dict, provider: str) -> dict:
    """Retain typed known fields without preserving an entire provider payload."""
    record_id = record.get('paperId') if provider == 'semantic_scholar' else record.get('id')
    if not isinstance(record_id, str):
        record_id = None
    identifiers = []
    locations = []
    external = record.get('externalIds') if isinstance(record.get('externalIds'), dict) else {}
    doi = record.get('doi') if provider == 'openalex' else external.get('DOI')
    if isinstance(doi, str):
        doi = doi.lower().removeprefix('https://doi.org/').removeprefix('http://doi.org/')
        identifiers.append(PaperIdentifier(scheme='doi', value=doi, provider=provider,
                                          metadata_path='doi' if provider == 'openalex' else 'externalIds.DOI',
                                          record_id=record_id))
    if isinstance(record_id, str):
        identifiers.append(PaperIdentifier(scheme=provider, value=record_id, provider=provider,
                                          metadata_path='id' if provider == 'openalex' else 'paperId'))
    if provider == 'semantic_scholar':
        ext = external
        if isinstance(ext, dict):
            parsed = arxiv_identifier(ext.get('ArXiv'))
            if parsed:
                identifiers.append(PaperIdentifier(scheme='arxiv', value=parsed[0], revision=parsed[1],
                                                  provider=provider, metadata_path='externalIds.ArXiv', record_id=record_id))
            acl = ext.get('ACL')
            ident = identifier_from_url(f'https://aclanthology.org/{acl}/', provider, 'externalIds.ACL') if isinstance(acl, str) else None
            if ident:
                identifiers.append(ident)
        pdf = record.get('openAccessPdf') or {}
        items = [('openAccessPdf', {'pdf_url': pdf.get('url')})] if isinstance(pdf, dict) else []
    else:
        items = [('best_oa_location', record.get('best_oa_location')),
                 ('primary_location', record.get('primary_location'))]
        raw_locations = record.get('locations') if isinstance(record.get('locations'), list) else []
        items += [(f'locations[{i}]', loc) for i, loc in enumerate(raw_locations)]
    for path, loc in items:
        if not isinstance(loc, dict):
            continue
        urls = {}
        ids = []
        for field, output in [('pdf_url', 'pdf_url'), ('landing_page_url', 'landing_url')]:
            url = loc.get(field)
            if not isinstance(url, str):
                continue
            try:
                parts = urlsplit(url)
                if parts.scheme not in {'http', 'https'} or not parts.hostname or parts.username:
                    continue
            except ValueError:
                continue
            urls[output] = url
            ident = identifier_from_url(url, provider, f'{path}.{field}')
            if ident:
                ids.append(ident)
                identifiers.append(ident)
        if not urls:
            continue
        source = loc.get('source') if isinstance(loc.get('source'), dict) else {}
        locations.append(FullTextLocation(
            location_id=hashlib.sha256(f'{provider}:{record_id}:{path}'.encode()).hexdigest()[:20],
            source=provider, metadata_path=path, source_record_id=record_id,
            provenance=[{'source': provider, 'metadata_path': path}],
            identifiers=ids, version=loc.get('version') if isinstance(loc.get('version'), str) else None,
            host_type=source.get('type') if isinstance(source.get('type'), str) else None, license=loc.get('license') if isinstance(loc.get('license'), str) else None,
            **urls,
        ))
    if doi:
        parsed = arxiv_identifier(doi)
        if parsed:
            identifiers.append(PaperIdentifier(scheme='arxiv', value=parsed[0], revision=parsed[1],
                                              provider=provider, metadata_path='doi', record_id=record_id))
    unique = {(x.scheme, x.value, x.revision): x for x in identifiers}
    identifiers = list(unique.values())
    related = [x for x in identifiers if x.scheme in {'doi', 'arxiv', 'acl'}]
    relations = [VersionRelation(identifiers=related, status='confirmed', source=provider,
                                 metadata_path='externalIds' if provider == 'semantic_scholar' else 'locations',
                                 record_id=record_id, title_match=True, author_match=True)] if len(related) > 1 else []
    explicit_ids = [x for x in identifiers if x.scheme == 'arxiv' and x.revision]
    explicit = explicit_ids[0] if len(explicit_ids) == 1 else None
    return dict(identifiers=identifiers, fulltext_locations=locations, version_relations=relations,
                version_target=VersionTarget(identifiers=identifiers, revision=explicit.revision if explicit else None,
                                             evidence='provider_reported'))


def derived_locations(paper: Paper) -> list[FullTextLocation]:
    ids = list(paper.identifiers)
    parsed = arxiv_identifier(paper.doi)
    if parsed and not any(x.scheme == 'arxiv' for x in ids):
        ids.append(PaperIdentifier(scheme='arxiv', value=parsed[0], revision=parsed[1],
                                   provider='doi', metadata_path='doi'))
    for url in [str(paper.url), str(paper.open_access_pdf_url or '')]:
        ident = identifier_from_url(url, 'official_url', 'url')
        if ident and not any((x.scheme, x.value, x.revision) == (ident.scheme, ident.value, ident.revision) for x in ids):
            ids.append(ident)
    result = []
    for ident in ids:
        if ident.scheme == 'arxiv':
            url = f'https://arxiv.org/pdf/{ident.value}{ident.revision or ""}'
        elif ident.scheme == 'acl':
            url = f'https://aclanthology.org/{ident.value}.pdf'
        else:
            continue
        result.append(FullTextLocation(location_id=f'{ident.scheme}:{ident.value}{ident.revision or ""}',
                                      source=f'{ident.scheme}_official', pdf_url=url, metadata_path=ident.metadata_path,
                                      identifiers=[ident]))
    return result


def source_locations(paper: Paper) -> list[FullTextLocation]:
    """Merge URL provenance without silently resolving conflicting version labels."""
    merged = {}
    for loc in paper.fulltext_locations + derived_locations(paper):
        key = canonical_url(loc.pdf_url or loc.landing_url or '')
        if key in merged:
            previous = merged[key]
            conflicting = previous.version_conflicting or loc.version_conflicting or bool(previous.version and loc.version and previous.version != loc.version)
            merged[key] = previous.model_copy(update={
                'identifiers': list({(x.scheme, x.value, x.revision): x for x in previous.identifiers + loc.identifiers}.values()),
                'version': None if conflicting else previous.version or loc.version,
                'version_conflicting': conflicting,
                'provenance': previous.provenance + loc.provenance,
            })
        else:
            merged[key] = loc
    versions = {'publishedVersion': 0, 'acceptedVersion': 1, 'submittedVersion': 2}
    def key(loc):
        target = paper.version_target.revision
        exact = bool(target and any(i.revision == target and i.scheme == 'arxiv' for i in loc.identifiers))
        host = urlsplit(loc.pdf_url or loc.landing_url or '').hostname or ''
        return (0 if exact else 1, versions.get(loc.version, 3),
                0 if host == 'aclanthology.org' or loc.host_type == 'publisher' else 1 if host == 'arxiv.org' else 2)
    return sorted(merged.values(), key=key)


def classify_error(error: BaseException) -> tuple[str, str, bool, int | None, str | None]:
    status = getattr(error, 'http_status', None)
    if isinstance(error, HTTPError):
        status = error.code
    if isinstance(status, int):
        headers = getattr(error, 'headers', None)
        retry_after = headers.get('Retry-After') if headers else getattr(error, 'retry_after', None)
        return 'http', f'http_{status}', status in {429, 500, 502, 503, 504}, status, retry_after
    cause = error.reason if isinstance(error, URLError) else error
    if isinstance(cause, ssl.SSLCertVerificationError):
        return 'tls_certificate', 'certificate_verification_failed', False, None, None
    if isinstance(cause, ssl.SSLError):
        return 'tls_transport', 'tls_error', True, None, None
    if isinstance(cause, socket.gaierror):
        temporary = cause.errno == socket.EAI_AGAIN
        return 'dns', 'dns_temporary' if temporary else 'dns_permanent', temporary, None, None
    if isinstance(cause, TimeoutError) or getattr(error, 'reason_code', '') == 'timeout':
        return 'timeout', 'timeout', True, None, None
    if isinstance(cause, ConnectionError) or isinstance(cause, OSError) and cause.errno in {errno.ECONNRESET, errno.ECONNREFUSED, errno.ECONNABORTED}:
        return 'connection', 'connection_error', True, None, None
    return getattr(error, 'error_kind', None) or 'unknown', getattr(error, 'error_code', None) or 'network_error', getattr(error, 'retryable', False), None, None


def retry_delay(raw: str | None, retry: int) -> float:
    if raw:
        try:
            return max(0, float(raw))
        except ValueError:
            try:
                return max(0, (parsedate_to_datetime(raw) - datetime.now(timezone.utc)).total_seconds())
            except (TypeError, ValueError):
                pass
    return float(2 ** retry)


class SourceLookupContext:
    def __init__(self, policy: SourceRecoveryPolicy | None = None, *, clock=time.monotonic, sleeper=time.sleep):
        self.policy = policy or SourceRecoveryPolicy()
        self.clock, self.sleeper = clock, sleeper
        self.cache: dict[tuple[str, str, str], tuple[dict | None, str]] = {}
        self.new_lookups = 0
        self.paper_lookups: set[str] = set()
        self.recovery_transports = 0
        self.abstract_transports = 0
        self.baseline_transports = 0
        self.lookup_attempts: list[SourceLookupAttempt] = []
        self.on_update = None

    def counters(self):
        return dict(new_source_lookups=self.new_lookups, recovery_transports=self.recovery_transports,
                    abstract_transports=self.abstract_transports, baseline_transports=self.baseline_transports,
                    source_and_download_transports=self.recovery_transports + self.abstract_transports + self.baseline_transports)

    def request(self, url, fetcher, *, recovery: bool, on_attempt, retries: bool = True, baseline: bool = False):
        from literature_review.pdf_fetch import FetchResult, PdfDownloadError
        for index in range(1, (self.policy.max_retries if retries else 0) + 2):
            counted = recovery and not (baseline and index == 1)
            if counted and self.recovery_transports >= self.policy.recovery_transports_per_run:
                on_attempt(dict(reason_code='budget_exhausted', budget_scope='recovery_transports', attempt_index=index))
                raise PdfDownloadError('budget_exhausted')
            if counted:
                self.recovery_transports += 1
            elif baseline:
                self.baseline_transports += 1
            elif not baseline:
                self.abstract_transports += 1
            started = self.clock()
            try:
                result = fetcher(url)
                if isinstance(result, FetchResult) and result.http_status is not None and not 200 <= result.http_status < 300:
                    raise PdfDownloadError('http_error', http_status=result.http_status, retry_after=result.retry_after)
            except (OSError, URLError, PdfDownloadError) as error:
                kind, code, retryable, status, after = classify_error(error)
                wait = retry_delay(after, index - 1)
                can_retry = retries and retryable and index <= self.policy.max_retries and wait <= 30
                reason = 'lookup_not_found' if status == 404 else 'timeout' if kind == 'timeout' else getattr(error, 'reason_code', 'network_error')
                on_attempt(dict(reason_code=reason, http_requested=True, attempt_index=index,
                                elapsed_ms=max(0, self.clock() - started) * 1000, http_status=status,
                                error_kind=kind, error_code=code, retry_wait=wait if can_retry else None))
                if retryable and wait > 30:
                    on_attempt(dict(reason_code='retry_deferred', attempt_index=index))
                if not can_retry:
                    raise
                if recovery and self.recovery_transports >= self.policy.recovery_transports_per_run:
                    on_attempt(dict(reason_code='budget_exhausted', budget_scope='recovery_transports', attempt_index=index + 1))
                    raise PdfDownloadError('budget_exhausted') from None
                self.sleeper(wait)
            else:
                on_attempt(dict(reason_code='transport_success', http_requested=True, attempt_index=index,
                                elapsed_ms=max(0, self.clock() - started) * 1000,
                                http_status=result.http_status if isinstance(result, FetchResult) else None))
                return result

    def lookup_openalex(self, paper: Paper, fetcher, *, phase='source_resolution') -> dict | None:
        doi = (paper.doi or '').lower()
        if not doi:
            return None
        key = ('openalex', 'work', doi)
        lookup_id = uuid.uuid4().hex
        def record(values):
            self.lookup_attempts.append(SourceLookupAttempt(lookup_id=lookup_id, paper_id=paper.paper_id,
                                                            provider='openalex', identifier=doi, phase=phase, **values))
            if self.on_update:
                self.on_update()
        if key in self.cache:
            payload, reason = self.cache[key]
            record(dict(reason_code=reason, cache_hit=True))
            return payload
        if phase == 'source_resolution':
            if (not self.policy.new_source_lookups_per_paper or paper.paper_id in self.paper_lookups
                    or self.new_lookups >= self.policy.new_source_lookups_per_run):
                record(dict(reason_code='budget_exhausted', budget_scope='new_source_lookups'))
                return None
            self.new_lookups += 1
            self.paper_lookups.add(paper.paper_id)
        url = ('https://api.openalex.org/works/https://doi.org/' + quote(doi, safe='')
               + '?select=id,doi,title,authorships,abstract_inverted_index,primary_location,locations,best_oa_location')
        try:
            payload = self.request(url, fetcher, recovery=phase == 'source_resolution', on_attempt=record)
            if not isinstance(payload, dict):
                raise ValueError('invalid metadata')
            reason = 'lookup_success'
            record(dict(reason_code=reason))
        except (OSError, URLError, ValueError, RuntimeError) as error:
            # PdfDownloadError derives RuntimeError. No raw exception detail is persisted.
            payload = None
            status = getattr(error, 'http_status', None) or getattr(error, 'code', None)
            reason = 'lookup_not_found' if status == 404 else 'lookup_failed'
            record(dict(reason_code=reason, http_status=status if isinstance(status, int) else None))
        self.cache[key] = (payload, reason)
        return payload


def attach_lookup(paper: Paper, record: dict) -> bool:
    """Exact DOI, title and author match before adding any alternate identity."""
    doi = record.get('doi')
    doi = doi.lower().removeprefix('https://doi.org/') if isinstance(doi, str) else None
    title = record.get('title')
    raw_authors = record.get('authorships') if isinstance(record.get('authorships'), list) else []
    authors = {normal(a['author']['display_name']) for a in raw_authors
               if isinstance(a, dict) and isinstance(a.get('author'), dict) and isinstance(a['author'].get('display_name'), str)}
    if doi != paper.doi or not isinstance(title, str) or normal(title) != normal(paper.title) or not ({normal(a) for a in paper.authors} & (authors - {''})):
        return False
    info = metadata_sources(record, 'openalex')
    paper.identifiers = list({(x.scheme, x.value, x.revision): x for x in paper.identifiers + info['identifiers']}.values())
    paper.fulltext_locations = list({x.location_id: x for x in paper.fulltext_locations + info['fulltext_locations']}.values())
    paper.version_relations.extend(info['version_relations'])
    return True


def default_lookup_fetcher(url: str) -> dict:
    """Single bounded request, separate from search's own retry policy."""
    import json
    import os
    from literature_review.pdf_fetch import default_fetcher, decode_content
    headers = {'Accept': 'application/json', 'User-Agent': 'LiteratureReviewAgent/0.1'}
    if os.environ.get('OPENALEX_API_KEY'):
        headers['Authorization'] = 'Bearer ' + os.environ['OPENALEX_API_KEY']
    return json.loads(decode_content(default_fetcher(url, headers=headers)))
