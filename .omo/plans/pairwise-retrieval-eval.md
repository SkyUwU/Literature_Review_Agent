# pairwise-retrieval-eval - Work Plan

## TL;DR (For humans)

**What you'll get:** A new evaluation tool that runs your 8 real PDFs through BOTH the keyword-based search (lexical top-k) and the meaning-based search (embedding top-k), then asks the LLM, for matched ranking positions, which of the two passages is more relevant to the query. It outputs a per-query and overall win/loss verdict, avoiding the "everything scores 5" ceiling problem of the previous average-score tool, so you can tell which search is actually better at putting relevant passages on top.

**Why this approach:** With only 8 papers you have no hand-labeled data, so the LLM is the judge; comparing passages at the same rank (pair-wise A/B) is a direct, strict test of ranking quality. Six built-in queries cover the one axis where the two methods can differ — vocabulary mismatch (literal vs paraphrase vs concept) — plus the cross-topic code-generation case your new papers (AIDE/SWE-agent) made possible. Display order inside each pair is randomized with a fixed seed and the prompt says so, so the LLM cannot bias the comparison by position.

**What it will NOT do:** It will not switch the system to embedding search (measure-only, like the previous milestone). It will not modify any existing module — only two new files are added. It will not measure missed-paper recall (needs hand labels; deferred). It will not add dependencies or require a new key.

**Effort:** Medium
**Risk:** Low-Medium - two new files, no core changes; the only external needs are the existing `GEMINI_API_KEY`, a one-time ~130MB bge-small model download, and a running Langfuse server for tracing.

**Decisions to sanity-check:** (1) rank-based pairing: lexical rank i vs embedding rank i; same chunk at a rank = automatic tie, no LLM call; (2) 6 built-in queries (literal / synonym / concept / cross-topic-code / confusion / 2nd-synonym), overridable with `--query`; (3) verdict rule: <3 valid pairs → insufficient; embedding win rate ≥60% over decided pairs → embedding better; ≤40% → lexical better; else comparable; (4) position bias handled by fixed-seed RNG display-order randomization + an explicit "order is random" prompt statement; (5) per-pair LLM call (reuses existing repair-once path); (6) default `--top-k 16`.

Your next move: execute this plan in a separate worker session (e.g. `$start-work`). Full execution detail follows below.

---

## 下一步行動卡 (ACTION CARD — 執行 agent 每次開 session 第一件先看這塊)

