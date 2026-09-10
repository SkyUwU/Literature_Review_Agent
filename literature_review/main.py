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
import sys
from pathlib import Path

from literature_review import pipeline, search
from literature_review import embedding_retriever
from literature_review.embedding_retriever import Encoder
from literature_review.extraction import extract_pdf_text
from literature_review.llm_evidence import GeminiJsonClient, JsonGenerationClient, LlmEvidenceError
from literature_review.ollama_client import OllamaJsonClient
from literature_review.models import (
    EvidenceRetrievalPolicy,
    FullTextDocument,
    RankedPaper,
    SearchRequest,
)
from literature_review.pdf_downloader import Fetcher, default_fetcher, download_and_backfill
from literature_review.planning import create_llm_plan, create_rule_based_plan
from literature_review.screening import ScreeningResult, sample_candidates, screen_candidates
from literature_review.synthesis import SynthesisError
from literature_review.ranking import FilterPolicy, filter_and_rank

LIMIT = 100
MIN_YEAR = 2021
TOTAL_TARGET = 20
TOP_K_CHUNKS = 32
DEST_DIR = Path("data/papers")


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
        except Exception:
            # fall through to the deterministic rule-based plan
            pass
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


def _search_and_rank(
    query_text: str,
    *,
    json_fetcher: search.JsonFetcher,
    encoder: Encoder | None,
    paper_meta: dict[str, tuple[int | None, str | None]],
) -> list[RankedPaper]:
    """Search one query and rank its candidates, recording paper metadata.

    Returns the ranked paper list; shared by the legacy per-query download path,
    the M5e bucket-sampling path, and the gap follow-up round.
    """
    request = SearchRequest(query=query_text, limit=LIMIT, year_from=MIN_YEAR)
    response = search.search_papers(request, json_fetcher=json_fetcher)
    ranked = filter_and_rank(
        response, FilterPolicy(min_year=MIN_YEAR), encoder=encoder
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
    client_rcs: JsonGenerationClient | None = None,
    client_screen: JsonGenerationClient | None = None,
    use_llm_plan: bool = True,
    dry_run: bool = False,
    json_fetcher: search.JsonFetcher = search.fetch_json,
    pdf_fetcher: Fetcher | None = None,
    encoder: Encoder | None = None,
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

    When ``client_screen`` is provided (M5e, reuse the key1 planner client): every
    planned query is bucket-sampled (``sample_candidates``), all samples go into
    one LLM screening + gap call (``screen_candidates``), the gap may produce at
    most one follow-up round of new queries that are screened the same way, and
    the final keep/maybe decisions drive a single merged download against
    ``TOTAL_TARGET`` (keep downloaded entirely, maybe fills the remainder). With
    ``dry_run`` (or a missing screen client) screening is skipped and the legacy
    per-query download path runs unchanged.
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

    if use_screening:
        query_candidates: dict[str, list[RankedPaper]] = {}
        for planned in plan.queries:
            query_candidates[planned.query] = sample_candidates(
                _search_and_rank(
                    planned.query,
                    json_fetcher=json_fetcher,
                    encoder=effective_encoder,
                    paper_meta=paper_meta,
                )
            )
        screening_result = screen_candidates(query_candidates, client_screen)

        follow_up_candidates: dict[str, list[RankedPaper]] = {}
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
                    )
                )
            follow_up_screening = screen_candidates(follow_up_candidates, client_screen)
            for query, decisions in follow_up_screening.decisions.items():
                screening_result.decisions.setdefault(query, []).extend(decisions)

        ranked_by_id: dict[str, RankedPaper] = {}
        for candidates in query_candidates.values():
            for item in candidates:
                ranked_by_id.setdefault(item.paper.paper_id, item)
        for candidates in follow_up_candidates.values():
            for item in candidates:
                ranked_by_id.setdefault(item.paper.paper_id, item)

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

        result = download_and_backfill(
            [],
            dest_dir,
            TOTAL_TARGET,
            fetcher=fetcher,
            already_downloaded=already_downloaded,
            priority_groups={"keep": keep_ranked, "maybe": maybe_ranked},
        )
        stats_per_query.append(result.stats.to_dict())
        downloads.extend(
            {"paper_id": paper_id, "path": str(path)}
            for paper_id, path in zip(
                result.downloaded_paper_ids, result.downloaded_paths, strict=False
            )
        )
    else:
        target_n = math.ceil(TOTAL_TARGET / len(plan.queries))
        for planned in plan.queries:
            ranked_papers = _search_and_rank(
                planned.query,
                json_fetcher=json_fetcher,
                encoder=effective_encoder,
                paper_meta=paper_meta,
            )
            result = download_and_backfill(
                ranked_papers,
                dest_dir,
                target_n,
                fetcher=fetcher,
                already_downloaded=already_downloaded,
            )
            stats_per_query.append(result.stats.to_dict())
            downloads.extend(
                {"paper_id": paper_id, "path": str(path)}
                for paper_id, path in zip(
                    result.downloaded_paper_ids, result.downloaded_paths, strict=False
                )
            )

    if dry_run:
        output: dict[str, object] = {
            "plan": plan,
            "downloads": downloads,
            "stats_per_query": stats_per_query,
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
        client_rcs=client_rcs,
        retrieval_policy=EvidenceRetrievalPolicy(top_k=TOP_K_CHUNKS, max_chunks_per_paper=6),
        paper_meta=paper_meta,
    )
    return {
        "plan": plan,
        "downloads": downloads,
        "stats_per_query": stats_per_query,
        "failed_extractions": failed_extractions,
        "report": report,
        "screening": (
            screening_result.model_dump(mode="json")
            if screening_result is not None
            else None
        ),
        "follow_ups": follow_ups,
        "dry_run": False,
    }


