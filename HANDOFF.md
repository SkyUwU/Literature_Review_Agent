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
| M4 — RCS 1-10 scoring rework | `llm_evidence.py`, `models.py`, `assessment.py` | Done; anchored 1-10 scores, float aggregation, chunk recommendation removed |
| M5a — planner prompt diversification | `planning.py`, `main.py` | Done; distinct facets, overlap check+repair, purpose log |
| M5b — RCS calibration | `models.py`, `llm_evidence.py`, `pipeline.py` | Done; dual rationale, A9 bands, index input, B=4 |
| M5e — candidate pool upgrade | `screening.py`, `main.py`, `pdf_downloader.py` | Done; bucket sampling + LLM screening + gap follow-ups |
| M6 — RCS on local Ollama | `ollama_client.py`, `llm_evidence.py` | Done; qwen3:8b batch loop, Gemini fallback |
| C2a — pymupdf4llm extraction + chapter chunking | `extraction.py`, `evidence.py`, `models.py` | Done; markdown tables, section-aware chunks |
| C2b — functional scoring | `functional.py`, `models.py`, `pipeline.py` | Done; single 1-10 utility, quota∩threshold selection |
| C2c — claim-level markers + key3 | `synthesis.py`, `models.py`, `main.py` | Done; [claim-N] markers + programmatic materials |
| C2d — aspect metadata filters | `synthesis.py`, `coverage.py` | Done; journal-prefix/metadata-line filtering |
| C2e — three-call two-stage report generation | `synthesis.py`, `models.py`, `pipeline.py` | Done; outline → report → directions |
| S1-S5 — scoring/output-line adjustments | `synthesis.py`, `functional.py`, `models.py` | Done; 0.7×max+0.3×mean, sub-query sampling |
| LLM Input Hygiene | `functional.py`, `synthesis.py`, `pairwise_eval.py` | Done; no page numbers in LLM inputs |
| Output Traceability | `models.py`, `synthesis.py`, `pipeline.py` | Done; claim_id/claim_ids in external outputs |
| M5c Semantic Scholar primary search | `ss_search.py`, `main.py`, `search.py`, `pdf_downloader.py` | Done; SS primary + OpenAlex fallback + DOI abstract backfill; F4 real run deferred (Gemini quota) |

The current suite has 414 tests. Do not replace tests with only live API checks.

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
- Future directions are proposed by a third call that sees the query and the claims only — they are not limited to explicit limitations found in the retrieved evidence, and the deterministic fallback is unchanged. LLM output is validated with `model_validate_json()` against Pydantic schemas; unknown claim IDs or missing/invalid inline citation markers raise `SynthesisError`.
- The report is direct prose with inline `[claim-N]` citation markers validated against the supplied evidence set; deterministic and fake-client tests cover the path without a live API call.

## Latest: Langfuse observability (2026-08-31)

Added Langfuse SDK (4.15.1) observability with Plan B tree tracing:
- `@observe(name=...)` added to `rcs` (llm_evidence.py), `llm_call` (GeminiJsonClient.generate_json), `paper_notes` and `synthesis_report` (synthesis.py).
- `pipeline.py` flushes Langfuse telemetry before CLI exit with graceful degradation (Langfuse down / missing keys never blocks the pipeline; dry-run reads no keys).
- Dashboard at `http://localhost:3000` shows per-stage tree traces (rcs / paper_notes / synthesis_report), each wrapping an `llm_call` child layer (Plan B tree structure).
- 74 tests still green; `@observe` is a no-op in the test env (no keys), so no insurance code was needed (YAGNI).
- Requires a self-hosted Langfuse (`LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` / `LANGFUSE_HOST`) and Gemini for the LLM stage; confirm the server is up via `curl http://localhost:3000/api/public/health`.

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

## M4 — RCS 評分層改版：1-10 分數制 + float 聚合 + 去 chunk recommendation（2026-09-06）

M4 改版 LLM 評分層（C1），把 RCS 的舊 5 分制升級為 1-10 分制並加上操作型錨點，刪除零決策作用的 chunk 層 `recommendation`，聚合分數保留小數以恢復鑑別度：

- `literature_review/models.py`：`LlmEvidenceAssessment`/`EvidenceCitation` 兩分數範圍 `le=10`、新增 `model_config = ConfigDict(extra="forbid")`、chunk 層 `recommendation` 欄位**移除**；`PaperAssessment` 兩分數 int→float（`le=10`），論文層 `recommendation` **保留**（:230）；`AssessmentPolicy`/`EvidenceAggregationPolicy` 門檻 4/3/3 → **8/6/6**、`prior_score: float = 5.5`。
- `literature_review/assessment.py`：`_shrunk_mean` `round((n*mean + m*prior)/(n+m), 1)`（去 floor、保留小數、n=0 拋 ValueError）；聚合組裝移除 chunk 層 recommendation；metadata 初評 `relevance_score`/`evidence_quality_score` 對齊 1-10（`min(10, max(1, ceil(rank_score*10/3)))`；quality = base 4 + venue/citation 門檻）並連同 `assess_selected_papers` docstring 標 **Deprecated**（僅 `search.py --assess` 使用）。
- `literature_review/llm_evidence.py`：`build_evidence_prompt` 改 1-10 制、加 10/5/1 操作型錨點、明示「先寫 rationale 再給分數」、`do NOT penalize theory/framework papers`（無數字不扣分）、移除 recommendation 指示。
- `literature_review/synthesis.py`：報告 prompt 區塊 A summaries dict 移除 `recommendation`；論文層 recommendation 判定（:131 usable / :604 exclude）保留。
- `literature_review/demo.py`：範例 `PaperAssessment` 分數 8/6（對應新閾值）。
- 測試更新：`test_models`（1-10/float/拒 chunk recommendation）、`test_assessment`（聚合邊界、metadata 0-3 fixture + 新公式斷言、移除 LEGACY_POLICY）、`test_llm_evidence`（新 prompt 斷言）、`test_synthesis`/`test_pipeline`/`test_main`/`test_retrieval_eval`（fake JSON 去 chunk 層 recommendation、分數升到 10/10 使聚合後仍 usable）。Suite count after this milestone: **196 tests**。
- 驗收：改前真實 run baseline（`.omo/evidence/m4-baseline-before.log` + `m4-baseline-before-dist.json`，舊制下 4 篇全擠 3/4 分）已留檔；改後真實 run + 改前/改後對比 + 門檻校準建議接續在 Todo 8。

## K — paper-level semantic ranking + scaled parameters (2026-09-08)

K 把論文層排名從 lexical 換成語意 embedding（bge-small-en-v1.5），並放大規模常數，回應「關鍵詞比對把不錯論文隔絕在外」與「可評比論文太少」兩個瓶頸：

