"""Offline regression tests for downloaded-paper diagnostics and failure snapshots."""

import contextlib
import io
import json
import re
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from literature_review.diagnostics import RunDiagnosticsCollector
from literature_review.functional import select_quota_threshold
from literature_review.main import run_end_to_end, save_papers_output, save_report_output
from literature_review.models import DownloadedPaperEntry, FunctionalPaperScore, FunctionalScoringPolicy, Paper, PapersOutput
from literature_review.pipeline import NotesIncompleteError, NotesCheckpointError, run_synthesis_pipeline
from tests.test_pipeline import FakeEncoder, PAPER_TEXT, SynthesisFakeClient, make_document, note_payload
from tests.test_main import FakeJsonFetcher, FakePlanClient, pdf_bytes, record_for, results_payload
from literature_review.synthesis import SynthesisError


class StableFakeClient(SynthesisFakeClient):
    def generate_json(self, prompt, schema=None):
        if prompt.startswith("Summarize this single paper"):
            pid = re.search(r"Paper ID: (.+)\n", prompt).group(1)
            return json.dumps(note_payload(pid))
        if prompt.startswith("Propose future research directions"):
            ids = re.findall(r'"claim_id": "(claim-\d+)"', prompt)
            return json.dumps({"future_directions": [{
                "title": "Evaluate broader evidence coverage",
                "rationale": "The retained evidence motivates broader evaluation of evidence selection.",
                "supporting_claim_ids": ids[:1],
            }]})
        return super().generate_json(prompt, schema)


def output_for(*ids):
    return PapersOutput(run={}, papers=[DownloadedPaperEntry(
        paper=Paper(paper_id=pid, title=pid, authors=["Author"], year=2026,
                    abstract="A detailed abstract about evidence selection.", url="https://example.org"),
        query="evidence selection", local_path=f"{pid}.pdf",
    ) for pid in ids])