def _build_clients(
    arguments: argparse.Namespace,
) -> tuple[JsonGenerationClient | None, JsonGenerationClient | None, JsonGenerationClient | None]:
    """Build the stage clients: planning uses key1, RCS may use local Ollama, synthesis uses key2.

    The LLM planner is the default for a full run; it is skipped on ``--dry-run``
    (zero keys) and on ``--rule-based`` (escape hatch). A missing ``GEMINI_API_KEY``
    prints a warning and keeps ``client_plan`` as ``None``, letting
    :func:`_make_plan` fall back to the deterministic rule-based plan without
    aborting the run. ``--dry-run`` never inspects either key.

    ``client_rcs`` is built from the local Ollama endpoint when ``OLLAMA_BASE_URL``
    is configured (M6); when it is missing the client stays ``None`` and the RCS
    stage falls back to the Gemini synthesis client (existing behavior).
    """
    client_plan: JsonGenerationClient | None = None
    if not arguments.dry_run and not arguments.rule_based:
        api_key_1 = os.getenv("GEMINI_API_KEY")
        if api_key_1:
            try:
                client_plan = GeminiJsonClient(api_key=api_key_1)
            except LlmEvidenceError as error:
                print(f"LLM plan unavailable, falling back to rule-based plan: {error}", file=sys.stderr)
                client_plan = None
        else:
            print("GEMINI_API_KEY 未設定：改用 rule-based 搜尋計畫（逃生門）", file=sys.stderr)
    client_synth: JsonGenerationClient | None = None
    if not arguments.dry_run:
        api_key_2 = os.getenv("GEMINI_API_KEY_2")
        if not api_key_2:
            print("請在 .env 設定 GEMINI_API_KEY_2", file=sys.stderr)
            raise SystemExit(1)
        client_synth = GeminiJsonClient(api_key=api_key_2)
    client_rcs: JsonGenerationClient | None = None
    if not arguments.dry_run:
        if os.getenv("OLLAMA_BASE_URL"):
            try:
                client_rcs = OllamaJsonClient()
            except LlmEvidenceError as error:
                print(f"Ollama RCS client unavailable: {error}", file=sys.stderr)
                client_rcs = None
    return client_plan, client_synth, client_rcs


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
    arguments = parser.parse_args()

    try:
        query = input("請輸入 research query：")
    except EOFError:
        print("No query provided; exiting.", file=sys.stderr)
        raise SystemExit(1) from None

    client_plan, client_synth, client_rcs = _build_clients(arguments)

    try:
        result = run_end_to_end(
            query,
            dest_dir=DEST_DIR,
            client_plan=client_plan,
            client_synth=client_synth,
            client_rcs=client_rcs,
            client_screen=client_plan,
            use_llm_plan=not arguments.rule_based and not arguments.dry_run,
            dry_run=arguments.dry_run,
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
        pipeline._flush_langfuse()


if __name__ == "__main__":
    main()
