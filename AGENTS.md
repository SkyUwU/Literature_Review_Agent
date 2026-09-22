# Collaboration Guide

## 計畫與執行分工（Sisyphus 鐵則）

- Plan agent 官方問題期間，由 Sisyphus 兼任計畫與執行。
- 使用者說「討論計畫」「規劃」「分解步驟」「計畫」等語 → 只能計畫：最多寫計畫文件（`.omo/plans/*.md`），嚴禁建立、編輯、刪除任何程式碼或設定檔。
- 使用者說「執行」「開始做」「實作」等語 → 可以動程式碼，但動手前必須先向使用者確認再執行。
- 本專案不使用子代理（route B）：審核與驗收一律用 skill 由主代理執行，不得 `task(subagent_type=...)`。
- 使用者通常先討論；討論到確定要做，才會要求寫計畫。未經明示不得寫入 `.omo/plans/*.md`，也不得建立、編輯、刪除任何程式碼或設定檔。
- 計畫成文後，先用 `plan-review` skill 自我審核（唯讀），通過才進執行。
- 以計畫為準：執行時照計畫做，不自行偏離。
- 發現計畫與實際不符、或任何無法照計畫執行的情況 → 主動問使用者再動手；偏離應避免發生。
- 偏離經同意後，回頭更新計畫使其與 code 一致，並記進 HANDOFF。
- 此規則寫在 AGENTS.md，每次對話都會自動載入，即使上下文壓縮或開新 session 也不會遺忘。

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
uv run --env-file .env python -m literature_review.main
```
`literature_review.main` is the end-to-end entry (M3C): it interactively asks one query, plans with the LLM planner by default (`--rule-based` forces the deterministic fallback; `--dry-run` always uses it, so a dry run needs no key), searches/ranks/downloads each planned query independently, then extracts and synthesizes the report. `--dry-run` stops after downloads (no key). Parameters are mostly hard-coded (`LIMIT=100`, `TOTAL_TARGET=20`, `target_n = ceil(20 / query_count)`) with a search policy: research policy (run-folder-and-policy): the year window is the last `YEAR_WINDOW=3` years (`default_min_year()` = current year − 2, so 2026 → 2024) applied to every provider request plus a `FilterPolicy(min_year)` backstop, and a post-filter top-venue whitelist (`FilterPolicy.venues`, built from `ranking.TOP_VENUE_ALIASES` with containment matching over normalized aliases) drops papers whose venue is missing or not on the list. Downloads go to `data/run/`, which `main()` rebuilds (rmtree + recreate) at the start of every default run; `--dest-dir` or `DEST_DIR` override the folder and skip the clearing. Escape-hatch flags allow temporary one-off runs: `--year-from`/`--year-to` (year window), `--venues` (comma-separated alias tokens; empty string disables the filter). Search provider (M5c): with `SEMANTIC_SCHOLAR_API_KEY` set, planned queries are searched through Semantic Scholar first; a missing key, an empty SS result, or a failed SS request falls back to OpenAlex, then each SS placeholder abstract is backfilled from OpenAlex by DOI and any paper still lacking an abstract is dropped before ranking. Key wiring (C2c): `GEMINI_API_KEY` = planner + screening (missing → rule-based fallback, no abort), `GEMINI_API_KEY_2` = functional scoring + per-paper notes, `GEMINI_API_KEY_3` = report (three calls); a missing key2 or key3 exits with code 1. `.env` handling: `GeminiJsonClient` / `OllamaJsonClient` call `load_local_env()` in their constructor, so the stage CLIs (`pipeline`, `pairwise_eval`) read a local `.env` automatically; `main.py` calls it at the start of `_build_clients` as well, so `uv run python -m literature_review` works without `--env-file` (which still works and wins, since `load_local_env` never overwrites an already-set variable).

Langfuse observability: confirm the self-hosted server is up with `curl http://localhost:3000/api/public/health` before a full run; traces appear in the dashboard at `http://localhost:3000`.

If Git reports a dubious-ownership error, the user previously resolved it with:

```powershell
git config --global --add safe.directory C:/Users/User/Desktop/Literature_Review_Agent
```

## Current architecture

