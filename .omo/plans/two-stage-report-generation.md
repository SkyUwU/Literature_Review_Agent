# two-stage-report-generation - Work Plan

## TL;DR (For humans)
<!-- Fill this LAST, after the detailed plan below is written, so it summarizes the REAL plan. -->
<!-- Plain English for a non-engineer: NO file paths, NO todo numbers, NO wave/agent/tool names. -->

**What you'll get:** 綜合報告生成從「一次寫完」改成三步驟:①請 LLM 把全部證據 claim 分類成主題大綱(每節預先標註會引用哪些 claim)→ ②照大綱寫報告正文 → ③獨立產出「未來方向」(只看原始證據,不受報告大綱影響)。順便完成輸入瘦身:報告階段不再送每篇論文的分數摘要。對外輸出格式完全不變。

**Why this approach:** 未來方向是「提供方向」的前瞻輸出,不該被報告的整理框架限縮視野;而且目前負責報告的 API 金鑰用量很低,多加兩次呼叫幾乎零成本。三 call 各自任務單純、容易驗證引用正確。

**What it will NOT do:** 不會改變報告的對外格式(引用標記、方向欄位、材料清單照舊);不會動無金鑰時的備援逃生路徑;不會改評分、逐篇筆記、或哪些論文進報告的邏輯。

**Effort:** Medium
**Risk:** Medium - LLM 呼叫數 ×3,引用編號竄改機率上升;既有的自動修復機制兜底

**Decisions to sanity-check:** 大綱節數上限 2-4(可調)、方向呼叫輸入不含大綱、三次呼叫共用同一個報告金鑰(key3)、對外輸出格式不變

Your next move: approve.

---

> TL;DR (machine): Medium effort, Medium risk - 報告生成改三 call(大綱/報告/方向),輸出契約不變,拿掉 assessments 區塊

## Scope
### Must have
- models.py:新增 `LlmOutlineSection` + `LlmSynthesisOutline`(大綱契約)、`LlmSynthesisReportBatch`(call 2 契約)、`LlmSynthesisDirectionsBatch`(call 3 契約)。
- synthesis.py:三個新 prompt 函數(`build_outline_prompt` / `build_report_prompt` / `build_directions_prompt`)+ `synthesize_report` 改三 call 流程 + 簽名加 `query`。
- 大綱 call 的 `supporting_claim_ids` 白名單驗證(未知 id → repair 一輪 → 仍未知則 raise)。
- 方向 call(call 3)輸入 = claims + query,**不含大綱**;prompt 明示方向不限 limitation(收斂/缺口/延伸)。
- 三 call 一律不再送 assessments 摘要區塊(候選 E 輸入瘦身)。
- pipeline.py:把 `query` 從 `run_synthesis_pipeline` 傳進 `synthesize_report`。
- 測試遷移 + 新增(FakeSynthesisClient 序列回應、三 call 順序、prompt 內容斷言)。
- HANDOFF.md / README.md / STATE.md 同步;354 tests 基線之上全綠。

### Must NOT have (guardrails, anti-slop, scope boundaries)
- **不動外部輸出契約**:`SynthesisResponse` / `FutureDirection` / `PaperSource` / `LiteratureReviewReport` 欄位形狀零改動;`claim_chunks` 對照表機制不變。
- **不動確定性逃生路徑**:`_make_direction` / `_convergence_direction` / `_limitation_directions` / `_fallback_direction` / `_deterministic_directions`(synthesis.py:164-249)原樣保留。
- 不動 notes 生成、評分線、embedding 取樣、`LlmSynthesisBatch` 模型定義(保留相容,不再被 synthesize_report 使用)。
- 不動 demo.py、legacy RCS 契約(`EvidenceAssessmentResponse` 參數保留)。
- 不新增 env / key 配置(三 call 共用 key3)。
- 不做「大綱 human 審核停頓」——大綱階段完全自動,不互動。
- 不碰 `.env`、`data/papers/`;不執行 git commit(push 由使用者親做)。

## Verification strategy
> Zero human intervention - all verification is agent-executed.
- Test decision: **tests-after(既有測試遷移 + 新增測試)** + `unittest`(專案慣例);prompt 內容與順序以 fake client 捕捉斷言。
- Evidence: `.omo/evidence/two-stage-report-generation-tests.log`(全測試)、`.omo/evidence/two-stage-report-generation-smoke.log`(fake e2e)。
- 基線:現況 354 tests 全綠(`uv run python -m unittest discover -s tests -v`);執行後數字應上升(新增測試)。

