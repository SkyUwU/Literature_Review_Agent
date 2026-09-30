# langfuse-tracing - Work Plan

## TL;DR (For humans)
<!-- Fill this LAST, after the detailed plan below is written, so it summarizes the REAL plan. -->
<!-- Plain English for a non-engineer: NO file paths, NO todo numbers, NO wave/agent/tool names. -->

**What you'll get:** 替整個文獻回顧 Agent 的每個 LLM 呼叫加上可觀測性（observability）。用官方 Langfuse SDK 的追蹤裝飾器，把「證據摘要、逐篇筆記、綜合報告」三次主要 LLM 階段（以及任何中途修復重試）都記錄下來，讓你在 Langfuse 網頁上看到每一次呼叫花多少 token、耗時多長、內容是什麼。Logfuse 服務跑在你自己架設的 localhost:3000。

**Why this approach:** 先前跑 pipeline 只知道「有呼叫 LLM」，但不知道每次呼叫的成本、延遲、內容品質。有了追蹤，未來不管調整 top-k、換模型、或做搜尋優化，都有 baseline 可以比較，看得出改動是否真的變好。

**What it will NOT do:** 不會動任何現有的 pipeline / 搜尋 / 評分 / 合成邏輯（只加上「記錄」的外層包裝）。不會新增 langfuse 以外的依賴。Langfuse 服務連不上時，不會阻擋你的主要功能（優雅降級，照樣產出報告）。

**Effort:** Small
**Risk:** Low - 主要是新增依賴 + 幾個追蹤裝飾器 + 一個 flush；不改變任何既有行為
**Decisions to sanity-check:** (1) 採方案 B（樹狀追蹤）：在階段函數 + GeminiJsonClient.generate_json 都掛 @observe()，dashboard 能看到「階段含子呼叫與重試」的完整樹狀結構；(2) Langfuse 失敗一律優雅降級（try/except），不擋 pipeline；(3) final QA 需要使用者親開 http://localhost:3000 確認 trace 出現。

Your next move: Plan 已就緒。等使用者執行 /start-work langfuse-tracing。

---

> TL;DR (machine): Small effort, Low risk; add official langfuse SDK, wrap GeminiJsonClient.generate_json + three stage functions with @observe(), flush at CLI end, keep 74 tests green, verify by watching dashboard.

## Scope
### Must have
- `pyproject.toml` / `uv.lock` — 新增 `langfuse` 依賴（uv add langfuse）
- `literature_review/llm_evidence.py` — `GeminiJsonClient.generate_json()` 加 `@observe(name="llm_call")`；`summarize_and_rerank()` 加 `@observe(name="rcs")`
- `literature_review/synthesis.py` — `summarize_paper_notes()` 加 `@observe(name="paper_notes")`；`synthesize_report()` 加 `@observe(name="synthesis_report")`
- `literature_review/pipeline.py` — `main()` 於 pipeline 結束前呼叫 `flush()`（try/except 優雅降級）
- tests — 確保 74 tests 不破壞；測試環境禁能 Langfuse（no-op），不嘗試連網
- 實機驗證：跑 `--model gemini-3.6-flash`，使用者開 `http://localhost:3000` 確認 trace（rcs / paper_notes / synthesis_report）

### Must NOT have (guardrails, anti-slop, scope boundaries)
- 不變更任何現有 pipeline / 搜尋 / 評分 / 合成邏輯（只加追蹤裝飾器）
- 不新增 langfuse 以外依賴
- 不改 `.env`、`data/papers/`、`Summer_Project.pdf`
- worker 不執行任何 git 指令（commit 由使用者）
- 不因 Langfuse 連不上而阻擋 pipeline 主流程
- 不做 embedding / 搜尋強化 / schema-fix（另案）

## Verification strategy
> Zero human intervention except final dashboard watch - verification is agent-executed except the user looks at the dashboard.
- Test decision: tests-after + 既有 unittest 框架（追蹤裝飾器是外層包裝，不改變既有行為；確認 74 tests 不破壞）
- Evidence: .omo/evidence/task-<N>-langfuse-tracing.log