```text
ResearchIdea -> SearchPlan -> Semantic Scholar retrieval (OpenAlex fallback + DOI abstract backfill)
-> metadata filter/rank/select -> LLM screening (keep/maybe/exclude)
-> local PDF extraction (multi-PDF) -> EvidenceChunk retrieval
-> functional scoring (1-10 utility) -> evidence-backed paper assessment
-> per-paper claim notes -> synthesis report with [claim-N] markers / future directions
```

The pipeline now retrieves evidence with semantic (embedding) ranking by default — `literature_review.embedding_retriever.retrieve_evidence_embedding` replaced the lexical baseline for `pipeline.py`; the lexical `evidence_ranking.retrieve_evidence` is retained as compare/legacy only. The formal scoring layer is the C2b functional scoring (`literature_review.functional`: a single 1-10 utility per sampled chunk, per-paper top-2 sampling from the sub-query that pulled the paper in, batches of 5, quota∩threshold selection); the per-paper score blends max and mean (`0.7×max + 0.3×mean`, S3). The LLM contextual-summary/re-ranking interface (`summarize_and_rerank`) is retained as compare/legacy only. Synthesis output cites claims as `[claim-N]` with `claim_chunks` / `claim_id` / `claim_ids` tagging (C2c / Output Traceability). Langfuse observability (tree tracing on every LLM call, flushed before CLI exit) is implemented.

PDF acquisition (M3B): `literature_review.pdf_downloader` fetches open-access PDFs from the source-provider link recorded into `Paper.open_access_pdf_url` — the OpenAlex `best_oa_location.pdf_url` (recorded by `search.py`) or the Semantic Scholar `openAccessPdf.url` (M5c, `ss_search.py`). `download_pdf(paper, dest_dir)` writes into a caller-chosen directory (real runs use `data/run/`, smoke runs use a temp dir) with collision-resistant file names; missing OA links raise `NoOpenAccessError`, network/HTTP failures raise `PdfDownloadError`. `download_and_backfill(ranked_papers, dest_dir, target_n, *, already_downloaded=None)` keeps the top-N selection: failing papers are replaced by the next ranked candidates, and the pass reports OA coverage ratios (`oa_ratio_candidates`, `oa_ratio_attempted`, `shortfall`, `duplicate_reused`) with optional JSON stats output. `already_downloaded` is a shared set that deduplicates across multi-query runs (M3C) by `dedup_key(paper)` — normalized DOI first, title+year as fallback (M5c): a paper already in the set counts as satisfied (`duplicate_reused`) without writing a new file and without backfilling.

End-to-end entry (M3C): `literature_review/main.py` wires query -> SearchPlan -> per-query Semantic Scholar / OpenAlex search/rank (M5c) -> optional LLM screening (M5e: bucket sampling + keep/maybe/exclude + gap follow-ups via `client_screen`) -> download -> shared-set dedup -> PDF extraction -> synthesis report. There is no cross-query merging; each planned query keeps its own download target (`target_n = ceil(TOTAL_TARGET / len(plan.queries))`, duplicated papers consume quota). `run_end_to_end(...)` is fully injectable (`json_fetcher`, `pdf_fetcher`, plan/synthesis/screen clients, `ss_api_key`, `year_from/year_to/venues`) and `main()` keeps `--rule-based` / `--dry-run` plus the run-folder-and-policy escape hats `--dest-dir` / `--year-from` / `--year-to` / `--venues` (Amendment 1: the LLM planner is the default; rule-based is the escape hatch / fallback). Non-dry-run runs persist the full report as JSON under `data/outputs/` via `save_report_output` (`report_%Y%m%d_%H%M%S_%f.json`).

Paper-level ranking (K): real runs of `main.py` rank OpenAlex/SS candidates with `filter_and_rank(response, policy, encoder=...)` → `ranking.rank_papers_embedding` — bge-small-en-v1.5 embeddings of the query vs. each paper's title+abstract, with cosine similarity plus citation and recency as three equal-weight components (total 0..3). The `FilterPolicy` used by `main()` carries `min_year` (research-policy year window) and `venues` (top-venue whitelist); `filter_papers` drops papers missing the policy's venue or not matching any normalized alias by containment, then deduplicates same titles keeping the best record. `--dry-run` skips the embedding model entirely and keeps the deterministic lexical baseline (`rank_papers`); the `search.py --rank` CLI also keeps lexical (compare/legacy).

