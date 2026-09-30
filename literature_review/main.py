"""End-to-end entry point: query -> plan -> search -> rank -> download -> synthesize.

One interactive query produces a :class:`SearchPlan`; every planned query is then
searched and ranked. With a screening client (M5e, key1) each query's candidates
are bucket-sampled, screened in one LLM call, optionally extended by one gap
follow-up round, and downloaded in a single merged keep/maybe pass against
``TOTAL_TARGET``; without a screen client each query is downloaded independently
with ``target_n = ceil(TOTAL_TARGET / query_count)`` (legacy per-query path). A
shared ``already_downloaded`` set deduplicates in both paths: a paper whose id
is already in the set counts as satisfied without writing a new file and
without triggering a backfill. Real runs rank papers by bge-small-en-v1.5
embedding similarity to title+abstract (plus citation/recency) with hard-coded
limits ``LIMIT=100`` / ``TOTAL_TARGET=20`` / ``TOP_K_CHUNKS=32``. The LLM
planner is the default (``GEMINI_API_KEY``); ``--rule-based`` is the escape
hatch, and ``--dry-run`` always forces the deterministic rule-based plan with
lexical ranking (no embedding model), so a dry run stops after downloads with
no text extraction, embedding encoder, LLM, or API key touched.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import sys
import time
from collections.abc import Callable, Iterator
from datetime import date, datetime
from pathlib import Path
from urllib.parse import quote

from literature_review import pipeline, search, ss_search
from literature_review import embedding_retriever
from literature_review.embedding_retriever import Encoder
from literature_review.extraction import extract_pdf_text
from literature_review.llm_evidence import (
    DailyQuotaExhausted,
    GeminiJsonClient,
    JsonGenerationClient,
    LlmEvidenceError,
    LlmServiceError,
    load_local_env,
)
from literature_review.ollama_client import OllamaJsonClient
from literature_review.models import (
    DownloadedPaperEntry,
    FullTextDocument,
    Paper,
    PapersOutput,
    RankedPaper,
    SearchPlan,
    SearchRequest,
    SearchResponse,
    SynthesisResponse,
)
from literature_review.pdf_downloader import Fetcher, default_fetcher, download_and_backfill
from literature_review.planning import create_llm_plan, create_rule_based_plan
from literature_review.screening import (
    SampledCandidates,
    ScreeningResult,
    sample_candidates,
    screen_candidates,
)
from literature_review.synthesis import SynthesisError
from literature_review.ranking import (
    FilterPolicy,
    filter_and_rank,
    resolve_venues,
)

LIMIT = 100
YEAR_WINDOW = 3
TOTAL_TARGET = 20
TOP_K_CHUNKS = 32  # legacy: C2b per-paper sampling uses FunctionalScoringPolicy.top_chunks_per_paper
DEST_DIR = Path("data/run")
OPENALEX_DOI_LOOKUP_URL = "https://api.openalex.org/works/doi:"
BACKFILL_PACE_SECONDS = 0.2


def default_min_year(today: date | None = None) -> int:
    """Inclusive lower bound of the last ``YEAR_WINDOW`` years (2026 → 2024)."""
    return (today or date.today()).year - YEAR_WINDOW + 1


def _resolve_venues(raw: str | None) -> tuple[str, ...]:
    """Parse a ``--venues`` value into normalized whitelist tokens.

    ``None`` selects the built-in top-venue default; ``""``/``"none"``
    disable the restriction; recognized conference names expand to that
    conference's full alias set. See ``ranking.resolve_venues`` for the
    full matching semantics.
    """
    return resolve_venues(raw)


def _make_plan(
    query: str,
    *,
    use_llm_plan: bool,
    client_plan: JsonGenerationClient | None,
) -> "object":
    """Build a SearchPlan, preferring the LLM planner; the rule-based plan backs it up.

    The LLM planner runs whenever ``use_llm_plan`` is true and a client is
    available; a missing client or a failed attempt (bad key, network, invalid
    output) falls back to the deterministic rule-based plan, which always works
    without a key, so planning never crashes the entry point.
    """
    if use_llm_plan and client_plan is not None:
        try:
            return create_llm_plan(query, client_plan)
        except Exception as error:
            print(
                f"LLM plan unavailable, falling back to rule-based plan: {error}",
                file=sys.stderr,
            )
    return create_rule_based_plan(query)


def _print_plan(plan: object) -> None:
    """Print the selected search plan with every query's purpose and the perspectives.

    The plan printed here includes the facets each planned query targets, so a
    real run's stdout log stays traceable to the search angles (M5a Todo 3).
    """
    print(
        f"Search plan (generated_by={plan.generated_by}): "
        + "; ".join(f"{item.query} | purpose: {item.purpose}" for item in plan.queries)
        + f" | perspectives: {', '.join(plan.perspectives)}"
    )


def _needs_abstract(paper: Paper) -> bool:
    """True when a paper carries no usable abstract (placeholder or too short)."""
    return (
        paper.abstract == ss_search.ABSTRACT_PLACEHOLDER
        or len(paper.abstract.strip()) < 20
    )


def backfill_abstracts(
    papers: list[Paper],
    *,
    json_fetcher: search.JsonFetcher = search.fetch_json,
) -> list[Paper]:
    """Replace placeholder abstracts by looking each paper up in OpenAlex by DOI.

    Semantic Scholar sometimes returns records without an abstract, but the
    OpenAlex DOI endpoint usually still has one. Papers without a DOI, or whose
    lookup fails, keep the placeholder so the caller can filter them out.
    """
    last_request_at = 0.0
    for paper in papers:
        if paper.doi is None or not _needs_abstract(paper):
            continue
        elapsed = time.monotonic() - last_request_at
        if elapsed < BACKFILL_PACE_SECONDS:
            time.sleep(BACKFILL_PACE_SECONDS - elapsed)
        last_request_at = time.monotonic()
        url = (
            f"{OPENALEX_DOI_LOOKUP_URL}{quote(paper.doi, safe='')}"
            "?select=abstract_inverted_index"
        )
        try:
            payload = json_fetcher(url)
        except Exception as error:
            print(f"Abstract backfill failed for {paper.doi}: {error}", file=sys.stderr)
            continue
        abstract = search.reconstruct_abstract(payload.get("abstract_inverted_index"))
        if abstract is not None and len(abstract.strip()) >= 20:
            paper.abstract = abstract
        else:
            print(f"No OpenAlex abstract for {paper.doi}", file=sys.stderr)
    return papers


def _search_candidates(
    query_text: str,
    *,
    json_fetcher: search.JsonFetcher,
    ss_api_key: str | None,
    year_from: int,
    year_to: int | None = None,
) -> SearchResponse:
    """Search one query through Semantic Scholar, falling back to OpenAlex.

    With ``ss_api_key`` Semantic Scholar is primary; its placeholder abstracts are
    backfilled from OpenAlex and any paper still lacking an abstract is dropped.
    A missing key, an empty Semantic Scholar result, or a failed request (for
    example repeated HTTP 429) falls back to OpenAlex unchanged. ``year_from``
    and ``year_to`` bound the search window on the provider request itself.
    """
    request = SearchRequest(
        query=query_text, limit=LIMIT, year_from=year_from, year_to=year_to
    )
    if not ss_api_key:
        return search.search_papers(request, json_fetcher=json_fetcher)
    try:
        response = ss_search.search_ss(request, api_key=ss_api_key)
    except ss_search.SsSearchError as error:
        print(
            f"Semantic Scholar search failed; falling back to OpenAlex: {error}",
            file=sys.stderr,
        )
        return search.search_papers(request, json_fetcher=json_fetcher)
    if not response.papers:
        print(
            "Semantic Scholar returned no candidates; falling back to OpenAlex.",
            file=sys.stderr,
        )
        return search.search_papers(request, json_fetcher=json_fetcher)
    papers = backfill_abstracts(response.papers, json_fetcher=json_fetcher)
    papers = [paper for paper in papers if not _needs_abstract(paper)]
    return response.model_copy(
        update={
            "papers": papers,
            "skipped_candidates": response.total_candidates - len(papers),
        }
    )


def _search_and_rank(
    query_text: str,
    *,
    json_fetcher: search.JsonFetcher,
    encoder: Encoder | None,
    paper_meta: dict[str, tuple[int | None, str | None]],
    ss_api_key: str | None = None,
    year_from: int | None = None,
    year_to: int | None = None,
    venues: tuple[str, ...] = (),
) -> list[RankedPaper]:
    """Search one query and rank its candidates, recording paper metadata.

    Returns the ranked paper list; shared by the legacy per-query download path,
    the M5e bucket-sampling path, and the gap follow-up round. With ``ss_api_key``
    the search is served by Semantic Scholar, otherwise by OpenAlex. ``year_from``
    defaults to the current ``YEAR_WINDOW`` and is used both for the provider
    request and as the min-year backstop, so the window stays consistent; a
    non-empty ``venues`` whitelist hard-filters candidates after retrieval.
    """
    effective_year_from = year_from if year_from is not None else default_min_year()
    response = _search_candidates(
        query_text,
        json_fetcher=json_fetcher,
        ss_api_key=ss_api_key,
        year_from=effective_year_from,
        year_to=year_to,
    )
    ranked = filter_and_rank(
        response,
        FilterPolicy(min_year=effective_year_from, venues=venues),
        encoder=encoder,
    )
    paper_meta.update(
        {
            item.paper.paper_id: (item.paper.citation_count, item.paper.venue)
            for item in ranked.ranked_papers
        }
    )
    return ranked.ranked_papers


def run_end_to_end(
    query: str,
    *,
    dest_dir: Path,
    client_plan: JsonGenerationClient | None = None,
    client_synth: JsonGenerationClient | None = None,
    client_scoring: JsonGenerationClient | None = None,
    client_rcs: JsonGenerationClient | None = None,
    client_screen: JsonGenerationClient | None = None,
    client_report: JsonGenerationClient | None = None,
    use_llm_plan: bool = True,
    dry_run: bool = False,
    json_fetcher: search.JsonFetcher = search.fetch_json,
    pdf_fetcher: Fetcher | None = None,
    encoder: Encoder | None = None,
    ss_api_key: str | None = None,
    year_from: int | None = None,
    year_to: int | None = None,
    venues: tuple[str, ...] = (),
) -> dict[str, object]:
    """Run one full literature-review cycle for a bare query.

    ``use_llm_plan`` defaults to True, so the LLM planner drives a full run when a
    ``client_plan`` is available; set it to False (or rely on the fallback) for the
    deterministic rule-based plan.

    ``dry_run=True`` stops after the per-query download stage: the returned dict
    contains ``plan``, ``downloads`` (paper id + local path pairs), and
    ``stats_per_query``, and no LLM client is required or called. Papers are
    ranked with the lexical baseline in a dry run (``encoder`` is ignored, so no
    embedding model is ever built).
    ``dry_run=False`` additionally extracts the PDFs and produces a synthesis
    report through ``run_synthesis_pipeline``; that path needs ``client_synth``.
    ``encoder`` selects the semantic embedding ranking for real runs; when omitted
    the default local encoder is built once and shared across planned queries.
    All external I/O (OpenAlex JSON, PDF bytes, LLM) is injectable so tests never
    touch the real network or an API key.

    ``ss_api_key`` selects the primary search provider: when set, planned queries
    are searched through Semantic Scholar, placeholder abstracts are backfilled
    from OpenAlex by DOI, and papers still lacking an abstract are dropped before
    ranking; when ``None`` or empty the search uses OpenAlex directly.

    When ``client_screen`` is provided (M5e, reuse the key1 planner client): every
    planned query is bucket-sampled (``sample_candidates``), all samples go into
    one LLM screening + gap call (``screen_candidates``), the gap may produce at
    most one follow-up round of new queries that are screened the same way, and
    the final keep/maybe decisions drive a single merged download against
    ``TOTAL_TARGET`` (keep downloaded entirely, maybe fills the remainder). With
    ``dry_run`` (or a missing screen client) screening is skipped and the legacy
    per-query download path runs unchanged.

    A quota or provider failure during screening degrades instead of aborting:
    a failed main screening call downloads the candidates it already retrieved
    (no second search, since re-searching would spend quota the run does not
    need) and leaves ``screening`` as ``None``; a failed gap follow-up call only
    drops the follow-up queries and keeps the decisions already made.
    """
    plan = _make_plan(query, use_llm_plan=use_llm_plan, client_plan=client_plan)
    _print_plan(plan)
    already_downloaded: set[str] = set()
    paper_meta: dict[str, tuple[int | None, str | None]] = {}
    downloads: list[dict[str, str]] = []
    stats_per_query: list[dict[str, object]] = []
    fetcher = pdf_fetcher if pdf_fetcher is not None else default_fetcher
    effective_encoder = (
        None
        if dry_run
        else (encoder if encoder is not None else embedding_retriever.default_encoder())
    )

    use_screening = client_screen is not None and not dry_run
    screening_result: ScreeningResult | None = None
    follow_ups: list[dict[str, str]] = []
    paper_queries: dict[str, str] = {}
    paper_titles: dict[str, str] = {}
    follow_up_queries: set[str] = set()
    downloaded_papers: dict[str, Paper] = {}
    paper_priority: dict[str, str] = {}

    def _download_per_query(
        produce: Callable[[], Iterator[tuple[str, list[RankedPaper]]]],
    ) -> None:
        """Download each ``(query, ranked)`` group against its own share of the target.

        ``produce`` is a generator, so the legacy path can search query N+1 only
        after query N has been downloaded and keep its original interleaving,
        while the degraded screening path yields the candidates it has already
        retrieved instead of searching them a second time.
        """
        target_n = math.ceil(TOTAL_TARGET / len(plan.queries))
        for query_text, ranked_papers in produce():
            result = download_and_backfill(
                ranked_papers,
                dest_dir,
                target_n,
                fetcher=fetcher,
                already_downloaded=already_downloaded,
            )
            stats_per_query.append(result.stats.to_dict())
            for paper_id in result.downloaded_paper_ids:
                paper_queries[paper_id] = query_text
            for item in ranked_papers:
                paper_titles.setdefault(item.paper.paper_id, item.paper.title)
            downloads.extend(
                {"paper_id": paper_id, "path": str(path)}
                for paper_id, path in zip(
                    result.downloaded_paper_ids, result.downloaded_paths, strict=False
                )
            )
            papers_by_id = {
                item.paper.paper_id: item.paper for item in ranked_papers
            }
            for paper_id in result.downloaded_paper_ids:
                paper = papers_by_id.get(paper_id)
                if paper is not None:
                    downloaded_papers.setdefault(paper_id, paper)

    if use_screening:
        query_candidates: dict[str, SampledCandidates] = {}
        for planned in plan.queries:
            query_candidates[planned.query] = sample_candidates(
                _search_and_rank(
                    planned.query,
                    json_fetcher=json_fetcher,
                    encoder=effective_encoder,
                    paper_meta=paper_meta,
                    ss_api_key=ss_api_key,
                    year_from=year_from,
                    year_to=year_to,
                    venues=venues,
                )
            )
        try:
            screening_result = screen_candidates(query_candidates, client_screen, main_query=query)
        except (DailyQuotaExhausted, LlmServiceError) as error:
            # The screening key is out of quota (429) or the provider is
            # overloaded (503). Retrying would spend more of the daily budget
            # for nothing, so the run degrades to a plain rank-based download of
            # the candidates already retrieved — searching them again would
            # spend search quota the run does not need. screening_result stays
            # None, so the output records that no screening happened.
            print(
                f"Screening skipped ({type(error).__name__}: {error}); downloading the "
                "already-retrieved candidates without screening.",
                file=sys.stderr,
            )
            _download_per_query(
                lambda: (
                    (query_text, sampled.papers)
                    for query_text, sampled in query_candidates.items()
                )
            )
        else:
            follow_up_queries = {
                fu.query for fu in screening_result.gap.follow_up_queries
            }

            follow_up_candidates: dict[str, SampledCandidates] = {}
            if screening_result.gap.follow_up_queries:
                for fu in screening_result.gap.follow_up_queries:
                    follow_ups.append(
                        {
                            "query": fu.query,
                            "target_gap": fu.target_gap,
                            "reason": fu.reason,
                        }
                    )
                    follow_up_candidates[fu.query] = sample_candidates(
                        _search_and_rank(
                            fu.query,
                            json_fetcher=json_fetcher,
                            encoder=effective_encoder,
                            paper_meta=paper_meta,
                            ss_api_key=ss_api_key,
                            year_from=year_from,
                            year_to=year_to,
                            venues=venues,
                        )
                    )
                try:
                    follow_up_screening = screen_candidates(
                        follow_up_candidates, client_screen, main_query=query
                    )
                except (DailyQuotaExhausted, LlmServiceError) as error:
                    # The main screening already succeeded, so a failed gap round
                    # only costs the follow-up queries — the keep/maybe decisions
                    # collected so far stay valid and the run continues.
                    print(
                        f"Gap follow-up screening skipped ({type(error).__name__}: {error}); "
                        "keeping the decisions from the main screening round.",
                        file=sys.stderr,
                    )
                else:
                    for query, decisions in follow_up_screening.decisions.items():
                        screening_result.decisions.setdefault(query, []).extend(decisions)

            ranked_by_id: dict[str, RankedPaper] = {}
            for sampled in query_candidates.values():
                for item in sampled.papers:
                    ranked_by_id.setdefault(item.paper.paper_id, item)
            for sampled in follow_up_candidates.values():
                for item in sampled.papers:
                    ranked_by_id.setdefault(item.paper.paper_id, item)
            paper_titles = {
                paper_id: item.paper.title for paper_id, item in ranked_by_id.items()
            }
            # 追蹤 paper_id → 下載來源 query:後寫入者(follow-up)優先,供配額分組用
            for query_text, decisions in screening_result.decisions.items():
                for decision in decisions:
                    paper_queries[decision.paper_id] = query_text

            keep_ranked: list[RankedPaper] = []
            maybe_ranked: list[RankedPaper] = []
            seen_paper_ids: set[str] = set()
            for decisions in screening_result.decisions.values():
                for decision in decisions:
                    if decision.paper_id in seen_paper_ids:
                        continue
                    item = ranked_by_id.get(decision.paper_id)
                    if item is None:
                        # defensive: screening decisions resolve from the same pool
                        continue
                    seen_paper_ids.add(decision.paper_id)
                    if decision.priority == "keep":
                        keep_ranked.append(item)
                    elif decision.priority == "maybe":
                        maybe_ranked.append(item)
            paper_priority = {
                item.paper.paper_id: "keep" for item in keep_ranked
            } | {item.paper.paper_id: "maybe" for item in maybe_ranked}

            result = download_and_backfill(
                [],
                dest_dir,
                TOTAL_TARGET,
                fetcher=fetcher,
                already_downloaded=already_downloaded,
                priority_groups={"keep": keep_ranked, "maybe": maybe_ranked},
            )
            stats_per_query.append(result.stats.to_dict())
            downloaded_ids = set(result.downloaded_paper_ids)
            paper_queries = {
                paper_id: query
                for paper_id, query in paper_queries.items()
                if paper_id in downloaded_ids
            }
            downloads.extend(
                {"paper_id": paper_id, "path": str(path)}
                for paper_id, path in zip(
                    result.downloaded_paper_ids, result.downloaded_paths, strict=False
                )
            )
            for paper_id in result.downloaded_paper_ids:
                item = ranked_by_id.get(paper_id)
                if item is not None:
                    downloaded_papers[paper_id] = item.paper
    else:

        def _search_then_download() -> Iterator[tuple[str, list[RankedPaper]]]:
            for planned in plan.queries:
                yield planned.query, _search_and_rank(
                    planned.query,
                    json_fetcher=json_fetcher,
                    encoder=effective_encoder,
                    paper_meta=paper_meta,
                    ss_api_key=ss_api_key,
                    year_from=year_from,
                    year_to=year_to,
                    venues=venues,
                )

        _download_per_query(_search_then_download)

    if dry_run:
        papers_output = _make_papers_output(
            query,
            plan,
            follow_ups,
            stats_per_query,
            [],
            downloads,
            downloaded_papers,
            paper_queries,
            paper_priority,
        )
        output: dict[str, object] = {
            "plan": plan,
            "downloads": downloads,
            "stats_per_query": stats_per_query,
            "papers": papers_output,
            "dry_run": True,
        }
        if screening_result is not None:
            output["screening"] = screening_result.model_dump(mode="json")
            output["follow_ups"] = follow_ups
        return output

    if client_synth is None:
        raise ValueError("client_synth is required for a non-dry-run end-to-end run.")

    documents: list[FullTextDocument] = []
    failed_extractions: list[str] = []
    for entry in downloads:
        try:
            documents.append(
                extract_pdf_text(Path(entry["path"]), entry["paper_id"])
            )
        except Exception:
            # one bad PDF must not abort the whole run; its id is recorded instead
            failed_extractions.append(entry["paper_id"])
            continue
    if not documents:
        raise ValueError("All downloaded PDFs failed text extraction; cannot synthesize.")

    report = pipeline.run_synthesis_pipeline(
        documents,
        query,
        client_synth,
        client_scoring=client_scoring,
        client_rcs=client_rcs,
        client_report=client_report,
        paper_meta=paper_meta,
        paper_titles=paper_titles,
        paper_queries=paper_queries,
        follow_up_queries=follow_up_queries,
    )
    papers_output = _make_papers_output(
        query,
        plan,
        follow_ups,
        stats_per_query,
        failed_extractions,
        downloads,
        downloaded_papers,
        paper_queries,
        paper_priority,
    )
    return {
        "plan": plan,
        "downloads": downloads,
        "stats_per_query": stats_per_query,
        "failed_extractions": failed_extractions,
        "report": report,
        "papers": papers_output,
        "screening": (
            screening_result.model_dump(mode="json")
            if screening_result is not None
            else None
        ),
        "follow_ups": follow_ups,
        "dry_run": False,
    }


def _make_papers_output(
    query: str,
    plan: SearchPlan,
    follow_ups: list[dict[str, str]],
    stats_per_query: list[dict[str, object]],
    failed_extractions: list[str],
    downloads: list[dict[str, str]],
    downloaded_papers: dict[str, Paper],
    paper_queries: dict[str, str],
    paper_priority: dict[str, str],
) -> PapersOutput:
    """Assemble the recorded papers for one run.

    One entry per actually-written download, in download order, carrying the full
    ``Paper`` metadata, the query that pulled it in, the on-disk path, and (for the
    screening path) the keep/maybe decision. Entries whose paper or query is
    missing are defensively skipped and reported in ``run.warnings``; they never
    occur in the normal flow.
    """
    entries: list[DownloadedPaperEntry] = []
    warnings: list[str] = []
    for entry in downloads:
        paper_id = entry["paper_id"]
        paper = downloaded_papers.get(paper_id)
        query_text = paper_queries.get(paper_id)
        if paper is None or query_text is None:
            warnings.append(f"missing record for {paper_id}")
            continue
        entries.append(
            DownloadedPaperEntry(
                paper=paper,
                query=query_text,
                local_path=entry["path"],
                priority=paper_priority.get(paper_id),
            )
        )
    return PapersOutput(
        run={
            "query": query,
            "planned_queries": [planned.query for planned in plan.queries],
            "follow_ups": follow_ups,
            "stats_per_query": stats_per_query,
            "failed_extractions": failed_extractions,
            "warnings": warnings,
        },
        papers=entries,
    )


def key_env_name(suffix: int) -> str:
    """Map a key suffix to its environment variable name.

    ``1`` is the original unsuffixed ``GEMINI_API_KEY``; every other suffix uses
    the ``GEMINI_API_KEY_<N>`` convention already used by key2 / key3 and by
    ``pairwise_eval --api-key-suffix``.
    """
    return "GEMINI_API_KEY" if suffix == 1 else f"GEMINI_API_KEY_{suffix}"


def _build_clients(
    arguments: argparse.Namespace,
) -> tuple[
    JsonGenerationClient | None,
    JsonGenerationClient | None,
    JsonGenerationClient | None,
    JsonGenerationClient | None,
    JsonGenerationClient | None,
]:
    """Build clients for planning/screening, notes, scoring, and reporting.

    ``load_local_env()`` runs first for a real run. A dry run loads only the
    optional OpenAlex key so authenticated metadata search works without loading
    any Gemini keys or constructing LLM clients.

    The LLM planner is the default for a full run; it is skipped on ``--dry-run``
    (zero keys) and on ``--rule-based`` (escape hatch). A missing planner key
    prints a warning and keeps ``client_plan`` as ``None``, letting
    :func:`_make_plan` fall back to the deterministic rule-based plan without
    aborting the run. ``--dry-run`` never reads Gemini keys or constructs LLM clients.

    ``--plan-key`` / ``--notes-key`` / ``--report-key`` select which numbered key
    each stage uses (see :func:`key_env_name`), so a stage can move to a fresh
    key when the current one is exhausted. The planning and screening stages
    share the planner key, since the screening client is that same client.

    ``client_rcs`` is built from the local Ollama endpoint when ``OLLAMA_BASE_URL``
    is configured (M6); when it is missing the client stays ``None`` and the RCS
    stage falls back to the Gemini synthesis client (existing behavior).
    ``client_report`` (C2c) uses the dedicated ``GEMINI_API_KEY_3``: a full run
    without it exits with code 1 (the report has no fallback, mirroring key2).
    """
    plan_suffix = getattr(arguments, "plan_key", 1)
    notes_suffix = getattr(arguments, "notes_key", 2)
    scoring_suffix = getattr(arguments, "scoring_key", None)
    scoring_key_explicit = scoring_suffix is not None
    if scoring_suffix is None:
        scoring_suffix = notes_suffix
    report_suffix = getattr(arguments, "report_key", 3)
    if arguments.dry_run:
        load_local_env(only={"OPENALEX_API_KEY"})
    else:
        load_local_env()
    client_plan: JsonGenerationClient | None = None
    if not arguments.dry_run and not arguments.rule_based:
        api_key_1 = os.getenv(key_env_name(plan_suffix))
        if api_key_1:
            try:
                client_plan = GeminiJsonClient(api_key=api_key_1, label="plan+screening")
            except LlmEvidenceError as error:
                print(f"LLM plan unavailable, falling back to rule-based plan: {error}", file=sys.stderr)
                client_plan = None
        else:
            print(
                f"{key_env_name(plan_suffix)} 未設定：改用 rule-based 搜尋計畫（逃生門）",
                file=sys.stderr,
            )
    client_synth: JsonGenerationClient | None = None
    client_scoring: JsonGenerationClient | None = None
    if not arguments.dry_run:
        api_key_2 = os.getenv(key_env_name(notes_suffix))
        if not api_key_2:
            print(f"請在 .env 設定 {key_env_name(notes_suffix)}", file=sys.stderr)
            raise SystemExit(1)
        client_synth = GeminiJsonClient(api_key=api_key_2, label="paper-notes")
        if scoring_key_explicit or not os.getenv("OLLAMA_BASE_URL"):
            api_key_scoring = os.getenv(key_env_name(scoring_suffix))
            if not api_key_scoring:
                print(f"請在 .env 設定 {key_env_name(scoring_suffix)}", file=sys.stderr)
                raise SystemExit(1)
            client_scoring = GeminiJsonClient(
                api_key=api_key_scoring, label="functional-scoring"
            )
    client_rcs: JsonGenerationClient | None = None
    if not arguments.dry_run:
        if os.getenv("OLLAMA_BASE_URL"):
            try:
                client_rcs = OllamaJsonClient()
            except LlmEvidenceError as error:
                print(f"Ollama RCS client unavailable: {error}", file=sys.stderr)
                client_rcs = None
    client_report: JsonGenerationClient | None = None
    if not arguments.dry_run:
        api_key_3 = os.getenv(key_env_name(report_suffix))
        if not api_key_3:
            print(f"請在 .env 設定 {key_env_name(report_suffix)}", file=sys.stderr)
            raise SystemExit(1)
        client_report = GeminiJsonClient(api_key=api_key_3, label="report")
    return client_plan, client_synth, client_scoring, client_rcs, client_report


def save_report_output(
    report: SynthesisResponse,
    *,
    output_dir: str | Path = "data/outputs",
    timestamp: datetime | None = None,
) -> str | None:
    """Serialize *report* to a timestamped JSON file under *output_dir*.

    Returns the written file path on success, or ``None`` when the write
    fails (a warning is printed to stderr; the pipeline never crashes).
    """
    try:
        payload = report.model_dump(mode="json")
        Path(output_dir).mkdir(parents=True, exist_ok=True)
        ts = timestamp if timestamp is not None else datetime.now()
        filename = f"report_{ts.strftime('%Y%m%d_%H%M%S_%f')}.json"
        path = Path(output_dir) / filename
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        return str(path)
    except OSError as error:
        print(f"Failed to save report JSON: {error}", file=sys.stderr)
        return None


def save_papers_output(
    output: PapersOutput,
    *,
    output_dir: str | Path = "data/outputs",
    timestamp: datetime | None = None,
) -> str | None:
    """Serialize *output* (downloaded papers + run overview) to a timestamped JSON file.

    Shares the folder and timestamp convention of ``save_report_output`` so a
    single run's report and papers files line up. Returns the written file path
    on success, or ``None`` when the write fails (warning to stderr, no crash).
    """
    try:
        payload = output.model_dump(mode="json")
        Path(output_dir).mkdir(parents=True, exist_ok=True)
        ts = timestamp if timestamp is not None else datetime.now()
        filename = f"papers_{ts.strftime('%Y%m%d_%H%M%S_%f')}.json"
        path = Path(output_dir) / filename
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        return str(path)
    except OSError as error:
        print(f"Failed to save papers JSON: {error}", file=sys.stderr)
        return None


def main() -> None:
    parser = argparse.ArgumentParser(
        description="End-to-end literature review from an interactive query."
    )
    parser.add_argument(
        "--rule-based",
        action="store_true",
        help="Force the deterministic rule-based search planner (escape hatch; the default is the LLM planner)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Stop after downloads; no extraction, no embedding encoder, no LLM, no key",
    )
    parser.add_argument(
        "--dest-dir",
        type=Path,
        help="Download folder override; when set it is NOT cleared before the run",
    )
    parser.add_argument(
        "--year-from",
        type=int,
        help="Escape hatch: override the lower bound of the year window",
    )
    parser.add_argument(
        "--year-to",
        type=int,
        help="Escape hatch: override the upper bound of the year window",
    )
    parser.add_argument(
        "--venues",
        help="Comma-separated top venues by name (matches the built-in 17-conference list by key or alias and expands its aliases; unrecognized names filter as raw substrings with a warning); 'none' or an empty string disables the filter; default is all built-in top venues",
    )
    parser.add_argument(
        "--plan-key",
        type=int,
        default=1,
        metavar="N",
        help="Key suffix for the planner and the screening stage (1=GEMINI_API_KEY, 4=GEMINI_API_KEY_4); use a fresh key when the current one is quota-exhausted",
    )
    parser.add_argument(
        "--notes-key",
        type=int,
        default=2,
        metavar="N",
        help="Key suffix for per-paper notes (default 2)",
    )
    parser.add_argument(
        "--scoring-key",
        type=int,
        default=None,
        metavar="N",
        help="Key suffix for functional scoring (defaults to --notes-key for backward compatibility)",
    )
    parser.add_argument(
        "--report-key",
        type=int,
        default=3,
        metavar="N",
        help="Key suffix for the synthesis report stage (default 3)",
    )
    arguments = parser.parse_args()

    env_dest_dir = os.getenv("DEST_DIR")
    explicit_dest = arguments.dest_dir is not None or bool(env_dest_dir)
    dest_dir = arguments.dest_dir or Path(env_dest_dir or DEST_DIR)
    if not explicit_dest:
        # The default download folder is rebuilt for every system run so the
        # outputs reflect exactly one run; explicitly chosen folders belong to
        # the caller and are left untouched.
        shutil.rmtree(dest_dir, ignore_errors=True)
        dest_dir.mkdir(parents=True, exist_ok=True)

    try:
        query = input("請輸入 research query：")
    except EOFError:
        print("No query provided; exiting.", file=sys.stderr)
        raise SystemExit(1) from None

    client_plan, client_synth, client_scoring, client_rcs, client_report = _build_clients(arguments)

    ss_api_key = (
        None
        if arguments.dry_run
        else (os.getenv("SEMANTIC_SCHOLAR_API_KEY") or None)
    )

    try:
        result = run_end_to_end(
            query,
            dest_dir=dest_dir,
            client_plan=client_plan,
            client_synth=client_synth,
            client_scoring=client_scoring,
            client_rcs=client_rcs,
            client_screen=client_plan,
            client_report=client_report,
            use_llm_plan=not arguments.rule_based and not arguments.dry_run,
            dry_run=arguments.dry_run,
            ss_api_key=ss_api_key,
            year_from=arguments.year_from,
            year_to=arguments.year_to,
            venues=_resolve_venues(arguments.venues),
        )
    except (LlmEvidenceError, SynthesisError, ValueError) as error:
        print(f"Pipeline failed: {error}", file=sys.stderr)
        raise SystemExit(1) from error

    if arguments.dry_run:
        print(
            json.dumps(
                {
                    "query": query,
                    "plan": result["plan"].model_dump(mode="json"),
                    "downloads": result["downloads"],
                    "stats_per_query": result["stats_per_query"],
                    "dry_run": True,
                },
                ensure_ascii=True,
                indent=2,
            )
        )
    else:
        print(result["report"].model_dump_json(indent=2))
        print(
            json.dumps(
                {
                    "query": query,
                    "plan": result["plan"].model_dump(mode="json"),
                    "downloads": result["downloads"],
                    "stats_per_query": result["stats_per_query"],
                    "failed_extractions": result["failed_extractions"],
                    "screening": result["screening"],
                    "follow_ups": result["follow_ups"],
                    "dry_run": False,
                },
                ensure_ascii=True,
                indent=2,
            )
        )
        ts = datetime.now()
        saved_path = save_report_output(result["report"], timestamp=ts)
        if saved_path:
            print(f"Report saved to: {saved_path}")
        papers_path = save_papers_output(result["papers"], timestamp=ts)
        if papers_path:
            print(f"Papers saved to: {papers_path}")
        pipeline._flush_langfuse()


if __name__ == "__main__":
    main()