## Execution strategy
### Parallel execution waves
> Target 5-8 todos per wave. Fewer than 3 (except the final) means you under-split.
- Wave 1: Todo 1（uv add langfuse）、Todo 2（llm_evidence @observe）
- Wave 2: Todo 3（synthesis @observe）、Todo 4（pipeline flush）
- Wave 3: Todo 5（tests 保險 + 74 tests）、Todo 6（實機驗證 + dashboard watch）

### Dependency matrix
| Todo | Depends on | Blocks | Can parallelize with |
| --- | --- | --- | --- |
| 1 | 無 | 2,3,4 | 無 |
| 2 | 1 | 6 | 3 |
| 3 | 1 | 6 | 2 |
| 4 | 1 | 6 | 2,3 |
| 5 | 2,3,4 | 6 | 無 |
| 6 | 5 | 無 | 無 |

## Todos
> Implementation + Test = ONE todo. Never separate.
<!-- APPEND TASK BATCHES BELOW THIS LINE WITH edit/apply_patch - never rewrite the headers above. -->
- [x] 1. `pyproject.toml` / `uv.lock`：`uv add langfuse`，確認依賴生效
  > DONE (2026-08-31): verified by Prometheus — pyproject:10 `"langfuse>=4.15.1"`, uv.lock langfuse entry, evidence log task-1 OK, import 4.15.1, 74 tests OK.
  What to do / Must NOT do:
  1. 在 WSL 專案根跑 `uv add langfuse`（安裝最新穩定版官方 SDK）。
  2. 確認 `pyproject.toml` 的 `[project.dependencies]` 新增 `langfuse`，`uv.lock` 更新。
  3. 確認 `uv sync` 後可 `python -c "import langfuse"` 不報錯。
  4. 勿 bundle 其他依賴；勿改動既有依賴版本。
  Parallelization: Wave 1 | Blocked by: 無 | Blocks: 2,3,4
  References (executor has NO interview context - be exhaustive): pyproject.toml, uv.lock
  Acceptance criteria (agent-executable): `uv run python -c "import langfuse; print(langfuse.__version__)"` 成功印出版本；`grep -n "langfuse" pyproject.toml` 命中
  QA scenarios: happy → import 成功、pyproject 有 langfuse。Evidence .omo/evidence/task-1-langfuse-tracing.log
  Commit: Y | build(deps): add langfuse SDK for LLM observability

- [x] 2. `llm_evidence.py`：`GeminiJsonClient.generate_json` + `summarize_and_rerank` 加 `@observe()`
  > DONE (2026-08-31): verified by Prometheus — llm_evidence.py `:15 from langfuse import observe`, `:48 @observe(llm_call)` on generate_json (method), `:131 @observe(rcs)` on summarize_and_rerank (module), fn bodies unchanged, 74 tests OK x2.
  What to do / Must NOT do:
  1. 在 `literature_review/llm_evidence.py` 頂部 `from langfuse import observe`。
  2. `GeminiJsonClient.generate_json`（:46）加 `@observe(name="llm_call")`。
  3. `summarize_and_rerank`（:128）加 `@observe(name="rcs")`。
  4. 勿改函數 body（僅加裝飾器）；勿動 fake clients 測試。
  Parallelization: Wave 1 | Blocked by: 1 | Blocks: 5,6 | Can parallelize with: 3
  References: literature_review/llm_evidence.py:31-58,128-145
  Acceptance criteria (agent-executable): `grep -n "@observe" literature_review/llm_evidence.py` 命中 2 處（llm_call + rcs）；`uv run python -m unittest discover -s tests -v` 全綠
  QA scenarios: happy → tests 全綠；failure → 確認裝飾器不破壞 generate_json 回傳值（fake client 測試仍過）。Evidence .omo/evidence/task-2-langfuse-tracing.log
  Commit: Y | feat(llm_evidence): observe Gemini RCS calls and generate_json

