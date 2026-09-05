# Task 1A Handoff

## Start here

1. Read `AGENTS.md`.
2. Run `git status` before changing anything.
3. Run the test suite:

   ```powershell
   uv sync
   uv run python -m unittest discover -s tests -v
   ```

As of 2026-08-22, the latest committed work includes the local PDF-to-LLM pipeline. The only expected untracked source PDF is `Summer_Project.pdf`; do not commit it.

## Goal and scope

This repository implements ADSL summer-project **Task 1A: Literature Review Agent**. The intended end-to-end result is a traceable report that starts with a research idea, retrieves papers, evaluates relevance with evidence, synthesizes findings, and proposes future directions.

Task Connection does not prescribe a concrete protocol. Therefore, the project uses Pydantic models and JSON-friendly outputs so later tasks can consume the results.

## Implemented milestones

| Stage | Main files | Status |
| --- | --- | --- |
| Validated task contracts | `literature_review/models.py` | Done |
| OpenAlex search and normalization | `search.py` | Done; no key required |
| Metadata filtering/ranking | `ranking.py` | Done; explainable baseline |
| Candidate selection | `selection.py` | Done |
| Metadata-only initial assessment | `assessment.py` | Done; not full-text review |
| Search planning contract | `planning.py` | Done; rule-based fallback |
| PDF extraction and cross-page chunks | `extraction.py`, `evidence.py` | Done |
| First-stage chunk retrieval | `evidence_ranking.py` | Done; lexical baseline |
| LLM contextual-summary/re-ranking contract | `llm_evidence.py` | Done; fake-client tested and live Gemini smoke test succeeded |
| Evidence-based paper assessment | `assessment.py`, `models.py` | Done; deterministic aggregation retains chunk IDs and page ranges |
| Evidence-cited synthesis and future directions | `synthesis.py`, `pipeline.py`, `models.py`, `assessment.py` | Done; three-layer traceability from chunks to report |
| Search-plan query-only + LLM planner | `planning.py`, `llm_evidence.py`, `models.py` | Done; rule-based fallback + `create_llm_plan` |

The current suite has 74 tests. Do not replace tests with only live API checks.

## Important design decisions

- `SelectedPaperSet` is a candidate set for acquiring/reading full text, not a final literature-review inclusion decision.
- PDFs are local inputs stored in `data/papers/`, which is ignored due to size/copyright. `pypdf` extracts text and preserves source page numbers.
- `EvidenceChunk` can span pages and records `page_start` and `page_end`.
- The first chunk retrieval stage is intentionally cheap and deterministic. It currently ranks lexical matches; an embedding retriever can later replace it without changing the response contract.
- The second stage should be an LLM contextual-summary/re-ranking step. It must receive a bounded set of retrieved chunks, return structured evidence summaries with page references, and then support a paper-level assessment.
- Same-title records are a pragmatic deduplication baseline. The current rule prefers higher citation count, then venue presence, abstract length, then year. It is not proof of identity. Add DOI-based deduplication when DOI metadata is available.
- Evidence supports two distinct perspectives: relevance (how directly a chunk answers the query) and coverage (which topic aspects a paper addresses). Relevance drives retrieval and scoring; per-paper notes capture coverage so both views stay traceable.
- Low-relevance/exclude chunks are NOT gap evidence. Research gaps come only from explicit limitations stated in the retrieved evidence plus subsequent aspect-coverage analysis across papers; an unretrieved or excluded chunk does not prove absence.
- Aggregate scores use shrunk means (the sample mean blended toward a prior score when few chunks support a paper). Caveat: per-paper top-k retrieval allocation gives papers unequal chunk counts, so raw mean comparisons remain partially confounded even after shrinkage.

## Latest milestone: evidence-cited synthesis and future directions

`literature_review.synthesis` implements the evidence-cited synthesis stage with three-layer traceability: every synthesized claim traces from an `EvidenceChunk` through a per-paper `PaperSummary` note to the final report prose.

- `literature_review.pipeline` accepts multiple PDFs or a folder of PDFs in one run (`expand_pdf_inputs()` with order-preserving deduplication) and retrieves top-k chunks corpus-wide across all papers in one LLM call before aggregation. `--dry-run` still performs only local steps and never reads a key or calls an API.
- Because top-k is corpus-wide, a paper whose chunks never enter the top-k receives no assessment, no per-paper notes, and no place in the report (PaperQA2 semantics; the dry-run path always behaved this way).
- Aggregate paper scores use deterministic shrunk means; each retained `PaperAssessment.evidence` item keeps the chunk ID, page range, summary, scores, and original chunk recommendation.
- Per-paper notes are generated only for papers whose aggregated recommendation is include or consider; exclude papers contribute no synthesized claims.
- Future directions are anchored in explicit limitations found in the retrieved evidence, with deterministic convergence and fallback sources. LLM output is validated with `model_validate_json()` against Pydantic schemas; unknown chunk IDs or missing/invalid inline citation markers raise `SynthesisError`.
- The report is direct prose with inline `[chunk_id]` citation markers validated against the supplied evidence set; deterministic and fake-client tests cover the path without a live API call.