## Execution strategy
### Parallel execution waves
> Target 5-8 todos per wave. Fewer than 3 (except the final) means you under-split.

- **Wave 1**(模型契約):Todo 1(models 新契約)——獨立。
- **Wave 2**(三個 prompt 函數):Todo 3(outline prompt)、Todo 4(report prompt)、Todo 5(directions prompt)——互不依賴,可平行。
- **Wave 3**(呼叫端 + synthesize_report 三 call 流程):Todo 2(pipeline 傳 query)+ Todo 6(三 call 流程)——**Todo 2 與 Todo 6 為單一不可分割變更**。
- **Wave 4**(測試遷移 + 新增):Todo 7——依賴 6。
- **Wave 5**(證據 + 文件):Todo 8(全測試 + log)、Todo 9(文件同步)——依賴 7。

### Dependency matrix
| Todo | Depends on | Blocks | Can parallelize with |
| --- | --- | --- | --- |
| 1 (models) | - | 6 | 3, 4, 5 |
| 2 (pipeline call) | - | 6 (同變更) | - |
| 3 (outline prompt) | - | 6 | 1, 4, 5 |
| 4 (report prompt) | - | 6 | 1, 3, 5 |
| 5 (directions prompt) | - | 6 | 1, 3, 4 |
| 6 (synthesize_report) | 1, 2, 3, 4, 5 | 7 | - |
| 7 (tests) | 6 | 8, 9 | - |
| 8 (tests log) | 7 | - | 9 |
| 9 (docs) | 7 | - | 8 |

## Todos
> Implementation + Test = ONE todo. Never separate.
<!-- APPEND TASK BATCHES BELOW THIS LINE WITH edit/apply_patch - never rewrite the headers above. -->

### Wave 1

- [ ] 1. models.py: 新增 LlmOutlineSection / LlmSynthesisOutline / LlmSynthesisReportBatch / LlmSynthesisDirectionsBatch 四個模型,欄位約束與現有 synthesis 契約一致
  What to do / Must NOT do: 在 `LlmSynthesisBatch`(現 models.py:453-457)旁新增四個模型,不可改動任何既有模型欄位。設計:
  - `LlmOutlineSection(title: str min_length=3, purpose: str min_length=10, supporting_claim_ids: list[str] min_length=1)`
  - `LlmSynthesisOutline(sections: list[LlmOutlineSection] min_length=1)`(大綱契約;支援大綱 = 2-4 節由 prompt 引導,schema 不硬限制)
  - `LlmSynthesisReportBatch(report: str min_length=100)`(call 2 契約;不再含 future_directions)
  - `LlmSynthesisDirectionsBatch(future_directions: list[LlmSynthesisDirection] min_length=1)`(call 3 契約;**重用現有 LlmSynthesisDirection models.py:438-450,不可重造**)
  Must NOT do: 不動 `LlmSynthesisBatch`;不動 `LlmSynthesisDirection`;不動 `SynthesisResponse`/`FutureDirection`。
  Parallelization: Wave 1 | Blocked by: - | Blocks: 6
  References: literature_review/models.py:438-457(LlmSynthesisDirection/LlmSynthesisBatch 欄位風格);literature_review/models.py:359-383(FutureDirection 的 supporting 語意)
  Acceptance criteria (agent-executable): `uv run python -c "from literature_review.models import LlmSynthesisOutline, LlmSynthesisReportBatch, LlmSynthesisDirectionsBatch; print('ok')"` 成功;四個模型可實例化且欄位約束生效(空 sections / 空 future_directions / 過短 title 皆 ValueError)
  QA scenarios (name the exact tool + invocation): happy: 上述 import + 合法實例化;failure: 空清單 `LlmSynthesisDirectionsBatch(future_directions=[])` 應丟 pydantic ValidationError。Evidence `.omo/evidence/task-1-two-stage-report-generation.log`
  Commit: Y | feat(models): add two-stage synthesis contracts (outline / report / directions batches)

### Wave 2

