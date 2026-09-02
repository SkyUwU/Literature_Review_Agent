"""Pair-wise (A/B) comparison of lexical vs embedding top-k retrieval quality.

This is a compare-only evaluation: for each aligned ranking position it asks an
LLM judge, for the two chunks shown, which is more relevant to the query. The
display order inside each pair is randomized deterministically from
``FIXED_SEED`` (per valid pair index) and the prompt states that the order is
random, so the judge cannot bias the comparison by position.
"""

import argparse
import json
import os
import random
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel

from literature_review.embedding_retriever import (
    Encoder,
    default_encoder,
    retrieve_evidence_embedding,
)
from literature_review.evidence import chunk_document
from literature_review.evidence_ranking import retrieve_evidence
from literature_review.extraction import PdfExtractionError, extract_pdf_text
from literature_review.llm_evidence import (
    GeminiJsonClient,
    JsonGenerationClient,
    LlmEvidenceError,
    LlmOutputSyntaxError,
    build_json_repair_prompt,
)
from google.genai.errors import ClientError as GaosClientError  # 4xx 基底（其他 429 來源保險）
from google.genai._gaos.lib.compat_errors import RateLimitError as GaosRateLimitError
from literature_review.models import (
    ChunkPolicy,
    EvidenceChunk,
    EvidenceRetrievalPolicy,
    EvidenceRetrievalResponse,
)
from literature_review.retrieval_eval import expand_pdf_inputs

# Deterministic seed for A/B display-order randomization (position-bias control).
FIXED_SEED = 20260901

PairKind = Literal["same_chunk", "valid", "unpaired"]
MethodName = Literal["lexical", "embedding", "tie"]
VerdictName = Literal["embedding better", "lexical better", "comparable", "insufficient"]

# Minimum number of valid pairs required for a per-query verdict.
MIN_VALID_PAIRS = 3
# A method is declared better when its win rate over decided pairs reaches these bounds.
WIN_RATE_WIN = 0.6
WIN_RATE_LOSS = 0.4


def _sleep_quota(delay_seconds: float) -> None:
    """Free-tier rate limiting: sleep between real LLM judge calls."""
    if delay_seconds > 0:
        time.sleep(delay_seconds)


class PairwiseVerdict(BaseModel):
    """A single judge decision for one aligned ranking position."""

    pair_id: str
    winner: Literal["A", "B", "tie"]
    rationale: str


class BatchVerdict(BaseModel):
    """One judge decision positioned by array index (no pair_id needed from the LLM)."""
    winner: Literal["A", "B", "tie"]
    rationale: str


class BatchVerdictList(BaseModel):
    """Batch of judge decisions; verdicts[i] corresponds to the i-th input pair."""
    verdicts: list[BatchVerdict]


@dataclass(frozen=True)
class RankedPair:
    """One aligned ranking position between the lexical and embedding top-k lists."""

    rank: int
    kind: PairKind
    lexical_chunk: EvidenceChunk | None = None
    embedding_chunk: EvidenceChunk | None = None
    display_as_a: MethodName | None = None
    """Which method is shown as choice A to the judge (set only for valid pairs)."""


def pair_ranked(
    lexical: EvidenceRetrievalResponse,
    embedding: EvidenceRetrievalResponse,
) -> list[RankedPair]:
    """Align the two ranked lists position by position (rank 1..max top_k).

    - Same ``chunk_id`` on both sides at a rank -> ``same_chunk`` (recorded tie,
      never judged by the LLM).
    - Different chunk_ids -> ``valid`` pair (one judge call).
    - Either side missing that rank -> ``unpaired`` (counted only).
    A fresh seed per valid pair index keeps the display order deterministic.
    """
    lexical_by_rank = {item.rank: item.chunk for item in lexical.ranked_chunks}
    embedding_by_rank = {item.rank: item.chunk for item in embedding.ranked_chunks}
    max_rank = max(
        (lexical_by_rank or {1: None}).keys() | (embedding_by_rank or {1: None}).keys()
    )
    pairs: list[RankedPair] = []
    for rank in range(1, max_rank + 1):
        lexical_chunk = lexical_by_rank.get(rank)
        embedding_chunk = embedding_by_rank.get(rank)
        if lexical_chunk is None or embedding_chunk is None:
            pairs.append(
                RankedPair(
                    rank=rank,
                    kind="unpaired",
                    lexical_chunk=lexical_chunk,
                    embedding_chunk=embedding_chunk,
                )
            )
        elif lexical_chunk.chunk_id == embedding_chunk.chunk_id:
            pairs.append(
                RankedPair(
                    rank=rank,
                    kind="same_chunk",
                    lexical_chunk=lexical_chunk,
                    embedding_chunk=embedding_chunk,
                )
            )
        else:
            pairs.append(
                RankedPair(
                    rank=rank,
                    kind="valid",
                    lexical_chunk=lexical_chunk,
                    embedding_chunk=embedding_chunk,
                )
            )
    return assign_display_orders(pairs)