class DispositionTests(unittest.TestCase):
    def test_old_json_and_unique_downloaded_ids(self):
        self.assertEqual(PapersOutput.model_validate({"run": {}, "papers": []}).paper_dispositions, [])
        collector = RunDiagnosticsCollector(output_for("a", "a", "b"))
        collector.update("undownloaded", status="failed")
        self.assertEqual(list(collector.records), ["a", "b"])
        self.assertIsNone(collector.records["a"].functional_score)
        with self.assertRaises(ValueError):
            collector.update("a", notes_status="invented")

    def test_selection_reasons_and_legacy_results_match(self):
        scores = {pid: FunctionalPaperScore(paper_id=pid, utility_score=value, n_samples=2)
                  for pid, value in [("a", 9), ("b", 8), ("c", 5), ("d", 4)]}
        queries = {pid: "group" for pid in ["a", "b", "c", "d", "empty"]}
        collector = RunDiagnosticsCollector(output_for(*queries))
        kwargs = dict(n_first_round=2, n_follow_up=1, threshold=6)
        expected = select_quota_threshold(scores, queries, set(), **kwargs)
        actual = select_quota_threshold(scores, queries, set(), diagnostics=collector, **kwargs)
        self.assertEqual(actual, expected)
        self.assertEqual(collector.records["a"].selection_status, "included")
        self.assertEqual(collector.records["c"].reason_codes, ["threshold_not_met", "quota_not_selected"])
        self.assertEqual(collector.records["d"].group_rank, 4)
        self.assertIsNone(collector.records["empty"].functional_score)
        self.assertIsNone(collector.records["empty"].assessment)
        other = RunDiagnosticsCollector(output_for(*queries))
        select_quota_threshold(scores, queries, set(), diagnostics=other,
                               n_first_round=4, n_follow_up=1, threshold=6)
        self.assertEqual(other.records["c"].reason_codes, ["threshold_not_met"])
        select_quota_threshold(scores, queries, set(), diagnostics=other,
                               n_first_round=1, n_follow_up=1, threshold=6)
        self.assertEqual(other.records["b"].reason_codes, ["quota_not_selected"])

    def pipeline(self, collector, client=None, **kwargs):
        return run_synthesis_pipeline(
            [make_document("paper-1", PAPER_TEXT), make_document("paper-2", PAPER_TEXT)],
            "evidence selection", client or StableFakeClient(), encoder=FakeEncoder(),
            diagnostics=collector, notes_pacing_seconds=0, print_section_distribution=False, **kwargs)

    def test_success_scores_chunks_and_notes(self):
        collector = RunDiagnosticsCollector(output_for("paper-1", "paper-2"))
        result = self.pipeline(collector)
        for record in collector.records.values():
            self.assertEqual(record.stage, "synthesis")
            self.assertEqual(record.notes_status, "completed")
            self.assertEqual(record.functional_score, 10)
            self.assertTrue(record.scored_chunk_ids)
        self.assertEqual(len(result.paper_summaries), 2)

    def test_prepared_empty_document_keeps_null(self):
        collector = RunDiagnosticsCollector(output_for("paper-1"))
        with self.assertRaises(ValueError):
            run_synthesis_pipeline([make_document("paper-1", "oneverylongwordwithoutspaces")], "evidence selection",
                                   SynthesisFakeClient(), encoder=FakeEncoder(), diagnostics=collector)
        self.assertEqual(collector.records["paper-1"].status, "no_usable_chunks")
        self.assertIsNone(collector.records["paper-1"].functional_score)
        self.assertEqual(collector.output.run["failed_stage"], "sampling")

    def test_scoring_failure_leaves_unexecuted_notes_pending(self):
        collector = RunDiagnosticsCollector(output_for("paper-1", "paper-2"))
        with patch("literature_review.pipeline.score_chunks_functionally", side_effect=ValueError("secret request")):
            with self.assertRaises(ValueError):
                self.pipeline(collector)
        self.assertEqual(collector.output.run["failed_stage"], "scoring")
        self.assertNotIn("secret", collector.output.model_dump_json())
        self.assertTrue(all(r.notes_status == "pending" for r in collector.records.values()))

    def test_completed_scoring_batch_survives_later_failure(self):
        class BatchFailure(StableFakeClient):
            calls = 0
            def generate_json(self, prompt, schema=None):
                self.calls += 1
                if self.calls == 2:
                    raise ValueError("second batch failed")
                return super().generate_json(prompt, schema)
        collector = RunDiagnosticsCollector(output_for("paper-1", "paper-2"))
        with self.assertRaises(ValueError):
            self.pipeline(collector, BatchFailure(), functional_policy=FunctionalScoringPolicy(batch_size=1))
        self.assertEqual(collector.records["paper-1"].functional_score, 10)
        self.assertTrue(collector.records["paper-1"].scored_chunk_ids)
        self.assertEqual(collector.records["paper-1"].selection_status, "pending")
        self.assertIsNone(collector.records["paper-2"].functional_score)

    def test_failed_notes_continue_and_reuse_checkpoint(self):
        with tempfile.TemporaryDirectory() as directory:
            checkpoint = Path(directory) / "notes"
            collector = RunDiagnosticsCollector(output_for("paper-1", "paper-2"))
            import literature_review.pipeline as module
            real_notes = module.summarize_paper_notes
            def flaky(pid, *args, **kwargs):
                if pid == "paper-1":
                    raise ValueError("Authorization: Bearer secret-key and request body")
                return real_notes(pid, *args, **kwargs)
            with patch.object(module, "summarize_paper_notes", side_effect=flaky):
                with self.assertRaises(NotesIncompleteError):
                    self.pipeline(collector, notes_checkpoint_dir=checkpoint)
            self.assertEqual(collector.records["paper-1"].notes_status, "failed")
            self.assertEqual(collector.records["paper-2"].notes_status, "completed")
            self.assertEqual(collector.records["paper-1"].selection_status, "included")
            self.assertNotIn("secret-key", collector.output.model_dump_json())
            resumed = RunDiagnosticsCollector(output_for("paper-1", "paper-2"))
            self.pipeline(resumed, notes_checkpoint_dir=checkpoint)
            self.assertEqual(resumed.records["paper-1"].notes_status, "completed")
            self.assertEqual(resumed.records["paper-2"].notes_status, "reused")

    def test_manifest_mismatch_keeps_pending_notes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / "manifest.json").write_text('{}', encoding="utf-8")
            collector = RunDiagnosticsCollector(output_for("paper-1", "paper-2"))
            with self.assertRaises(NotesCheckpointError):
                self.pipeline(collector, notes_checkpoint_dir=path)
            self.assertEqual(collector.output.run["failed_stage"], "notes")
            self.assertTrue(all(r.notes_status == "pending" for r in collector.records.values()))

    def test_report_failures_keep_completed_notes(self):
        class FailingClient(StableFakeClient):
            def __init__(self, prefix):
                super().__init__()
                self.prefix = prefix
            def generate_json(self, prompt, schema=None):
                if prompt.startswith(self.prefix):
                    raise ValueError("provider request and headers")
                return super().generate_json(prompt, schema)
        for prefix in ["Write a fluent literature-review", "Propose future research directions"]:
            with self.subTest(prefix=prefix):
                collector = RunDiagnosticsCollector(output_for("paper-1", "paper-2"))
                with self.assertRaises((ValueError, SynthesisError)):
                    self.pipeline(collector, FailingClient(prefix))
                self.assertEqual(collector.output.run["failed_stage"], "synthesis")
                self.assertTrue(all(r.notes_status == "completed" for r in collector.records.values()))

    def test_missing_notes_chunks_are_not_success(self):
        import literature_review.pipeline as module
        real_sample = module.sample_formal_chunks_per_paper
        def sample(*args, **kwargs):
            scoring, notes = real_sample(*args, **kwargs)
            notes.pop("paper-1")
            return scoring, notes
        collector = RunDiagnosticsCollector(output_for("paper-1", "paper-2"))
        with patch.object(module, "sample_formal_chunks_per_paper", side_effect=sample):
            self.pipeline(collector)
        self.assertEqual(collector.records["paper-1"].notes_status, "no_usable_chunks")
        self.assertEqual(collector.records["paper-1"].stage, "notes")

    def test_atomic_write_failure_keeps_previous_json(self):
        with tempfile.TemporaryDirectory() as directory:
            output = output_for("a")
            timestamp = datetime(2026, 10, 2)
            path = Path(save_papers_output(output, output_dir=directory, timestamp=timestamp))
            previous = path.read_bytes()
            with patch.object(Path, "replace", side_effect=OSError("secret-key")), contextlib.redirect_stderr(io.StringIO()) as log:
                self.assertIsNone(save_papers_output(output_for("b"), output_dir=directory, timestamp=timestamp))
            self.assertIn("Failed to save", log.getvalue())
            self.assertNotIn("secret-key", log.getvalue())
            self.assertEqual(path.read_bytes(), previous)
            self.assertFalse(list(Path(directory).glob("*.tmp")))

    def test_writer_error_does_not_hide_pipeline_error(self):
        def writer(output):
            raise OSError("private headers")
        collector = RunDiagnosticsCollector(output_for("paper-1", "paper-2"), on_update=writer)
        with patch("literature_review.pipeline.score_chunks_functionally", side_effect=ValueError("original error")), contextlib.redirect_stderr(io.StringIO()) as log:
            with self.assertRaisesRegex(ValueError, "original error"):
                self.pipeline(collector)
        self.assertIn("Failed to save papers diagnostics", log.getvalue())
        self.assertNotIn("private headers", log.getvalue())

    def test_main_failed_extraction_persists_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            dest = Path(directory) / "pdfs"
            output = Path(directory) / "output"
            with patch("literature_review.main.extract_pdf_text", side_effect=ValueError("private request")):
                with self.assertRaises(ValueError):
                    run_end_to_end("evidence selection", dest_dir=dest, client_plan=FakePlanClient(3),
                                   client_synth=SynthesisFakeClient(), encoder=FakeEncoder(),
                                   json_fetcher=FakeJsonFetcher([results_payload(record_for("W1")), results_payload()]),
                                   pdf_fetcher=pdf_bytes, diagnostics_output_dir=output)
            snapshots = list(output.glob("papers_*.json"))
            self.assertEqual(len(snapshots), 1)
            saved = PapersOutput.model_validate_json(snapshots[0].read_text(encoding="utf-8"))
            self.assertEqual(saved.run["status"], "failed")
            self.assertEqual(saved.run["failed_stage"], "extraction")
            self.assertEqual(saved.paper_dispositions[0].reason_codes, ["extraction_failed"])
            self.assertIsNone(saved.paper_dispositions[0].functional_score)
            self.assertFalse(list(output.glob("report_*.json")))

    def main_flow(self, directory, **kwargs):
        return run_end_to_end(
            "evidence selection", dest_dir=Path(directory) / "pdfs",
            client_plan=FakePlanClient(3), client_synth=StableFakeClient(), encoder=FakeEncoder(),
            json_fetcher=FakeJsonFetcher([results_payload(record_for("W1"), record_for("W2"))]),
            pdf_fetcher=pdf_bytes, **kwargs)

    def test_main_success_snapshot_matches_report_timestamp(self):
        timestamp = datetime(2026, 10, 2, 12, 13, 14, 15)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "output"
            with patch("literature_review.main.extract_pdf_text", side_effect=lambda path, pid: make_document(pid, PAPER_TEXT)), patch("literature_review.pipeline._notes_pacing_seconds", return_value=0):
                result = self.main_flow(directory, diagnostics_output_dir=output, output_timestamp=timestamp)
            saved = list(output.glob("papers_*.json"))
            self.assertEqual(len(saved), 1)
            snapshot = PapersOutput.model_validate_json(saved[0].read_text(encoding="utf-8"))
            self.assertEqual(snapshot.run["status"], "completed")
            self.assertEqual(len(snapshot.paper_dispositions), len(snapshot.papers))
            report = Path(save_report_output(result["report"], output_dir=output, timestamp=timestamp))
            self.assertEqual(saved[0].name.removeprefix("papers_"), report.name.removeprefix("report_"))

    def test_main_scoring_failure_snapshot_is_failed(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "output"
            with patch("literature_review.main.extract_pdf_text", side_effect=lambda path, pid: make_document(pid, PAPER_TEXT)), patch("literature_review.pipeline.score_chunks_functionally", side_effect=ValueError("private headers")):
                with self.assertRaises(ValueError):
                    self.main_flow(directory, diagnostics_output_dir=output)
            saved = PapersOutput.model_validate_json(next(output.glob("papers_*.json")).read_text(encoding="utf-8"))
            self.assertEqual(saved.run["failed_stage"], "scoring")
            self.assertTrue(all(r.notes_status == "pending" for r in saved.paper_dispositions))

    def test_dry_run_does_not_persist_even_with_output_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "output"
            result = self.main_flow(directory, dry_run=True, diagnostics_output_dir=output)
            self.assertFalse(output.exists())
            self.assertTrue(result["dry_run"])


if __name__ == "__main__":
    unittest.main()