- [ ] 3. synthesis.py: 新增 build_outline_prompt(call 1 大綱 prompt)
  What to do / Must NOT do: 新函數 `build_outline_prompt(query: str, notes: list[dict], claim_counter_total: int)` → 產出餵 `generate_validated(client, LlmSynthesisOutline, ...)` 的 prompt 字串。內容要求:① 給使用者 query(text)與全部 claims(僅 usable papers,格式仿 build_synthesis_prompt 的 notes 區塊:claim_id/text/aspect)② 指示「把 claims 歸納成 2-4 個主題節,每節給 title + purpose + 該節將引用的 supporting_claim_ids(claim id 必須原樣抄自供給清單,不得發明/修改)」③ 主題式導向(背景與範疇 → 機制與工具 → 瓶頸與挑戰 等;避免照論文清單逐篇宣科)。Must NOT do: 不在此 prompt 送 assessments 摘要;不放 future_directions 指示(那是 call 3);不寫報告。
  Parallelization: Wave 2 | Blocked by: - | Blocks: 6
  References: literature_review/synthesis.py:702-755(build_synthesis_prompt 的 notes 區塊與 marker 指示風格);literature_review/synthesis.py:725-740(claim 編號邏輯,大綱 prompt 需與 build_claim_chunks 編號一致)
  Acceptance criteria (agent-executable): `uv run python -m unittest tests.test_synthesis -v` 中新增的 prompt 測試(見 Todo 7)全綠;單獨驗證:prompt 字串含全部 claim_id、不含「assessment」「utility_score」字樣
  QA scenarios: happy: 構造 2 節 claims 的 notes,prompt 含 claim-1..N;failure: 空 notes 應 raise(沿用現況行為—synthesize_report 已先檢查無 assessments 即 raise)。Evidence `.omo/evidence/task-3-two-stage-report-generation.log`
  Commit: Y | feat(synthesis): add outline prompt for two-stage report generation

- [ ] 4. synthesis.py: 新增 build_report_prompt(call 2 報告 prompt,拿掉 assessments 區塊)
  What to do / Must NOT do: 新函數 `build_report_prompt(outline: LlmSynthesisOutline, notes: list[dict])` → call 2 的 prompt。內容要求:① 保留現況 marker 指示(句尾 `[claim-N]` 原樣抄、不得發明、不加材料來源清單段——synthesis.py:742-747 的核心指示)② 加上大綱結構(outline.sections 序列化,indicate 每個 section 的 title/purpose/supporting_claim_ids,指示 LLM 依大綱組織 prose、把支持節的 claims 寫進對應節)③ **不再送 assessments 摘要區塊**(拿掉 synthesis.py:753 的 `Paper assessments:` 行)④ 不送 future_directions 指示;回傳契約為 `LlmSynthesisReportBatch`(只有 report)。Must NOT do: 不留 build_synthesis_prompt 的舊 call 使用權(舊函數如仍被測試引用→遷移測試到新函數;舊函數可刪除或保留為 legacy——**決定:刪除 build_synthesis_prompt,改由 build_report_prompt 取代**,測試同步遷移)。刪除時一併處理其蹤跡:① `__all__` 清單(synthesis.py:46 含 "build_synthesis_prompt"→移除)② :766 docstring 註解引用改指新函數 ③ 已 grep 確認全 repo 無其他 import(唯一呼叫點 :835、HANDOFF.md:278 文件描述由 Todo 9 同步)。不在此 prompt 放方向。
  Parallelization: Wave 2 | Blocked by: - | Blocks: 6
  References: literature_review/synthesis.py:741-755(現行 prompt 全文,指示文字遷移來源);literature_review/synthesis.py:758-769(_allowed_marker_ids 白名單機制不受影響)
  Acceptance criteria (agent-executable): 見 Todo 7 測試;直接驗證:prompt 含 outline 節標題、含全部 claim_id、**不含** "assessments" / "utility_score"
  QA scenarios: happy: outline 2 節 + claims,prompt 含節標題與 claim ids;failure: outline 為空 → 呼叫端防呆(見 Todo 6:大綱空即 raise,不進 call 2)。Evidence `.omo/evidence/task-4-two-stage-report-generation.log`
  Commit: Y | feat(synthesis): report prompt consumes outline, drops assessments block