def assign_display_orders(
    pairs: list[RankedPair],
    seed: int = FIXED_SEED,
) -> list[RankedPair]:
    """Randomize which method is shown as A per valid pair (deterministic).

    Uses a fresh ``random.Random(seed + valid_pair_index)`` per valid pair so the
    outcome is reproducible and independent of the number of pairs (no shared RNG
    state). Non-valid pairs keep ``display_as_a=None``.
    """
    ordered: list[RankedPair] = []
    valid_index = 0
    for pair in pairs:
        if pair.kind != "valid":
            ordered.append(pair)
            continue
        rng = random.Random(seed + valid_index)
        display_as_a: MethodName = "embedding" if rng.random() < 0.5 else "lexical"
        ordered.append(
            RankedPair(
                rank=pair.rank,
                kind=pair.kind,
                lexical_chunk=pair.lexical_chunk,
                embedding_chunk=pair.embedding_chunk,
                display_as_a=display_as_a,
            )
        )
        valid_index += 1
    return ordered


def build_pairwise_prompt(
    query: str,
    chunk_a: EvidenceChunk,
    chunk_b: EvidenceChunk,
    pair_id: str,
) -> str:
    """Build the A/B judge prompt for one aligned pair.

    The prompt explicitly states that the A/B display order is randomized and
    carries no meaning, and instructs the model to judge relevance to the query
    using only the two supplied chunks (no outside knowledge). Exact JSON output
    without Markdown fences.
    """
    return (
        "You are an impartial retrieval-quality judge.\n"
        "You will see two evidence chunks labeled A and B for one ranking position. "
        "The display order of A and B is randomized with a fixed seed and carries NO "
        "meaning — do not infer anything from the order.\n"
        "Judge ONLY which chunk is more relevant to the given query, using ONLY the "
        "text of the two supplied chunks. Do not use outside knowledge and do not "
        "invent claims.\n"
        f"Pair id: {pair_id}\n"
        f"Query: {query}\n"
        "Chunk A:\n"
        f"{json.dumps(_chunk_context(chunk_a), ensure_ascii=False)}\n"
        "Chunk B:\n"
        f"{json.dumps(_chunk_context(chunk_b), ensure_ascii=False)}\n"
        "Return exactly ONE JSON object, without Markdown code fences or any "
        "surrounding explanation, matching this schema exactly: "
        '{"pair_id": string, "winner": "A" or "B" or "tie", "rationale": string}.'
    )


def _chunk_context(chunk: EvidenceChunk) -> dict[str, object]:
    """Expose the same provenance fields the pipeline uses for grounded evidence."""
    return {
        "chunk_id": chunk.chunk_id,
        "paper_id": chunk.paper_id,
        "page_start": chunk.page_start,
        "page_end": chunk.page_end,
        "text": chunk.text,
    }


BATCH_SYSTEM = (
    "You are an impartial judge comparing retrieval results. "
    "Evaluate EACH pair INDEPENDENTLY. "
    "Do not let earlier pairs influence later ones. "
    "Output a JSON object with key 'verdicts' containing an array of objects, "
    "each with: pair_id, winner (A/B/tie), rationale. "
    "The order of verdicts MUST match the input order exactly."
)


