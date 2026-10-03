"""Bounded PDF retrieval and identity checks; transports are caller injectable."""
from __future__ import annotations

import gzip
import io
import json
import re
import time
import unicodedata
import zlib
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, quote, urlencode, urljoin, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

import pymupdf

from literature_review.models import DownloadAttempt, Paper

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


class PdfDownloadError(RuntimeError):
    def __init__(self, reason_code: str, *, http_status: int | None = None):
        known = {"invalid_url", "redirect_limit", "timeout", "network_error", "http_error",
                 "content_too_large", "decompressed_too_large", "invalid_gzip", "empty_content",
                 "no_oa_url", "download_failed", "storage_error"}
        if reason_code not in known:
            reason_code = "network_error"
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.http_status = http_status


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
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def default_fetcher(url: str) -> FetchResult:
    if not _http_url(url):
        raise PdfDownloadError("invalid_url")
    started = time.monotonic()
    try:
        opener = build_opener(_Redirects())
        request = Request(url, headers={"User-Agent": USER_AGENT})
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
        raise PdfDownloadError("http_error", http_status=error.code) from None
    except TimeoutError:
        raise PdfDownloadError("timeout") from None
    except (URLError, OSError):
        raise PdfDownloadError("network_error") from None


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


def recover_pdf(paper: Paper, destination: Path, *, fetcher: Fetcher,
                allow_recovery: bool = False, unpaywall_email: str | None = None,
                doi_cache: dict | None = None,
                on_attempt: Callable[[DownloadAttempt], None] | None = None) -> Path:
    cache = doi_cache if doi_cache is not None else {}
    seen: set[str] = set()
    extra = 0

    def record(source, stage, reason, **values):
        if on_attempt:
            on_attempt(DownloadAttempt(paper_id=paper.paper_id, source=source,
                                       stage=stage, reason_code=reason, **values))

    def attempt(url, source, location=None):
        nonlocal extra
        location = location or {}
        provenance = {k: location.get(k) for k in ("version", "host_type", "license")}
        safe_url = sanitize_url(url)
        if url in seen:
            record(source, "fetch", "url_loop", original_url=safe_url, **provenance)
            return None, []
        seen.add(url)
        if source != "provider":
            if extra >= 3:
                return None, []
            extra += 1
        info = {"original_url": safe_url, "http_requested": True, **provenance}
        try:
            raw = fetcher(url)
            result = raw if isinstance(raw, FetchResult) else FetchResult(raw)
            info.update(final_url=sanitize_url(result.final_url), http_status=result.http_status,
                        content_type=result.content_type, content_encoding=result.content_encoding)
            if result.http_status is not None and not 200 <= result.http_status < 300:
                raise PdfDownloadError("http_error", http_status=result.http_status)
            data = decode_content(result)
            fmt, identity = pdf_identity(data, paper)
            info.update(format=fmt, identity=identity)
            if fmt == "valid_pdf" and identity == "confirmed":
                try:
                    atomic_pdf(data, destination)
                except OSError:
                    raise PdfDownloadError("storage_error") from None
                record(source, "fetch", "downloaded", recovered=source != "provider",
                       local_path=str(destination), **info)
                return destination, []
            if fmt == "not_pdf" and re.search(br"<(?:!doctype\s+html|html|head|meta|a)\b", data[:4096], re.I):
                info["format"] = "html"
                links = html_links(data, result.final_url or url) if source == "provider" and allow_recovery else []
                record(source, "fetch", "html_response", **info)
                return None, links
            record(source, "fetch", identity if fmt == "valid_pdf" else fmt, **info)
        except (PdfDownloadError, TimeoutError, OSError) as error:
            info["http_status"] = getattr(error, "http_status", None) or info.get("http_status")
            reason = error.reason_code if isinstance(error, PdfDownloadError) else "timeout" if isinstance(error, TimeoutError) else "network_error"
            record(source, "fetch", reason, **info)
        return None, []

    if paper.open_access_pdf_url:
        path, links = attempt(str(paper.open_access_pdf_url), "provider")
        if path:
            return path
        for url in links:
            if extra >= 3:
                break
            path, _ = attempt(url, "html_pdf")
            if path:
                return path
    else:
        record("provider", "complete", "no_oa_url")
    if allow_recovery:
        if not paper.doi:
            record("unpaywall", "unpaywall", "skipped_no_doi")
        elif not unpaywall_email:
            record("unpaywall", "unpaywall", "skipped_no_email")
        else:
            doi = paper.doi.lower().removeprefix("https://doi.org/")
            endpoint = f"https://api.unpaywall.org/v2/{quote(doi, safe='')}?{urlencode({'email': unpaywall_email})}"
            requested = doi not in cache
            if requested:
                try:
                    raw = fetcher(endpoint)
                    response = raw if isinstance(raw, FetchResult) else FetchResult(raw)
                    if response.http_status is not None and not 200 <= response.http_status < 300:
                        raise PdfDownloadError("http_error", http_status=response.http_status)
                    payload = json.loads(decode_content(response))
                    if not isinstance(payload, dict):
                        raise ValueError("Invalid Unpaywall payload")
                    others = payload.get("oa_locations") or []
                    if not isinstance(others, list):
                        raise ValueError("Invalid OA locations")
                    locations = [payload.get("best_oa_location")] + others
                    sanitized = []
                    for index, loc in enumerate(locations):
                        if isinstance(loc, dict):
                            for key in ("url_for_pdf", "url", "host_type", "version", "license"):
                                if loc.get(key) is not None and not isinstance(loc[key], str):
                                    raise ValueError("Invalid OA location field")
                            sanitized.append({k: sanitize_url(loc.get(k)) if k in {"url_for_pdf", "url"} else loc.get(k)
                                              for k in ("url_for_pdf", "url", "host_type", "version", "license")}
                                             | {"source": "unpaywall", "metadata_path": "best_oa_location" if index == 0 else f"oa_locations[{index - 1}]"})
                    cache[doi] = (locations, sanitized, "lookup_success", {
                        "http_status": response.http_status,
                        "final_url": sanitize_url(response.final_url),
                        "content_type": response.content_type,
                        "content_encoding": response.content_encoding,
                    })
                except (PdfDownloadError, TimeoutError, OSError, ValueError, TypeError) as error:
                    reason = error.reason_code if isinstance(error, PdfDownloadError) else "timeout" if isinstance(error, TimeoutError) else "lookup_failed"
                    cache[doi] = ([], [], reason, {"http_status": getattr(error, "http_status", None)})
            locations, sanitized, reason, metadata = cache[doi]
            record("unpaywall", "unpaywall", reason, original_url=sanitize_url(endpoint),
                   oa_locations=sanitized, http_requested=requested, **metadata)
            versions = {"publishedVersion": 0, "acceptedVersion": 1, "submittedVersion": 2}
            for loc in sorted((x for x in locations if isinstance(x, dict)),
                              key=lambda x: versions.get(x.get("version"), 3)):
                url = loc.get("url_for_pdf")
                if not url or not _http_url(url) or url in seen or extra >= 3:
                    continue
                path, _ = attempt(url, "unpaywall_pdf", loc)
                if path:
                    return path
    record("download", "complete", "recovery_exhausted" if allow_recovery else "download_failed")
    if not paper.open_access_pdf_url and not allow_recovery:
        raise NoOpenAccessError("no_oa_url")
    raise PdfDownloadError("download_failed")
