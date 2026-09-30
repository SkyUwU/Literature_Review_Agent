"""Download open-access PDFs for selected papers into a caller-chosen directory.

The module is deliberately caller-driven: ``dest_dir`` is passed in (the real
pipeline uses ``data/papers/``, smoke runs use a temp directory) and the network
fetcher is injectable so tests never touch the real network.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from literature_review.models import Paper, RankedPaper

USER_AGENT = "LiteratureReviewAgent/0.1 (academic; contact: local)"

Fetcher = Callable[[str], bytes]


class PdfDownloadError(RuntimeError):
    """Raised when a PDF cannot be downloaded (network/HTTP failure)."""


class NoOpenAccessError(RuntimeError):
    """Raised when a paper has no open-access PDF URL to download."""


def default_fetcher(url: str) -> bytes:
    """Fetch the PDF bytes over HTTPS with a timeout and a user agent."""
    try:
        request = Request(url, headers={"User-Agent": USER_AGENT})
        with urlopen(request, timeout=30) as response:
            return response.read()
    except HTTPError as error:
        raise PdfDownloadError(f"HTTP {error.code} while downloading {url}") from error
    except URLError as error:
        raise PdfDownloadError(f"Network error downloading {url}: {error.reason}") from error


def _safe_filename(paper: Paper) -> str:
    """Build a collision-resistant file name from the paper id or DOI."""
    identifier = paper.paper_id
    if "doi.org" in identifier:
        identifier = identifier.rsplit("/", 1)[-1]
    elif identifier.startswith("http"):
        identifier = identifier.rstrip("/").rsplit("/", 1)[-1]
    slug = re.sub(r"[^A-Za-z0-9._-]", "_", identifier).strip("._")
    return f"{slug or 'paper'}.pdf"


def dedup_key(paper: Paper) -> str:
    """Build a provider-independent dedup key for cross-source paper identity.

    Prefers the normalised DOI so the same work found via OpenAlex and Semantic
    Scholar collapses to one key even though the provider ids differ. Falls back
    to a punctuation/case-insensitive ``title:...:year`` when no DOI is present.
    """
    if paper.doi:
        return f"doi:{paper.doi}"
    norm_title = re.sub(r"[^a-z0-9]", "", paper.title.lower())
    return f"title:{norm_title}:{paper.year}"


def download_pdf(
    paper: Paper,
    dest_dir: Path,
    *,
    fetcher: Fetcher = default_fetcher,
) -> Path:
    """Download one paper's open-access PDF into ``dest_dir``.

    Raises ``NoOpenAccessError`` when the paper carries no OA URL and
    ``PdfDownloadError`` on network/HTTP failure. The caller decides ``dest_dir``.
    """
    if paper.open_access_pdf_url is None:
        raise NoOpenAccessError(f"Paper {paper.paper_id} has no open-access PDF URL.")
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    destination = dest_dir / _safe_filename(paper)
    content = fetcher(str(paper.open_access_pdf_url))
    if not content:
        raise PdfDownloadError(f"Empty content for {paper.open_access_pdf_url}")
    destination.write_bytes(content)
    return destination


@dataclass
class DownloadStats:
    """OA coverage and download-success statistics for one backfill run."""

    requested: int = 0
    candidates_available: int = 0
    with_oa_link: int = 0
    attempted: int = 0
    downloaded: int = 0
    failed_no_oa: int = 0
    failed_network: int = 0
    failed_other: int = 0
    duplicate_reused: int = 0
    shortfall: int = 0

    @property
    def oa_ratio_candidates(self) -> float:
        """Fraction of candidate papers that expose an OA PDF link (coverage)."""
        if self.candidates_available == 0:
            return 0.0
        return round(self.with_oa_link / self.candidates_available, 3)

    @property
    def oa_ratio_attempted(self) -> float:
        """Fraction of attempted downloads that succeeded (link quality)."""
        if self.attempted == 0:
            return 0.0
        return round(self.downloaded / self.attempted, 3)

    def to_dict(self) -> dict[str, object]:
        return {
            "requested": self.requested,
            "candidates_available": self.candidates_available,
            "with_oa_link": self.with_oa_link,
            "attempted": self.attempted,
            "downloaded": self.downloaded,
            "failed_no_oa": self.failed_no_oa,
            "failed_network": self.failed_network,
            "failed_other": self.failed_other,
            "duplicate_reused": self.duplicate_reused,
            "shortfall": self.shortfall,
            "oa_ratio_candidates": self.oa_ratio_candidates,
            "oa_ratio_attempted": self.oa_ratio_attempted,
        }


@dataclass
class DownloadResult:
    """Outcome of a download-and-backfill pass over ranked papers."""

    downloaded_paths: list[Path] = field(default_factory=list)
    downloaded_paper_ids: list[str] = field(default_factory=list)
    failed_paper_ids: list[str] = field(default_factory=list)
    stats: DownloadStats = field(default_factory=DownloadStats)


def download_and_backfill(
    ranked_papers: list[RankedPaper],
    dest_dir: Path,
    target_n: int,
    *,
    fetcher: Fetcher = default_fetcher,
    stats_path: Path | None = None,
    already_downloaded: set[str] | None = None,
    priority_groups: dict[str, list[RankedPaper]] | None = None,
) -> DownloadResult:
    """Try to download the top ``target_n`` ranked papers, backfilling failures.

    Failed papers (no OA link or network error) are replaced by the next ranked
    candidates until ``target_n`` is reached or the candidate list is exhausted.
    ``attempted`` counts only papers that actually carry an OA link and were tried;
    papers without an OA link are counted separately as ``failed_no_oa``.
    When ``already_downloaded`` is given, papers whose id is already in that set
    count as satisfied (``duplicate_reused``) without writing a new file and
    without triggering a backfill; successfully downloaded ids are added back.
    When ``stats_path`` is given, the OA-coverage statistics are written as JSON.

    When ``priority_groups`` is given (M5e), the ``"keep"`` group is downloaded in
    its entirety — the LLM screening layer already judged those papers high
    quality, so they may exceed ``target_n`` (downstream RCS filters the rest) —
    and the ``"maybe"`` group is used only to fill the total count up to
    ``target_n`` (rank order within the small filler group). The plain
    ``ranked_papers`` path keeps the original top-N backfill behaviour untouched.
    """
    result = DownloadResult()
    stats = result.stats
    stats.requested = target_n

    def _attempt(ranked: RankedPaper) -> None:
        key = dedup_key(ranked.paper)
        if already_downloaded is not None and key in already_downloaded:
            stats.duplicate_reused += 1
            return
        if ranked.paper.open_access_pdf_url is None:
            result.failed_paper_ids.append(ranked.paper.paper_id)
            stats.failed_no_oa += 1
        else:
            stats.attempted += 1
            try:
                path = download_pdf(ranked.paper, dest_dir, fetcher=fetcher)
            except PdfDownloadError:
                result.failed_paper_ids.append(ranked.paper.paper_id)
                stats.failed_network += 1
            except Exception:
                result.failed_paper_ids.append(ranked.paper.paper_id)
                stats.failed_other += 1
            else:
                result.downloaded_paths.append(path)
                result.downloaded_paper_ids.append(ranked.paper.paper_id)
                stats.downloaded += 1
        if already_downloaded is not None:
            already_downloaded.add(key)

    if priority_groups is None:
        stats.candidates_available = len(ranked_papers)
        stats.with_oa_link = sum(
            1 for ranked in ranked_papers if ranked.paper.open_access_pdf_url is not None
        )
        for ranked in ranked_papers:
            if stats.downloaded + stats.duplicate_reused >= target_n:
                break
            _attempt(ranked)
    else:
        keep_papers = priority_groups.get("keep", [])
        maybe_papers = priority_groups.get("maybe", [])
        combined = keep_papers + maybe_papers
        stats.candidates_available = len(combined)
        stats.with_oa_link = sum(
            1 for ranked in combined if ranked.paper.open_access_pdf_url is not None
        )
        for ranked in keep_papers:
            _attempt(ranked)
        for ranked in maybe_papers:
            if stats.downloaded + stats.duplicate_reused >= target_n:
                break
            _attempt(ranked)

    stats.shortfall = max(0, target_n - stats.downloaded - stats.duplicate_reused)
    if stats_path is not None:
        stats_path = Path(stats_path)
        stats_path.parent.mkdir(parents=True, exist_ok=True)
        stats_path.write_text(json.dumps(stats.to_dict(), indent=2) + "\n")
    return result