def build_batch_prompt(prompts: list[str]) -> str:
    """Combine multiple single-pair prompts into one batch prompt.

    ``prompts`` is a list of per-pair prompt strings, in the order the judge
    must answer.  Each is labelled only by its array INDEX so the LLM never
    sees a rank-derived identifier (avoids id transposition in long batches).
    """
    lines = [BATCH_SYSTEM, "", "=== PAIRS ==="]
    for i, prompt in enumerate(prompts):
        lines.append(f"\n--- INDEX {i} ---")
        lines.append(prompt)
    return "\n".join(lines)


def _load_verdict(raw_output: str, pair_id: str) -> PairwiseVerdict:
    """Validate the judge's JSON, distinguishing malformed JSON from schema drift."""
    normalized = raw_output.strip()
    if normalized.startswith("```") and normalized.endswith("```"):
        lines = normalized.splitlines()
        normalized = "\n".join(lines[1:-1]).strip()
    try:
        verdict = PairwiseVerdict.model_validate_json(normalized)
    except ValueError as error:
        details = "invalid JSON or schema mismatch"
        if hasattr(error, "errors"):
            issues = error.errors(include_url=False)
            if issues:
                location = ".".join(str(part) for part in issues[0]["loc"])
                details = f"{location}: {issues[0]['msg']}"
                if issues[0].get("type") == "json_invalid":
                    raise LlmOutputSyntaxError(
                        f"Pairwise judge output is malformed JSON ({details})."
                    ) from error
        raise LlmEvidenceError(
            f"Pairwise judge output failed validation ({details})."
        ) from error
    if verdict.pair_id != pair_id:
        raise LlmEvidenceError(
            f"Pairwise judge returned pair_id {verdict.pair_id!r}, expected {pair_id!r}."
        )
    return verdict


def judge_pair(
    client: JsonGenerationClient,
    prompt: str,
    pair_id: str,
    request_delay_seconds: float = 0.0,
) -> PairwiseVerdict:
    """One judge call with exactly one repair attempt on malformed JSON.

    A persistently malformed response propagates ``LlmEvidenceError``.
    """
    _sleep_quota(request_delay_seconds)
    raw_output = client.generate_json(prompt, PairwiseVerdict.model_json_schema())
    try:
        return _load_verdict(raw_output, pair_id)
    except LlmOutputSyntaxError:
        _sleep_quota(request_delay_seconds)
        repaired = client.generate_json(
            build_json_repair_prompt(raw_output), PairwiseVerdict.model_json_schema()
        )
        return _load_verdict(repaired, pair_id)


MAX_BATCH = 8  # ≤10 is the quality-safe zone per LLM-as-judge research.


def judge_batch(
    client: JsonGenerationClient,
    prompts: list[str],
    request_delay_seconds: float = 0.0,
) -> list[BatchVerdict]:
    """Judge multiple pairs in one LLM call.

    ``prompts`` is a list of per-pair prompt strings in a fixed order; the LLM
    returns ``verdicts`` as an array aligned by INDEX to those prompts.
    Returns verdicts in the same order.
    """
    _sleep_quota(request_delay_seconds)
    schema = BatchVerdictList.model_json_schema()
    raw = client.generate_json(build_batch_prompt(prompts), schema)
    try:
        result = BatchVerdictList.model_validate_json(raw)
    except (ValueError, LlmOutputSyntaxError):
        _sleep_quota(request_delay_seconds)
        repaired = client.generate_json(
            build_json_repair_prompt(raw), schema
        )
        result = BatchVerdictList.model_validate_json(repaired)
    if len(result.verdicts) != len(prompts):
        raise LlmEvidenceError(
            f"Batch judge returned {len(result.verdicts)} verdicts "
            f"for {len(prompts)} input pairs."
        )
    # Positional alignment: verdicts[i] already corresponds to prompts[i].
    # This REPLACES the old pair_id-set-equality validation that caused the 429/name bug.
    return result.verdicts


