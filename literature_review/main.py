"""End-to-end entry point: query -> plan -> search -> rank -> download -> synthesize.

One interactive query produces a :class:`SearchPlan`; every planned query is then
searched, ranked, and downloaded independently (no cross-query merging). A shared
``already_downloaded`` set deduplicates across queries: a paper whose id is
already in the set counts as satisfied without writing a new file and without
triggering a backfill. The LLM planner is the default (``GEMINI_API_KEY``);
``--rule-based`` is the escape hatch, and ``--dry-run`` always forces the
deterministic rule-based plan, so a dry run stops after downloads with no text
extraction, embedding encoder, LLM, or API key touched.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from pathlib import Path

from literature_review import pipeline, search
from literature_review.extraction import extract_pdf_text
from literature_review.llm_evidence import GeminiJsonClient, JsonGenerationClient, LlmEvidenceError
from literature_review.models import (
    EvidenceRetrievalPolicy,
    FullTextDocument,
    SearchRequest,
)
from literature_review.pdf_downloader import Fetcher, default_fetcher, download_and_backfill
from literature_review.planning import create_llm_plan, create_rule_based_plan
from literature_review.synthesis import SynthesisError
from literature_review.ranking import FilterPolicy, filter_and_rank

LIMIT = 50
MIN_YEAR = 2021
TOTAL_TARGET = 15
TOP_K_CHUNKS = 8
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


def run_end_to_end(
    query: str,
    *,
    dest_dir: Path,
    client_plan: JsonGenerationClient | None = None,
    client_synth: JsonGenerationClient | None = None,
    use_llm_plan: bool = True,
    dry_run: bool = False,
    json_fetcher: search.JsonFetcher = search.fetch_json,
    pdf_fetcher: Fetcher | None = None,
) -> dict[str, object]:
    """Run one full literature-review cycle for a bare query.

    ``use_llm_plan`` defaults to True, so the LLM planner drives a full run when a
    ``client_plan`` is available; set it to False (or rely on the fallback) for the
    deterministic rule-based plan.

    ``dry_run=True`` stops after the per-query download stage: the returned dict
    contains ``plan``, ``downloads`` (paper id + local path pairs), and
    ``stats_per_query``, and no LLM client is required or called.
    ``dry_run=False`` additionally extracts the PDFs and produces a synthesis
    report through ``run_synthesis_pipeline``; that path needs ``client_synth``.
    All external I/O (OpenAlex JSON, PDF bytes, LLM) is injectable so tests never
    touch the real network or an API key.
    """
    plan = _make_plan(query, use_llm_plan=use_llm_plan, client_plan=client_plan)
    target_n = math.ceil(TOTAL_TARGET / len(plan.queries))
    already_downloaded: set[str] = set()
    downloads: list[dict[str, str]] = []
    stats_per_query: list[dict[str, object]] = []
    fetcher = pdf_fetcher if pdf_fetcher is not None else default_fetcher

    for planned in plan.queries:
        request = SearchRequest(query=planned.query, limit=LIMIT, year_from=MIN_YEAR)
        response = search.search_papers(request, json_fetcher=json_fetcher)
        ranked = filter_and_rank(response, FilterPolicy(min_year=MIN_YEAR))
        result = download_and_backfill(
            ranked.ranked_papers,
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
        return {
            "plan": plan,
            "downloads": downloads,
            "stats_per_query": stats_per_query,
            "dry_run": True,
        }

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
        retrieval_policy=EvidenceRetrievalPolicy(top_k=TOP_K_CHUNKS),
    )
    return {
        "plan": plan,
        "downloads": downloads,
        "stats_per_query": stats_per_query,
        "failed_extractions": failed_extractions,
        "report": report,
        "dry_run": False,
    }


def _build_clients(arguments: argparse.Namespace) -> tuple[JsonGenerationClient | None, JsonGenerationClient | None]:
    """Build the two stage clients: planning uses key1, synthesis uses key2.

    The LLM planner is the default for a full run; it is skipped on ``--dry-run``
    (zero keys) and on ``--rule-based`` (escape hatch). A missing ``GEMINI_API_KEY``
    prints a warning and keeps ``client_plan`` as ``None``, letting
    :func:`_make_plan` fall back to the deterministic rule-based plan without
    aborting the run. ``--dry-run`` never inspects either key.
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
    return client_plan, client_synth


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

    client_plan, client_synth = _build_clients(arguments)

    try:
        result = run_end_to_end(
            query,
            dest_dir=DEST_DIR,
            client_plan=client_plan,
            client_synth=client_synth,
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
