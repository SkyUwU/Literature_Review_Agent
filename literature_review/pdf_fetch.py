"""Bounded PDF retrieval and identity checks; transports are caller injectable."""
from __future__ import annotations

import gzip
import io
import json
import re
import time
import unicodedata
import zlib
import hashlib
import uuid
from datetime import datetime, timezone
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, quote, urlencode, urljoin, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

import pymupdf

from literature_review.models import DownloadAttempt, Paper, RetrievedFullText, PaperIdentifier, VersionRelation
from literature_review.source_resolution import (
    SourceLookupContext, source_locations, classify_error, identifier_from_url, arxiv_identifier, canonical_url,
)

MAX_BYTES = 25 * 1024 * 1024
TIMEOUT = 30
USER_AGENT = "LiteratureReviewAgent/0.1 (academic; contact: local)"


@dataclass(frozen=True)
class FetchResult:
    content: bytes
    original_url: str | None = None
    final_url: str | None = None
    http_status: int | None = None
    content_type: str | None = None
    content_encoding: str | None = None
    retry_after: str | None = None


class PdfDownloadError(RuntimeError):
    def __init__(self, reason_code: str, *, http_status: int | None = None,
                 error_kind: str | None = None, error_code: str | None = None,
                 retryable: bool = False, retry_after: str | None = None):
        known = {"invalid_url", "redirect_limit", "timeout", "network_error", "http_error",
                 "content_too_large", "decompressed_too_large", "invalid_gzip", "empty_content",
                 "no_oa_url", "download_failed", "storage_error", "budget_exhausted"}
        if reason_code not in known:
            reason_code = "network_error"
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.http_status = http_status
        self.error_kind, self.error_code = error_kind, error_code
        self.retryable, self.retry_after = retryable, retry_after


class NoOpenAccessError(PdfDownloadError):
    pass


Fetcher = Callable[[str], bytes | FetchResult]


def sanitize_url(url: str | None) -> str | None:
    if not url:
        return None
    try:
        parts = urlsplit(url)
        port = parts.port
    except (ValueError, TypeError):
        return None
    # Retain only a small allowlist: signed URLs and unknown parameters may contain secrets.
    query = urlencode([(k, v) for k, v in parse_qsl(parts.query)
                       if k.lower() in {"id", "article", "paper", "doi", "download"}])
    host = parts.hostname or ""
    if port:
        host += f":{port}"
    return urlunsplit((parts.scheme, host, parts.path, query, ""))


def _http_url(url: str) -> bool:
    try:
        parts = urlsplit(url)
        parts.port
        return parts.scheme in {"http", "https"} and bool(parts.hostname) and not parts.username
    except (ValueError, TypeError):
        return False


class _Redirects(HTTPRedirectHandler):
    def __init__(self):
        self.count = 0
        self.deadline = time.monotonic() + TIMEOUT

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        self.count += 1
        if time.monotonic() >= self.deadline:
            raise PdfDownloadError("timeout")
        req.timeout = max(0.01, self.deadline - time.monotonic())
        if self.count > 5 or not _http_url(newurl):
            raise PdfDownloadError("redirect_limit")
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected and urlsplit(req.full_url).hostname != urlsplit(newurl).hostname:
            for header in ('Authorization', 'X-api-key', 'Cookie'):
                redirected.remove_header(header)
        return redirected


def default_fetcher(url: str, *, headers: dict | None = None) -> FetchResult:
    if not _http_url(url):
        raise PdfDownloadError("invalid_url")
    started = time.monotonic()
    try:
        opener = build_opener(_Redirects())
        request = Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
        with opener.open(request, timeout=TIMEOUT) as response:
            chunks = []
            size = 0
            while True:
                if time.monotonic() - started > TIMEOUT:
                    raise PdfDownloadError("timeout")
                # Bound each blocking read by the remaining overall budget.
                if getattr(response, "fp", None) is not None:
                    raw = getattr(response.fp, "raw", None)
                    sock = getattr(raw, "_sock", None)
                    if sock is not None:
                        sock.settimeout(max(0.01, TIMEOUT - (time.monotonic() - started)))
                chunk = response.read1(min(65536, MAX_BYTES + 1 - size))
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
                if size > MAX_BYTES:
                    raise PdfDownloadError("content_too_large")
            return FetchResult(b"".join(chunks), url, response.geturl(), response.status,
                               response.headers.get("Content-Type"),
                               response.headers.get("Content-Encoding"))
    except HTTPError as error:
        raise PdfDownloadError("http_error", http_status=error.code,
                               retry_after=error.headers.get('Retry-After') if error.headers else None) from None
    except TimeoutError:
        raise PdfDownloadError("timeout") from None
    except (URLError, OSError) as error:
        kind, code, retryable, _, _ = classify_error(error)
        raise PdfDownloadError("network_error", error_kind=kind, error_code=code, retryable=retryable) from None