- [ ] 5. synthesis.py: 新增 build_directions_prompt(call 3 方向 prompt,輸入不含大綱)
  What to do / Must NOT do: 新函數 `build_directions_prompt(query: str, notes: list[dict])` → call 3 的 prompt。內容要求:① 給使用者 query 與全部 claims(與 call 1/2 同一批 notes 格式)② 指示「產出 1-N 條未來方向:從全部 claims 找跨論文收斂點、尚未解決的缺口、可延伸的方向;不限 limitation」③ 每條方向給 title/rationale/supporting_paper_ids/supporting_claim_ids(claim id 原樣抄;paper id 原樣抄)④ 指示方向是前瞻輸出,與報告 prose 分離。**Must NOT do: 輸入不含大綱(使用者定案:避免方向被報告框架限縮)**;不送 assessments;回傳契約 `LlmSynthesisDirectionsBatch`。
  Parallelization: Wave 2 | Blocked by: - | Blocks: 6
  References: literature_review/synthesis.py:742-752(現行 prompt 中 direction 欄位指示文字可參考);literature_review/synthesis.py:842-852(方向 id 驗證邏輯沿用)
  Acceptance criteria (agent-executable): 見 Todo 7 測試;直接驗證:prompt 不含大綱節標題、不含 assessments
  QA scenarios: happy: 2 論文 claims,prompt 含 claim ids 與 query;failure: 空 claims → 沿用現況(方向 call 至少需 1 條方向,空清單由 schema min_length 拒絕)。Evidence `.omo/evidence/task-5-two-stage-report-generation.log`
  Commit: Y | feat(synthesis): independent future-directions prompt (no outline input)

### Wave 3

> ⚠️ Todo 2 與 Todo 6 是**單一不可分割變更**:呼叫端 `query=query` 與簽名擴展必須同時完成並一起驗證(分開做會 TypeError)。

- [ ] 2. pipeline.py: 把 query 往下傳給 synthesize_report(呼叫端加 `query=query`;簽名擴展在 Todo 6 同步完成)
  What to do / Must NOT do: `run_synthesis_pipeline` 已收到 `query` 參數(main.py:324-334 傳入);把 `query=query` 加進 pipeline.py:285-287 的 `synthesize_report(...)` 呼叫。**與 Todo 6 的簽名修改同一次變更完成與驗證**(不得先做)。Must NOT do: 不改 run_synthesis_pipeline 自身簽名;不動其他呼叫點(目前只有 pipeline.py 一處呼叫 synthesize_report)。
  Parallelization: Wave 3 | Blocked by: - | Blocks: 6(與 6 同變更)
  References: literature_review/pipeline.py:285-287(synthesize_report 呼叫點);literature_review/main.py:324-334(query 已在 run_synthesis_pipeline 簽名)
  Acceptance criteria (agent-executable): 與 Todo 6 合併驗證——`uv run python -m unittest tests.test_pipeline -v` 全綠且 tests.test_synthesis 的 LlmSynthesisReportTests 遷移版全綠;`grep -n "query=" literature_review/pipeline.py` 命中的呼叫行帶 `query=query`
  QA scenarios: happy: pipeline 呼叫帶 `query=query`、全測試綠;failure: 若 Todo 6 未同步完成,呼叫 TypeError——不得單獨提交任一側。Evidence `.omo/evidence/task-2-two-stage-report-generation.log`(與 task-6 同 log)
  Commit: Y(與 Todo 6 同一 commit)| feat(synthesis): two-stage three-call synthesis (outline -> report -> directions)