The `SearchPlan` contract is query-only: `SearchPlan.idea` is optional. The LLM planner is the default — `planning.create_llm_plan(query, client)` produces a validated plan (`generated_by="llm"`) using the shared `llm_evidence.generate_validated` call-once-parse-repair helper; `planning.create_rule_based_plan(query)` is the deterministic fallback (missing/failed key, and always for `--dry-run`; `--rule-based` forces it outright). See `HANDOFF.md` for the next integration scope and decisions.

## Live vs legacy path map

main.py 的正式路徑與保留的舊路徑對照（識別「現行 vs legacy」用；legacy 皆不動、僅存檔或對照）。

| 階段 | 現行（live） | legacy・逃生門・對照 |
|---|---|---|
| 規劃 | `create_llm_plan`（key1） | rule-based（`--dry-run` / `--rule-based` / 無 key） |
| 搜尋 | `ss_search.search_ss`（SS 主源，有 key） | `search.search_papers`（OpenAlex fallback / CLI） |
| 摘要補齊 | `backfill_abstracts`（OpenAlex DOI lookup） | placeholder 救不回 → drop |
| 篩選 | M5e screening（分桶→單一 call→gap≤1 輪） | 無 screen client → legacy per-query 下載 |
| 論文排名 | `ranking.rank_papers_embedding`（bge-small） | `ranking.rank_papers`（lexical：dry-run / `search.py --rank`） |
| 去重 | `pdf_downloader.dedup_key` = DOI→title+year | 舊 paper_id 去重（已改寫） |
| 下載 | `download_and_backfill`（keep/maybe priority_groups） | 舊 top-N backfill |
| 抽取 | `extraction.extract_pdf_text`（pymupdf4llm markdown） | pypdf 僅 fallback |
| 切分 | `evidence.chapter_chunk_document`（`{paper_id}-c{n}`） | `evidence.chunk_document`（舊格式） |
| 檢索 | `embedding_retriever.retrieve_evidence_embedding`（top-32、per-paper cap 6） | `evidence_ranking.retrieve_evidence`（lexical 對照） |
| 評分 | `functional`（1-10 utility、top-2/篇、0.7×max+0.3×mean、配額∩閾值） | `llm_evidence.summarize_and_rerank`（RCS，C2b 後 legacy；M6 Ollama 為其逃生門） |
| 筆記 | `synthesis.paper_notes`（key2、claim_id） | — |
| 報告 | C2e 三 call（outline→report→directions、key3、`[claim-N]`） | 舊單 call（`LlmSynthesisBatch` 保留逃生/測試） |
| 材料清單 | `synthesis.render_materials_section`（程式組裝） | LLM 直出（已退役） |
| 落盤 | `main.save_report_output` → `data/outputs/` | — |

不在 main 的路徑（各別 CLI / 參考概念）：`selection.py` + `SelectedPaperSet`（`search.py --assess`）、`assessment.py`（metadata 初評，deprecated）、`coverage.py`（覆蓋包，參考）、`pairwise_eval.py` / `retrieval_eval.py`（比較工具，非 pipeline）。

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
- 功能性評分 / functional scoring: the C2b formal scoring layer — a single 1-10 utility per sampled chunk (`literature_review.functional`), per-paper top-2 sampling, batches of 5, quota∩threshold selection (≥ `FUNCTIONAL_THRESHOLD`); the per-paper score is `0.7×max + 0.3×mean` of its sampled chunks (S3).
- Claim 標註 / claim tagging: `[claim-N]` markers in the report plus `claim_chunks` / `claim_id` / `claim_ids` fields mapping every claim to its source chunks and papers (C2c / Output Traceability).
- LLM 篩選 / screening: the M5e candidate-filtering call that assigns keep/maybe/exclude to bucket-sampled OpenAlex candidates before PDF download, with optional gap follow-up queries.
- bounded evidence（有限證據集）: every generated claim may rely only on the supplied chunk set, never on external knowledge.
