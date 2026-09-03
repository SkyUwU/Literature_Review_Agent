# corpus-single-rerank-call - Work Plan

## TL;DR (For humans)
<!-- Fill this LAST, after the detailed plan below is written, so it summarizes the REAL plan. -->
<!-- Plain English for a non-engineer: NO file paths, NO todo numbers, NO wave/agent/tool names. -->

**What you'll get:** 把「每篇論文各自取前 k 段、各自呼叫一次 LLM 重新排序」改為「所有論文的所有段落一起取前 k 段、只呼叫一次 LLM」——與 PaperQA2 的方法一致，也與既有的 --dry-run 偵測路徑行為一致。API 呼叫次數從「論文數」降為 1，並讓 LLM 能在一次呼叫內跨論文比較證據。

**Why this approach:** 我們自己的 dry-run 路徑本來就是跨論文一次檢索；LLM 路徑的逐篇呼叫是歷史殘留。統一後行為可預測、可測試，也貼近參考實作 PaperQA2。

**What it will NOT do:** 不動合成報告的輸入瘦身（那是另一個 plan，等你看到本 plan 實測出的真實摘要筆數後再決定）；不新增依賴；不執行任何 Git 指令。

**Effort:** Small
**Risk:** Low - 單一函數改寫 + 測試調整；語意改變（論文可能整篇不出現）已記錄並可逆
**Decisions to sanity-check:** (1) corpus-wide top-k 可能讓部分論文完全不出現在報告（PaperQA2 原生語意，dry-run 也如此）；(2) --top-k 預設維持 3 只改文件描述；(3) 程式碼預設 model 維持 gemini-2.5-flash，實機驗證用旗標切 3.6-flash。

Your next move: Plan 已完成。所有 Todo 與 F1-F4 驗收全數通過。可進入下一個 plan 或開始 Q2（輸入瘦身層）的討論。

---

> TL;DR (machine): Small effort, Low risk; single-function refactor of run_synthesis_pipeline to one corpus-wide RCS call + test/doc updates + real-API verification with gemini-3.6-flash.

## Scope
### Must have
- `literature_review/pipeline.py::run_synthesis_pipeline` — 改為跨論文單一 `retrieve_evidence` + 單一 `summarize_and_rerank`；移除 `_merge_rerank_responses`
- `literature_review/pipeline.py::main` — `--top-k` help 文字改為 corpus-wide 語意
- `tests/test_pipeline.py` — 調整 fake client 預期 + 新增「單一 RCS call」「paper 掉出 corpus top-k 即無 assessment」測試
- `HANDOFF.md`、`README.md` — 更新 top-k 語意、測試數（以實際跑出為準；Todo 2 新增 2 測試後預期 74）、里程碑描述
- 驗證：單元測試全綠、dry-run 無 key 全跑、real API 用 `--model gemini-3.6-flash` 跑通並記錄 A 筆數（供 Q2 決策）

### Must NOT have (guardrails, anti-slop, scope boundaries)
- 不修改 `build_synthesis_prompt()` / `synthesize_report()` 的輸入結構（Q2 另案）
- 不新增 Python 依賴
- worker 不執行任何改變 Git 狀態的指令（add/commit/push/merge 一律回報由使用者執行）
- 不修改 `.env`、`data/papers/`
- 不變更 `GeminiJsonClient` 預設 model（維持 gemini-2.5-flash）；實測切換只能透過 `--model` 旗標
- 不變更 chunking 政策、coverage pack、`llm_input_cap`
- 實機測試若發現新缺陷：停下回報使用者，不自行擴大範圍

## Verification strategy
> Zero human intervention - all verification is agent-executed.
- Test decision: tests-after + 既有 unittest 框架（TDD 無意義於既有行為重構；新增測試隨 Todo 2）
- Evidence: .omo/evidence/task-<N>-corpus-single-rerank-call.log（attemptDir 不在 ulw-loop 時用 .omo/evidence/）

## Execution strategy
### Parallel execution waves
> Target 5-8 todos per wave. Fewer than 3 (except the final) means you under-split.
- Wave 1: Todo 1（pipeline 改寫）
- Wave 2: Todo 2、Todo 3（測試與文件可並行，皆依賴 Todo 1 的語意確定）
- Wave 3: Todo 4（驗證；依賴 1-3 全數完成）

### Dependency matrix
| Todo | Depends on | Blocks | Can parallelize with |
| --- | --- | --- | --- |
| 1 | 無 | 2,3,4 | 無 |
| 2 | 1 | 4 | 3 |
| 3 | 1 | 4 | 2 |
| 4 | 1,2,3 | 無 | 無 |