## Latest: Langfuse observability (2026-08-31)

Added Langfuse SDK (4.15.1) observability with Plan B tree tracing:
- `@observe(name=...)` added to `rcs` (llm_evidence.py), `llm_call` (GeminiJsonClient.generate_json), `paper_notes` and `synthesis_report` (synthesis.py).
- `pipeline.py` flushes Langfuse telemetry before CLI exit with graceful degradation (Langfuse down / missing keys never blocks the pipeline; dry-run reads no keys).
- Dashboard at `http://localhost:3000` shows per-stage tree traces (rcs / paper_notes / synthesis_report), each wrapping an `llm_call` child layer (Plan B tree structure).
- 74 tests still green; `@observe` is a no-op in the test env (no keys), so no insurance code was needed (YAGNI).
- Requires a self-hosted Langfuse (`LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` / `LANGFUSE_HOST`) and Gemini for the LLM stage; confirm the server is up via `curl http://localhost:3000/api/public/health`.

The next milestone is report assembly driven by search convergence; see the suggested sequence below.

## Latest milestone: lexical-vs-embedding retrieval comparison (2026-09-01)

Added a compare-only evaluation tool that measures which retrieval method finds more relevant top-k chunks, using the LLM as judge (no hand-labeled data required):

- `literature_review/embedding_retriever.py`: semantic top-k over the same `EvidenceRetrievalResponse` contract as the lexical baseline, using `BAAI/bge-small-en-v1.5` (local, free). The query carries the official BGE retrieval prefix `"Represent this sentence for searching relevant passages: "`; document chunks do not, so the comparison is fair.
- `literature_review/retrieval_eval.py`: runs lexical `retrieve_evidence` and embedding top-k over the same chunks/query/top-k, issues SEPARATE LLM judge calls per group (`A−B`, `B−A`, overlap) to avoid order bias, skips any empty group with no LLM call, and emits a JSON report with `overlap`, `only_lexical`, `only_embedding`, `avg_relevance_lexical`, `avg_relevance_embedding`, and a verdict string.
- Verdict rule is explicit: average-relevance difference >= 0.5 → winner ("embedding better"/"lexical better"); < 0.5 → comparable.
- Added `sentence-transformers` dependency. Tests inject a fake encoder and fake LLM client, so no model download or key is needed for the suite (80 tests green).
- Scope is compare-only: the lexical retriever is NOT replaced; adoption is a verdict-driven future milestone. No pipeline/assessment/ranking core file was modified (files added: `embedding_retriever.py`, `retrieval_eval.py`, + 2 test files).
- CLI: `uv run python -m literature_review.retrieval_eval data/papers "<query>" --top-k 8 --judge-model gemini-3.6-flash`. The first real run downloads ~130MB of bge-small weights from Hugging Face.

## Latest milestone: pair-wise lexical-vs-embedding comparison (2026-09-03)

`literature_review.pairwise_eval` is a compare-only evaluation tool that pairs the top-k chunks from the lexical (`retrieve_evidence`) and embedding (`retrieve_evidence_embedding`) retrievers at aligned ranks, then asks the LLM as judge which passage is more relevant to the query (no hand-labeled data required):

