"""Caller-owned downloaded-paper diagnostics; persistence is explicitly opt-in."""

from collections.abc import Callable
import uuid
import sys
from functools import wraps

from literature_review.models import PaperDisposition, PapersOutput, ProcessingStage, RunDiagnostics


def sanitized_error(error: BaseException) -> str:
    # Provider exceptions may embed keys, headers, request bodies or source text.
    # Persist only the class; the stage and structured reason carry context.
    return f"{type(error).__name__}: processing failed (details omitted)"


class RunDiagnosticsCollector:
    def __init__(self, output: PapersOutput, *, run_id: str | None = None,
                 notes_checkpoint_id: str | None = None,
                 on_update: Callable[[PapersOutput], object] | None = None):
        self.output = output
        self.run = RunDiagnostics(run_id=run_id or uuid.uuid4().hex,
                                  notes_checkpoint_id=notes_checkpoint_id)
        self.stage: ProcessingStage = "download"
        self.on_update = on_update
        self.records = {entry.paper.paper_id: PaperDisposition(paper_id=entry.paper.paper_id)
                        for entry in output.papers}
        output.paper_dispositions = list(self.records.values())

    def flush(self) -> None:
        self.output.run.update(self.run.model_dump(mode="json"))
        if self.on_update is not None:
            try:
                self.on_update(self.output)
            except OSError as error:
                print(f"Failed to save papers diagnostics: {sanitized_error(error)}", file=sys.stderr)

    def enter(self, stage: ProcessingStage) -> None:
        self.stage = stage

    def update(self, paper_id: str, **values: object) -> None:
        if paper_id not in self.records:
            return  # Never add undownloaded candidates.
        record = self.records[paper_id]
        validated = PaperDisposition.model_validate({**record.model_dump(), **values})
        self.records[paper_id] = validated
        self.output.paper_dispositions = list(self.records.values())
        self.flush()

    def fail(self, error: BaseException) -> None:
        self.run.status = "failed"
        self.run.failed_stage = self.stage
        self.run.error = sanitized_error(error)
        self.flush()


def record_failures(function):
    """Retain failure state for direct callers without changing return contracts."""
    @wraps(function)
    def wrapped(*args, **kwargs):
        diagnostics = kwargs.get("diagnostics")
        try:
            result = function(*args, **kwargs)
        except Exception as error:
            if diagnostics is not None:
                diagnostics.fail(error)
            raise
        if diagnostics is not None:
            diagnostics.run.status = "completed"
            diagnostics.flush()
        return result
    return wrapped

