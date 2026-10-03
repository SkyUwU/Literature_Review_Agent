"""Caller-owned downloaded-paper diagnostics; persistence is explicitly opt-in."""

from collections.abc import Callable
import uuid
import sys
from functools import wraps

from literature_review.models import PaperDisposition, PapersOutput, ProcessingStage, RunDiagnostics, CandidateEvent, StageSummary


def sanitized_error(error: BaseException) -> str:
    # Provider exceptions may embed keys, headers, request bodies or source text.
    # Persist only the class; the stage and structured reason carry context.
    return f"{type(error).__name__}: processing failed (details omitted)"


class RunDiagnosticsCollector:
    def __init__(self, output: PapersOutput, *, run_id: str | None = None,
                 notes_checkpoint_id: str | None = None,
                 on_update: Callable[[PapersOutput], object] | None = None):
        self.output = output
        self.run = RunDiagnostics(run_id=run_id or output.run.get('run_id') or uuid.uuid4().hex,
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


def safe_paper(paper):
    from literature_review.pdf_fetch import sanitize_url
    values = paper.model_dump(mode='json')
    values['url'] = sanitize_url(values['url'])
    values['open_access_pdf_url'] = sanitize_url(values.get('open_access_pdf_url'))
    for loc in values['fulltext_locations']:
        loc['pdf_url'] = sanitize_url(loc.get('pdf_url'))
        loc['landing_url'] = sanitize_url(loc.get('landing_url'))
    for venue in values['publication_venues']:
        venue['source_url'] = sanitize_url(venue.get('source_url'))
    return values


class CandidateDiagnostics:
    """Query-scoped evidence for records never admitted to downloaded dispositions."""
    def __init__(self, query, *, on_update=None):
        self.output = PapersOutput(run={'query': query, 'run_id': uuid.uuid4().hex, 'status': 'in_progress'})
        self.on_update = on_update
        self.stage = 'search'
        self.current = None
        self.source_context = None

    def begin(self, query, round='initial'):
        self.stage = 'search'
        self.current = StageSummary(query_id=f'q{len(self.output.stage_summaries) + 1}', query=query,
            round=round, provider='unknown', counts={k: None for k in (
                'received', 'normalized', 'abstract_usable', 'after_year', 'after_venue', 'ranked',
                'sampled', 'screened_keep', 'screened_maybe', 'screened_reject', 'download_selected',
                'download_attempted', 'download_valid')})
        self.output.stage_summaries.append(self.current)
        self.flush()

    def event(self, paper_id, stage, action, reasons=(), *, provider=None, record_key=None, **extra):
        if self.current is None:
            return
        self.output.candidate_events.append(CandidateEvent(
            event_id=uuid.uuid4().hex, query_id=self.current.query_id, query=self.current.query,
            round=self.current.round, provider=provider or self.current.provider,
            record_key=record_key or f'{provider or self.current.provider}:{paper_id}', paper_id=paper_id,
            stage=stage, action=action, reason_codes=list(reasons), sequence=len(self.output.candidate_events) + 1,
            **extra))

    def observe(self, provider, record, paper, index):
        self.current.provider = provider
        self.current.counts['received'] = index + 1
        self.current.counts['normalized'] = (self.current.counts['normalized'] or 0) + int(paper is not None)
        raw_id = record.get('id') or record.get('paperId') if isinstance(record, dict) else None
        if not isinstance(raw_id, str) or not raw_id:
            raw_id = None
        key = f'{provider}:{raw_id}' if raw_id else f'{self.current.query_id}:{provider}:record:{index}'
        reasons = []
        if not paper:
            reasons = ['missing_abstract'] if isinstance(record, dict) and provider == 'openalex' and not record.get('abstract_inverted_index') else ['invalid_metadata']
        self.event(paper.paper_id if paper else raw_id, 'normalization', 'retained' if paper else 'excluded',
                   reasons, provider=provider, record_key=key)
        if paper:
            values = safe_paper(paper)
            self.output.candidate_records[key] = {k: values[k] for k in ('paper_id', 'doi', 'title', 'year', 'authors', 'venue',
                'publication_venues', 'identifiers', 'fulltext_locations', 'version_relations', 'version_target', 'open_access_pdf_url')}
        elif isinstance(record, dict):
            self.output.candidate_records[key] = {k: record[k] for k in ('id', 'paperId', 'title', 'year', 'publication_year')
                                                  if k in record and isinstance(record[k], (str, int))}
        else:
            self.output.candidate_records[key] = {'record_ordinal': index, 'normalization_status': 'invalid_metadata'}
        self.flush()

    def counts(self, **counts):
        if self.current is None:
            return  # A caller may replace the entire search stage with an injected result.
        self.current.counts.update(counts)
        self.flush()

    def activate(self, query, round=None):
        self.current = next((s for s in reversed(self.output.stage_summaries) if s.query == query and (round is None or s.round == round)), self.current)

    def flush(self):
        self.output.run['stage'] = self.stage
        if self.source_context:
            self.output.source_lookup_attempts = list(self.source_context.lookup_attempts)
            self.output.run['source_recovery_policy'] = self.source_context.policy.model_dump()
            self.output.run['source_counters'] = self.source_context.counters()
        self.output.run['candidate_totals'] = {
            'record_key_count': len(self.output.candidate_records),
            'unique_provider_paper_ids': len({(e.provider, e.paper_id) for e in self.output.candidate_events if e.paper_id}),
            'definition': 'record keys are provider+ID or query+record ordinal; IDs are provider scoped; per-query totals count repeated retrievals',
        }
        if self.on_update:
            try:
                self.on_update(self.output)
            except OSError as error:
                print(f'Failed to save candidate diagnostics: {sanitized_error(error)}', file=sys.stderr)

    def adopt(self, output):
        output.candidate_events = self.output.candidate_events
        output.stage_summaries = self.output.stage_summaries
        output.candidate_records = self.output.candidate_records
        output.source_lookup_attempts = self.output.source_lookup_attempts
        output.run.update({k: v for k, v in self.output.run.items() if k not in {'query', 'stage'}})
        self.output = output
        self.flush()