## Todos
> Implementation + Test = ONE todo. Never separate.
<!-- APPEND TASK BATCHES BELOW THIS LINE WITH edit/apply_patch - never rewrite the headers above. -->
- [x] 1. `pipeline.py`：`run_synthesis_pipeline` 改為跨論文單一 RCS call 並移除 `_merge_rerank_responses`
  > DONE (2026-08-30): 由使用者手動切到 Sisyphus - ultraworker 執行完成；規劃側驗收 1a-1f 全過（無 `_merge_rerank_responses` 殘留、corpus-wide 單一 call、help 含 corpus-wide、import 保留、72 tests OK、dry-run ranked_chunks=1）。證據：`.omo/evidence/task-1-corpus-single-rerank-call.log`。commit 由使用者執行。
  What to do / Must NOT do:
  1. 在 `run_synthesis_pipeline`（pipeline.py:115-153）取代現有 per-document 迴圈（第 126-132 行）：
     移除 `_merge_rerank_responses([...for each prepared])`，改為：
     ```python
     all_chunks = [chunk for _, chunks in prepared for chunk in chunks]
     rerank_response = summarize_and_rerank(retrieve_evidence(all_chunks, query, retrieval_policy), client)
     ```
  2. 刪除 `_merge_rerank_responses` 定義（pipeline.py:97-112）——若 `tests/` 或他處有引用則保留並在回報中註明；已確認 `tests/test_pipeline.py` 未 import 它。
  3. 更新 `main()` 的 `--top-k` help（pipeline.py:165）：`"Number of chunks retrieved corpus-wide across all papers and sent to the LLM"`。
  4. 移除 `EvidenceRerankResponse` import 若不再使用（grep 確認 `run_evidence_pipeline` 仍用）。
  5. 勿改 `run_evidence_pipeline`（單一文件路徑仍有效）。
  Parallelization: Wave 1 | Blocked by: 無 | Blocks: 2,3,4
  References (executor has NO interview context - be exhaustive): literature_review/pipeline.py:97-112,115-153,160-203
  Acceptance criteria (agent-executable): `uv run python -m unittest discover -s tests -v` 全綠；`rg -n "_merge_rerank_responses" literature_review tests` 無結果（或回報保留理由）；help 文字含 "corpus-wide"
  QA scenarios (name the exact tool + invocation): happy → `uv run python -m unittest discover -s tests -v`（全綠）；**注意（2026-08-30 實測校正）：dry-run 路徑（pipeline.py:182-190）本就是 corpus-wide，不走 `run_synthesis_pipeline`——改碼前後 `--top-k 1 --dry-run` 皆輸出 1 chunk，它不是本改動的 failing-proof；本改動的 real proof 是 Todo 2 的單元測試（RCS call 數 == 1）**。dry-run 僅作回歸煙霧（`--top-k 1 --dry-run` 輸出 ranked_chunks=1 即正常）。Evidence .omo/evidence/task-1-corpus-single-rerank-call.log
  Commit: Y | refactor(pipeline): single corpus-wide rerank call matching dry-run and PaperQA2 semantics