def evaluate_query(
    lexical: EvidenceRetrievalResponse,
    embedding: EvidenceRetrievalResponse,
    query: str,
    client: JsonGenerationClient,
    request_delay_seconds: float = 0.0,
    batch_size: int = MAX_BATCH,
) -> dict[str, object]:
    """Pair, judge (in batches), and map winners back to methods for one query."""
    pairs = pair_ranked(lexical, embedding)
    valid_pairs = [p for p in pairs if p.kind == "valid"]

    if not valid_pairs:
        report = aggregate_query(pairs, {})
        report["query"] = query
        report["pairs"] = []
        return report

    # Build per-pair prompts; keep a local id-map by position for recovery.
    local_ids: list[str] = []          # local_ids[i] = pair_id of the i-th valid pair
    prompts: list[str] = []            # prompts[i] = prompt for the i-th valid pair
    for pair in valid_pairs:
        assert pair.display_as_a in ("lexical", "embedding")
        chunk_a = (
            pair.lexical_chunk if pair.display_as_a == "lexical" else pair.embedding_chunk
        )
        chunk_b = (
            pair.embedding_chunk if pair.display_as_a == "lexical" else pair.lexical_chunk
        )
        pair_id = f"pair-{pair.rank}"
        local_ids.append(pair_id)
        prompts.append(build_pairwise_prompt(query, chunk_a, chunk_b, pair_id))

    # Batch the prompts (size <= MAX_BATCH) and judge positionally.
    batch_verdicts: list[BatchVerdict] = []
    for i in range(0, len(prompts), batch_size):
        batch_verdicts.extend(
            judge_batch(
                client,
                prompts[i : i + batch_size],
                request_delay_seconds=request_delay_seconds,
            )
        )

    # Positional decode: verdict j -> local_ids[j] -> method (via display_as_a).
    outcomes: dict[str, MethodName] = {}
    judged_pairs: list[dict[str, object]] = []
    for j, pair in enumerate(valid_pairs):
        verdict = batch_verdicts[j]
        pair_id = local_ids[j]
        winner: MethodName
        if verdict.winner == "tie":
            winner = "tie"
        elif verdict.winner == "A":
            winner = pair.display_as_a
        else:  # "B"
            winner = "embedding" if pair.display_as_a == "lexical" else "lexical"
        outcomes[pair_id] = winner
        judged_pairs.append(
            {
                "rank": pair.rank,
                "lexical_chunk_id": pair.lexical_chunk.chunk_id,
                "embedding_chunk_id": pair.embedding_chunk.chunk_id,
                "display_as_a": pair.display_as_a,
                "winner": winner,
            }
        )
    report = aggregate_query(pairs, outcomes)
    report["query"] = query
    report["pairs"] = judged_pairs
    return report


def aggregate_query(
    pairs: list[RankedPair],
    outcomes: dict[str, MethodName],
) -> dict[str, object]:
    """Compute per-query stats and an explicit verdict from the mapped winners.

    - valid_pairs < MIN_VALID_PAIRS -> ``insufficient``.
    - Else win_rate = embedding_wins / (embedding_wins + lexical_wins) over
      DECIDED pairs only (judge ties excluded from the denominator but counted).
    - win_rate >= 0.6 -> ``embedding better``; <= 0.4 -> ``lexical better``;
      otherwise ``comparable``.
    """
    valid_pairs = sum(1 for pair in pairs if pair.kind == "valid")
    same_chunk_ties = sum(1 for pair in pairs if pair.kind == "same_chunk")
    unpaired = sum(1 for pair in pairs if pair.kind == "unpaired")

    embedding_wins = sum(1 for method in outcomes.values() if method == "embedding")
    lexical_wins = sum(1 for method in outcomes.values() if method == "lexical")
    judge_ties = sum(1 for method in outcomes.values() if method == "tie")

    decided = embedding_wins + lexical_wins
    win_rate = embedding_wins / decided if decided else None

    verdict: VerdictName
    if valid_pairs < MIN_VALID_PAIRS:
        verdict = "insufficient"
    elif win_rate is None:
        verdict = "comparable"
    elif win_rate >= WIN_RATE_WIN:
        verdict = "embedding better"
    elif win_rate <= WIN_RATE_LOSS:
        verdict = "lexical better"
    else:
        verdict = "comparable"

    return {
        "valid_pairs": valid_pairs,
        "same_chunk_ties": same_chunk_ties,
        "unpaired": unpaired,
        "embedding_wins": embedding_wins,
        "lexical_wins": lexical_wins,
        "judge_ties": judge_ties,
        "win_rate_embedding": win_rate,
        "verdict": verdict,
    }