> **這是單一事實來源：目前要做哪個 Todo、讀哪個指令包。每次只更新這裡，不要靠轉述長命令。**
>
> **當前狀態：Todo 5（文件）+ Todo 6（skills）→ commit**
> - **已驗收**：Todo 1/2/3 ✅、4b/4c ✅、4d/4e ✅、4f ✅、4g ✅、4h ✅、smoke ✅
> - **Smoke 結果**：`PYEXIT=0`，`overall_verdict: embedding better`，6 queries 全跑完，142 tests OK
> - **下一動**：Todo 5（AGENTS.md + HANDOFF.md 文件更新）+ Todo 6（opencode.json + 2 skills）+ commit
>
> ## Todo 5 + Todo 6 指令（給執行 session）
>
> **先更新 STATE.md**，然後按以下步驟執行：
>
> ### Todo 5：文件更新
>
> 1. **AGENTS.md**：在 Commands 區塊加一行：
>    ```
>    uv run python -m literature_review.pairwise_eval data/papers --top-k 16 --judge-model gemini-3.6-flash
>    ```
>    （對齊現有命令風格）
>
> 2. **HANDOFF.md**：加一個「Latest milestone」段落，包含：
>    - pairwise_eval 做什麼（rank-pairing + A/B judge + verdict）
>    - 6 built-in queries
>    - verdict 規則（≥3 valid pairs, 0.6/0.4 thresholds）
>    - position-bias 處理（fixed-seed randomization）
>    - top-k 16 default
>    - add-only scope，不改動現有模組
>    - 檔案清單：`pairwise_eval.py`, `test_pairwise_eval.py`
>    - suite count：142 tests
>    - **不要宣稱 embedding 已被採用**，只說「compare-only, adoption is a verdict-driven future milestone」
>
> 3. 跑 `grep -n "pairwise_eval" AGENTS.md` + `grep -n "pairwise" HANDOFF.md` 驗證
> 4. 存 log 到 `.omo/evidence/task-5-pairwise-retrieval-eval.log`
>
> ### Todo 6：opencode.json + 2 skills
>
> 建立三個檔案（內容如下，不得偏差）：
>
> (a) `/home/sky/projects/Literature_Review_Agent/opencode.json`：
> ```json
> {"$schema":"https://opencode.ai/config.json","instructions":["AGENTS.md",".omo/STATE.md"]}
> ```
>
> (b) `.opencode/skills/project-context/SKILL.md`：
> frontmatter: name: project-context; description: 載入本專案進行中的工作狀態。當使用者說「載入專案狀態」「現況如何」「現在做到哪」「繼續專案」「resume 專案」「project context」時使用。
> body: 依序讀取 (1) `.omo/STATE.md`（錨點：內含「目前階段 + 對應計畫檔路徑 + 最新指令包路徑」）；(2) 依 STATE.md 記錄的路徑讀計畫檔 → (3) 依 STATE.md 記錄的路徑讀最新指令包 → (4) `.omo/evidence/` 最新 log；以「現在做到哪｜下一步｜卡點」三行式回覆；只回報不執行；衝突以計畫 amendment/行動卡為準；維持 route B 紀律。**動態定位：不得寫死特定階段的路徑；一律以 STATE.md 記載的路徑為準，找不到則掃描 `.omo/plans/` 與 `.omo/start-work/` 下修改時間最新的 `*.md`。**
>
> (c) `.opencode/skills/review-progress/SKILL.md`：
> frontmatter: name: review-progress; description: 總結並審核專案進度。當使用者說「總結並審核」「審核進度」「review progress」「gate」時使用。
> body: 讀 `.omo/STATE.md`（錨點）+ 依 STATE.md 路徑讀計畫檔 + 最新指令包 + `.omo/evidence/` 最新 log；對照計畫「下一步行動卡」檢查目前 Todo 是否完成、判準是否滿足；逐項給 verdict：✅ APPROVE / ❌ FAIL / ⚠️ 需補充；總結「已完成｜下一步｜風險」。**動態定位：同 project-context，以 STATE.md 為錨、不寫死階段路徑。**
> 步驟1: 對每個 [x] todo，比對 .omo/evidence/ 對應 log 與計畫檔該 todo 的 acceptance criteria；逐 todo 給 APPROVE/FAIL（FAIL 需附具體缺失）。
> 步驟2: 讀 .omo/STATE.md（若與計畫 amendment 衝突以 amendment 為準）；輸出三行式摘要：現況（哪些 todo 完成/卡點）｜下一步（下一個待執行指令包或其指定動作）｜待決策。
> 步驟3: 不修改任何檔案，只回報；維持 route B 紀律（不 spawn subagent、不 git、不印 key）。
>
> 5. 存 log 到 `.omo/evidence/task-6-workspace-ergonomics.log`
> 6. 告訴使用者重啟 opencode 以載入新設定
>
> ### Commit
>
> 全部完成後，執行：
> ```powershell
> git add literature_review/pairwise_eval.py tests/test_pairwise_eval.py
> git commit -m "feat(eval): add pair-wise lexical-vs-embedding retrieval comparison tool"
> git add AGENTS.md HANDOFF.md
> git commit -m "docs: document pair-wise retrieval comparison milestone"
> git add opencode.json .opencode/ .omo/STATE.md
> git commit -m "chore(config): add project context skills and opencode config"
> ```
>
> **不要 commit** `.omo/evidence/`、`.omo/plans/`、`.omo/start-work/`、`data/papers/`、`.env`。

---

> TL;DR (machine): Medium effort, Low-Medium risk, deliverables = pairwise evaluation tool (pairing + A/B judge + verdict aggregation + CLI) + tests + JSON report + paper classification record; add-only, no dependency; built-in 6-query set; top-k 16 default.