def decode_content(result: FetchResult) -> bytes:
    data = result.content
    if len(data) > MAX_BYTES:
        raise PdfDownloadError("content_too_large")
    if data.startswith(b"\x1f\x8b"):
        try:
            with gzip.GzipFile(fileobj=io.BytesIO(data)) as stream:
                chunks = []
                size = 0
                deadline = time.monotonic() + TIMEOUT
                while size <= MAX_BYTES:
                    if time.monotonic() >= deadline:
                        raise PdfDownloadError("timeout")
                    chunk = stream.read(min(65536, MAX_BYTES + 1 - size))
                    if not chunk:
                        break
                    chunks.append(chunk)
                    size += len(chunk)
                data = b"".join(chunks)
        except (OSError, EOFError, zlib.error):
            raise PdfDownloadError("invalid_gzip") from None
        if len(data) > MAX_BYTES:
            raise PdfDownloadError("decompressed_too_large")
    if not data:
        raise PdfDownloadError("empty_content")
    return data


def _normal(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKC", text).casefold() if c.isalnum())


def pdf_identity(data: bytes, paper: Paper) -> tuple[str, str]:
    if not data.startswith(b"%PDF-"):
        return "not_pdf", "unconfirmed"
    try:
        with pymupdf.open(stream=data, filetype="pdf") as doc:
            if doc.is_encrypted or doc.page_count < 1:
                return "invalid_pdf", "unconfirmed"
            metadata_title = (doc.metadata or {}).get("title", "")
            text = doc[0].get_text()[:4000]
            metadata = " ".join(str(v) for v in (doc.metadata or {}).values())
    except Exception:
        return "invalid_pdf", "unconfirmed"
    # The first page avoids treating a DOI in a later bibliography as identity.
    normalized_title = _normal(paper.title)
    first_line = next((line for line in text.splitlines() if line.strip()), "")
    title_match = bool(normalized_title) and (
        normalized_title == _normal(metadata_title) or normalized_title == _normal(first_line)
        or (len(normalized_title) >= 12 and normalized_title in _normal(text))
    )
    doi = (paper.doi or "").lower().removeprefix("https://doi.org/")
    dois = {m.rstrip(".,;)").lower() for m in re.findall(r"10\.\d{4,9}/[^\s<>]+", text + " " + metadata)}
    if doi and doi in dois:
        return "valid_pdf", "confirmed"
    if doi and dois and doi not in dois:
        return "valid_pdf", "mismatch"
    if title_match:
        return "valid_pdf", "confirmed"
    # Explicit PDF title is identity evidence; generic creator labels are ignored.
    if metadata_title and _normal(metadata_title) not in {"untitled", "document", "microsoftword"}:
        return "valid_pdf", "mismatch"
    return "valid_pdf", "unconfirmed"


class _Links(HTMLParser):
    def __init__(self, base: str):
        super().__init__()
        self.base = base
        self.links: list[str] = []
        self.anchor: str | None = None

    def add(self, url: str):
        try:
            url = urljoin(self.base, url)
        except ValueError:
            return
        if _http_url(url) and url not in self.links:
            self.links.append(url)

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "meta" and values.get("name", "").lower() == "citation_pdf_url":
            self.add(values.get("content", ""))
        if tag == "a":
            self.anchor = values.get("href")
            href = self.anchor or ""
            try:
                explicit_pdf = urlsplit(href).path.lower().endswith(".pdf")
            except ValueError:
                explicit_pdf = False
            host = urlsplit(self.base).hostname or ""
            if explicit_pdf or (
                (host == "aaai.org" or host.endswith(".aaai.org")) and "/article/download/" in href
            ):
                self.add(href)

    def handle_data(self, data):
        if self.anchor and data.strip().lower() in {"pdf", "download pdf", "full text pdf"}:
            self.add(self.anchor)

    def handle_endtag(self, tag):
        if tag == "a":
            self.anchor = None


def html_links(data: bytes, base: str) -> list[str]:
    parser = _Links(base)
    parser.feed(data[:1024 * 1024].decode("utf-8", errors="replace"))
    host = urlsplit(base).hostname or ""
    if host == "aclanthology.org" and not urlsplit(base).path.endswith(".pdf"):
        parser.add(urlsplit(base).path.rstrip("/") + ".pdf")
    return parser.links


def atomic_pdf(data: bytes, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with NamedTemporaryFile(dir=destination.parent, suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(data)
        temporary.replace(destination)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)


def inspect_fulltext(data: bytes, paper: Paper, url: str, location=None):
    """Separate work identity from revision evidence; URL alone never confirms identity."""
    fmt, identity = pdf_identity(data, paper)
    actual = []
    revision = None
    evidence = 'unknown'
    version_match = 'unknown'
    reason = 'conservative_title_match' if identity == 'confirmed' else identity
    if fmt != 'valid_pdf':
        return fmt, identity, None
    with pymupdf.open(stream=data, filetype='pdf') as doc:
        metadata = doc.metadata or {}
        text = doc[0].get_text()[:4000]
        metadata_title = metadata.get('title') or ''
    title = _normal(paper.title)
    first_line = next((line for line in text.splitlines() if line.strip()), '')
    title_match = (title == _normal(metadata_title) or title == _normal(first_line)
                   or len(title) >= 12 and title in _normal(text))
    # Explicit DOI labels / metadata are identity evidence; unlabelled mentions are observations.
    labelled = re.findall(r'(?:doi\s*[:=]\s*|https?://doi\.org/)(10\.\d{4,9}/[^\s<>]+)', text, re.I)
    labelled += re.findall(r'10\.\d{4,9}/[^\s<>]+', ' '.join(str(v) for v in metadata.values()))
    labelled = {x.rstrip('.,;)').lower() for x in labelled}
    observed_dois = {x.rstrip('.,;)').lower() for x in re.findall(r'10\.\d{4,9}/[^\s<>]+', text)}
    for doi in labelled:
        actual.append(PaperIdentifier(scheme='doi', value=doi, provider='pdf_observed', metadata_path='metadata/first_page'))
    arxiv_matches = []
    for match in re.finditer(r'arxiv\s*:\s*([A-Za-z0-9./-]+)', text, re.I):
        parsed = arxiv_identifier(match[1])
        if parsed:
            arxiv_matches.append(parsed)
    # Reject contradictory explicit identifiers, never resolve them by title alone.
    allowed_dois = {paper.doi} if paper.doi else set()
    allowed_arxiv = {x.value for x in paper.identifiers if x.scheme == 'arxiv'}
    derived = arxiv_identifier(paper.doi)
    if derived:
        allowed_arxiv.add(derived[0])
    for known in [str(paper.url), str(paper.open_access_pdf_url or '')]:
        ident = identifier_from_url(known, 'provider', 'url')
        if ident and ident.scheme == 'arxiv':
            allowed_arxiv.add(ident.value)
    for relation in paper.version_relations:
        if relation.status == 'confirmed' and relation.title_match and relation.author_match:
            allowed_dois.update(x.value for x in relation.identifiers if x.scheme == 'doi')
            allowed_arxiv.update(x.value for x in relation.identifiers if x.scheme == 'arxiv')
    allowed_dois.update(f'10.48550/arxiv.{base.lower()}' for base in allowed_arxiv)
    author_match = any(_normal(author) in _normal(text) for author in paper.authors)
    named_author = metadata.get('author') or ''
    author_line = re.search(r'^authors?\s*:\s*(.+)$', text, re.I | re.M)
    explicit_authors = named_author or (author_line[1] if author_line else '')
    author_conflict = bool(explicit_authors and _normal(explicit_authors) not in {'unknown', 'anonymous'}
                           and not any(_normal(author) in _normal(explicit_authors) for author in paper.authors))
    explicit_title_conflict = bool(metadata_title and _normal(metadata_title) not in {'untitled', 'document', 'microsoftword'}
                                   and _normal(metadata_title) != title and not title_match)
    if len(labelled) > 1 or labelled - allowed_dois or explicit_title_conflict or author_conflict:
        identity = 'mismatch'
    elif len(set(arxiv_matches)) > 1:
        identity = 'unconfirmed'
    elif arxiv_matches:
        base, revision = arxiv_matches[0]
        actual.append(PaperIdentifier(scheme='arxiv', value=base, revision=revision,
                                      provider='pdf_observed', metadata_path='first_page'))
        if base not in allowed_arxiv:
            identity = 'mismatch'
        elif title_match and (identity == 'confirmed' or author_match):
            identity = 'confirmed'
            reason = 'arxiv_identifier_and_title' if identity == 'confirmed' else reason
        # Cross-DOI exception also requires title and author, not just the relationship.
        if observed_dois and paper.doi and paper.doi not in observed_dois:
            identity = 'confirmed' if base in allowed_arxiv and title_match and author_match and not (observed_dois - allowed_dois) else 'mismatch'
        evidence = 'pdf_observed'
        requested = identifier_from_url(url, 'official', 'pdf_url')
        target = paper.version_target
        target_ids = {i.value for i in target.identifiers if i.scheme == 'arxiv' and i.revision == target.revision}
        if requested and requested.scheme == 'arxiv' and requested.value == base and requested.revision == revision and revision and target.revision == revision and base in target_ids:
            version_match = 'exact'
            evidence = 'official'
        elif (target.revision and revision and revision != target.revision) or (paper.doi and not derived):
            version_match = 'alternative'
    elif paper.doi and paper.doi in labelled:
        identity = 'confirmed'
        reason = 'labelled_doi'
    elif labelled and labelled <= allowed_dois and title_match and author_match:
        identity = 'confirmed'
        reason = 'confirmed_version_relation_and_identity'
        version_match = 'alternative'
    elif observed_dois and (len(observed_dois) > 1 or not title_match):
        # An arbitrary first-page DOI mention is not enough to identify a document.
        identity = 'unconfirmed' if not (observed_dois - allowed_dois) else 'mismatch'
    if identity != 'confirmed':
        return fmt, identity, None
    version = location.version if location else None
    if arxiv_matches:
        version = 'arxivVersion'  # Repository revision does not establish its peer-review status.
    elif version:
        evidence = 'provider_reported'
    requested = identifier_from_url(url, 'official', 'pdf_url')
    if (requested and requested.scheme == 'acl' and title_match and author_match
            and any(i.scheme == 'acl' and i.value == requested.value for i in paper.version_target.identifiers)
            and (not paper.doi or paper.doi in labelled)):
        actual.append(requested)
        version_match, evidence, version = 'exact', 'official', 'publishedVersion'
    details = dict(actual_identifiers=actual, revision=revision, version=version,
                   version_match=version_match, version_evidence=evidence, identity_evidence=reason,
                   relation_evidence=[r for r in paper.version_relations if r.status == 'confirmed'] if version_match == 'alternative' else [])
    return fmt, identity, details


def recover_pdf(paper: Paper, destination: Path, *, fetcher: Fetcher,
                allow_recovery: bool = False, unpaywall_email: str | None = None,
                doi_cache: dict | None = None, on_attempt=None,
                source_context: SourceLookupContext | None = None, on_fulltext=None) -> Path:
    context = source_context or SourceLookupContext()
    cache = doi_cache if doi_cache is not None else {}
    seen = set()
    extra = 0
    locations = source_locations(paper) if allow_recovery else []
    links = []

    def record(source, stage, reason, **values):
        if on_attempt:
            on_attempt(DownloadAttempt(paper_id=paper.paper_id, source=source, stage=stage,
                                       reason_code=reason, attempt_id=uuid.uuid4().hex, **values))

    def attempt(url, source, location=None, *, parse_links=False):
        nonlocal extra
        canonical = canonical_url(url)
        if canonical in seen:
            record(source, 'fetch', 'url_loop', original_url=sanitize_url(url))
            return None
        if source != 'provider':
            if extra >= context.policy.extra_document_urls_per_paper:
                record(source, 'complete', 'budget_exhausted', budget_scope='extra_document_urls')
                return None
            extra += 1
        seen.add(canonical)
        if not _http_url(url):
            record(source, 'fetch', 'invalid_url', failure_step='fetch')
            return None
        safe = sanitize_url(url)
        base_info = dict(original_url=safe, location_id=location.location_id if location else None,
                         version=location.version if location else None,
                         host_type=location.host_type if location else None,
                         license=location.license if location else None)
        last = {}
        def transport(values):
            nonlocal last
            last = values
            if values['reason_code'] != 'transport_success':
                reason = 'http_error' if values['reason_code'] == 'lookup_not_found' else values['reason_code']
                record(source, 'fetch', reason, failure_step='fetch', **base_info,
                       **{k: v for k, v in values.items() if k != 'reason_code'})
        try:
            raw = context.request(url, fetcher, recovery=allow_recovery, baseline=source == 'provider',
                                  retries=allow_recovery, on_attempt=transport)
        except (PdfDownloadError, OSError):
            return None
        result = raw if isinstance(raw, FetchResult) else FetchResult(raw)
        info = dict(base_info, **{k: v for k, v in last.items() if k not in {'reason_code', 'http_status'}})
        info.update(final_url=sanitize_url(result.final_url), http_status=result.http_status,
                    content_type=result.content_type, content_encoding=result.content_encoding)
        step = 'decode'
        try:
            data = decode_content(result)
            step = 'identity'
            fmt, identity, details = inspect_fulltext(data, paper, result.final_url or url, location)
            info.update(format=fmt, identity=identity)
            if fmt == 'valid_pdf' and identity == 'confirmed':
                step = 'write'
                atomic_pdf(data, destination)
                fulltext = RetrievedFullText(source=source, location_id=info['location_id'],
                    location=location.model_copy(update={'pdf_url': sanitize_url(location.pdf_url), 'landing_url': sanitize_url(location.landing_url)}) if location else None,
                    original_url=safe, final_url=info['final_url'], local_path=str(destination),
                    target=paper.version_target, sha256=hashlib.sha256(data).hexdigest(),
                    downloaded_at=datetime.now(timezone.utc).isoformat(), **details)
                record(source, 'fetch', 'downloaded', recovered=source != 'provider',
                       local_path=str(destination), fulltext=fulltext, **info)
                if on_fulltext:
                    on_fulltext(fulltext)
                return destination
            if fmt == 'not_pdf' and re.search(br'<(?:!doctype\s+html|html|head|meta|a)\b', data[:4096], re.I):
                if allow_recovery and parse_links:
                    links.extend(html_links(data, result.final_url or url))
                record(source, 'fetch', 'html_response', **{**info, 'format': 'html'})
            else:
                record(source, 'fetch', identity if fmt == 'valid_pdf' else fmt,
                       failure_step='signature' if fmt == 'not_pdf' else 'parser' if fmt == 'invalid_pdf' else 'identity', **info)
        except (PdfDownloadError, OSError) as error:
            record(source, 'fetch', error.reason_code if isinstance(error, PdfDownloadError) else 'storage_error',
                   failure_step=step, **info)
        return None

    # A known exact revision takes precedence over an unversioned baseline URL.
    if allow_recovery and paper.version_target.revision:
        for loc in locations:
            if loc.pdf_url and any(i.scheme == 'arxiv' and i.revision == paper.version_target.revision for i in loc.identifiers):
                path = attempt(loc.pdf_url, loc.source, loc)
                if path:
                    return path
    if paper.open_access_pdf_url:
        url = str(paper.open_access_pdf_url)
        loc = next((x for x in locations if x.pdf_url == url), None)
        path = attempt(url, 'provider', loc, parse_links=True)
        if path:
            return path
    else:
        record('provider', 'complete', 'no_oa_url')
    if allow_recovery:
        for loc in locations:
            if loc.pdf_url:
                path = attempt(loc.pdf_url, loc.source, loc)
                if path:
                    return path
        if not links:
            landing = next((x for x in locations if x.landing_url and not x.pdf_url), None)
            if landing:
                path = attempt(landing.landing_url, 'landing_page', landing, parse_links=True)
                if path:
                    return path
        for url in links:
            path = attempt(url, 'html_pdf')
            if path:
                return path
        if arxiv_identifier(paper.doi):
            record('unpaywall', 'unpaywall', 'skipped_arxiv_doi')
        elif not paper.doi:
            record('unpaywall', 'unpaywall', 'skipped_no_doi')
        elif not unpaywall_email:
            record('unpaywall', 'unpaywall', 'skipped_no_email')
        elif extra < context.policy.extra_document_urls_per_paper:
            doi = paper.doi.lower()
            endpoint = f'https://api.unpaywall.org/v2/{quote(doi, safe="")}?{urlencode({"email": unpaywall_email})}'
            if doi not in cache:
                lookup_status = None
                def lookup_transport(values):
                    record('unpaywall', 'unpaywall', values['reason_code'], original_url=sanitize_url(endpoint),
                           failure_step='lookup' if values['reason_code'] != 'transport_success' else None,
                           **{k: v for k, v in values.items() if k != 'reason_code'})
                try:
                    raw = context.request(endpoint, fetcher, recovery=True, on_attempt=lookup_transport)
                    payload = json.loads(decode_content(raw if isinstance(raw, FetchResult) else FetchResult(raw)))
                    if not isinstance(payload, dict) or not isinstance(payload.get('oa_locations', []), list):
                        raise ValueError('invalid locations')
                    locs = [payload.get('best_oa_location')] + (payload.get('oa_locations') or [])
                    if any(any(loc.get(k) is not None and not isinstance(loc.get(k), str) for k in ('url_for_pdf', 'url', 'version', 'license', 'host_type')) for loc in locs if isinstance(loc, dict)):
                        raise ValueError('invalid location')
                    bibliographic = {key: payload.get(key) for key in ('doi', 'title', 'z_authors')}
                    cache[doi] = (locs, 'lookup_success', bibliographic)
                except (PdfDownloadError, OSError, ValueError, TypeError) as error:
                    cache[doi] = ([], 'lookup_failed', {})
                    lookup_status = getattr(error, 'http_status', None)
                cached = False
            else:
                cached = True
            locs, reason, bibliographic = cache[doi]
            sanitized = [{k: sanitize_url(loc.get(k)) if k in {'url_for_pdf', 'url'} else loc.get(k)
                          for k in ('url_for_pdf', 'url', 'host_type', 'version', 'license')}
                         for loc in locs if isinstance(loc, dict)]
            record('unpaywall', 'unpaywall', reason, original_url=sanitize_url(endpoint),
                   cache_hit=cached, oa_locations=sanitized,
                   http_status=lookup_status if not cached else None)
            from literature_review.models import FullTextLocation
            versions = {'publishedVersion': 0, 'acceptedVersion': 1, 'submittedVersion': 2}
            for index, loc in enumerate(sorted((x for x in locs if isinstance(x, dict)), key=lambda x: versions.get(x.get('version'), 3))):
                url = loc.get('url_for_pdf')
                if url and _http_url(url):
                    ident = identifier_from_url(url, 'unpaywall', f'oa_locations[{index}].url_for_pdf')
                    authors = bibliographic.get('z_authors')
                    names = [_normal(' '.join(str(a.get(k) or '') for k in ('given', 'family'))) for a in authors if isinstance(a, dict)] if isinstance(authors, list) else []
                    related = (bibliographic.get('doi') == paper.doi and isinstance(bibliographic.get('title'), str)
                               and _normal(bibliographic['title']) == _normal(paper.title)
                               and any(_normal(a) in names for a in paper.authors))
                    if ident and related:
                        original = PaperIdentifier(scheme='doi', value=paper.doi, provider='unpaywall', metadata_path='doi')
                        relation = VersionRelation(identifiers=[original, ident], status='confirmed', source='unpaywall',
                            metadata_path=f'oa_locations[{index}]', record_id=paper.doi, title_match=True, author_match=True)
                        if relation not in paper.version_relations:
                            paper.version_relations.append(relation)
                    location = FullTextLocation(location_id=f'unpaywall:{doi}:{index}', source='unpaywall_pdf',
                        pdf_url=url, metadata_path='oa_locations', version=loc.get('version'),
                        identifiers=[ident] if ident else [],
                        host_type=loc.get('host_type'), license=loc.get('license'))
                    path = attempt(url, 'unpaywall_pdf', location)
                    if path:
                        return path
    record('download', 'complete', 'recovery_exhausted' if allow_recovery else 'download_failed')
    if not paper.open_access_pdf_url and not allow_recovery:
        raise NoOpenAccessError('no_oa_url')
    raise PdfDownloadError('download_failed')
