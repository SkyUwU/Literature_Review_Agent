"""Tests for the PDF downloader: fake fetcher only, no real network."""

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from literature_review.models import Paper, RankedPaper
from literature_review.pdf_downloader import (
    NoOpenAccessError,
    PdfDownloadError,
    _safe_filename,
    dedup_key,
    download_and_backfill,
    download_pdf,
)


def make_paper(paper_id: str, oa_url: str | None = None) -> Paper:
    return Paper(
        paper_id=paper_id,
        title=f"{paper_id} title about literature reviews",
        authors=["A. Author"],
        year=2023,
        abstract="This paper studies automated literature review generation in depth.",
        url="https://example.org/landing",
        open_access_pdf_url=oa_url,
    )


def rank(paper: Paper, rank_index: int) -> RankedPaper:
    return RankedPaper(
        paper=paper,
        rank=rank_index,
        score=1.0,
        matched_terms=[],
        rationale="test rationale for ranking a candidate paper",
    )


class DownloadPdfTests(unittest.TestCase):
    def test_downloads_oa_pdf_to_dest_dir(self) -> None:
        with TemporaryDirectory() as tmp:
            dest = Path(tmp)
            paper = make_paper("W123", "https://example.org/W123.pdf")
            path = download_pdf(paper, dest, fetcher=lambda url: b"%PDF-1.4 fake")

            self.assertTrue(path.exists())
            self.assertEqual(path.read_bytes(), b"%PDF-1.4 fake")
            self.assertEqual(path.name, "W123.pdf")

    def test_missing_oa_url_reports_no_open_access(self) -> None:
        with TemporaryDirectory() as tmp:
            paper = make_paper("W456")
            with self.assertRaises(NoOpenAccessError):
                download_pdf(paper, Path(tmp), fetcher=lambda url: b"%PDF-1.4 fake")

    def test_network_failure_reports_error_without_crashing(self) -> None:
        with TemporaryDirectory() as tmp:
            paper = make_paper("W789", "https://example.org/W789.pdf")

            def failing_fetcher(url: str) -> bytes:
                raise PdfDownloadError("HTTP 500 while downloading")

            with self.assertRaises(PdfDownloadError):
                download_pdf(paper, Path(tmp), fetcher=failing_fetcher)

    def test_empty_content_is_treated_as_failure(self) -> None:
        with TemporaryDirectory() as tmp:
            paper = make_paper("W999", "https://example.org/W999.pdf")
            with self.assertRaises(PdfDownloadError):
                download_pdf(paper, Path(tmp), fetcher=lambda url: b"")