## Scope
### Must have
- New module `literature_review/pairwise_eval.py`: rank-aligned pairing of lexical top-k vs embedding top-k; same-chunk ranks become recorded ties (NO LLM call); per-pair A/B LLM judge with fixed-seed display-order randomization; per-query win-rate verdict; overall verdict across the built-in query set; CLI emitting a JSON report.
- Built-in default query set (6, in this order): (1) `literature review agent` (baseline literal); (2) `automatic tool that summarizes research papers` (synonym rewrite); (3) `writing a survey with help from AI` (concept-level, no keyword overlap); (4) `an AI agent that writes and improves code` (cross-topic positive — target: AIDE/SWE-agent); (5) `agent` (confusion/precision probe); (6) `software engineering automation with language models` (second synonym for the code group).
- `--query` repeatable CLI override (runs only the supplied queries); `--top-k` default **16**; `--judge-model` default `gemini-2.5-flash`.
- New Pydantic pair-verdict model defined **inside pairwise_eval.py** (not models.py): `pair_id`, `winner: Literal["A","B","tie"]`, `rationale`; validated with `model_validate_json()`; schema forwarded to the provider.
- Per-query JSON report: pairs (rank, lexical chunk_id, embedding chunk_id, same_chunk flag, display_order, winner), stats (valid_pairs, same_chunk_ties, judge_ties, embedding_wins, lexical_wins, win_rate_embedding over decided pairs), and a verdict string; plus an overall verdict across queries.
- New `tests/test_pairwise_eval.py` with a fake LLM client + fake encoder (no key, no model download, no network).
- Paper classification record: all 8 PDFs under `data/papers/` classified into (lit-review/research-ideation) vs (code) groups, written to `.omo/evidence/papers-classification.md`, with the query-mapping assumption validated (if any paper contradicts the assumed split, note it and adjust query-set comments / smoke interpretation).
- Docs: add the pairwise CLI to AGENTS.md `Commands`; add a milestone section to HANDOFF.md.

### Must NOT have (guardrails, anti-slop, scope boundaries)
- Do NOT modify ANY existing module: `llm_evidence.py`, `retrieval_eval.py`, `embedding_retriever.py`, `evidence_ranking.py`, `models.py`, `pipeline.py`, `evidence.py`, `extraction.py`, assessment/ranking/selection/planning/synthesis code — import-only reuse.
- Do NOT reuse `build_evidence_prompt` (that is the 1-5 relevance prompt; a NEW pairwise prompt must be built in pairwise_eval.py). Do NOT modify the existing `build_evidence_prompt`/`summarize_and_rerank` behavior.
- Do NOT add dependencies (no `uv add`; sentence-transformers already present). Do NOT require a new API key or model.
- Do NOT implement ground-truth labeling / recall@k / missed-chunk measurement in this version (deferred, needs labels).
- Do NOT adopt or replace the lexical retriever; this milestone only compares.
- Do NOT commit `data/papers/`, `.env`, or report artifacts with keys; do NOT print keys.
- Do NOT touch `ranking.py`, `selection.py`, `planning.py`, `synthesis.py`, `assessment.py`.
- Do NOT remove/replace any existing test.

## Verification strategy
> Zero human intervention - all verification is agent-executed.
- Test decision: TDD (tests alongside each module) + framework `unittest`.
- Evidence: `.omo/evidence/` with `task-<N>-pairwise-retrieval-eval.<ext>` per todo.
- Run: `uv run python -m unittest discover -s tests -v` (expect 85 existing + new pairwise tests green; no network / no key in tests).
- Real-run gate: `curl http://localhost:3000/api/public/health` must be OK before the full run (Langfuse tracing).

## Execution strategy
### Parallel execution waves
> Target 5-8 todos per wave. Do not over-split; keep each todo one bounded, committable unit.
- Wave 1 (parallel): paper classification (Todo 1) + pairwise core implementation (Todo 2).
- Wave 2: CLI + report wiring + tests (Todo 3).
- Wave 3: real smoke over `data/papers` with real Gemini (Todo 4).
- Wave 4: docs (Todo 5).

### Dependency matrix
| Todo | Depends on | Blocks | Can parallelize with |
| --- | --- | --- | --- |
| 1 (paper classification) | none | 4 | 2 |
| 2 (pairwise core + tests) | none | 3 | 1 |
| 3 (CLI + report + tests) | 2 | 4,5 | none |
| 4 (real smoke) | 1,3 | 5 | none |
| 5 (docs/AGENTS/HANDOFF) | 3 | - | 4 |