def aggregate_overall(reports: list[dict[str, object]]) -> str:
    """Overall verdict across queries: majority of decisive per-query verdicts.

    Only ``embedding better`` / ``lexical better`` count; equal decisives or none
    -> ``comparable overall`` (plan decision 5).
    """
    embedding_wins = sum(1 for report in reports if report["verdict"] == "embedding better")
    lexical_wins = sum(1 for report in reports if report["verdict"] == "lexical better")
    if embedding_wins > lexical_wins:
        return "embedding better"
    if lexical_wins > embedding_wins:
        return "lexical better"
    return "comparable overall"


# Built-in query set (plan decision 2); order is fixed and pinned by tests.
BUILTIN_QUERIES: tuple[str, ...] = (
    "literature review agent",
    "automatic tool that summarizes research papers",
    "writing a survey with help from AI",
    "an AI agent that writes and improves code",
    "agent",
    "software engineering automation with language models",
)


def build_parser() -> argparse.ArgumentParser:
    """CLI parser; exposed separately so unit tests can assert argument parsing."""
    parser = argparse.ArgumentParser(
        description="Compare lexical vs embedding retrieval over local PDFs with a pairwise (A/B) LLM judge."
    )
    parser.add_argument(
        "inputs", nargs="+", help="Path(s) to research-paper PDFs or folders of PDFs"
    )
    parser.add_argument(
        "--top-k", type=int, default=16, help="Number of chunks retrieved per method"
    )
    parser.add_argument(
        "--judge-model",
        default="gemini-2.5-flash",
        help="Gemini model used to judge pairwise relevance (LLM judge).",
    )
    parser.add_argument(
        "--query",
        action="append",
        default=None,
        help="Override the built-in query set (repeatable); when supplied only these queries run.",
    )
    parser.add_argument(
        "--judge-delay-s",
        type=float,
        default=None,
        help="Seconds to sleep between real LLM judge calls (free-tier rate limiting); overrides PAIRWISE_JUDGE_DELAY_S env var.",
    )
    parser.add_argument(
        "--checkpoint",
        default=None,
        help="Path to accumulate per-query results (JSON). Enables resume on next run.",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Skip queries already completed in the checkpoint file (requires --checkpoint).",
    )
    return parser


def build_report(
    chunks: list[EvidenceChunk],
    queries: Sequence[str],
    retrieval_policy: EvidenceRetrievalPolicy,
    client: JsonGenerationClient,
    encoder: Encoder | None = None,
    request_delay_seconds: float = 0.0,
    checkpoint_path: str | None = None,
) -> dict[str, object]:
    """Run the pairwise comparison per query; return one JSON-serializable report.

    The report carries ``top_k``, ``query_set``, the per-query blocks, and the
    overall verdict in a single object read for direct JSON serialization.
    When ``checkpoint_path`` is given, the accumulated snapshot (completed
    query list + per-query blocks so far) is atomically rewritten after each
    query so an interrupted run can resume.
    """
    per_query: list[dict[str, object]] = []
    completed: list[str] = []
    for query in queries:
        lexical = retrieve_evidence(chunks, query, retrieval_policy)
        embedding = retrieve_evidence_embedding(chunks, query, retrieval_policy, encoder=encoder)
        per_query.append(evaluate_query(lexical, embedding, query, client, request_delay_seconds=request_delay_seconds))
        completed.append(query)
        if checkpoint_path is not None:
            _write_checkpoint(checkpoint_path, queries, per_query, completed)
    return {
        "top_k": retrieval_policy.top_k,
        "query_set": list(queries),
        "queries": per_query,
        "overall_verdict": aggregate_overall(per_query),
    }