- [x] 3. `synthesis.py`：`summarize_paper_notes` + `synthesize_report` 加 `@observe()`
  > DONE (2026-08-31): verified by Prometheus — synthesis.py `:36 from langfuse import observe`, `:458 @observe(paper_notes)` on summarize_paper_notes, `:569 @observe(synthesis_report)` on synthesize_report, fn bodies unchanged, 74 tests OK x2 (0.02s, no network). All 4 @observe for Plan B now in place.
  What to do / Must NOT do:
  1. 在 `literature_review/synthesis.py` 頂部 `from langfuse import observe`。
  2. `summarize_paper_notes`（:456）加 `@observe(name="paper_notes")`。
  3. `synthesize_report`（:584 附近）加 `@observe(name="synthesis_report")`。
  4. 勿改函數 body；勿動既有合成邏輯。
  Parallelization: Wave 2 | Blocked by: 1 | Blocks: 5,6 | Can parallelize with: 2
  References: literature_review/synthesis.py:456-495,580-631
  Acceptance criteria (agent-executable): `grep -n "@observe" literature_review/synthesis.py` 命中 2 處；`uv run python -m unittest discover -s tests -v` 全綠
  QA scenarios: happy → tests 全綠。Evidence .omo/evidence/task-3-langfuse-tracing.log
  Commit: Y | feat(synthesis): observe paper-note and synthesis LLM calls

- [x] 4. `pipeline.py`：`main()` 於結束前 `flush()`
  > DONE (2026-08-31): verified by Prometheus — pipeline.py `:10-12` try/except get_client, `:145` _flush_langfuse helper, `:185,:197` two flush calls, dry-run exit 0 confirmed, graceful degradation (disabled client), 74 tests OK x2.
  What to do / Must NOT do:
  1. 在 `literature_review/pipeline.py` `main()` 的 dry-run return 與 synthesis print 之後、函數結尾前，對 Langfuse global client 呼叫 `flush()`，並以 try/except 包住（Langfuse 連不上不影響 pipeline）。
  2. flush 需在 `if __name__ == "__main__": main()` 的 main 執行結束前送出 telemetry。
  3. 建議 import 用 try/except：`try: from langfuse import get_client; _LANGFUSE = get_client() except Exception: _LANGFUSE = None`，flush 時若 `_LANGFUSE is None` 則跳過。
  4. 勿在 dry-run 路徑引入 Langfuse 依賴錯誤（dry-run 不該因 Langfuse 失敗而壞）。
  Parallelization: Wave 2 | Blocked by: 1 | Blocks: 5,6 | Can parallelize with: 2,3
  References: literature_review/pipeline.py:140-183
  Acceptance criteria (agent-executable): `grep -n "flush" literature_review/pipeline.py` 命中；dry-run 仍正常（`uv run ... --dry-run` exit 0）；非 dry-run mock flush 避免真實連網（測試用）
  QA scenarios: happy → dry-run 過；failure → Langfuse 連不上時 pipeline 仍產出（try/except 驗證）。Evidence .omo/evidence/task-4-langfuse-tracing.log
  Commit: Y | feat(pipeline): flush Langfuse telemetry before CLI exit

- [x] 5. tests：確認 74 tests 不破壞；僅在測試異常時補禁能保險（先測後補）
  > DONE (2026-08-31): verified by Prometheus — 74 tests OK x2 (normal + LANGFUSE_DEBUG); no network attempt (0.02s); Langfuse auto-disabled (no-op) during tests; insurance NOT needed (YAGNI); no code changes.
  What to do / Must NOT do:
  1. 先直接跑 `uv run python -m unittest discover -s tests -v`，確認 74 tests 全綠嗎。fake clients 不繼承 GeminiJsonClient，理論上不受 @observe() impact。
  2. **觀察**測試過程是否嘗試連 `localhost:3000`（可暫時設 LANGFUSE_DEBUG=true 或觀察耗時）。若測試全綠且無連網嘗試 → 完成，**不額外加任何保險 code**（YAGNI）。
  3. **僅當**測試出現異常（如紅、卡住、明顯嘗試連 Langfuse）時，才補保險：於 test setup 確保 `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` 未設，或設 `sample_rate=0`，使 @observe() 為 no-op。
  4. 勿新增需真實 Langfuse server 的測試。
  Parallelization: Wave 3 | Blocked by: 2,3,4 | Blocks: 6 | Can parallelize with: 無
  References: tests/test_llm_evidence.py, tests/test_pipeline.py
  Acceptance criteria (agent-executable): `uv run python -m unittest discover -s tests -v` → Ran 74 tests, OK；正常情況下不新增任何 test/code 變更
  QA scenarios: happy → 74 tests 全綠、無連網、無 code 變更；failure → 補保險（unset keys / sample_rate=0）後重跑仍全綠。Evidence .omo/evidence/task-5-langfuse-tracing.log
  Commit: N（若未補任何 code 則無 commit；若測試需要新增 test helper 則標 Y）