## Todos
> Implementation + Test = ONE todo. Never separate.
- [x] 1. Classify all 8 PDFs under `data/papers/` and validate the two-group corpus assumption  (verified APPROVE 2026-09-01; evidence `.omo/evidence/papers-classification.md` + `.omo/evidence/task-1-pairwise-retrieval-eval.md`)
  What to do / Must NOT do: For each PDF in `data/papers/` (AIDE, Nova, OpenScholar, PaperQA2, ReseachAgent, Spark, STORM, SWE_agent), determine its actual topic by extracting the first page text (`extract_pdf_text`) or title/abstract section, and classify it as `litreview` (literature review / paperQA / survey / research-ideation agents) or `code` (code/software-engineering agents). Write a classification table (filename, actual title, arXiv id if visible, group, confidence) to `.omo/evidence/papers-classification.md`. Expected from prior web research (verify, do not trust blindly): AIDE + SWE_agent = code; OpenScholar, PaperQA2, STORM, ReseachAgent (arXiv 2404.07738), Nova (arXiv 2410.13185) = lit-review/ideation; Spark = unverified at plan time — pay special attention to Spark. If any classification contradicts the assumed two-group split used by the built-in query set, record the discrepancy and its impact on interpreting queries 4/6 in the same file, and flag it in the smoke report (Todo 4). Do NOT modify any code; do NOT rename files.
  Parallelization: Wave 1 | Blocked by: none | Blocks: 4
  References: `data/papers/` (8 PDFs, confirmed present); `literature_review/extraction.py` `extract_pdf_text`; prior web research (AIDE arXiv 2502.13138; SWE-agent arXiv 2405.15793; ResearchAgent arXiv 2404.07738; Nova arXiv 2410.13185; OpenScholar/PaperQA2/STORM established).
  Acceptance criteria (agent-executable): `.omo/evidence/papers-classification.md` exists and lists ALL 8 filenames with group + confidence; Spark (or any other) is not left undetermined — if unverifiable from the PDF itself, mark it explicitly as `unknown` with the reason and confirm whether queries 4/6 interpretation is affected.
  QA scenarios: happy = all 8 rows completed with confidence; failure = a PDF cannot be extracted → mark `unknown` + reason, do not crash. Evidence `.omo/evidence/task-1-pairwise-retrieval-eval.md`.
  Commit: N (artifact only, not committed)