- `literature_review/ranking.py`：新增 `rank_papers_embedding(papers, query, encoder)` —— `encode_query`（BGE prefix）產出 query 向量，論文側 `title\nabstract` 一批編碼，cosine 相似度 clip 於 0，與 citation、recency 三成分等權相加（總分 0..3，排序鍵同 lexical）；`filter_and_rank(response, policy, *, encoder=None)` 分支——有 encoder → embedding 排名，無 → 維持 lexical 原行為。lexical `rank_papers` 保留（dry-run / `search.py --rank` 用）。
- `literature_review/main.py`：`LIMIT` 50→100、`TOTAL_TARGET` 15→20、`TOP_K_CHUNKS` 8→16（`MIN_YEAR=2021` 不變）；`run_end_to_end(..., encoder: Encoder | None = None)` 新參數，`effective_encoder` dry-run 恆 `None`（dry-run 不建/不碰模型，維持零 key 語義)、否則預設 `embedding_retriever.default_encoder()`（以模組屬性呼叫，確保測試 patch 生效，**偏離計畫字面的 direct import**）；迴圈內 `filter_and_rank(..., encoder=effective_encoder)`。
- `tests/test_ranking.py`：+6 tests（cosine 排序、三成分補償、負相似 clip、向量數不符 ValueError、有/無 encoder 路徑分支），`test_ranking` 10 tests OK。
- `tests/test_main.py`：`test_target_n_follows_ceil_formula` pairs 改 `((4,5),(5,4))`（舊 pairs 在 TOTAL_TARGET=20 下候選數不足、`len(downloads)==20` 會破——**計畫假設「值改不破」有誤，已修正**）；+2 tests（dry-run 忽略 injected encoder sentinel；`QUERY_PREFIX`/`W-special` fake encoder 把原 lexical 落選論文抬到 rank 1 進入下載集——embedding run 須為 real run，因 dry-run 拒用 encoder）。
- 測試數：**204 tests OK**（196 既有 + 6 test_ranking + 2 test_main）。證據 log：`.omo/evidence/k-task-1-k-retrieval-improvements.log`、`k-task-2-k-retrieval-improvements.log`。
- 驗收（Todo 4 真實 run 已完成 ✅，Wave 2）：同 query「literature review agent」完整 run（LLM plan + 真實 OpenAlex + 19 PDFs + embedding 論文排名 + RCS + synthesis）對比 M4-after——**分數分布頂端明顯上移且有 usable**：5 篇聚合中 1 consider（W7140287209 The AI Scientist，rel 6.2 / qual 6.6，4 chunks）、4 exclude（M4-after 為 4/4 全 exclude、0 usable → synthesis 無法執行）；本次 synthesis 正常產出（report 含 12 inline citations / 4 unique chunk ids、2 future directions、`generated_by=llm`、AIza 命中 0）。結果也是 embedding 換血證據：The AI Scientist 標題/摘要無「literature review agent」字面組合，lexical 排名排不上、embedding cosine 才帶入。證據：`.omo/evidence/k-after-real.log` + `k-after-dist.json` + `k-before-after-compare.md`。執行偏離：① fresh shell 需 `uv run --env-file .env`（plain `uv run` 讀不到 key，首跑因缺 key fallback + exit 1）；② 下載落點為 main.py 硬編碼 `DEST_DIR=data/papers`（計畫要求 temp，但 CLI 無法覆寫）→ 已把 19 個 `W*.pdf` 移至 `/tmp/k-after-downloads/`、`data/papers/` 還原為原始 8 檔；③ failed_extractions=W4387533377（JPEG 偽 PDF：`invalid pdf header: b'\xff\xd8\xff\xe0\x00'`，容錯跳過）；④ elapsed/plan 數未含於 log（main.py 非 dry-run 只印 report JSON），可查 Langfuse dashboard。

## K2 — TOP_K_CHUNKS 16→32 驗收 run（2026-09-08）

K2 是 K 的後繼小調（純參數 + 驗收 run）：把 RCS 評分券的跨論文 top-k 從 16 放寬到 32，回應「19 篇下載只有 5 篇拿到評分券」的涵蓋瓶頸。假設：更多論文進 RCS → 入評分論文數與 usable 數可能上升。

- `literature_review/main.py`：`TOP_K_CHUNKS` 16→32（一行常數 + docstring；`LIMIT=100`/`TOTAL_TARGET=20`/`MIN_YEAR=2021` 不動）。
- 文件同步：README.md「Run the full pipeline (M3C)」段、AGENTS.md line 34 常數敘述。
- 測試數：**204 tests OK**。證據 log：`.omo/evidence/k2-task-1-k2-topk-32.log`。
- 驗收（Todo 3 真實 run 已完成 ✅）：同 query「literature review agent」完整 run（LLM plan + 真實 OpenAlex + 18 PDFs + embedding 論文排名 + RCS + synthesis）對比 k-after-dist.json——**進評分論文數 5→4（不升反降，假設未成立）**、usable 維持 1（W7140287209 The AI Scientist consider，rel 6.2 / qual 6.8，6 chunks；K 為 6.2/6.6、4 chunks）、其餘 3 exclude（W4401667275 rel 5.5/qual 5.4 17 chunks、W4393065402 4.4/5.7 3 chunks、新進 W4391006361 3.0/5.6 6 chunks；K 獨有的 W4402901320/W4416209823 被擠出）。分布 rel 3.0/6.2/median 4.95、qual 5.4/6.8/median 5.65。synthesis 產出 1 source、**14 inline citations（6 unique）**、2 future directions、`generated_by=llm`、AIza 0 命中；**報告尾端中文瑕疵仍在（材料來源清單/涵蓋 Chunk ID 附錄）**——已知 A5 待辦。**結論：涵蓋改善未成立**（top-32 名額被 W4401667275 單篇吃 17/32，分配仍集中）；依結論規則「仍 1 usable → 附閾值建議 C（consider rel 6→4.5 + qual 卡 5.5）」供使用者決策、不實作。證據：`.omo/evidence/k2-after-real.log` + `k2-after-dist.json` + `k2-vs-k-compare.md`。執行偏離比照 K 先例：① `--env-file .env`；② 下載落點 data/papers → 18 個 `W*.pdf` 移至 `/tmp/k2-after-downloads/` 還原 8 檔；③ 本 run 無 failed_extractions。

## K3 — per-paper cap 遞補 + shrinkage_strength 4→3（2026-09-08）

K3 回應 K2 教訓（跨論文 top-32 被 W4401667275 單篇吃 17/32）與歸因勘誤（跨 run 對比必須固定下載集）：cross-paper top-k 加 **per-paper quota（遞補式）**，單篇最多 6 chunks，名額遞補給後面的論文；`shrinkage_strength` 4→3（少 chunks 論文分數少被 prior 5.5 壓低）。

- `literature_review/models.py`：`EvidenceRetrievalPolicy.max_chunks_per_paper`（`int | None = Field(default=None, ge=1, le=50)`，None=舊行為完全保留）；`EvidenceAggregationPolicy.shrinkage_strength` default 4→3。
- `literature_review/embedding_retriever.py`：`_score_ranked` 加遞補分支（cap=None 維持原 path 直接取前 top_k；cap=N 時單篇超過跳過、名額遞補、依序 rank 1..N）。
- `literature_review/main.py`：`retrieval_policy=EvidenceRetrievalPolicy(top_k=TOP_K_CHUNKS, max_chunks_per_paper=6)`。
- 測試：test_models.py shrinkage 斷言 4→3、test_assessment.py 6 個聚合數值斷言同步（shrinkage 3：6.8→7.1、6.4→6.6、8.5→8.8、8.0→8.2、6.0→6.1；`below_8_0_falls_to_consider` 輸入 9×9→9×6 維持語意）、test_embedding_retriever.py +5（cap None 等價、遞補、cap 後不足 top_k 全收、cap6/top32 分散、shrinkage 3 聚合）。**209 tests OK**。log：`.omo/evidence/k3-task-1-models.log` + `k3-task-2-backfill.log`。
- 驗收（Todo 3 固定集 A/B + Todo 4 真實 run ✅）：**固定集**（K2 的 18 PDF → `/tmp/k3-fixture/`）retrieval 層——A（cap=None）完全重現 K2 分配（17/6/6/3、4 篇論文）證明 fixture 有效；**B（cap=6）進評分論文 4→9**（W4401671778 3、W4323848232 2、W4407173730/W4362515116/W4402909345 各 1 新進），單篇壟斷（17/32）消除。**真實 run sanity**：進評分 **9**、1 consider（W4401667275 6.3/6.5——K2 時代為 exclude 5.5/5.4，cap 聚焦 + shrink 3 使分數上移）、8 exclude、報告 1 source + 2 future directions、`generated_by=llm`、AIza 0、無 failed_extractions；**首次 run 隨機 LLM miss 失敗**（chunk 完整性檢查無 retry），重試即成功，15 個首輪 PDF 在 `/tmp/k3-failed-run/`。**結論：cap 遞補目標達成**（涵蓋 4→9、壟斷消除）；真實 run 下載集浮動（W7140287209 缺席）故 usable 換人，retrieval 結論以固定集為準。證據：`.omo/evidence/k3-retrieval-compare.md` + `k3-after-real.log` + `k3-after-dist.json`。執行偏離比照先例：`--env-file .env`、下載落點 data/papers → 18 個 `W*.pdf` 移至 `/tmp/k3-after-downloads/` 還原 8 檔。

## K3b — shrinkage_strength 3 → 1（2026-09-08，離線重算驗證）

K3 驗收後使用者裁示 m=1（cap=6 已限制 n≤6，收縮平均與實測平均差異過大；m 3→1 只保留「單一/極少 chunks 不獨裁」的最小干預）。純聚合參數 + 離線驗證，不重跑 LLM。