- [ ] 6. synthesis.py: synthesize_report 改三 call 流程(大綱 → 報告 → 方向),簽名加 query,大綱/方向 id 驗證
  What to do / Must NOT do: 改 `synthesize_report`(synthesis.py:808-885):簽名加 keyword-only `query: str | None = None`(放 `paper_assessments` 之後)。流程:
  1. assessments/usable_ids 判定(現況 :824-831 保留);
  2. **call 1 大綱**:`build_claim_chunks(usable_notes)` 產生 claim_chunks 後,`outline = generate_validated(client, LlmSynthesisOutline, build_outline_prompt(query or "", notes_dicts), LlmSynthesisOutline.model_json_schema())`;驗證 outline 內每個 `supporting_claim_ids` ⊆ set(claim_chunks)——**未知 id → 一次 repair(重現 _repair_claim_markers 的模式:_build_claim_repair_prompt 或同構 repair prompt 帶 expected ids)→ 仍未知 raise SynthesisError**;
  3. **call 2 報告**:`batch = generate_validated(client, LlmSynthesisReportBatch, build_report_prompt(outline, notes_dicts), LlmSynthesisReportBatch.model_json_schema())`;沿用 `_repair_claim_markers`(adapt 到 LlmSynthesisReportBatch)與 `_allowed_marker_ids`;
  4. **call 3 方向**:`directions = generate_validated(client, LlmSynthesisDirectionsBatch, build_directions_prompt(query or "", notes_dicts), LlmSynthesisDirectionsBatch.model_json_schema())`;沿用 :842-852 的 paper/claim 白名單驗證(未知 → raise,行為不變);
  5. 組裝 SynthesisResponse:report=batch.report、future_directions 來自 call 3(directions.future_directions;FutureDirection 轉換同 :874-882)、其餘欄位沿用;
  6. 觀測:把三個 LLM call 各拆成內部包裝函數(如 `_outline_call` / `_report_call` / `_directions_call`,各持 generate_validated),各加 `@observe(name="synthesis_outline")` / `("synthesis_report_writing")` / `("synthesis_directions")`;**不可在通用 `generate_validated`(llm_evidence.py:207)上加裝飾器**(會影響全部呼叫者);外層 synthesize_report 的既有 `@observe(name="synthesis_report")` 保留。
  Must NOT do: 不動 SynthesisResponse/其他輸出欄位;不動確定性路徑;不改 `_repair_claim_markers` 對未知 id 的 raise 語意;不動 notes 的 `summarize_paper_notes`。**注意:Todo 2 的 pipeline 呼叫修改與本 todo 的簽名修改必須同一時間完成**——執行時將兩處變更視為一個不可分割單元驗證。
  Parallelization: Wave 3 | Blocked by: 1, 3, 4, 5 | Blocks: 7
  References: literature_review/synthesis.py:808-885(現行 synthesize_report);literature_review/synthesis.py:772-805(_repair_claim_markers 模式);literature_review/synthesis.py:791-798(repair prompt 用法);literature_review/synthesis.py:791/generate_validated 別名 `_generate_validated`(synthesis 內 import,沿用此別名,勿改);也可參 literature_review/llm_evidence.py:207(底層通用函數定義,勿在其上加裝飾器)
  Acceptance criteria (agent-executable): `uv run python -m unittest tests.test_synthesis -v` 中 LlmSynthesisReportTests 遷移版全綠(Todo 7 完成後);fake client 三回應序列時 synthesize_report 回傳 SynthesisResponse 且 report/future_directions 分別來自 call 2/call 3
  QA scenarios: happy: FakeSynthesisClient 依序回 outline → reportbatch → directionsbatch,斷言產出;failure: call 1 大綱含未知 claim id → repair 一次後仍錯 → SynthesisError;call 3 含未知 paper id → SynthesisError(沿用現況)。Evidence `.omo/evidence/task-6-two-stage-report-generation.log`
  Commit: Y | feat(synthesis): two-stage three-call synthesis (outline -> report -> directions)

### Wave 4

- [ ] 7. tests: 遷移 LlmSynthesisReportTests 到三 call,新增大綱/方向/prompt 內容/query 傳遞測試,FakeSynthesisClient 支援序列回應
  What to do / Must NOT do: ① `FakeSynthesisClient`(tests/test_synthesis.py:496-608)擴充:支援依呼叫序回不同 JSON 的回應序列(如 `responses: list[str]` 依 call 序 pop;或 scripted per-call)——**不可改成真呼叫 Gemini**;② 遷移 LlmSynthesisReportTests(:609-680)四測試:happy(三回應)、unknown_marker(仍是 call 2 的 marker)、no_markers、repair_retry;③ 新增測試:大綱 unknown claim id → repair 一輪 → 仍未知 raise;方向 call 輸入不含大綱文本(檢查 call 3 prompt 字串無節標題);call 1/2/3 prompt 皆不含 assessed(utility_score);query 有傳入 call 1 與 call 3 prompt;方向 call 的 paper/claim 未知 → raise(既有 :1141-1150 保留);④ 確定性路徑測試(:434-495)與其他既有測試零改動。Must NOT do: 不改 build_claim_chunks/_allowed_marker_ids;不刪既有測試(只遷移/新增)。
  Parallelization: Wave 4 | Blocked by: 6 | Blocks: 8, 9
  References: tests/test_synthesis.py:496-680(FakeSynthesisClient + LlmSynthesisReportTests);tests/test_synthesis.py:1141-1150(direction 驗證測試);tests/test_synthesis.py:434-495(確定性路徑測試—不動)
  Acceptance criteria (agent-executable): `uv run python -m unittest discover -s tests -v` 全綠且測試數 > 354(新增 ≥ 8)
  QA scenarios: happy: 全部新測試單獨跑綠;failure: 任一測試斷言 prompt 不含 assessments 失敗 → 代表 E 瘦身沒做乾淨。Evidence `.omo/evidence/task-7-two-stage-report-generation.log`
  Commit: Y | test(synthesis): migrate two-stage synthesis tests, cover outline/directions prompts

### Wave 5

