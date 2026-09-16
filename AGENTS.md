# Collaboration Guide

## User and communication

- The user is a new CS master's student working on ADSL summer-project Task 1A, a Literature Review Agent.
- Reply primarily in Traditional Chinese. Keep explanations short and implementation-focused unless the user asks to learn a concept.
- The user works in Windows PowerShell (Anaconda `uv`) and OpenCode may run in a WSL clone. Give PowerShell commands.
- Windows and WSL are independent Git working copies; sync through a shared remote (GitHub recommended) and never edit the same feature in both before committing.
- The user prefers progress on the assignment over broad tutorials. Explain only new decisions, errors, and commands they must run.
- Never ask the user to paste API keys. Keep secrets (Gemini, Langfuse) in a local `.env`, which is ignored by Git.

## Engineering workflow

- Read `HANDOFF.md` before changing this project. Update it when a material milestone or architectural decision changes.
- Inspect `git status` before editing. Preserve existing user changes and never add `Summer_Project.pdf` or `data/papers/` to Git.
- Make one bounded milestone at a time. Do not combine unrelated refactors, dependencies, or features in one change. Add or update tests, run them, then state the exact `git add`, `git commit`, and `git status` commands.
- Use Pydantic models as the interfaces between stages. Outputs must preserve source provenance, such as provider, paper ID, file path, and page range.
- Avoid claiming that an abstract- or metadata-based score is a full-text scholarly assessment; a chunk-based synthesis is likewise not a whole-paper review.
- Prefer deterministic, testable baselines before adding LLM/API behavior. Later model-based components must use the same data contracts where practical. For LLM JSON, send the Pydantic JSON schema when the provider supports it and still validate the returned text with `model_validate_json()`.

## Commands

```powershell
uv sync
uv run python -m unittest discover -s tests -v
uv run python -m literature_review.search "literature review agent" --limit 10 --year-from 2024 --rank
uv run python -m literature_review.pipeline data/papers "literature review agent" --top-k 8 --dry-run
uv run python -m literature_review.pipeline data/papers "literature review agent" --top-k 8 --model gemini-3.6-flash
uv run python -m literature_review.retrieval_eval data/papers "literature review agent" --top-k 8 --judge-model gemini-3.6-flash
uv run python -m literature_review.pairwise_eval data/papers --top-k 16 --judge-model gemini-3.6-flash
uv run python -m literature_review.main --dry-run
uv run python -m literature_review.main --rule-based
```
`literature_review.main` is the end-to-end entry (M3C): it interactively asks one query, plans with the LLM planner by default (`--rule-based` forces the deterministic fallback; `--dry-run` always uses it, so a dry run needs no key), searches/ranks/downloads each planned query independently, then extracts and synthesizes the report. `--dry-run` stops after downloads (no key). Parameters are hard-coded (`LIMIT=100`, `MIN_YEAR=2021`, `TOTAL_TARGET=20`, `TOP_K_CHUNKS=32`; `target_n = ceil(20 / query_count)`); planning uses `GEMINI_API_KEY`, the synthesis stage uses `GEMINI_API_KEY_2`.

Langfuse observability: confirm the self-hosted server is up with `curl http://localhost:3000/api/public/health` before a full run; traces appear in the dashboard at `http://localhost:3000`.

If Git reports a dubious-ownership error, the user previously resolved it with:

```powershell
git config --global --add safe.directory C:/Users/User/Desktop/Literature_Review_Agent
```

## Current architecture

```text
ResearchIdea -> SearchPlan -> OpenAlex retrieval -> metadata filter/rank/select
-> LLM screening (keep/maybe/exclude) -> local PDF extraction (multi-PDF) -> EvidenceChunk retrieval
-> functional scoring (1-10 utility) -> evidence-backed paper assessment
-> per-paper claim notes -> synthesis report with [claim-N] markers / future directions
```

The pipeline now retrieves evidence with semantic (embedding) ranking by default — `literature_review.embedding_retriever.retrieve_evidence_embedding` replaced the lexical baseline for `pipeline.py`; the lexical `evidence_ranking.retrieve_evidence` is retained as compare/legacy only. The formal scoring layer is the C2b functional scoring (`literature_review.functional`: a single 1-10 utility per sampled chunk, per-paper top-2 sampling, batches of 5, quota∩threshold selection); the LLM contextual-summary/re-ranking interface (`summarize_and_rerank`) is retained as compare/legacy only. Synthesis output cites claims as `[claim-N]` with `claim_chunks` / `claim_id` / `claim_ids` tagging (C2c / Output Traceability). Langfuse observability (tree tracing on every LLM call, flushed before CLI exit) is implemented.