def _write_checkpoint(
    path: str,
    queries: Sequence[str],
    per_query: list[dict[str, object]],
    completed: list[str],
) -> None:
    """Atomically write the accumulated report snapshot to ``path``.

    Content grows: includes only what has been computed so far, plus the list
    of completed query strings so a later run can resume.
    """
    snapshot = {
        "top_k": None,
        "query_set": list(queries),
        "completed": completed,
        "queries": per_query,
        "partial": len(completed) < len(queries),
    }
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(snapshot, fh, ensure_ascii=True, indent=2)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def _default_paper_id(pdf_path: str) -> str:
    return pdf_path.rsplit("/", maxsplit=1)[-1].rsplit("\\", maxsplit=1)[-1]


def _merge_resumed_report(
    prior: dict[str, object],
    fresh: dict[str, object],
) -> dict[str, object]:
    """Merge a partially-completed checkpoint with the freshly computed block.

    ``prior`` carries the full ``query_set`` and the already-completed per-query
    blocks; ``fresh`` carries the blocks for the queries run in this invocation.
    Returns one full report ordered by ``query_set`` with a recomputed overall
    verdict.
    """
    query_set = list(prior["query_set"])
    by_query: dict[str, dict[str, object]] = {}
    for report in (prior, fresh):
        for block in report["queries"]:
            by_query[block["query"]] = block
    merged = [by_query[q] for q in query_set]
    top_k = fresh.get("top_k") if fresh.get("top_k") is not None else prior.get("top_k")
    return {
        "top_k": top_k,
        "query_set": query_set,
        "queries": merged,
        "overall_verdict": aggregate_overall(merged),
    }


def main(argv: list[str] | None = None) -> None:
    arguments = build_parser().parse_args(argv)
    chunk_policy = ChunkPolicy()
    retrieval_policy = EvidenceRetrievalPolicy(top_k=arguments.top_k)

    if arguments.judge_delay_s is not None:
        request_delay_seconds = arguments.judge_delay_s
    else:
        try:
            request_delay_seconds = float(os.environ.get("PAIRWISE_JUDGE_DELAY_S", "0"))
        except ValueError:
            print("PAIRWISE_JUDGE_DELAY_S must be a number.", file=sys.stderr)
            raise SystemExit(1) from None
    print(f"Pairwise eval: judge delay = {request_delay_seconds}s", file=sys.stderr)

    try:
        pdf_paths = expand_pdf_inputs(arguments.inputs)
        all_chunks: list[EvidenceChunk] = []
        for path in pdf_paths:
            document = extract_pdf_text(path, _default_paper_id(path))
            all_chunks.extend(chunk_document(document, chunk_policy))
        if not all_chunks:
            raise ValueError("No usable evidence chunks were produced from the supplied PDFs.")

        queries = arguments.query if arguments.query is not None else list(BUILTIN_QUERIES)
        client = GeminiJsonClient(model=arguments.judge_model)

        prior_report: dict[str, object] | None = None
        if arguments.resume:
            if not arguments.checkpoint:
                raise ValueError("--resume requires --checkpoint")
            if os.path.exists(arguments.checkpoint):
                with open(arguments.checkpoint, encoding="utf-8") as fh:
                    prior_report = json.load(fh)
                completed = prior_report.get("completed", [])
                queries = [q for q in queries if q not in set(completed)]
                if not queries:
                    print("All queries already completed; nothing to run.", file=sys.stderr)
                    raise SystemExit(0)
        elif arguments.checkpoint and os.path.exists(arguments.checkpoint):
            os.remove(arguments.checkpoint)

        report = build_report(
            all_chunks,
            queries,
            retrieval_policy,
            client,
            encoder=default_encoder(),
            request_delay_seconds=request_delay_seconds,
            checkpoint_path=arguments.checkpoint,
        )
        if prior_report is not None:
            report = _merge_resumed_report(prior_report, report)
        print(json.dumps(report, ensure_ascii=True, indent=2))
    except (PdfExtractionError, LlmEvidenceError, ValueError, GaosRateLimitError) as error:
        print(f"Pairwise eval failed: {error}", file=sys.stderr)
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()