- [x] 2. Implement pairwise core in `literature_review/pairwise_eval.py` + tests  (verified APPROVE 2026-09-01; evidence `.omo/evidence/task-2-pairwise-retrieval-eval.log`)
  What to do / Must NOT do: Implement, in the NEW module only: (a) `pair_ranked(lexical, embedding)` — zip aligned ranks 1..top_k from the two `EvidenceRetrievalResponse.ranked_chunks` lists; a rank is `same_chunk` when both sides carry the same `chunk.chunk_id` (record it, NO LLM call), `valid pair` when the ids differ, and `unpaired` when either side has no item at that rank (count only). (b) Deterministic display-order randomization: `random.Random(FIXED_SEED)` (module-level constant seed, e.g. `20260901`) shuffled per valid pair (or derive seed per pair index — fix ONE approach and document it); the prompt MUST contain an explicit statement that the chunk order is random and carries no meaning. (c) `build_pairwise_prompt(query, chunk_a, chunk_b, pair_id)` producing a NEW prompt (do NOT reuse `build_evidence_prompt`): instruct the model to judge only relevance to the query for the TWO given chunks, no outside knowledge, and return exactly one JSON object without fences: `{pair_id, winner: "A"|"B"|"tie", rationale}`. Chunk context must include chunk_id, paper_id, page range, text (mirror the fields used by `build_evidence_prompt`, llm_evidence.py:79-99). (d) `judge_pair(...)`: call `client.generate_json(prompt, PairwiseVerdict.model_json_schema())`; validate with `PairwiseVerdict.model_validate_json`; on `LlmOutputSyntaxError` do ONE repair attempt via `build_json_repair_prompt` (llm_evidence.py:102-108); a persistent failure propagates `LlmEvidenceError` (consistent with retrieval_eval). (e) `PairwiseVerdict(pydantic.BaseModel)` defined in this module: `pair_id: str`, `winner: Literal["A","B","tie"]`, `rationale: str`; do NOT add it to models.py. (f) Per-query aggregation `aggregate_query(...)`: valid_pairs < 3 → verdict `insufficient`; else win_rate = embedding_wins / (embedding_wins + lexical_wins) over DECIDED pairs (judge-returned ties excluded from the denominator but counted in stats); >= 0.6 → `embedding better`; <= 0.4 → `lexical better`; else `comparable`. (g) Overall aggregation `aggregate_overall(...)`: count per-query verdicts; if `embedding better` count > `lexical better` count → `embedding better overall`; if < → `lexical better overall`; else `comparable overall` (insufficient/comparable queries are recorded but do not break the majority reasoning; if no query is decisive, `insufficient overall`). Do NOT modify retrieval_eval.py / llm_evidence.py / models.py; import from them. Map `winner` back to a method AFTER judging using the recorded display_order (reported per pair).
  Parallelization: Wave 1 | Blocked by: none | Blocks: 3
  References: `literature_review/retrieval_eval.py:40-75` (`_ids`, `_judge_group` patterns), `:78-160` (compare flow + `_compute_verdict` precedent for explicit rules); `literature_review/embedding_retriever.py:49-88` (`retrieve_evidence_embedding`), `:23` (`Encoder`); `literature_review/evidence_ranking.py` `retrieve_evidence`; `literature_review/llm_evidence.py:26-31` (`JsonGenerationClient` protocol), `:33-61` (`GeminiJsonClient`), `:18-23` (errors), `:102-108` (`build_json_repair_prompt`), `:111-128` (validation pattern); `literature_review/models.py` `EvidenceChunk`/`EvidenceRetrievalPolicy`/`EvidenceRetrievalResponse`/`RankedEvidenceChunk` (133-164 region — re-read exact lines).
  Acceptance criteria (agent-executable): `tests/test_pairwise_eval.py` passes, covering (a) same-chunk rank → tie recorded and NO `generate_json` call; (b) differing rank → exactly ONE judge call; (c) display order is deterministic for the same seed and the prompt contains the order-is-random statement; (d) winner mapping back to a method respects the randomized display order; (e) verdict rule boundaries (0.6/0.4/<3 valid pairs); (f) judge-tie excluded from win-rate denominator but counted; (g) overall majority verdict; (h) malformed JSON → one repair call → success; (i) pairing with a shorter rank list → `unpaired` counted.
  QA scenarios: happy = fake judge client returns scripted verdicts, assert call counts + verdicts; failure = fake returns malformed JSON forever → expect `LlmEvidenceError` propagation. Evidence `.omo/evidence/task-2-pairwise-retrieval-eval.log`.
  Commit: Y | feat(eval): add pair-wise lexical-vs-embedding comparison core

- [x] 3. Implement CLI + JSON report in `literature_review/pairwise_eval.py` + tests  (verified APPROVE 2026-09-01; evidence `.omo/evidence/task-3-pairwise-retrieval-eval.log`; deviation: drop unused `import os` as Todo 4 step 0)
  What to do / Must NOT do: Add `main()` + `if __name__ == "__main__"` mirroring `retrieval_eval.py:185-226`: argparse with positional `inputs` (nargs+, folders/PDFs), `--top-k` (default 16), `--judge-model` (default `gemini-2.5-flash`), `--query` (action=append, repeatable, default None → run the built-in 6-query set; when supplied, run exactly the supplied queries). Pipeline inside main: `expand_pdf_inputs` (import from `literature_review.retrieval_eval`, public function), per-PDF `extract_pdf_text` + `chunk_document(ChunkPolicy())`, gather all chunks; build `EvidenceRetrievalPolicy(top_k)`; run `retrieve_evidence` (lexical) and `retrieve_evidence_embedding(..., encoder=default_encoder())` per query; pair, judge with `GeminiJsonClient(model=...)`; aggregate; print one JSON report (ensure_ascii=True, indent=2) containing per-query blocks + overall verdict + query metadata. Paper id: define a local `_default_paper_id` mirroring `retrieval_eval.py:181-182` (basename) — do NOT import the underscore-private helper. Wrap the run in the same try/except as retrieval_eval (PdfExtractionError, LlmEvidenceError, ValueError → stderr + SystemExit(1)). Do NOT write the report to a file in code (CLI prints; the smoke run in Todo 4 redirects it to `.omo/evidence/`). Do NOT print API keys.
  Parallelization: Wave 2 | Blocked by: 2 | Blocks: 4,5
  References: `literature_review/retrieval_eval.py:163-226` (expand_pdf_inputs, paper_id, main, argparse, error handling, JSON print); `literature_review/evidence.py` `chunk_document`; `literature_review/extraction.py` `extract_pdf_text`/`PdfExtractionError`; `literature_review/embedding_retriever.py:26-41` `default_encoder` (first call downloads ~130MB); AGENTS.md Commands pattern (`python -m literature_review.xxx ...`).
  Acceptance criteria (agent-executable): a CLI unit test asserts argument parsing (default query set vs override, top-k default 16) and that the report JSON contains expected keys for at least one synthetic call path using a fake client — expose a helper `build_report(...)`/`run_eval(...)` decoupled from `main()` so tests need no real Gemini; `uv run python -m unittest discover -s tests -v` stays green. `--help` prints the description and all flags.
  QA scenarios: happy = `uv run python -m literature_review.pairwise_eval --help` lists inputs/--top-k/--judge-model/--query; failure = nonexistent input path → clean `SystemExit(1)` message (can be asserted with a synthetic call). Evidence `.omo/evidence/task-3-pairwise-retrieval-eval.log`.
  Commit: Y | feat(eval): add pairwise eval CLI and JSON report