- `literature_review/models.py`：`EvidenceAggregationPolicy.shrinkage_strength` default 3→**1**（公式 `(n*mean + 1*5.5)/(n+1)`，prior=5.5）。
- 測試同步（test_models.py 斷言 3→1；test_assessment.py 6 個聚合斷言以 m=1 手算更新：7.8/6.8、7.8/7.8、9.5/7.7、8.7、7.6、6.5；兩處語意衝突調整輸入——`below_8_0`(9,8)×6→(8,8)×6 維持 consider、`below_6_0`(6,6)×12→(5,5)×12 維持 exclude、traceability chunk_two rel 9→8 維持 consider；test_embedding_retriever.py 測試名/斷言 m=3→1、7.8）。**209 tests OK**。log：`.omo/evidence/k3b-task-1-shrinkage1.log`。
- 離線重算（Todo 2）：從 `k3-after-real.log` 區塊 A 抽 9 篇論文 32 chunks 的 chunk-level scores，`_shrunk_mean(m=3)` 重算對比 `k3-after-dist.json` **9/9 完全一致**（確定性還原成功）→ m=1 重算：**usable 維持 1**（W4401667275 6.3→6.5/6.5→6.8 consider 不變）、**recommendation 零變化**（8 exclude 全數維持）；分數變化符合公式（mean>5.5 微升、mean<5.5 下降，非計畫預期之「普遍微升」——此 run 9 篇中 8 篇 rel mean<5.5）。依 Success criteria：usable 仍 1 → 維持「閾值後議」。產物：`.omo/evidence/k3b-offline-m1.md` + `.json`。
- 收尾（Todo 3）：209 tests 複跑 OK；data/papers 零變更（8 檔）；不 commit。git status 額外 M：`.omo/STATE.md` + `AGENTS.md`（K3 驗收後規劃更新遺留，TOP_K_CHUNKS 16→32 文件同步——非 K3b 造成）。

## M5a — planner prompt 多面向 + query 重合度驗證 + purpose log（2026-09-08）

M5a 回應 K/K2/K3「三輪皆 1 usable」的診斷（2026-09-08 定案：**天花板約束非閾值約束**，候選池組成才是杠杆）：`build_llm_plan_prompt` 原本只要求 1-5 條 query + purpose + perspectives，無「面向互補/詞彙多樣」指示 → LLM 子查詢語意高度重疊 → OpenAlex（字面匹配）撈回同一批論文 → 池子窄。解法 = 面向多樣 + 關鍵詞多樣（同義詞/上下位/不同表述），並補上 log 記錄與重合度驗證。

- `literature_review/planning.py`：
  - `build_llm_plan_prompt` 加 4 項指示（英文精簡）：each query targets a distinct facet（avoid near-duplicate queries）；do not reuse the same head terms（prefer synonyms, hyponyms, alternative phrasings）；purpose 說明該面向資訊需求；stay on-topic（防過度發散）。
  - 新純函式 `query_overlap(a, b) -> float`（lowercase + 去非字母數字 token 後的 Jaccard ∩/∪，設計意圖=檢查「關鍵詞重疊」、刻意**不用 embedding**——OpenAlex 認字面、同義詞改寫正是想要的行為，不得被 flag）與 `max_query_overlap(queries)`；`QUERY_OVERLAP_THRESHOLD = 0.5`。
  - `create_llm_plan` 包一層**外層 retry**（不碰共用 `generate_validated`）：plan 出來任兩兩 overlap > 0.5 → 一次 diversification repair 呼叫（列出原 queries + 重寫要求）；修復後仍超標 → **接受第一版 + logger.warning 紀綠**（重疊=品質問題非可用性問題，不降級 rule-based、不採重寫版）；rule-based fallback 不套驗證。
- `literature_review/main.py`：`_print_plan(plan)`——`run_end_to_end` 選定 plan 後打印每條 query + purpose 清單與 perspectives（修 M3C「log 只記 query 字串、追溯不到面向」缺口）。
- 測試（`tests/test_planning.py`）：prompt 新指示斷言 1、overlap 單元測試 6（已知病例「literature review agent AI」vs「AI literature review agent tools」=0.8 須 flag；理想改寫「literature review agent」vs「systematic review automation」<0.5 不 flag；identical=1.0、disjoint=0.0、大小寫標點、max 取最高對）、fake-client repair 測試 2（修復成功=2 次呼叫且結果 ≤0.5；修復仍超標=保留第一版）。**218 tests OK**（209 既有 + 9 新）。log：`.omo/evidence/m5a-todo12.log`（Todo 1+2）、`m5a-todo3.log`（Todo 3）、`m5a-final-suite.log`。
- 驗收（Todo 4，🔄 部分：planning 層 ✅、下游被外部配額阻塞）：Langfuse health 200 ✅；同 query「literature review agent」完整 run 三次，**三次都在 RCS 被 Gemini free-tier 429 中斷**（`generate_content_free_tier_requests, limit: 20, model: gemini-3.6-flash`；使用者確認 2026-09-08 用量超限）——**但 planning 每次都成功並留下多面向證據**：子查詢字面重合度 M4 baseline（舊 prompt）`max=0.444` vs M5a run1 `0.077` / run2 `0.059` / run3 `0.133`，皆遠低於 0.5 且面向互異（架構/檢索/評測/人機協作等）。產物：`.omo/evidence/m5a-queries-overlap.json` + `m5a-after-quota-fail.log` + `m5a-after-quota-fail2.log` + `m5a-after.log`（run3 止於 plan）。**殘餘（待配額恢復續跑）**：進評分池組成（K3 對比=9 篇）、usable 數、天花板 rel——需一次完整 run，續跑指令比照先例 `printf 'literature review agent\n' | uv run --env-file .env python -m literature_review.main`，跑完把新 `W*.pdf` 移出還原 `data/papers/`。執行偏離比照先例：`--env-file .env`；下載落點為 main.py 硬編碼 `data/papers`（三次失敗 run 累積 24 個 `W*.pdf` 已移至 `/tmp/m5a-downloads/`、還原原 21 檔）。

## M6 — RCS 評分遷移本地 Ollama（qwen3:8b，batch 迴圈）（2026-09-09）

M6 回應「Gemini free-tier 429 連日卡 run」：RCS（量大窄任務）遷移本地 Ollama，逐篇筆記/synthesis/planner 維持 Gemini。`OLLAMA_BASE_URL` 缺設 → fallback 既有 Gemini RCS（逃生門）。