- [x] 6. 實機驗證：跑 pipeline + 使用者開 dashboard 確認 trace
  > DONE (2026-08-31): verified by Prometheus — pipeline run 2 exit 0 (report 4452 chars, 2 paper_summaries, 2 future_directions); stderr no Langfuse disabled warning; USER confirmed dashboard shows rcs x2 / paper_notes x2 / synthesis_report x2 with nested llm_call child layer. Plan B tree tracing CONFIRMED. (2 trace groups = 2 runs: 1st timeout residual + 2nd success, expected.)
  What to do / Must NOT do:
  1. 確認 `.env` 有 LANGFUSE_SECRET_KEY / LANGFUSE_PUBLIC_KEY / LANGFUSE_HOST，且 WSL 可 `curl http://localhost:3000/api/public/health` → OK。
  2. 開新 WSL terminal（確保讀到最新 .env），跑 `uv run python -m literature_review.pipeline data/papers "literature review agent" --top-k 8 --model gemini-3.6-flash`。
  3. 成功 → log 記錄 exit 0、RCS/notes/report 完成。
  4. 請使用者開 `http://localhost:3000`，確認出現 rcs / paper_notes / synthesis_report 三類 trace（含 generate_json 子層）。
  5. 若遇 429：連 3 次回報 quota，不算失敗；若 Langfuse trace 未出現：檢查 flush / keys / host，回報但不自行改 code。
  Parallelization: Wave 3 | Blocked by: 5 | Blocks: 無
  References: literature_review/pipeline.py; .env; http://localhost:3000
  Acceptance criteria (agent-executable): pipeline exit 0；使用者 dashboard 看到 trace（或 429/連線證據）
  QA scenarios: happy → user 確認 dashboard 有 trace；failure → 429 或 trace 未出現的證據 + 回報。Evidence .omo/evidence/task-6-langfuse-tracing.log
  Commit: N（無 code 變更）

## Final verification wave
> Runs in parallel after ALL todos. ALL must APPROVE. Surface results and wait for the user's explicit okay before declaring complete.
- [x] F1. Plan compliance audit
  > APPROVE (2026-08-31): grep verified — llm_evidence.py @observe(rcs):131 + @observe(llm_call):48; synthesis.py @observe(paper_notes):458 + @observe(synthesis_report):569; pipeline.py flush helper:145 + calls:185,:197 + try/except get_client:10,:12. pyproject langfuse>=4.15.1. All map to plan Todos.
- [x] F2. Code quality review
  > APPROVE (2026-08-31): git diff each file showed ONLY decorators/import/flush additions; function bodies unchanged; try/except guards graceful degradation; no type:ignore/empty-catch/new deps; dry-run path unaffected (exit 0). AGENTS.md dry-run-no-key discipline preserved.
- [x] F3. Real manual QA
  > APPROVE (2026-08-31): USER confirmed dashboard shows rcs / paper_notes x2 / synthesis_report with nested llm_call child layer; pipeline run 2 exit 0 (report 4452 chars, 2 paper_summaries, 2 future_directions).
- [x] F4. Scope fidelity
  > APPROVE (2026-08-31): changed files only pyproject.toml, uv.lock, llm_evidence.py, synthesis.py, pipeline.py (+ .omo/). No langfuse changes to .env/data/papers/Summer_Project.pdf; default model gemini-2.5-flash unchanged.

## Commit strategy
- Todo 1-4 各附一個 commit（見各 Todo 的 Commit 行）。**由使用者執行**，worker 只回報確切指令。
- Todo 5-6 無 code 變更（或 test-only），不產生 commit（見各 Todo Commit 行）。
- 不合併回 main；全部推上 feature/plan-doc。

## Success criteria
- `uv run python -m unittest discover -s tests -v` 全綠（74 tests）
- GeminiJsonClient.generate_json + 三個階段函數皆 @observe()，成樹狀 trace
- pipeline 結束前 flush()，Langfuse 失敗優雅降級不擋主流程
- 實機驗證成功：使用者 dashboard 看到 rcs / paper_notes / synthesis_report
- 使用者完成 git commit + push 至 feature/plan-doc