- [ ] 8. 全測試 + fake e2e smoke,log 存 .omo/evidence/
  What to do / Must NOT do: ① `uv run python -m unittest discover -s tests -v` 全跑,紀錄測試數與 OK;② fake e2e:用 scripted FakeSynthesisClient(三回應)跑 `synthesize_report` 端到端,驗證三 call 順序與 SynthesisResponse 組裝;③ 兩份 log 存 `.omo/evidence/two-stage-report-generation-tests.log` 與 `-smoke.log`。Must NOT do: 不跑真實 Gemini main(掛帳,OpenAlex 429 風險);不動 data/papers。
  Parallelization: Wave 5 | Blocked by: 7 | Blocks: -
  References: AGENTS.md(Commands: `uv run python -m unittest discover -s tests -v`);先例 log 命名:.omo/evidence/c2d-tests.log
  Acceptance criteria (agent-executable): 兩份 log 存在且 tests log 末行 OK、smoke log 顯示三 call 依序(outline→report→directions)
  QA scenarios: happy: log 全綠;failure: 任一測試紅 → 回報執行代理修復(不得隱藏)。Evidence `.omo/evidence/two-stage-report-generation-tests.log` / `-smoke.log`
  Commit: N(log 在 .gitignore,不進 repo)

- [ ] 9. 文件同步:HANDOFF.md / README.md / STATE.md
  What to do / Must NOT do: ① HANDOFF.md:報告生成段落更新為三 call 流程;② README.md:架構說明(command 區塊不變,但若提到合成流程的段落同步);③ STATE.md:現況一句話區 + 架構裁示區補「報告生成三 call(2026-09-13 定案:大綱→報告→方向獨立;assessments 區塊移除)」+ 候選 E 標記 ✅。Must NOT do: 不重寫其他段落;不要在 README 放 API key。
  Parallelization: Wave 5 | Blocked by: 7 | Blocks: -
  References: HANDOFF.md、README.md(「Run the full pipeline」段)、.omo/STATE.md(現況區 + 下一步 + 候選 E)
  Acceptance criteria (agent-executable): grep 三份文件命中「outline」及「future directions」新流程描述;`git diff --stat` 顯示三檔僅相關段落變動
  QA scenarios: happy: 三檔 grep 命中;failure: STATE 候選 E 仍掛「未實作」→ 漏更新。Evidence(n/a,以 grep 輸出為證)
  Commit: Y | docs: record two-stage report generation (outline -> report -> directions)

## Final verification wave
> Runs in parallel after ALL todos. ALL must APPROVE. Surface results and wait for the user's explicit okay before declaring complete.
- [ ] F1. Plan compliance audit
- [ ] F2. Code quality review
- [ ] F3. Real manual QA
- [ ] F4. Scope fidelity

## Commit strategy
- 執行代理**不執行任何 git commit**(專案慣例:commit/push 一律由使用者親做;執行代理只改 code、跑測試、存 log、回報)。
- 執行完成並驗收通過後,使用者執行:
  ```powershell
  :: 1. code(依 wave 順序的 commit message)
  git add literature_review/models.py literature_review/synthesis.py literature_review/pipeline.py tests/test_synthesis.py
  git commit -m "feat: two-stage three-call report generation (outline -> report -> future directions)"

  :: 2. docs
  git add HANDOFF.md README.md .omo/STATE.md .omo/plans/two-stage-report-generation.md
  git commit -m "docs: record two-stage report generation acceptance"

  :: 3. push(含先前未推的 M5e/C2d 等,依 git status 現況)
  git push origin feature/plan-doc
  ```
- ⚠️ 若 M5e 9 檔與 C2d 計畫檔尚未 commit,先依先前交付的指令補上,再執行上述三筆。
- `.omo/evidence/` 在 .gitignore(內部工作產物,不進 repo;`git add` 忽略屬正常,不要 `-f`)。

## Success criteria
- 現況 `LlmSynthesisReportTests` 全數遷移為三 call 序列,測試總數 > 354 且全綠。
- `synthesize_report(..., query=...)` 依序完成大綱→報告→方向三個 LLM call;call 3 的輸入不含大綱;三 call 的 prompt 皆不含 assessments 摘要。
- `SynthesisResponse` 外部形狀不變:report 帶 `[claim-N]`、future_directions 帶 supporting_claim_ids、claim_chunks 對照表照舊——下游(`main.py` 組裝、`render_materials_section`、驗證契約)零改動維運。
- 方向 prompt 不綁 limitation,可產出收斂/缺口/延伸類方向。
- 確定性路徑與 legacy 契約零改動;`uv run python -m unittest discover -s tests -v` 全綠。