- Rank pairing: lexical rank i vs embedding rank i; the same chunk id at a rank is recorded as a same-chunk tie with NO LLM call; differing ids form a valid pair; a rank present on only one side is counted unpaired.
- Six built-in queries (in order): `literature review agent` (literal), `automatic tool that summarizes research papers` (synonym), `writing a survey with help from AI` (concept-level), `an AI agent that writes and improves code` (cross-topic code), `agent` (precision/confusion), `software engineering automation with language models` (second code synonym). `--query` overrides the set.
- Verdict rule per query: fewer than 3 valid pairs → `insufficient`; embedding win rate over decided pairs >= 0.6 → `embedding better`; <= 0.4 → `lexical better`; else `comparable`. An overall verdict aggregates across queries.
- Position-bias handling: per-pair display order is randomized with a fixed seed (`FIXED_SEED`), and the prompt states the order is random and carries no meaning.
- CLI defaults to `--top-k 16`; judge model defaults to `gemini-2.5-flash` (real runs pass `--judge-model gemini-3.6-flash`, matching the pipeline model). Transient 429/500 judge calls use bounded backoff retry (5s/10s/20s, up to 3 attempts); `--api-key-suffix N` selects `GEMINI_API_KEY_N`; `--checkpoint` accumulates per-query results for resume.
- Scope is compare-only: the lexical retriever is NOT replaced; adoption is a verdict-driven future milestone. Main files: `pairwise_eval.py`, `test_pairwise_eval.py`; supporting changes to `embedding_retriever.py` (one-time chunk encoding / embedding cache) and `llm_evidence.py` (`GeminiJsonClient` accepts an explicit `api_key`).
- Suite count after this milestone: 142 tests.

## Latest milestone: embedding retrieval adoption (2026-09-04)

M1's pair-wise evaluation (6 queries, `overall_verdict: embedding better`) made embedding the evidence-driven default. The pipeline now retrieves with semantic ranking and no longer calls the lexical baseline.