class BackfillTests(unittest.TestCase):
    def test_backfills_failed_papers_to_target(self) -> None:
        with TemporaryDirectory() as tmp:
            dest = Path(tmp)
            papers = [
                make_paper(f"W{i}", f"https://example.org/{i}.pdf" if i not in (2, 4) else None)
                for i in range(1, 7)
            ]
            ranked = [rank(paper, index) for index, paper in enumerate(papers, start=1)]

            result = download_and_backfill(ranked, dest, target_n=4, fetcher=lambda url: b"%PDF-1.4 fake")

            self.assertEqual(len(result.downloaded_paths), 4)
            self.assertEqual(result.stats.downloaded, 4)
            self.assertEqual(result.stats.failed_no_oa, 2)
            self.assertEqual(result.stats.shortfall, 0)
            names = {path.name for path in result.downloaded_paths}
            self.assertIn("W1.pdf", names)
            self.assertIn("W3.pdf", names)
            self.assertIn("W5.pdf", names)
            self.assertIn("W6.pdf", names)

    def test_shortfall_when_candidates_exhausted(self) -> None:
        with TemporaryDirectory() as tmp:
            dest = Path(tmp)
            papers = [make_paper("W1"), make_paper("W2")]
            ranked = [rank(paper, index) for index, paper in enumerate(papers, start=1)]

            result = download_and_backfill(ranked, dest, target_n=4, fetcher=lambda url: b"%PDF-1.4 fake")

            self.assertEqual(result.stats.downloaded, 0)
            self.assertEqual(result.stats.failed_no_oa, 2)
            self.assertEqual(result.stats.shortfall, 4)
            self.assertEqual(result.stats.oa_ratio_candidates, 0.0)

    def test_stats_ratios_are_correct(self) -> None:
        with TemporaryDirectory() as tmp:
            dest = Path(tmp)
            papers = [
                make_paper("W1", "https://example.org/1.pdf"),
                make_paper("W2", "https://example.org/2.pdf"),
                make_paper("W3"),
            ]
            ranked = [rank(paper, index) for index, paper in enumerate(papers, start=1)]

            def flaky_fetcher(url: str) -> bytes:
                if url.endswith("/2.pdf"):
                    raise PdfDownloadError("timeout")
                return b"%PDF-1.4 fake"

            result = download_and_backfill(ranked, dest, target_n=3, fetcher=flaky_fetcher)

            self.assertEqual(result.stats.with_oa_link, 2)
            self.assertEqual(result.stats.attempted, 2)
            self.assertEqual(result.stats.downloaded, 1)
            self.assertEqual(result.stats.failed_network, 1)
            self.assertEqual(result.stats.failed_no_oa, 1)
            self.assertEqual(result.stats.oa_ratio_candidates, round(2 / 3, 3))
            self.assertEqual(result.stats.oa_ratio_attempted, 0.5)

    def test_stats_json_written_when_path_given(self) -> None:
        with TemporaryDirectory() as tmp:
            dest = Path(tmp)
            stats_path = Path(tmp) / "stats.json"
            papers = [make_paper("W1", "https://example.org/1.pdf")]
            ranked = [rank(paper, 1) for paper in papers]

            download_and_backfill(
                ranked, dest, target_n=1, fetcher=lambda url: b"%PDF-1.4 fake", stats_path=stats_path
            )

            self.assertTrue(stats_path.exists())
            payload = json.loads(stats_path.read_text())
            self.assertEqual(payload["downloaded"], 1)
            self.assertEqual(payload["oa_ratio_candidates"], 1.0)

    def test_duplicate_already_downloaded_counts_as_reused(self) -> None:
        with TemporaryDirectory() as tmp:
            dest = Path(tmp)
            papers = [
                make_paper(f"W{i}", f"https://example.org/{i}.pdf")
                for i in range(1, 4)
            ]
            ranked = [rank(paper, index) for index, paper in enumerate(papers, start=1)]
            already_downloaded = {dedup_key(papers[0])}

            result = download_and_backfill(
                ranked,
                dest,
                target_n=2,
                fetcher=lambda url: b"%PDF-1.4 fake",
                already_downloaded=already_downloaded,
            )

            self.assertEqual(result.stats.downloaded, 1)
            self.assertEqual(result.stats.duplicate_reused, 1)
            self.assertEqual(result.stats.shortfall, 0)
            self.assertEqual(result.downloaded_paper_ids, ["W2"])
            self.assertEqual(len(result.downloaded_paths), 1)
            self.assertFalse((dest / "W1.pdf").exists())
            self.assertEqual(
                already_downloaded, {dedup_key(papers[0]), dedup_key(papers[1])}
            )

    def test_all_target_duplicates_stops_early_without_new_files(self) -> None:
        with TemporaryDirectory() as tmp:
            dest = Path(tmp)
            papers = [
                make_paper(f"W{i}", f"https://example.org/{i}.pdf")
                for i in range(1, 4)
            ]
            ranked = [rank(paper, index) for index, paper in enumerate(papers, start=1)]
            already_downloaded = {dedup_key(paper) for paper in papers}

            result = download_and_backfill(
                ranked,
                dest,
                target_n=2,
                fetcher=lambda url: b"%PDF-1.4 fake",
                already_downloaded=already_downloaded,
            )

            self.assertEqual(result.stats.downloaded, 0)
            self.assertEqual(result.stats.duplicate_reused, 2)
            self.assertEqual(result.stats.shortfall, 0)
            self.assertEqual(result.downloaded_paper_ids, [])
            self.assertEqual(list(dest.iterdir()), [])

    def test_backfill_failure_still_backfills_with_pairing(self) -> None:
        with TemporaryDirectory() as tmp:
            dest = Path(tmp)
            papers = [
                make_paper("W1"),
                make_paper("W2", "https://example.org/2.pdf"),
            ]
            ranked = [rank(paper, index) for index, paper in enumerate(papers, start=1)]

            result = download_and_backfill(ranked, dest, target_n=1, fetcher=lambda url: b"%PDF-1.4 fake")

            self.assertEqual(result.stats.failed_no_oa, 1)
            self.assertEqual(result.stats.downloaded, 1)
            self.assertEqual(result.downloaded_paper_ids, ["W2"])
            self.assertEqual(len(result.downloaded_paths), 1)