- `literature_review/ollama_client.py`（新）：`OllamaJsonClient`（OpenAI 相容 `/v1/chat/completions`，temperature 0.2、stream False、`response_format` 用 **`json_schema` structured output**——OpenAI 包裝格式 `{"response_format":{"type":"json_schema","json_schema":{"name":"evidence_batch","schema":<schema>}}}`）。**Todo 0/4 實測結論**：`json_object` 只保證回裸 JSON、**不強制 schema 形狀**（Qwen3 曾回 `{"evidence_chunks":[...]}` 缺 `assessments` 外層）；`json_schema` 包裝格式實測欄位形狀完全遵從（裸 `json_schema:` 格式無效，model 自造欄位）。測試 4 例（body 斷言 json_schema 包裝格式）。
- `literature_review/llm_evidence.py`：`summarize_and_rerank` 改 **batch 迴圈**（`RCS_BATCH_SIZE=4`；原 8 實測 Qwen3 **漏 chunk / repair 竄改 chunk_id**——`W4378983550-p6-7-c16` 錯寫成 `W4401667275-p6-7-c16`，B=4 兩批皆 ok）；`build_evidence_prompt` 支援 chunk 子集；repair 兜底 `except LlmEvidenceError`（Qwen 缺欄位拋此而非 syntax error）。測試同步（SubsetFakeClient、batch 邊界 9→3 calls、repair 補回/仍漏 raise）。
- `literature_review/pipeline.py` + `main.py`：`run_synthesis_pipeline` 拆 `client_rcs` 參數（**只用於** `summarize_and_rerank`）；`_build_clients` 在 `OLLAMA_BASE_URL` 設時建 `OllamaJsonClient` 給 RCS，否則 `None` → pipeline 內部 fallback Gemini client。`test_main.py` 4 個既有測試 patch 加 `OLLAMA_BASE_URL=""` 防 CI 環境干擾。**227 tests OK**。
- 校準（Todo 4，免費）：`data/papers/` 21 檔基準（`/tmp/k3-fixture/` 已不存在→依計畫優先序②）；20 可用文件（W4392881457 JPEG 偽 PDF 抽取失敗）、32 chunks、**8 calls = ceil(32/4)、0 repairs**；10 篇進評分池。**Ollama 尺度明顯偏高 vs Gemini 歷史**：rel 4.2-9.2 vs 2.1-6.5、天花板 9.2（ReseachAgent）vs 6.3、include 2 + consider 5 vs K3 的 1 consider；chunk 層多數 10/10（AIDE 10/10 疑似過度樂觀，對照 K3c Gemini 偏保守）。抽查 5 chunks 待使用者親測（`.omo/evidence/m6-calibration.md`）。**結論：跨 run 比較必須標註 RCS 模型；閾值 8/6 在 Ollama 尺度下是否過寬 → M5b 決策**。
- 真實 run（Todo 5，🔄 部分：RCS ✅、下游被 key2 配額阻塞）：同 query「literature review agent」完整 run **兩次**（至多重試一次上限），**兩次都在 RCS（Ollama）成功後卡在逐篇筆記（Gemini key2）429**（`generate_content_free_tier_requests, limit: 20, model: gemini-3.6-flash`）——planner key1 ✅（各產 4 條多面向 query）、下載 ✅（17 + 11 個 `W*.pdf` 已移至 `/tmp/m6-real-run-pdfs/`）、`data/papers/` 已還原 21 檔基準。**RCS-on-Ollama 真實 run 驗證通過**（兩次皆零錯誤走完 RCS 階段）。產物：`.omo/evidence/m6-real-run.log`（第一次）+ `m6-real-retry.log`（重試）。**殘餘（待配額恢復續跑）**：完成 notes+synthesis 的完整 run（進評分池/usable/天花板 rel、report JSON），續跑指令 `printf 'literature review agent\n' | uv run --env-file .env python -m literature_review.main`，跑完移出新 `W*.pdf` 還原。

## M5b — RCS 校準改版：schema 欄位順序/雙 rationale/A9 分帶/輸入精簡 + B 定案（2026-09-10）

M5b 回應 M6 校準發現的 5 個病徵（2026-09-09 定案）：schema 欄位順序被架空（rationale 在分數後）、10/5/1 錨點鬆散、單 rationale 混兩維度、RCS 輸入過胖（250 字 text + chunk_id/paper_id/頁碼 漏 metadata）、閾值未隨模型重校。