- [~] 4. Full suite + real smoke on `data/papers` with real Gemini  (blocked: route B — depends on user-executed Todo 1 + Todo 3)
  What to do / Must NOT do: (a) Run the FULL suite: `uv run python -m unittest discover -s tests -v` — all green (85 existing + new). (b) Confirm Langfuse health: `curl http://localhost:3000/api/public/health` (document result; do NOT block the smoke if Langfuse is down — pipeline degrades gracefully per HANDOFF.md:63-70, but record it). (c) Confirm `GEMINI_API_KEY` present in `.env` (source it via the existing `load_local_env` path — do NOT print the key). (d) Real run: `uv run python -m literature_review.pairwise_eval data/papers --top-k 16 > .omo/evidence/report-pairwise-retrieval-eval.json 2> .omo/evidence/smoke-pairwise-retrieval-eval.log` (first run downloads ~130MB bge-small weights — expect the delay). (e) Verify the report JSON has 6 per-query verdicts + overall verdict, each verdict ∈ {embedding better, lexical better, comparable, insufficient}; sanity-check that query (4)/(6) (code group) and (1)-(3)/(5) (lit-review group) behave sensibly given Todo 1's classification; note any surprise in the log. Do NOT modify code during the smoke; do NOT commit report artifacts (`.omo/` is untracked evidence) and never copy keys into them.
  Parallelization: Wave 3 | Blocked by: 1,3 | Blocks: 5
  Amendment 2026-09-01 (planner decision): smoke MUST pass `--judge-model gemini-3.6-flash` explicitly — first attempt with default gemini-2.5-flash hit free-tier 429 (limit 20 req/min, metric generate_content_free_tier_requests) at the ~20th judge call, twice (structural pacing mismatch, not transient). 3.6-flash is the environment-proven pipeline model (AGENTS.md) with higher free-tier RPM; code default stays gemini-2.5-flash (no code/test change). CLI override only. Fallback if 3.6-flash also 429s: planner authorizes a pacing fix (configurable delay in judge_pair/evaluate_query/build_report, default 0, wired from env var in main()).
  Amendment 2026-09-01b (Todo 4b/4c, VERIFIED): pacing+import code+tests done and APPROVED (CLI flag `--judge-delay-s`, stderr self-verification, `GaosRateLimitError` correctly caught, 119 green). Smoke STILL 429.
  Amendment 2026-09-02 (ROOT CAUSE IDENTIFIED): Gemini free tier is **5 RPM / 20 RPD** (NOT 20 RPM). 3.5s spacing → 17/min → exceeds 5 RPM. Solution: **batch pairwise comparisons** — `judge_batch()` sends 8 pairs per LLM call → 6–8 total calls → 13s pacing fits 5 RPM. `batch_size=8` chosen (quality safe zone ≤10 per LLM-as-judge research). Day 1 single run feasible (8 calls ≪ 20 RPD). Todo 4d: batch refactor.
  References: Todo 1 classification; AGENTS.md Commands (langfuse health curl; pipeline run pattern; `--top-k` semantics); HANDOFF.md:63-70 (Langfuse), :82-83 (first-run model download note).
  Acceptance criteria (agent-executable): full suite green; `report-pairwise-retrieval-eval.json` exists, parses, and contains exactly 6 per-query blocks (or the count of `--query` overrides used) + overall; every verdict string ∈ the allowed set; Langfuse health result recorded in the log.
  QA scenarios: happy = all 6 verdicts present + sensible; failure = verdict set violated or suite red → fix code (back to Todo 2/3) and rerun; failure = network/key error → record and report to user instead of mutating tests. Evidence `.omo/evidence/report-pairwise-retrieval-eval.json` + `.omo/evidence/smoke-pairwise-retrieval-eval.log`.
  Commit: N (run only; code already committed in 2/3) | bump docs commit in 5