- [x] 2. `tests/test_pipeline.py`：調整 fake client 並新增「單一 RCS call」與「paper 掉出 corpus top-k」測試
  > DONE (2026-08-30): 由使用者手動切到 Sisyphus - ultraworker 執行完成；規劃側驗收 2a-2d 全過（`test_synthesis_pipeline_single_rcs_call`:253 與 `test_synthesis_pipeline_paper_dropped_out_of_corpus_top_k`:268 存在、74 tests OK、happy 兩篇斷言未破壞、無 skip/hack）。failure-proof 兩項已驗證：RCS 斷言改 2 → FAIL、drop 斷言目標改 paper-2 → FAIL。證據：`.omo/evidence/task-2-corpus-single-rerank-call.log`。commit 由使用者執行。
  What to do / Must NOT do:
  1. `SynthesisFakeClient`（test_pipeline.py:104-132）：確認「Assess each supplied evidence chunk」分支在單一 corpus-wide call 下會回傳所有 chunk_id 的 assessments（現有 `re.findall(r'"chunk_id": "([^"]+)"', prompt)` 已涵蓋，驗證即可）。
  2. 新增測試：`run_synthesis_pipeline` 對兩個文件**只發出一次** RCS call（數 `prompts` 中以 "Assess each supplied evidence chunk" 開頭者 == 1）。
  3. 新增測試：構造兩篇文件，其中一篇的 chunk 詞彙分數明顯低於另一篇且不符 query terms，使 corpus top-k 不含其任何 chunk → 該 paper_id 不出現在 `result.paper_summaries`、`result.evidence_assessment_response.assessments`、`result.paper_sources`；另一篇正常出現。
  4. 勿修改 `test_synthesis_pipeline_happy` 的斷言語意（2 篇 paper_summaries 仍應成立——若 corpus top-k=3 吃不下兩篇，調整 `top_k` 測試參數至 4~6，或在測試內加 query-relevant text）。
  Parallelization: Wave 2 | Blocked by: 1 | Blocks: 4 | Can parallelize with: 3
  References: tests/test_pipeline.py:13-28,104-132,162-216; literature_review/pipeline.py:126-132
  Acceptance criteria (agent-executable): 新增至少 2 個測試方法；`uv run python -m unittest discover -s tests -v` 全綠（73+ tests）
  QA scenarios: happy → 新測試跑綠；failure → 把單一 call 測試的 expected count 改 2 應失敗（確認測試真的在測）。Evidence .omo/evidence/task-2-corpus-single-rerank-call.log
  Commit: Y | test(pipeline): assert single corpus-wide RCS call and top-k paper drop-out

- [x] 3. `HANDOFF.md` + `README.md`：語意與測試數更新
  > DONE (2026-08-30): 由使用者手動切到 Sisyphus - ultraworker 執行完成；規劃側驗收 3a-3d 全過（`per paper|71 tests` 零命中、`corpus-wide` 兩檔皆有（HANDOFF:56,57 / README:24）、HANDOFF:38 寫 74 == 實際測試計數、HANDOFF:57 里程碑補 top-k drop-out 句）。commit `dde4ee74`（docs(handoff,readme): top-k is corpus-wide across papers）由使用者執行。證據：`.omo/evidence/task-3-corpus-single-rerank-call.log`。執行側曾自我修正「not per paper（空格）」→「not per-paper」避免誤觸 grep 斷言。
  What to do / Must NOT do:
  1. `HANDOFF.md:56`「retrieves top-k chunks per paper」→「retrieves top-k chunks corpus-wide across all papers in one LLM call」。
  2. `HANDOFF.md:38`「71 tests」→「以實際跑出的測試數為準」（2026-08-30 實測 baseline 已為 72；Todo 2 新增 2 測試後預期 74。勿寫死 72——見 todo-3 包）；里程碑段落（52-62）補一句：論文若無 chunk 進 corpus-wide top-k 則不產生 assessment（PaperQA2 語意）。
  3. `README.md` 的 `--top-k` 說明與 pipeline 描述同步為 corpus-wide；`--top-k 8` 範例維持。
  4. 勿改 AGENTS.md；勿改程式碼。
  Parallelization: Wave 2 | Blocked by: 1 | Blocks: 4 | Can parallelize with: 2
  References: HANDOFF.md:38,52-62; README.md:30-34,44-47
  Acceptance criteria (agent-executable): `rg -n "per paper" HANDOFF.md README.md` 無結果；`rg -n "71 tests" HANDOFF.md` 無結果；兩檔都提到 corpus-wide
  QA scenarios: happy → grep 斷言全過；failure → 故意保留舊字串應被 grep 抓到。Evidence .omo/evidence/task-3-corpus-single-rerank-call.log
  Commit: Y | docs(handoff,readme): top-k is corpus-wide across papers