- `literature_review/pipeline.py`: every retrieval call now uses `embedding_retriever.retrieve_evidence_embedding` (`run_evidence_pipeline`, `retrieve_from_pdf`, `run_synthesis_pipeline`, and `main`'s dry-run path). The functions accept an optional injectable `encoder` (option B); when omitted the real `default_encoder()` builds `BAAI/bge-small-en-v1.5`, so the first real run downloads ~130MB from Hugging Face. The lexical `evidence_ranking.retrieve_evidence` is retained as legacy/compare-only and is no longer imported by the pipeline.
- `literature_review/coverage.py`: added a references/bibliography lowest-priority rule. Per chunk, references(14) ranks below appendix(13); the rule is implemented exactly per the C1-C4 decision table so body chunks (priorities 1-12) are never demoted and appendix handling is preserved. In the no-section-header fallback, the stride selection drops references-region chunks. The `_REFERENCES_REGEX` (`^[0-9]*\.?\s*(?:references|bibliography)\b`) is line-anchored, case-insensitive, and honor-bound to <80-char header lines.
- No `--retriever` switch was added; no embeddings are persisted (recomputed each run); top-k and embedding model are unchanged.
- Tests: pipeline embedding-path tests use an injected fake encoder (no model download); coverage tests T1-T6 cover C1-C4 + bibliography synonym + fallback. Suite count after this milestone: **150 tests**.
- Files changed: `pipeline.py`, `coverage.py`, `test_pipeline.py`, `test_synthesis.py`; docs.

## Latest milestone: LLM planner (2026-09-04)

M3A makes the search-plan contract query-only and adds an LLM planner, keeping the deterministic rule-based fallback and the same `SearchPlan` Pydantic contract so the downstream OpenAlex search does not change.

- `literature_review/models.py`: `SearchPlan.idea` changed from required `ResearchIdea` to `ResearchIdea | None = None`. The `ResearchIdea` model is retained for richer future inputs; `LiteratureReviewReport.idea` is unchanged.
- `literature_review/planning.py`: `create_rule_based_plan` now takes a bare `query: str` (returns `generated_by="rule_based"`, `idea=None`) as the deterministic fallback. New `create_llm_plan(query, client, *, max_queries=5)` asks a `JsonGenerationClient` for a validated `SearchPlan` (`generated_by="llm"`) and forces `idea=None` after validation. `build_llm_plan_prompt` documents the query-only structure; `PlanningError` covers parse/schema failures.
- `literature_review/llm_evidence.py`: extracted the shared `strip_code_fence` + `generate_validated` helpers (call once → parse → repair exactly once). `literature_review/synthesis.py` now delegates its `_generate_validated` to the same helper, so the search-plan and synthesis stages share one LLM-JSON validation path without importing synthesis's privates.
- The `SearchPlan` schema is sent as `client.generate_json(prompt, SearchPlan.model_json_schema())`; the returned text is still validated with `model_validate_json()` and auto-repaired once on failure.
- Tests: `tests/test_planning.py` rewritten for the query-only signature plus T-llm-1..4 (valid plan, schema-as-second-arg, repair-once, unrepairable → `PlanningError`) using a fake client (no key). Suite count after this milestone: **154 tests**.
- Development stays fake-client first (no key); a live Gemini smoke test is the acceptance gate before commit.

## M3B — automatic OA PDF downloader (lexical normalization + download/backfill)

M3B adds the PDF acquisition component: OpenAlex `best_oa_location.pdf_url` is captured into `Paper.open_access_pdf_url`, the metadata `lexical_score` is normalized into the 0..1 unit scale (title hits weighted 3x vs abstract hits, `(3*title_hits + abstract_hits) / (4*max(len(query_terms), 1))`) so citation/recency/lexical contribute equal weight with the total in 0..3, and the new `literature_review.pdf_downloader` downloads OA PDFs with backfill.

- `literature_review/models.py`: `Paper.open_access_pdf_url: HttpUrl | None = None`.
- `literature_review/search.py`: `REQUESTED_FIELDS` adds `best_oa_location`; `paper_from_openalex` fills `open_access_pdf_url` from `best_oa_location.pdf_url`.
- `literature_review/ranking.py`: lexical score normalized (title-weighted, unit scale); equal weights so `total_score = lexical + citation + recency` in 0..3; rationale text updated.
- `literature_review/pdf_downloader.py`: `download_pdf(paper, dest_dir, *, fetcher)` writes into a caller-chosen directory (injectable fetcher keeps tests network-free); `NoOpenAccessError` / `PdfDownloadError` classify failures; `download_and_backfill(ranked_papers, dest_dir, target_n, *, fetcher, stats_path)` backfills failures from lower-ranked candidates and reports `DownloadStats` (oa_ratio_candidates, oa_ratio_attempted, shortfall) with optional JSON output.
- Tests: `tests/test_pdf_downloader.py` (fake fetcher) and `tests/test_ranking_normalization.py`; the existing `test_rank_prefers_query_term_in_title` keeps passing because title hits stay weighted. Suite count after this milestone: **163 tests**.
- Development and smoke never write into `data/papers/`: real smoke downloads to a temp directory only.

## M3C — main.py end-to-end entry (query -> plan -> search -> download -> synthesize)

M3C wires every milestone into one interactive entry: `literature_review.main` asks one query, builds a `SearchPlan` (LLM planner by default using key1; rule-based is the fallback — `--rule-based` forces it, and `--dry-run` always uses it), then for every planned query searches OpenAlex, filters/ranks, and downloads `target_n = ceil(15 / query_count)` PDFs. No cross-query merging: a shared `already_downloaded` set deduplicates by `paper_id` (duplicates count as satisfied and never trigger backfill; only download failures backfill that query's own continuation). `--dry-run` stops after downloads — no extraction, no embedding encoder, no LLM, no key.

- `literature_review/main.py` (new): module constants `LIMIT=50`, `MIN_YEAR=2021`, `TOTAL_TARGET=15`, `TOP_K_CHUNKS=8`, `DEST_DIR=data/papers`; `run_end_to_end(query, *, dest_dir, client_plan, client_synth, use_llm_plan=True, dry_run, json_fetcher, pdf_fetcher)` returns plan/downloads/stats_per_query (+ `report` on non-dry-run); `main()` reads one interactive query (`input`), handles EOF with exit 1, builds both stage clients, prints the dry-run summary or the `SynthesisResponse` JSON, and flushes Langfuse. Failed `extract_pdf_text` calls are recorded in `failed_extractions` and skipped; if every PDF fails, the run raises `ValueError`.
- `literature_review/pdf_downloader.py` (extended): `download_and_backfill` gains `already_downloaded: set[str] | None`; duplicate papers increment `DownloadStats.duplicate_reused` without writing a file; `DownloadResult.downloaded_paper_ids` pairs one-to-one with `downloaded_paths`; `shortfall` excludes duplicates (duplicates = quota consumed).
- Key split: planning uses `GEMINI_API_KEY` (default LLM planner; a missing key1 prints a warning and falls back to rule-based without aborting), synthesis uses `GEMINI_API_KEY_2`; a missing key2 on a non-dry-run `main()` prints a clear message and exits 1. `--dry-run` never inspects either key. No key value ever reaches stdout/log (grep `AIza` must be empty in smoke logs).
- Gemini model: all defaults now use `gemini-3.6-flash` — `gemini-2.5-flash` was retired upstream (first real M3C smoke hit a 404 on 2026-09-05). Updated `GeminiJsonClient` default, `pipeline --model`, `pairwise_eval`/`retrieval_eval --judge-model`, and the `test_pairwise_eval` default assertion.
- Tests: `tests/test_main.py` (16 tests: the original 9 plus Amendment 1 — default LLM planner, `--rule-based` forces rule-based, `--dry-run` forces rule-based with zero keys, missing key1 falls back without exit, `_build_clients` key gating) plus 3 `tests/test_pdf_downloader.py` cases. Suite count after Amendment 1: **183 tests**.
- Known limitations (deliberate, out of scope): no multi-query merging, no OpenAlex pagination (`LIMIT=50` < the 200 ceiling), no DOI-based dedup, no Unpaywall/arXiv backfill (post-M3C), and the pipeline still cannot select `--api-key-suffix` for synthesis (key2 is the dedicated synthesis key).
- Smoke: fake e2e (no key) in `.omo/evidence/smoke-m3c-fake-e2e.log`; the real smoke plan (real OpenAlex + real Gemini, temp dest dir only) awaits user confirmation before consuming keys.

## Windows and WSL/OpenCode handoff

- The Windows folder (`C:\Users\User\Desktop\Literature_Review_Agent`) uses Anaconda/Windows `uv`. OpenCode runs in WSL and should use WSL-native `uv`, not the Windows environment. The user has already installed WSL `uv`; verify it with `uv --version` rather than reinstalling it.
- Recommended one-time WSL project setup:

  ```bash
  mkdir -p ~/projects
  cd ~/projects
  git clone https://github.com/SkyUwU/Literature_Review_Agent.git
  cd ~/projects/Literature_Review_Agent
  uv sync
  ```

- These are two independent Git working copies. Clone only once; do not repeatedly copy or re-clone after every handoff. The private GitHub remote `origin` is now configured. Choose one as the active copy for a milestone, commit there, run `git push`, then run `git pull --ff-only` in the other copy before editing. A configured local bare repository could also serve this role, but do not use either checked-out working directory as an informal bidirectional remote.
- Do not copy `.venv`, PDFs, or uncommitted source files between copies. For a one-time WSL setup on this same private machine, copying the ignored `.env` from Windows is acceptable; alternatively create it from `.env.example`. Never commit, paste, or upload `.env`. If Gemini is needed in WSL, ensure that the WSL clone has its own local `.env`.

Suggested sequence after the evidence-cited synthesis step:

1. report assembly (search convergence);
2. aspect-coverage gap analysis;
3. automatic PDF acquisition and GROBID pre-processing for real papers (the LLM planner and embedding-stage upgrades are already done).

## API and operational notes

- OpenAlex is the primary paper search provider because it works without an API key. `search.py` uses `urllib`, a User-Agent, timeout, and retries. A past Windows/Python TLS EOF error was intermittent; `curl.exe` returned HTTP 200. If it returns repeatedly, consider switching that module to `httpx`, not disabling TLS verification.
- Semantic Scholar previously returned rate-limit errors in the shared environment. Treat it as an optional later metadata/citation-graph source, with caching and backoff.
- Google Gemini API keys and quotas are managed in Google AI Studio. The user should inspect **API Keys** and **Dashboard > Usage**. Free-tier limits are adequate for a very small smoke test but vary by model/project.

## External implementation references

- PaperQA2: use its pattern of retrieve chunks -> contextual summaries -> LLM re-ranking -> grounded answer. Do not copy/install the whole project.
  https://github.com/Future-House/paper-qa
- OpenScholar: reference for retrieval + reranking and optional citation constraints.
  https://github.com/AkariAsai/OpenScholar
- STORM: reference for research planning before report generation.
  https://github.com/stanford-oval/storm

## Handoff prompt for another coding agent

```text
Work in this repository on ADSL summer-project Task 1A. Read AGENTS.md and HANDOFF.md first. Inspect git status and preserve user changes. Implement only the current next milestone in HANDOFF.md (report assembly/search convergence). Do not request or print API keys; use .env/environment variables. Keep Pydantic contracts, add tests where needed, run the full test suite, and report the exact PowerShell or WSL commands for the user to commit. Reply in Traditional Chinese and keep explanations concise.
```

## New Codex session starter prompt

Use this prompt when starting a fresh Codex task for this repository. It intentionally delegates details to the tracked documents instead of replaying long chat history:

```text
Continue the ADSL Summer Project Task 1A in C:\Users\User\Desktop\Literature_Review_Agent. Read AGENTS.md and HANDOFF.md before taking action. The Gemini PDF evidence smoke test, provenance-preserving PaperAssessment aggregation, and evidence-cited synthesis with future directions are complete; implement only the current next milestone: report assembly (search convergence). Inspect git status first, do not commit Summer_Project.pdf or data/papers/, add tests, run the full suite, and give exact PowerShell commit commands. Reply concisely in Traditional Chinese. Do not ask for or print API keys.
```