- `literature_review/models.py`：`LlmEvidenceAssessment` 欄位順序修正——`rationale_relevance`/`rationale_quality` 在分數**前**（M4 I 項「先理由後分數」不再被 schema 順序架空）、移除單一 `rationale`（實務為 `rationale_relevance` field alias，`rationale` 僅作為**輸出相容別名**——實際只送雙欄位、不送舊單欄）。`LlmEvidenceAssessmentBatch.assessments`（每批一格）。
- `literature_review/llm_evidence.py`：`build_evidence_prompt` 改 A9 分帶——rel/qual 各 5 帶（9-10/7-8/5-6/3-4/1-2，每帶 1-2 句釋義 +「9-10 是例外等級」共用規範，取代舊 10/5/1 錨點）；A10 三層結構「用自己的話重述 chunk 核心 → 說明與 query 的關係 → 給分」；**輸入精簡**——prompt 只給 `## Chunk N` + text（250 字），metadata 以 `Citations: {n} | Venue: {v}`（缺則 `n/a`）帶入當參考訊號；**不送** chunk_id/paper_id/頁碼。`summarize_and_rerank` 用 `index_map = {str(i+1): chunk_id}` 組合 index→id；`_resolve_indexes` 遇 unknown index raise → 一次有期望 ids 的 repair（**修 identity-failure repair 漏帶 `expected_chunk_ids` 的實作缺口**：except 分支原本不帶 ids，與 Todo 3/docstring 宣稱不符，實測 qwen3:8b B=1 高頻竄改索引 '2'/'A1'/'C1' 時救不回）。
- `literature_review/pipeline.py`：`_rcs_batch_size()` 讀 `RCS_BATCH_SIZE` env（預設 **"4"**——Todo 5 校準定案，見下；非法值 fallback 4）。`main.py` 無契約變更。
- 測試同步（8 個測試檔）：`_prompt_chunk_indexes` 改抓 `## Chunk (\d+)`；fake assessments 用 index 字串 ＋雙欄位；schema 順序/分帶措辭/`Citations`/`Venue`/`Do NOT penalize` 斷言；新增 `IndexDriftFakeClient` + `test_drifted_index_is_repaired_once_with_expected_ids`（unknown index 一次 repair 帶期望 ids 修復）。**230 tests OK**（`test-suite-m5b-todo3.log`）。
- 校準（Todo 5，免費全 Ollama）：`data/papers/` 21 檔基準、20 可用文件 32 chunks、同 M6 設定。**B=1 vs B=4**：B=1 32 chunks/48 calls/**16 repairs**（竄改率 50%，掃描 7/14 batches 回 '2'/'A1'/'C1'——無參照漂移實證）；B=4 32 chunks/10 calls/**2 repairs**（穩定）。**B 定案 = B=4**（計畫判準：B=1 有無參照漂移 → 回 B=4）。**10 分比例 15/32（M6）→ B=4 3/32 / B=1 1/32**——A9/A10 分帶收斂有效。論文層天花板 9.2（M6）→ 7.8-7.9；include 0 / consider 9-10 / exclude 0-1（M6 為 2/5/3）——天花板 < include 8 → **閾值決策懸置**（建議 include 8→7 或維持，待使用者 5 樣本抽查裁定；決策文件 `.omo/evidence/m5b-calibration.md`，留待填欄位）。
- 真實 run（Todo 6，🔄 掛帳——非 Gemini 配額，是 **OpenAlex 429**）：Gemini 兩 key 配額探測皆可用（35s/14.5s）；兩次嘗試（retryAfter 39s/31s 各等後重試）皆被 OpenAlex「Anonymous search is temporarily rate-limited」429 中斷（外部 API 暫態負載、非 code）；依計畫「至多 1 次嘗試 + 1 次 retry；超限即掛帳」→ **Todo 6 掛帳**。`data/papers/` 已還原 21 檔基準（run1 的 3 個新 `W*.pdf` 移出至 `/tmp/m5b-partial-run/`）。log：`.omo/evidence/m5b-real-final.log`（run1）+ `m5b-real-retry.log`（run2）。**殘餘（待 OpenAlex 負載恢復續跑）**：完整 run 一次（M5b 端到端 + M6 Todo 5 關閉 + M5a 下游數據），指令 `printf 'literature review agent\n' | uv run --env-file .env python -m literature_review.main`，跑完移出新 PDF 還原，log 存 `.omo/evidence/m5b-real-final.log`。

## M5b.1 — RCS prompt 小修：5 段指示（子主題定位/反污染/主題混雜/獨立性/metadata 用法）（2026-09-10）

M5b.1 回應使用者 5 樣本人類基準（①5/6 ②8/8 ③2/9 ④8/8 ⑤4/7）揭露的 rel 系統性錯誤——③資料豐富誘導高估（AIDE）、⑤背景/方法被當核心（protocol）、④破碎表格保守、主題混雜誤判。**只動 `llm_evidence.py` prompt 文字 + 測試 + 校準重跑**，不動 schema/流程。

- `literature_review/llm_evidence.py`：`build_evidence_prompt` 在 scoring guide 前插入 5 段指示（英文原文，語義未改）：①sub-question 定位（先一句話陳述 query 的具體子問題，再判 chunk 主體內容是否真正對應）②anti-pollution（rich data/tables/methodology 不自動 = relevant；兩分數獨立）③mixed-topic（以 chunk 主要篇幅討論內容為準，不因孤立片語重疊給高分）④independence（high rel 不必然 high qual）⑤metadata-use（citation/venue 只是次要支援訊號，不因低引文單獨降 qual）。
- 測試：`tests/test_llm_evidence.py` 新增 `test_evidence_prompt_contains_m5b1_score_guidance`（斷言 sub-question / NOT automatically relevant / majority of the chunk / independently / secondary supporting signals 在 prompt）。**231 tests OK**。
- 校準重跑（Todo 3-4，免費全 Ollama、設定與 M5b 完全一致）：證據 `.omo/evidence/m5b1-calibration.{json,log,md}`。**全量 vs M5b**：10 分比例 B4 9%→12.5% / B1 3%→12.5%（**回升**，緊縮效果未如預期）；天花板 rel 7.8-7.9 → **8.5**（include 0 → B4 1 篇 PaperQA2 8.5/8.2、B1 2 篇）；repairs B4 2→3 / B1 16→11（竄改率 50%→34% 改善）。
- **5 樣本 |Δrel| 平均誤差無改善**（B4 3.60→3.80、B1 3.40→3.40）；|Δqual| B4 1.40 持平、B1 1.40→1.20。**特別檢查未過（Success criterion 4）**：樣本③ AIDE rel 未顯著下降（M5b B1=8/B4=9 → M5b.1 B1=9/B4=8，人類 2）；樣本⑤ protocol rel 未下降（B1=8/B4=10 → B1=9/B4=9，人類 4）。檢討：8B 模型遵從長 prompt 能力有限，「主體內容」判斷太抽象、embedding top-32 已選入相關 chunk。
- **B 決策（只建議不拍板）**：依計畫判準（|Δrel| 差 0.40 <0.5 且 B4 repairs 顯著少）→ 案 A（建議）維持 B=4；案 B 改 B=1（現數據不支持）。**閾值**：新天花板 8.5 ≥ 7 → 建議維持 M5b 案 (b) include 7/consider 6；但 ③⑤ 高估未解，收進的 include 可能是高估論文，建議以 Todo 6 真實 run 複核。

## M5e — 候選池升級：LLM 摘要篩選層 + 分桶選樣 + gap follow-up（2026-09-10）

M5e 回應「1 篇 usable 是候選池組成問題」：在 search/rank 與 download 之間插入一次 LLM 篩選（key1，與 planner 同 client），用分桶選樣壓縮輸入、keep/maybe 三層級排序下載、gap 分析補 follow-up 檢索。Gemini 全包（planner/screening=key1，notes/synthesis=key2）；RCS 不動（仍 Ollama、B=4）；`SearchPlan` 契約不動（object 保持 planner 輸出原樣）。

- 新增 `literature_review/screening.py`：`search_samples`（B 桶 rank 前 50% + 近 1-2 年；A 桶前 20% + 引用前 30%；C 桶分位 40-70% + 引用前 30%，比例 ~25/50/25）、`build_screening_prompt`（全部 query 候選合併單一 prompt、`## Query N`＋`### [DOC_n] year title`＋摘要、**doc_id 制**：候選以全域連續 `[DOC_n]` 編號呈現，prompt 明示「只對清單內論文決策、不得新增清單外論文」；回傳 `doc_id` 由程式對回 paper_id——**2026-09-13 使用者裁示從 title copy 制改為 doc_id 制**，避免長標題（含 LaTeX 符號）複製出錯、並讓清單數量一目了然）、`build_follow_up_queries`（gap 分析 cap 3 條、`generated_by="gap-follow-up"`、帶 `reason`，輸出為 `FollowUpProposal` 清單）、`screen_candidates(client, ..., parse=...)`（共用 `llm_evidence.generate_validated` 一次呼叫＋一次 repair；resolution 錯誤——重複 doc_id/缺篇——併入 parse 後亦吃一次 repair budget；**unknown doc_id（幻覺/清單外）不再 fatal，改為 stderr warning 後丟棄該決策**，missing 檢查保留）。決策集合 `ScreeningDecision`：query/title/recommendation（keep/maybe/exclude）/rationale。
- `literature_review/main.py`：`run_end_to_end` 新參數 `client_screen`（缺設 None＝legacy per-query 路徑維持原行為）；**screening 路徑**＝分桶選樣 → 單一 merged `screen_candidates` call → gap follow-up 迴圈（至多 1 輪，provenance 記錄於 `follow_ups`）→ **單一 merged download**（`stats_per_query` 僅 1 筆）；`--dry-run`／無 key 時篩選層跳過（`use_screening = client_screen is not None and not dry_run`）。`sample_candidates(ranked, per_query_target=24)`。
- `literature_review/pdf_downloader.py`：`download_and_backfill` 新增 `priority_groups` 參數——`keep` 組**全下**（不限 target_n，品質已由篩選層把關、下游 RCS 過濾）、`maybe` 組僅補位至 target_n（組內 rank 序）；缺省走原 top-N backfill。
- `literature_review/planning.py`：planner prompt 維度詞庫內部化（3-5 條 query 覆蓋不同面向/同義詞變體；契約不動）。
- `literature_review/pipeline.py`：`_notes_pacing_seconds()` 讀 `NOTES_PACING_SECONDS` env（預設 **"4"**、非數字或負值 fallback 4.0、`0` 停用）——逐篇筆記 calls 間 sleep，緩解 Gemini key2 429 頻率。
- 測試：`tests/test_screening.py`（**10 tests**：分桶組成/邊界、prompt 格式只含 title 無 id、決策解析、缺篇/重複 title/unknown title resolution 錯誤 repair、follow-up cap）；`tests/test_pipeline.py`（+3 pacing tests）；`tests/test_main.py`（+3 M5e 接線：dry-run 傳 `client_screen` 仍跳過篩選、無 screen client 維持 legacy per-query、fake screen e2e 含 follow-up→merged download→報告 4 篇）。**248 tests OK**（`test-suite-m5e.log`）。py_compile 全 OK。
- **2026-09-13 修正——screening 契約改 doc_id 制**（S1-S5 真實 run 第三次卡在 screening LLM 幻覺標題 `LLM$\times$MapReduce-V2: ...`——論文真實存在但不在候選池、repair 仍回同標題＝LLM 長標題複製失誤；使用者裁示改 ID 制）：候選改以**全域連續編號 `[DOC_n]`**（跨 query 依 query+rank 順序編號、`_doc_id_map` 對應 paper_id）呈現於 prompt，決策契約 `_LlmScreenDecision.title` → `doc_id`（pattern `^\[?DOC_\d+\]?$`），prompt 加限制句「只對清單內論文決策、不得新增清單外論文」；`_resolve_output`：unknown doc_id（幻覺）**stderr warning 後丟棄該決策（不再 fatal）**、重複 doc_id 仍 raise、缺篇仍 raise——「多出」情境不再 abort，missing 真候選仍兜底。`ScreeningDecision.title` 對外契約不變（仍填 paper title）。測試遷移：`tests/test_screening.py` 11 tests（+unknown-drop 實例、unknown 不能頂替缺篇）、`tests/test_main.py` FakeScreenClient 改吐 doc_id。**350 tests OK**。
- 真實 smoke 視配額結果：dry-run smoke 卡 OpenAlex 429 兩次（retryAfter 33-36s）後第三次成功——4 條 rule-based query、下載 5+2+3+3=13 篇、shortfall 0、`stats_per_query` 4 筆＝dry-run 維持 legacy per-query（screening 正確跳過）；log `.omo/evidence/m5e-dryrun-smoke.log`；`data/papers/` 還原 21 檔（新檔 W4318707231.pdf 移出至 `/tmp/m5e-dryrun-run/`）。
- **Todo 5 真實 run 掛帳**（使用者 2026-09-10 裁示：修改事項清單清空前不跑整條）；判定 = usable ≥ 2 且天花板 rel > 8.5（M5b.1 校準基準）；指令與還原先例比照 M5b，log `.omo/evidence/m5e-real-final.log`。

## C2b — 功能性分數評分改版（Todo 0-4 ✅ + Todo 5 測試 ✅，真實 run 掛帳）

C2b 把評分從「corpus-wide RCS（rel/qual 雙分數 + 收縮平均）」改成「功能性單一 1-10 分數 + per-paper 取樣 + 配額∩閾值入選」：功能性語意 = 對研究 idea 的證據貢獻（10 直接推進 / 5 背景價值 / 1 無貢獻），取代舊 rel/qual 雙維契約；每篇僅樣本 top-2 chunks（主 query embedding 取樣）小批次（5/batch）評分；入選 = per-query 組內 top-N（第一輪 2 / follow-up 1）∩ 平均 ≥ 閾值 6.0（env `FUNCTIONAL_THRESHOLD` 覆寫）；收縮平均與 consider 層級退役（assessment 只剩 include/exclude）。**RCS 保留 compare/legacy，不刪**。

- `literature_review/models.py`：新增 `LlmFunctionalAssessment`（恰三 key：chunk_id/rationale(min_length=20)/utility_score(1-10)，`extra="forbid"`）、`LlmFunctionalAssessmentBatch`、`FunctionalScoringPolicy`（`top_chunks_per_paper=2`、`batch_size=5`、`threshold=6.0`、`n_first_round=2`、`n_follow_up=1`、`min_words=4`）、`FunctionalPaperScore`；`EvidenceCitation`/`PaperAssessment` 遷移 `utility_score`（兩分法：include/exclude）；`retrieval_policy`/`TOP_K_CHUNKS` 退役標 legacy（C2a 的 `chapter_chunk_document` + `drop_noise_sections` 於此接線，chunk_id = `{paper_id}-c{n}`）。
- `literature_review/functional.py`（新增）：`build_functional_prompt`（只給 index + text，title/section 為包裝參考；10/5/1 操作型錨點）、`sample_top_chunks_per_paper`（黑名單過濾 → 自家 chunks 內 embedding top_n）、`score_chunks_functionally`（批次迴圈 + index resolve + expected-ids repair，`FunctionalScoringError(LlmEvidenceError)`）、`aggregate_functional`（平均 round 1 位，產分數容器）、`select_quota_threshold`（per-query 分組 → 降序取 N → 閾值篩 → 統一建最終 `PaperAssessment`；平手規則 = 高 utility 優先、同分 chunk_id lexicographic；未評分論文以 exclude + 原因記錄）。
- `literature_review/pipeline.py`：`run_synthesis_pipeline` 串接 functional 評分路徑（`client_rcs` 語意改為功能性評分 client、缺省 fallback `client`）；`_prepare_documents` 新增 `min_words` 過濾（近空文件印 stderr 並跳過，不改全文）；`retrieval_policy`/`aggregation_policy` 僅簽名相容、忽略。`main.py` 組裝 `paper_titles`/`paper_queries`/`follow_up_queries` 傳入（downloads 迴圈記錄 paper_id → query）。
- `literature_review/synthesis.py`：合成 prompt 拿掉 A 區塊（逐 chunk 證據摘要），只送筆記 + 入選論文功能性 assessment 摘要；`_allowed_marker_ids` = 入選功能性證據 chunks ∪ notes coverage；`_convergence_direction`/`_fallback_direction` 改用 utility_score（收斂判據 `_CONVERGENCE_UTILITY_SCORE = 6`）。
- 測試遷移（Todo 4/5）：功能契約測試 +8（functional.py 全部單元）；`test_pipeline.py` 遷移 9 處（payload 三函式改功能性、SynthesisFakeClient regex `"([^"]+-c\d+)"`、PaperDropFakeClient 改 required_keys 解析、`rcs_single_batch` → `functional_single_batch`、`unrelated_paper_below_threshold_excluded` 取代 corpus-top-k 語意）；`test_main.py` payload 三函式 + regex 遷移；`test_synthesis.py` 收斂/fallback 分數改 7/8、4。**308 tests OK**（`c2b-tests.log`，287 → 308 全綠、0 刪除遷移）。py_compile 全 OK。
- **Todo 5 真實 run 掛帳**（使用者 2026-09-11 裁示：評分線路改 Gemini、B=8、報告第三 key 等併入 C2c 一次落地，C2b 驗收時提醒加 `GEMINI_API_KEY_3` 到 .env（WSL+Windows））；指令 `printf 'literature review agent\n' | uv run --env-file .env python -m literature_review.main`；收集功能性分數分布、入選數 vs 配額/shortfall、「keep 但 exclude」衝突集合人工抽查、閾值校準建議（6.0 預設 vs 分布）、對照 M5b.1 斷裂基準只記分布不硬比。**待辦**：commit/push 由使用者親做（指令見計畫檔 Commit strategy，code 1 筆 + docs 1 筆 + push）。

## C2a — PDF 抽取換血 pymupdf4llm + 章節感知切分（2026-09-10）

C2a 把 PDF 抽取從 pypdf 裸文字換成 pymupdf4llm（markdown 輸出，表格完整），並新增章節感知的證據切分——治療 K3c/A2「表格破碎→LLM 保守→低估」病根（抽好 vs 硬看）。C2a 不接 pipeline（接線 = C2b），框架層全相容。

- `pyproject.toml`/`uv.lock`：新增 `pymupdf`、`pymupdf4llm`、`langchain-text-splitters`。
- `literature_review/extraction.py`：`extract_pdf_text` 主路徑改用 `pymupdf4llm.to_markdown(page_chunks=True)`（`extraction_method="pymupdf4llm"`，頁號取自 per-page metadata,1-based）；失敗時 stderr 警告並 fallback 原 pypdf（`extraction_method="pypdf"`）。簽名不變、`FullTextDocument`/`PageText` 保留。**已知限制**：`A Additional...` 型字母前綴 appendix 標題不被 `_APPENDIX_REGEX`（數字前綴限定）識別，維持舊行為。
- `literature_review/models.py`：`EvidenceChunk` 新欄位 `section: str | None`（markdown heading path，如 `"Title > 1 Introduction"`）；`page_start`/`page_end`、`EvidenceSummary`/`EvidenceCitation`/`ChunkReference` 的 page 欄位改 `int | None`（不做頁碼回推工程，現成才填）。
- `literature_review/evidence.py`：新增 `chapter_chunk_document(document, policy)`——`MarkdownHeaderTextSplitter` 三層（`#/##/###`）切分、section 帶完整 heading path、**段落優先細切**（空行分組、累積至 ≤ `max_words`=350、表格 block 整體保留、超長純文字段落才滑動視窗兜底）、chunk_id = `{paper_id}-c{n}`（全域連續，不再嵌 page）。**實作發現**：`MarkdownHeaderTextSplitter` 會把段落間 `\n\n` 正規化成 `  \n`（硬換行），`_split_section_paragraphs` 需先 `_HARD_BREAK_REGEX` 還原，否則整段變單一 chunk 走滑動視窗路徑。`chunk_document` 舊函式完整保留（legacy/compare，section=None、舊 chunk_id 格式）。
- `literature_review/coverage.py`：`_SECTION_HEADERS` 加入 acknowledgments（priority 14，與 references 同級最低）、新增 `classify_chunk(chunk)`（有 section → 最後一級章節名對照總表；無 section → 舊 `classify_section(text)`）、`_classify_heading` 單一來源、`_select_pack`/`_region_priority` 改用 `classify_chunk` + page `or 0` 相容（references/acknowledgments/appendix 偵測 section 優先）。新增黑名單工具 `drop_noise_sections(chunks, *, drop_appendix=True)`——只丟 references/acknowledgments（appendix 可選），其餘全收、無 section chunk 不誤殺；分類判定與 `classify_chunk` 共用。
- 測試：`tests/test_evidence.py`（+8：heading path 繼承、chunk_id 全域連續、無標題 section=None、page None、段落優先不破界、表格 block 保留、滑動兜底、overlap 拒絕）；`tests/test_synthesis.py`（+7：classify_chunk 帶/無 section、drop_noise_sections 丟/留/不誤殺、drop_appendix 開關）；`tests/test_extraction.py`（+1：pymupdf 合成表格 PDF fixture sanity check）。**264 tests OK**（248 → +16），log `.omo/evidence/c2a-tests.log`。
- 證據：`.omo/evidence/c2a-table-compare.md`（真實樣本 W3195625625 表格頁：舊 pypdf 破碎 vs 新完整 markdown 表格）；`.omo/evidence/c2a-chapter-smoke.md`（3 真實樣本端到端：chunks 76/74/69、每篇 References 黑名單識別、表格 chunk 帶 `|` 行、chunk_id 全域連續、無標題 PDF 自動標題偵測有效）。樣本 `/tmp/c2a-poc/`（W3195625625 有書籤、ReseachAgent 無書籤、PaperQA2 有書籤），`data/papers/` 21 檔未動。
- **待辦**：commit/push 由使用者親做（指令見計畫檔 Commit strategy，兩筆 + push）；C2b（功能性分數評分改版）計畫為下一里程碑。

## C2c — 報告 claim-level 標註 + key3 + materials 程式組裝（2026-09-12）

C2c 把報告生成從「逐 chunk marker」改成「claim-level marker」（設計 B）：LLM 只產 `[claim-N]` 標註（全域連續、文件序編號），程式機械 claim→chunks 展開；`[claim-N]` 驗證 + 未知 claim 修復（一輪 `_generate_validated` repair）；`render_materials_section` 程式組裝材料來源清單（A5：LLM 不碰）；報告第三 key（`GEMINI_API_KEY_3`）；`FUNCTIONAL_BATCH_SIZE` 5→8。

- `literature_review/synthesis.py`：`build_synthesis_prompt` 重寫（notes 含 `claim_id`/`text`/`aspect`、assessments 只含 `paper_id`/`utility_score`/`rationale`、指示「End every factual sentence with `[claim-N]`」、無材料清單句）；`_allowed_marker_ids` 改用 `build_claim_chunks(usable_notes)` 產 claim id 集合；`_build_claim_repair_prompt`（unknown markers → prompt 修復）；`_repair_claim_markers`（no markers → SynthesisError、unknown → 一輪修復 + re-check → still unknown → raise）；`synthesize_report` 呼叫 `_repair_claim_markers`、limitation text `[claim-N]`；`render_materials_section(paper_sources, paper_summaries, claim_chunks)` — 程式組裝 `## 材料來源清單`。
- `literature_review/models.py`：`FunctionalScoringPolicy.batch_size` 預設 5→8。
- `literature_review/functional.py`：`FUNCTIONAL_BATCH_SIZE = 5` → 8。
- `literature_review/pipeline.py`：`run_synthesis_pipeline` 簽名加 `client_report: JsonGenerationClient | None`；呼叫 `synthesize_report(..., client_report or client, ...)`；LLM 路徑自動 append `render_materials_section`（`generated_by == "llm"` 才 append，deterministic 路徑自帶自己的材料清單）。
- `literature_review/main.py`：`_build_clients` 回傳 4-tuple `(client_plan, client_synth, client_rcs, client_report)`；key2 缺 → exit 1（在 key3 之前）；key3 缺 → exit 1；`run_end_to_end` 簽名加 `client_report`，pass-through 到 pipeline；`main()` 解包 4-tuple。
- 測試遷移：`test_synthesis.py` — LlmSynthesisReportTests 改用 `[claim-1]`/`[claim-2]` markers、`assertNotIn("材料來源清單")`、markers ⊆ `set(result.claim_chunks)`、新增 repair-retry test（RepairingClient 2-call）；direction test marker → `[claim-1]`；`test_pipeline.py` — SynthesisFakeClient/PaperDropFakeClient regex → `"claim_id": "(claim-\d+)"`、`cited_claim_ids`、drops 材料來源清單；happy assertions → markers ⊆ set(result.claim_chunks) + assertIn 材料來源清單；batch comment 5→8；`test_main.py` — SynthesisFakeClient regex + cited_claim_ids + drops 材料來源清單；`_build_clients` tests 4-tuple + GEMINI_API_KEY_3 env patches + call counts (Gemini 3/rule-based 2/ollama 3+1) + new `test_build_clients_missing_key3_exits`；`test_full_run_produces_report` assertIn("[claim-" + assertIn 材料來源清單；`test_models.py` batch_size 5→8；`test_functional.py` comment 5→8。**335 tests OK**（`c2c-tests.log`，308→335、+2 新測試、0 刪除）。py_compile 全 OK。
- README.md：marker 契約更新（`[chunk_id]` → `[claim-N]`）、key config 更新（key2=notes/scoring、key3=report）。
- **Todo 5 真實 run 掛帳**（最終線路：評分 Gemini+B=8、notes key2、報告 key3；收集：claim marker 正確率、材料清單渲染、報告尾端中文瑕疵消失確認、claim→chunks 完整性、各 key call 次數統計）；指令 `printf 'literature review agent\n' | uv run --env-file .env python -m literature_review.main`；`data/papers/` 還原 21 檔基準。**待辦**：commit/push 由使用者親做（指令見計畫檔 Commit strategy，code 1 筆 + docs 1 筆 + push）。

## S1-S5 — 評分線/輸出線調整（2026-09-13；真實 run 掛帳）

S1-S5 四個小改動一次落地：輸出層統一 claim（S1）、top-2 取樣用 sub-query（S2）、論文分數 0.7×max+0.3×mean 聚合（S3）、功能性 prompt 三類支持+0 分錨點（S4）、aspect 編號合併（S5，觀察用）。

- `literature_review/models.py`：`FutureDirection` 雙欄位並存——`supporting_claim_ids`（LLM 路徑）+ `supporting_chunk_ids`（deterministic 路徑），validator 至少一欄非空；`utility_score` 4 處 `ge=1`→`ge=0`（S4 0 分錨點）；`FunctionalScoringPolicy.max_weight = 0.7`（S3，`ge=0, le=1`）。
- `literature_review/synthesis.py`：`_expand_claim_ids` 刪除（S1：LLM 路徑直接填 `supporting_claim_ids`，不再機械展開回 chunk）；`merge_numbered_section` 新增（S5：`2.1 X`→`2`，單段/無編號原樣保留）+ `_group_chunks_by_section` 每 chunk aspect 過 merge——清單層與 claim aspect 一致；deterministic 路徑維持 `supporting_chunk_ids`（逃生路徑舊行為不破）。
- `literature_review/functional.py`：`sample_top_chunks_per_paper(..., query_map=None)`（S2：用下載該論文的 sub-query 做 embedding 取樣，`(query_map or {}).get(paper_id) or query` 缺省/空字串回退主 query）；`aggregate_functional(..., max_weight=0.7)`（S3：`max_weight×max + (1-max_weight)×mean`，`round(...,1)` 維持、n=1 不收斂）；`build_functional_prompt` 重寫（S4：`(0-10)`、三類支持檢查清單明示「舉例不限於此」、0 分錨點 = 純常識/泛泛而談/無關、「若給 0 分 rationale 說明」）。
- `literature_review/pipeline.py`：傳 `query_map=paper_queries`（S2）、`max_weight=effective_functional_policy.max_weight`（S3）。
- `literature_review/demo.py`：維持相容（`supporting_chunk_ids` 補丁）。
- 測試遷移與新增：`test_models.py`（雙欄位 validator 兩路徑各一測試）；`test_functional.py`（query_map 有/無/空三路、[8,7]→7.8、[8,9]→8.8、[8,3]→7.2、max_weight 0/1 極值、n=1 collapse、prompt 三類/0 分、(0-10) 驗證通過）；`test_synthesis.py`（`merge_numbered_section` 四規則 + aspect 分組一致性 + FutureDirection 兩路徑）。**346 tests OK**（`s1s5-tests.log`，335→346、+11、零刪除）。fake e2e（test_main+test_pipeline）37 tests OK（`s1s5-fake-e2e.log`）。S3 離線重算對照 `s3-aggregation-compare.md`：mean 版入選 4/10 → 加權版 7/10，threshold 6.0 建議維持（標註待真實 run 複核）。
- README.md：無需改動（功能性評分細節不屬 README 契約面）。
- **真實 run 掛帳**（S1-S5 未跑；最終線路一次跑，涵蓋 S2 sub-query 取樣、S3 加權聚合分布、S4 0 分出現率、S5 aspect 碎片化觀察——無層級論文（全部 #）的 aspect 清單是否碎片化、編號合併能否解決）；commit/push 由使用者親做。

## C2e — 兩階段報告生成：outline → report → directions（2026-09-13）

C2e 把合成報告從「單一 LLM call（一包 `{report, future_directions}`）」改成**三 call 兩階段流程**（2026-09-13 使用者定案）：call 1 請 LLM 先規劃主題式大綱（2-4 節、每節 title/purpose/`supporting_claim_ids`），call 2 依大綱寫報告正文，call 3 **獨立**產出未來方向（只看 query+claims，不看大綱，避免報告框架窄化方向）。三次呼叫共用報告金鑰（key3＝`client_report or client`）。對外輸出契約（`SynthesisResponse`/`FutureDirection`/`[claim-N]` marker/材料來源清單）**完全不變**。

- `literature_review/models.py`：新增 4 個 LLM 契約模型——`LlmOutlineSection`（title≥3、purpose≥10、supporting_claim_ids≥1）、`LlmSynthesisOutline`（sections≥1）、`LlmSynthesisReportBatch`（只有 report≥100）、`LlmSynthesisDirectionsBatch`（future_directions≥1）；legacy `LlmSynthesisBatch` 保留（逃生路徑/測試用）。
- `literature_review/synthesis.py`：`_build_notes_payload(paper_summaries, usable_ids)`（notes 精簡版，claim id 與 `build_claim_chunks` 完全同序）＋ `build_outline_prompt(query, notes, claim_counter_total)` / `build_report_prompt(outline, notes)` / `build_directions_prompt(query, notes)`；`build_synthesis_prompt` **刪除**（由 `build_report_prompt` 取代，`__all__` 同步）。`synthesize_report(..., query=None)` 改三 call：`_outline_call`（name="synthesis_outline"）→ `_repair_outline_markers`（unknown claim id 修復一輪、仍未知 raise）→ `_report_call`（name="synthesis_report_writing"）→ `_repair_claim_markers`（既有語意保留）→ `_directions_call`（name="synthesis_directions"）→ 方向 paper/claim id 驗證（outside supplied evidence → raise）。三 call prompt 皆不含 assessments 摘要（候選 E 落地）與 utility_score。
- `literature_review/pipeline.py`：`synthesize_report(..., query=query)` 傳 query。
- 測試遷移與新增：`test_synthesis.py` — `FakeSynthesisClient` 改序列回應（三 call 依序 pop）、新增 `TwoStagePromptTests`（三 prompt 內容：含全部 claim_id／含大綱節標題／不含 assessments／query 進 call 1+3）、大綱 unknown claim repair→reject、方向 paper/claim unknown reject；`test_pipeline.py`/`test_main.py` — `SynthesisFakeClient`/`PaperDropFakeClient` 遷移三 call（outline/report/directions 分支、`future_directions` 移出 report call）。**363 tests OK**（`two-stage-report-generation-tests.log`，354→363、+9、零刪除）。fake e2e 三 call 依序驗證 `two-stage-report-generation-smoke.log`（outline→report→directions prefix、report 來自 call 2、directions 來自 call 3、unknown paper direction rejection OK）。
- **真實 run 掛帳**（與 S1-S5 同批；最終線路一次跑）；commit/push 由使用者親做。

## Latest milestone: M5c — Semantic Scholar primary search (2026-09-21)

M5c makes Semantic Scholar the primary search provider, keeps OpenAlex as the fallback/CLI source, and backs up SS abstracts through OpenAlex DOI lookups:

- `literature_review/ss_search.py` (new): SS adapter with the same `SearchRequest`/`SearchResponse` contracts as `search.py`. 2.0s pacing (double the keyed 1 RPS limit; 1.1s tripped 429 on 2026-09-19), exponential backoff honoring `Retry-After` (max 3 retries), `paper_from_ss` normalizer (`paperId` used as `paper_id`, DOI from `externalIds.DOI`, citation count filled, `openAccessPdf.url` guarded for null/empty). Abstracts that are missing or shorter than 20 chars are filled with `ABSTRACT_PLACEHOLDER` so `Paper.abstract` (required, min_length=20) never crashes; a record missing `paperId`/`title`/`year` or with empty authors returns None exactly like the OpenAlex adapter.
- `literature_review/models.py` + `search.py`: `Paper.doi: str | None` added; OpenAlex `REQUESTED_FIELDS` includes `doi` and `paper_from_openalex` normalizes the bare DOI (`norm_doi` strips `https://doi.org/` / `http://doi.org/` / `doi:` and lowercases).
- `literature_review/pdf_downloader.py`: dedup moved from `paper_id` to `dedup_key(paper)` — normalized DOI first, `title:title+year` fallback — so the same DOI from different providers deduplicates within the merged `already_downloaded` set.
- `literature_review/main.py`: `_search_candidates` calls `ss_search.search_ss` when `SEMANTIC_SCHOLAR_API_KEY` is set (missing key, empty result, or failed request falls back to OpenAlex); `backfill_abstracts` replaces placeholder abstracts via `https://api.openalex.org/works/doi:{doi}` using `search.reconstruct_abstract` (0.2s pacing); papers still lacking an abstract are dropped before `filter_and_rank`. `run_end_to_end` is injectable via `ss_api_key`.
- Verification: **F1** full suite PASS (414 tests), **F2** probe metrics PASS, **F3** dry-run PASS, **F4b** scoped M5c real check PASS, **F4** full real run DEFERRED (Gemini free-tier quota 429 at the functional-scoring stage after the whole SS path — search + backfill + rank + download + extraction — had completed; per HANDOFF precedent, over-quota = 掛帳). Evidence: `.omo/evidence/m5c-todo0-probe.log`, `ss-probe-20260921T115955Z.json`, `m5c-smoke.log`, `m5c-real-check.json`.
- Probe highlights: DOI overlap between SS and OpenAlex is low (Jaccard 0.05–0.13), so switching to SS also changes the candidate pool substantially; SS abstract coverage is 0.91–0.92 (well above the 0.40 threshold), SS DOI coverage 97–98% (is what makes DOI dedup viable), and SS OA-PDF coverage is 0.55–0.76 (slightly below OpenAlex's 0.72, so download shortfalls remain a real-run concern).
- Note: HANDOFF's older "21-file `data/papers/` baseline" statements were accurate at their time; the current baseline is **26 files** (pre-dates M5c smoke on 2026-08-26..2026-09-16). Historical records are intentionally not rewritten.

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

Suggested sequence for the next milestones (track the current one in `.omo/STATE.md`; each plan lives in `.omo/plans/`):

1. Unpaywall no-OA backfill for papers without an OpenAlex/SS OA link;
2. smaller candidates tracked in `.omo/STATE.md` (e.g. functional-prompt Section interpretation, M5f embedding query representation, `data/papers/` accumulation strategy).

## API and operational notes

- OpenAlex was the original primary search provider because it works without an API key; since M5c it is the fallback/backfill source, but `search.py` still serves the `search`/`--rank` CLI and the SS fallback. `search.py` uses `urllib`, a User-Agent, timeout, and retries. A past Windows/Python TLS EOF error was intermittent; `curl.exe` returned HTTP 200. If it returns repeatedly, consider switching that module to `httpx`, not disabling TLS verification.
- Semantic Scholar is now the primary search provider (M5c) when `SEMANTIC_SCHOLAR_API_KEY` is set; `ss_search.py` paces at 2.0s and retries 429 with backoff. SS's free-tier default "no key" 1 RPS limit and past shared-environment rate-limit errors are why the keyed adapter stays conservative.
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
Work in this repository on ADSL summer-project Task 1A. Read AGENTS.md and HANDOFF.md first, then `.omo/STATE.md` for the current milestone and `.omo/plans/<slug>.md` as the authoritative plan. Inspect git status and preserve user changes. Implement only the current next milestone tracked in `.omo/STATE.md`. Do not request or print API keys; use .env/environment variables. Keep Pydantic contracts, add tests where needed, run the full test suite, and report the exact PowerShell or WSL commands for the user to commit. Reply in Traditional Chinese and keep explanations concise.
```

## New Codex session starter prompt

Use this prompt when starting a fresh Codex task for this repository. It intentionally delegates details to the tracked documents instead of replaying long chat history:

```text
Continue the ADSL Summer Project Task 1A in C:\Users\User\Desktop\Literature_Review_Agent. Read AGENTS.md and HANDOFF.md before taking action, then `.omo/STATE.md` for the current milestone and `.omo/plans/` for the authoritative plan. The LLM planner, PDF acquisition, end-to-end `main.py`, functional scoring, claim-level note/report markers, and JSON report output are complete; implement only the current next milestone tracked in `.omo/STATE.md`. Inspect git status first, do not commit Summer_Project.pdf, data/papers/, or data/outputs/, add tests, run the full suite, and give exact PowerShell commit commands. Reply concisely in Traditional Chinese. Do not ask for or print API keys.
```