class DedupKeyTests(unittest.TestCase):
    def _paper(
        self,
        paper_id: str,
        *,
        doi: str | None = None,
        title: str = "A Title",
        year: int = 2024,
        oa_url: str | None = None,
    ) -> Paper:
        return Paper(
            paper_id=paper_id,
            doi=doi,
            title=title,
            authors=["A. Author"],
            year=year,
            abstract="This paper studies automated literature review generation in depth.",
            url="https://example.org/landing",
            open_access_pdf_url=oa_url,
        )

    def test_doi_takes_priority_over_title(self) -> None:
        paper = self._paper("W123", doi="10.1145/abc", title="Whatever")
        self.assertEqual(dedup_key(paper), "doi:10.1145/abc")

    def test_title_and_year_fallback_when_no_doi(self) -> None:
        paper = self._paper("W123", title="Lit Review!", year=2024)
        self.assertEqual(dedup_key(paper), "title:litreview:2024")

    def test_same_doi_different_provider_ids_collapse(self) -> None:
        openalex = self._paper("https://openalex.org/W123", doi="10.1145/abc")
        semantic = self._paper("S2:abc123", doi="10.1145/abc")
        self.assertEqual(dedup_key(openalex), dedup_key(semantic))

    def test_same_title_different_year_stays_distinct(self) -> None:
        first = self._paper("W1", title="Same Title", year=2023)
        second = self._paper("W2", title="Same Title", year=2024)
        self.assertNotEqual(dedup_key(first), dedup_key(second))

    def test_safe_filename_still_uses_paper_id(self) -> None:
        self.assertEqual(_safe_filename(self._paper("W123", doi="10.1145/abc")), "W123.pdf")
        self.assertEqual(
            _safe_filename(self._paper("https://openalex.org/W123")), "W123.pdf"
        )
        self.assertEqual(_safe_filename(self._paper("https://doi.org/10.1145/abc")), "abc.pdf")

    def test_cross_source_same_doi_downloads_once(self) -> None:
        with TemporaryDirectory() as tmp:
            dest = Path(tmp)
            openalex = self._paper(
                "https://openalex.org/W123",
                doi="10.1145/abc",
                title="Shared Work",
                oa_url="https://example.org/oa.pdf",
            )
            semantic = self._paper("S2:abc123", doi="10.1145/abc", title="Shared Work")
            ranked = [rank(openalex, 1), rank(semantic, 2)]
            already_downloaded: set[str] = set()

            result = download_and_backfill(
                ranked,
                dest,
                target_n=2,
                fetcher=lambda url: b"%PDF-1.4 fake",
                already_downloaded=already_downloaded,
            )

            self.assertEqual(result.stats.downloaded, 1)
            self.assertEqual(result.stats.duplicate_reused, 1)
            self.assertEqual(already_downloaded, {"doi:10.1145/abc"})

    def test_failure_registers_dedup_key_to_avoid_retry(self) -> None:
        with TemporaryDirectory() as tmp:
            dest = Path(tmp)
            failing = self._paper(
                "W1", doi="10.1145/fail", oa_url="https://example.org/missing.pdf"
            )
            already_downloaded: set[str] = set()

            def failing_fetcher(url: str) -> bytes:
                raise PdfDownloadError("boom")

            download_and_backfill(
                [rank(failing, 1)],
                dest,
                target_n=1,
                fetcher=failing_fetcher,
                already_downloaded=already_downloaded,
            )

            self.assertIn("doi:10.1145/fail", already_downloaded)


if __name__ == "__main__":
    unittest.main()