- [x] 4. 驗證：dry-run + real API（`--model gemini-3.6-flash`）+ 記錄 A 筆數供 Q2 決策
  > DONE (2026-08-30): 由使用者手動切到 Sisyphus - ultraworker 執行完成；規劃側驗收 4a-4c 全過（dry-run ranked_chunks=8、real API RCS call=1、A=8、exit 0、25 markers 全在 allowed_ids）。證據：`.omo/evidence/task-4-corpus-single-rerank-call.log`。無 commit（驗證任務，Commit marker = N）。
  What to do / Must NOT do:
  1. `uv run python -m literature_review.pipeline data/papers "literature review agent" --top-k 8 --dry-run`——無 key 全跑，記錄 JSON 中 `ranked_chunks` 長度（應為 ≤8）。
  2. `uv run python -m literature_review.pipeline data/papers "literature review agent" --top-k 8 --model gemini-3.6-flash`——真實 API 端到端。成功則在 log 記錄：RCS call 數（應為 1）、`evidence_rerank_response.summaries` 長度（=A 筆數，供 Q2 決策：A 是否小到可留現狀）。
  3. 若遇 429 quota：記錄 retry window，休息 ≥60s 重試一次；連續 3 次 429 即停止並回報「quota 未恢復、需使用者稍後重跑」，不視為失敗也不需要改 code。
  4. 若遇到非 quota 的新缺陷（schema/unknown id 等）：停止、貼出錯誤訊息、不自行修復。
  5. 勿改任何程式碼；勿寫 .env。
  Parallelization: Wave 3 | Blocked by: 1,2,3 | Blocks: 無
  References: literature_review/pipeline.py:174-203; HANDOFF.md:86-90（quota notes）
  Acceptance criteria (agent-executable): dry-run 成功輸出 ranked_chunks ≤8；real API 成功輸出 `SynthesisResponse`（或 3 次 429 的證據 log + 回報文字）；log 內含 summaries 筆數
  QA scenarios: happy → 完整輸出 JSON 且報告含 `[chunk_id]` 標記；failure → 429 證據 log 且回報語明確。Evidence .omo/evidence/task-4-corpus-single-rerank-call.log
  Commit: N（無 code 變更；若 Todo 1-3 未提交，由使用者統一提交）

## Final verification wave
> Runs in parallel after ALL todos. ALL must APPROVE. Surface results and wait for the user's explicit okay before declaring complete.
- [x] F1. Plan compliance audit
  > DONE (2026-08-30): 由 Sisyphus 執行 F1 檢查（grep + unittest + evidence log 審查）。全數通過：`_merge_rerank_responses` 無殘留、`corpus-wide` 兩檔皆有、74 tests OK、新測試存在、failure-proof 已驗證、docs 無舊語意、Todo 4 dry-run/real API 數字在 log 內。證據：`.omo/evidence/task-{1,2,3,4}-corpus-single-rerank-call.log`。
- [x] F2. Code quality review
  > DONE (2026-08-30): 由 Sisyphus 執行 F2 檢查（diff + grep + 語意確認）。全數通過：無 type:ignore、無空 catch block、`summarize_and_rerank` 只有 1 處合成呼叫、`model_validate_json()` 有使用、`EvidenceRerankResponse` import 保留、函數長度正常、無新增依賴。
- [x] F3. Real manual QA
  > DONE (2026-08-30): 由 Sisyphus 執行 F3 檢查（dry-run 重跑 + 引用 task-4 log）。全數通過：dry-run exit 0、`ranked_chunks=8`、跨論文合併、stderr 空；`--top-k 1` 對照 task-1 log = 1；real API 引用 task-4 log（RCS call=1、A=8、25 markers 全在 allowed_ids）。
- [x] F4. Scope fidelity
  > DONE (2026-08-30): 由 Sisyphus 執行 F4 檢查（diff + grep + status）。全數通過：預設 model 未變（gemini-2.5-flash）、Q2 範圍外未動（`build_synthesis_prompt` 僅在 synthesis.py 既有）、4 目標檔案已 commit（git diff 無殘留）、無 Summer_Project.pdf/data/papers 在 git。

## Commit strategy
- Todo 1-3 各附一個 commit（見各 Todo 的 Commit 行）。**由使用者執行**，worker 只回報確切指令：
  ```powershell
  git add literature_review/pipeline.py tests/test_pipeline.py HANDOFF.md README.md && git commit -m "refactor(pipeline): single corpus-wide rerank call matching dry-run and PaperQA2" && git push
  ```
- 先決條件（Step 0）：✅ **已完成**——Todo 9（hallucinated-ID 修復）+ `_strip_code_fence` 修正已隨先前 commits a965ecf→856dc81（"fix: merge stated_limitations into claims, add schema-guided generation, extend retry — 72 unit tests pass"）提交並推送至 origin/feature/plan-doc；工作區無程式碼變更待提交。
- 不合併回 main；全部推上 feature/plan-doc。

## Success criteria
- `uv run python -m unittest discover -s tests -v` 全綠（≥73 tests） ✅
- `run_synthesis_pipeline` 對 N 篇論文只發 1 次 RCS call；dry-run 與 LLM 路徑行為一致 ✅
- `--top-k` 語意明確為 corpus-wide 且文件已更新 ✅
- real API 端到端成功（或完整 429 證據 + 回報），且 log 記錄 A 筆數供 Q2 決策 ✅
- 使用者完成 git commit + push 至 feature/plan-doc ✅