- [~] 5. Update AGENTS.md `Commands` (add pairwise CLI + add route-B "no subagent" rule) and HANDOFF.md milestone section  (blocked: route B — depends on user-executed Todo 3)
  What to do / Must NOT do: Add ONE line to AGENTS.md Commands block: `uv run python -m literature_review.pairwise_eval data/papers --top-k 16 --judge-model gemini-3.6-flash` (match the existing command style; keep `--judge-model` default consistent with the code default `gemini-2.5-flash` — the AGENTS.md line may pass an explicit model). Add a HANDOFF.md "Latest milestone" section like the retrieval comparison one (HANDOFF.md:74-83): what pairwise_eval does, the rank-pairing rule, the 6 built-in queries, the verdict rule (>=3 valid pairs, 0.6/0.4 thresholds), position-bias handling, top-k 16 default, add-only scope, file list, suite count after this milestone. Do NOT claim embedding adoption; state "compare-only, adoption is a verdict-driven future milestone". Do NOT touch other AGENTS.md/HANDOFF.md content; do NOT commit `data/papers/`.
  Parallelization: Wave 4 | Blocked by: 3 | Blocks: none (can run in parallel with 4 but must include the suite count from 4; if run concurrently, wait for 4 or mark suite count as of 4)
  References: AGENTS.md Commands section; HANDOFF.md:74-83 (previous milestone section to mirror); HEAD current branch `feature/plan-doc`.
  Acceptance criteria (agent-executable): `grep -n "pairwise_eval" AGENTS.md` finds the command; `grep -n "pairwise" HANDOFF.md` finds the milestone section with the verdict rule and file list.
  QA scenarios: happy = both greps hit; failure = missing section → add before commit. Evidence `.omo/evidence/task-5-pairwise-retrieval-eval.log`.
  Commit: Y | docs: document pair-wise retrieval comparison milestone

- [ ] 6. Workspace ergonomics: create `opencode.json` + 2 skills (aux tooling, zero product code)  (user-approved 2026-09-01: 「總結+審核」例行化也做；needs opencode restart to take effect)
  What to do / Must NOT do: Create EXACTLY three files with the content below (decision-complete; no deviation):
  (a) `/home/sky/projects/Literature_Review_Agent/opencode.json`:
      {"$schema":"https://opencode.ai/config.json","instructions":["AGENTS.md",".omo/STATE.md"]}
      Effect: every new session auto-loads .omo/STATE.md (現況/下一步/卡點/待決策).
