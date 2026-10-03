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

from literature_review.models import Paper, RankedPaper, DownloadAttempt

from literature_review.pdf_fetch import (
    FetchResult, Fetcher, NoOpenAccessError, PdfDownloadError, default_fetcher, recover_pdf,
)


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
    return recover_pdf(paper, Path(dest_dir) / _safe_filename(paper), fetcher=fetcher)


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
    http_attempts: int = 0
    recovered: int = 0

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
            "http_attempts": self.http_attempts,
            "recovered": self.recovered,
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
    download_attempts: list[DownloadAttempt] = field(default_factory=list)


def download_and_backfill(
    ranked_papers: list[RankedPaper],
    dest_dir: Path,
    target_n: int,
    *,
    fetcher: Fetcher = default_fetcher,
    stats_path: Path | None = None,
    already_downloaded: set[str] | None = None,
    priority_groups: dict[str, list[RankedPaper]] | None = None,
    allow_recovery: bool = False,
    unpaywall_email: str | None = None,
    doi_cache: dict | None = None,
    on_attempt: Callable[[DownloadAttempt], None] | None = None,
    on_progress: Callable[[DownloadResult], None] | None = None,
    already_attempted: set[str] | None = None,
) -> DownloadResult:
    """Try to download the top ``target_n`` ranked papers, backfilling failures.

    Failed papers (no OA link or network error) are replaced by the next ranked
    candidates until ``target_n`` is reached or the candidate list is exhausted.
    ``attempted`` counts candidate papers tried (on the legacy path, only those
    with an OA link); ``http_attempts`` counts actual transport calls including
    Unpaywall lookups, excluding cached lookups. Redirects are within a fetch.
    ``downloaded`` counts only parser-valid, identity-confirmed PDFs.
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
    doi_cache = doi_cache if doi_cache is not None else {}
    already_attempted = already_attempted if already_attempted is not None else set()
    result = DownloadResult()
    stats = result.stats
    stats.requested = target_n

    def record(attempt: DownloadAttempt) -> None:
        result.download_attempts.append(attempt)
        if attempt.http_requested:
            stats.http_attempts += 1
        if on_attempt:
            on_attempt(attempt)

    def _attempt(ranked: RankedPaper) -> None:
        key = dedup_key(ranked.paper)
        if already_downloaded is not None and key in already_downloaded:
            stats.duplicate_reused += 1
            already_attempted.add(key)
            record(DownloadAttempt(paper_id=ranked.paper.paper_id, source="dedup",
                                   stage="complete", reason_code="duplicate_reused"))
            return
        if key in already_attempted:
            record(DownloadAttempt(paper_id=ranked.paper.paper_id, source="dedup",
                                   stage="complete", reason_code="previous_attempt"))
            return
        already_attempted.add(key)
        if ranked.paper.open_access_pdf_url is not None or allow_recovery:
            stats.attempted += 1

        start = len(result.download_attempts)
        try:
            path = recover_pdf(
                ranked.paper, Path(dest_dir) / _safe_filename(ranked.paper), fetcher=fetcher,
                allow_recovery=allow_recovery, unpaywall_email=unpaywall_email,
                doi_cache=doi_cache, on_attempt=record,
            )
        except NoOpenAccessError:
            result.failed_paper_ids.append(ranked.paper.paper_id)
            stats.failed_no_oa += 1
        except PdfDownloadError:
            result.failed_paper_ids.append(ranked.paper.paper_id)
            reasons = {a.reason_code for a in result.download_attempts[start:]}
            if "no_oa_url" in reasons:
                stats.failed_no_oa += 1
            elif reasons & {"network_error", "timeout", "http_error"}:
                stats.failed_network += 1
            else:
                stats.failed_other += 1
        else:
            result.downloaded_paths.append(path)
            result.downloaded_paper_ids.append(ranked.paper.paper_id)
            stats.downloaded += 1
            stats.recovered += int(result.download_attempts[-1].recovered)
            if already_downloaded is not None:
                already_downloaded.add(key)
        stats.shortfall = max(0, target_n - stats.downloaded - stats.duplicate_reused)
        if on_progress:
            on_progress(result)

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
        for ranked in maybe_papers:
            if dedup_key(ranked.paper) not in already_attempted:
                attempt = DownloadAttempt(paper_id=ranked.paper.paper_id, source="selection",
                                          stage="complete", reason_code="target_satisfied")
                record(attempt)

    stats.shortfall = max(0, target_n - stats.downloaded - stats.duplicate_reused)
    if stats_path is not None:
        stats_path = Path(stats_path)
        stats_path.parent.mkdir(parents=True, exist_ok=True)
        stats_path.write_text(json.dumps(stats.to_dict(), indent=2) + "\n")
    return result