PDF acquisition (M3B): `literature_review.pdf_downloader` fetches open-access PDFs from the OpenAlex `best_oa_location.pdf_url` recorded by `search.py` into `Paper.open_access_pdf_url`. `download_pdf(paper, dest_dir)` writes into a caller-chosen directory (real runs use `data/papers/`, smoke runs use a temp dir) with collision-resistant file names; missing OA links raise `NoOpenAccessError`, network/HTTP failures raise `PdfDownloadError`. `download_and_backfill(ranked_papers, dest_dir, target_n, *, already_downloaded=None)` keeps the top-N selection: failing papers are replaced by the next ranked candidates, and the pass reports OA coverage ratios (`oa_ratio_candidates`, `oa_ratio_attempted`, `shortfall`, `duplicate_reused`) with optional JSON stats output. `already_downloaded` is a shared set that deduplicates across multi-query runs (M3C): a paper id already in the set counts as satisfied (`duplicate_reused`) without writing a new file and without backfilling.

End-to-end entry (M3C): `literature_review/main.py` wires query -> SearchPlan -> per-query OpenAlex search/rank -> optional LLM screening (M5e: bucket sampling + keep/maybe/exclude + gap follow-ups via `client_screen`) -> download -> shared-set dedup -> PDF extraction -> synthesis report. There is no cross-query merging; each planned query keeps its own download target (`target_n = ceil(TOTAL_TARGET / len(plan.queries))`, duplicated papers consume quota). `run_end_to_end(...)` is fully injectable (`json_fetcher`, `pdf_fetcher`, plan/synthesis/screen clients), and `main()` keeps `--rule-based` / `--dry-run` only (Amendment 1: the LLM planner is the default; rule-based is the escape hatch / fallback). Non-dry-run runs persist the full report as JSON under `data/outputs/` via `save_report_output` (`report_%Y%m%d_%H%M%S_%f.json`).

Paper-level ranking (K): real runs of `main.py` rank OpenAlex candidates with `filter_and_rank(response, policy, encoder=...)` → `ranking.rank_papers_embedding` — bge-small-en-v1.5 embeddings of the query vs. each paper's title+abstract, with cosine similarity plus citation and recency as three equal-weight components (total 0..3). `--dry-run` skips the embedding model entirely and keeps the deterministic lexical baseline (`rank_papers`); the `search.py --rank` CLI also keeps lexical (compare/legacy).

The `SearchPlan` contract is query-only: `SearchPlan.idea` is optional. The LLM planner is the default — `planning.create_llm_plan(query, client)` produces a validated plan (`generated_by="llm"`) using the shared `llm_evidence.generate_validated` call-once-parse-repair helper; `planning.create_rule_based_plan(query)` is the deterministic fallback (missing/failed key, and always for `--dry-run`; `--rule-based` forces it outright). See `HANDOFF.md` for the next integration scope and decisions.

## Terminology

- Chunk（證據段落）: one text span extracted from a local PDF, identified as `{paper_id}-c{n}` (C2a chapter-aware chunking) and carrying its inclusive page range.
- 證據摘要 / evidence summary: the RCS output for one chunk — a contextual summary plus relevance/quality scores plus page references, validated against Pydantic schemas (RCS is compare/legacy; the formal path uses functional scoring).
- 覆蓋包 / coverage pack: the bounded set of chunks assembled for a single paper (coverage.py); not part of the formal pipeline — per-paper notes use the paper's own chunks under `llm_input_cap`, so the pack is kept as a legacy/reference concept.
- RCS: the LLM contextual-summary/re-ranking stage implemented by `summarize_and_rerank()`; shorthand for its summaries-and-scores output (compare/legacy only since C2b).
- 筆記主張 / PaperSummaryClaim: one claim inside a single paper's note, citing a `ChunkReference` back to source chunks.
- 逐篇筆記 / PaperSummary（per-paper notes）: structured contribution/method/experiments/results/limitations claims for one paper.
- 綜合報告 / synthesis report: direct prose with inline `[claim-N]` citation markers plus structured future directions; the claim-to-chunk mapping is programmatic (`claim_chunks`).
- 未來方向 / FutureDirection: an evidence-anchored research direction carrying its own provenance.
- 收縮平均 / shrunk mean: aggregate score blending the sample mean toward a prior when few chunks support a paper (`round((n*mean + m*prior)/(n+m), 1)`, m=1; kept for the metadata initial assessment, retired from the formal functional path by C2b).
- include/exclude（推薦等級）: recommendation levels (the consider band was retired by C2b); only include papers receive per-paper notes and enter the synthesis report.
- 功能性評分 / functional scoring: the C2b formal scoring layer — a single 1-10 utility per sampled chunk (`literature_review.functional`), per-paper top-2 sampling, batches of 5, quota∩threshold selection (≥ `FUNCTIONAL_THRESHOLD`).
- Claim 標註 / claim tagging: `[claim-N]` markers in the report plus `claim_chunks` / `claim_id` / `claim_ids` fields mapping every claim to its source chunks and papers (C2c / Output Traceability).
- LLM 篩選 / screening: the M5e candidate-filtering call that assigns keep/maybe/exclude to bucket-sampled OpenAlex candidates before PDF download, with optional gap follow-up queries.
- bounded evidence（有限證據集）: every generated claim may rely only on the supplied chunk set, never on external knowledge.