(b) `.opencode/skills/project-context/SKILL.md`:
frontmatter: name: project-context; description: 載入本專案進行中的工作狀態。當使用者說「載入專案狀態」「現況如何」「現在做到哪」「繼續專案」「resume 專案」「project context」時使用。body: 依序讀取 (1) `.omo/STATE.md`（錨點：內含「目前階段 + 對應計畫檔路徑 + 最新指令包路徑」）；(2) 依 STATE.md 記錄的路徑讀計畫檔 → (3) 依 STATE.md 記錄的路徑讀最新指令包 → (4) `.omo/evidence/` 最新 log；以「現在做到哪｜下一步｜卡點」三行式回覆；只回報不執行；衝突以計畫 amendment/行動卡為準；維持 route B 紀律。**動態定位：不得寫死特定階段（如 pairwise-retrieval-eval）的路徑；一律以 STATE.md 記載的路徑為準，找不到則掃描 `.omo/plans/` 與 `.omo/start-work/` 下修改時間最新的 `*.md`。**
(c) `.opencode/skills/review-progress/SKILL.md`:
frontmatter: name: review-progress; description: 總結並審核專案進度。當使用者說「總結並審核」「審核進度」「review progress」「gate」時使用。body: 讀 `.omo/STATE.md`（錨點）+ 依 STATE.md 路徑讀計畫檔 + 最新指令包 + `.omo/evidence/` 最新 log；對照計畫「下一步行動卡」檢查目前 Todo 是否完成、判準是否滿足；逐項給 verdict：✅ APPROVE / ❌ FAIL / ⚠️ 需補充；總結「已完成｜下一步｜風險」。**動態定位：同 project-context，以 STATE.md 為錨、不寫死階段路徑。**
      步驟1: 對每個 [x] todo，比對 .omo/evidence/ 對應 log 與計畫檔該 todo 的 acceptance criteria；逐 todo 給 APPROVE/FAIL（FAIL 需附具體缺失）。
      步驟2: 讀 .omo/STATE.md（若與計畫 amendment 衝突以 amendment 為準）；輸出三行式摘要：現況（哪些 todo 完成/卡點）｜下一步（下一個待執行指令包或其指定動作）｜待決策。
      步驟3: 不修改任何檔案，只回報；維持 route B 紀律（不 spawn subagent、不 git、不印 key）。
  Must NOT touch product code or tests; must NOT commit `data/papers/`/`.env`; these files are for both working copies — commit `opencode.json` + `.opencode/` + `.omo/STATE.md` via git so Windows/WSL sync (STATE.md updates = tiny commits + push/pull; do NOT commit other `.omo/` content). After creating, tell the user to quit and restart opencode (config is read once at startup, not hot-reloaded).
  Parallelization: Wave 5 (aux; independent of Todo 4/5) | Blocked by: none | Blocks: none
  References: `opencode.json` schema (https://opencode.ai/config.json); `customize-opencode` skill (skills live in `.opencode/skills/<name>/SKILL.md`, frontmatter name+description required, lowercase-hyphen name must match folder).
  Acceptance criteria (agent-executable): all three files exist with the exact content; no other file changed; `git status` clean apart from the three files.
  QA scenarios: happy = files present; failure = content drift → fix to spec. Evidence `.omo/evidence/task-6-workspace-ergonomics.log`.
  Commit: Y (bundled with docs commit or separate `chore(config): add project context skills`)

## Final verification wave
> Runs in parallel after ALL todos. ALL must APPROVE. Surface results and wait for the user's explicit okay before declaring complete.
- [~] F1. Plan compliance audit  (blocked: awaits all todos)
- [~] F2. Code quality review  (blocked: awaits all todos)
- [~] F3. Real manual QA  (blocked: awaits all todos)
- [~] F4. Scope fidelity  (blocked: awaits all todos)

## Commit strategy

After ALL todos pass and the final wave approves, commit on the current branch (`feature/plan-doc`) — do NOT commit `data/papers/`, `.env`, or `.omo/evidence/` artifacts. Suggested sequence (run by the user):

```powershell
git status
git add literature_review/pairwise_eval.py tests/test_pairwise_eval.py
git commit -m "feat(eval): add pair-wise lexical-vs-embedding retrieval comparison tool"
git add AGENTS.md HANDOFF.md
git commit -m "docs: document pair-wise retrieval comparison milestone"
git status
```

Then sync via the GitHub remote and `git pull --ff-only` in the other working copy (Windows) before any further edits there. Do NOT combine this milestone with any other change.

## Success criteria

- `uv run python -m unittest discover -s tests -v` → all green (85 existing + new pairwise tests; no network, no key in tests).
- `git status`/diff shows ONLY the two new files plus `AGENTS.md`/`HANDOFF.md` — zero edits to any existing product module.
- Real smoke produces `.omo/evidence/report-pairwise-retrieval-eval.json` with 6 per-query verdicts + overall verdict, all in the allowed set.
- `.omo/evidence/papers-classification.md` classifies all 8 PDFs and validates the lit-review vs code two-group assumption used by the query set.
- The final wave (F1-F4) all approve; AGENTS.md command line and HANDOFF.md milestone section exist.
- Outcome feeds a future adoption decision only — the lexical retriever is not replaced by this milestone.