# .omo/STATE.md — 專案現況小抄（最後更新：2026-09-04）

> 每個 session 開始自動載入（`opencode.json` instructions）。gate 過後由規劃 agent 更新；執行 agent 只讀不改。
> 可隨時用 `@.omo/STATE.md` 重新載入，避免依賴對話記憶。

## 現況（一句話）
- **M1（pairwise-retrieval-eval）已完整收尾 ✅**：Todo 1-6 全完成、smoke 通過（embedding better）、commit+push、gitignore 整理、skill 建立。
- **M2（embedding-retrieval-adoption）已完整收尾 ✅**：Todo 1-4 全完成（換 embedding + coverage References 低權重 + 文件 + 真實 API smoke 驗收通過）、code/docs 已 commit+push。
- **M3A（LLM planner + query-only SearchPlan）已完整收尾 ✅ 並已 commit**（`2a09f7b` code + `c5414a1` docs，154 tests OK + 真實 smoke 過，`SearchPlan.idea` = `ResearchIdea∣str∣None` 且寫入 query 保留追溯）。目前階段 = M3A 完成，下一步規劃 M3B（抓 PDF）。

## 下一步
1. **使用者 push**（M3A 已 commit，尚未 push）。
2. **下一個 milestone**：M3B（抓 PDF：OpenAlex/arXiv/SS OA 全文 PDF，需定資料來源策略，見 architecture-futures.md）。
3. M1 遺留：opencode 尚未重啟（skill 需重啟才生效）；Windows 端尚未 pull 同步。
4. **skill 待辦**：計畫驗證做成 `review-plan` skill（先有 checklist：`.omo/notes/plan-review-checklist.md`；skill 之後交執行代理建立，重啟生效）。

## M3 方向（使用者偏好，2026-09-04 定）
- **先做部件、最後用 main.py 串接**（不是先串骨架）。部件順序：① LLM planner（M3A，進行中）→ ② 抓 PDF → ③ main.py 串接。
- **M3A = LLM planner ✅（已 commit）**：`SearchPlan.idea` 改 `ResearchIdea∣str∣None`（query-only，寫入 query 保留追溯）、新增 `create_llm_plan(query, client)` 用 LLM 生成 `SearchPlan`（`generated_by="llm"`），共用 `llm_evidence.generate_validated`，開發期 fake client → 最後真實 smoke。`create_rule_based_plan(query)` 保留為 fallback。計畫：`.omo/plans/llm-planner.md`。commit：`2a09f7b`（code）+ `c5414a1`（docs）。
- **抓 PDF**（M3B 候選）：從 OpenAlex/arXiv/SS 抓 OA 全文 PDF，需定資料來源策略（見 architecture-futures.md）。
- **main.py 串接**（M3C 候選）：idea → plan → search → 抓 PDF → pipeline → 報告 的完整入口，參數寫死 + 可互動式問 query。

## M2 成果摘要（2026-09-04 完成）
- Todo 1：pipeline 換成 embedding 檢索（`retrieve_evidence_embedding`，可注入 encoder 方案 B）。144 tests OK（`test-suite-m2-todo1.log`）。
- Todo 2：coverage.py 加 References 精確區間規則（C1-C4 + `_REFERENCES_PRIORITY=14`），T1-T6 測試全綠。150 tests OK（`test-suite-m2-todo2.log`）。
- Todo 3：AGENTS.md / HANDOFF.md / README 更新（grep 命中）。
- Todo 4：dry-run（embedding rationale）+ 真實 API smoke 驗收通過（無 `Pipeline failed`、無 key 洩漏、`generated_by=llm`、22 coverage_chunk_ids 正常）。
- **已知非阻塞小瑕疵**：真實 run 的 `report` 尾端出現「材料來源清單/證據摘要/涵蓋 Chunk 列表」中文（unicode 跳脫）附加段落——記下，**以後調整 prompt 時一併處理**，不阻擋 M2。

## 執行方式（M2）
- **用了 route B**（規劃 agent 不自行觸發執行）：使用者手動把執行指令包貼給執行代理改 code，改完回報 → 規劃 agent 驗收。✅

## 流程決定（2026-09-04 使用者要求）
- **以後這類「要記錄的輸出/祥測 smoke 結果 log」：由執行代理跑並存檔到 `.omo/evidence/`**，規劃 agent 只負責驗收（讀 log、核對），不自已嘗試寫非 .omo/*.md 檔案。
- **git commit/push 一律由使用者（本人）親自做**：執行代理只改 code、跑測試、存 log、回報，**不執行任何 git commit**。規劃 agent 也不 commit。此慣例在計畫檔 Commit strategy 已標明，之後每個里程碑沿用。

## M3A（LLM planner）驗收現況（2026-09-04）
- **功能已驗收通過 ✅**：154 tests OK（`test-suite-m3a-llm-planner.log`）；無 key fake 驗證 + 真實 Gemini smoke（`smoke-m3a-llm-planner.log`）都過，`generated_by=llm`、`idea=None`、無 key 洩漏。
- **code 實作確認為 query-only**：`models.py` `SearchPlan.idea` 可選（`None`）、`create_llm_plan(query, client)`、`planning.PlanningError`、共用 `llm_evidence.generate_validated` 抽出且 synthesis 已遷移、`create_rule_based_plan(query)` fallback。ResearchIdea 模型保留。
- **待辦**：commit/push **由使用者做**（計畫檔 Commit strategy 有分兩次指令）。

## 架構已知現況（覆蓋包 coverage pack）
- 覆蓋包（`max_chunks_per_paper=6` + References 低權重）目前**沒有**作為逐篇筆記輸入；逐篇筆記輸入 = 該論文全部 chunk 用 `llm_input_cap=40` 截斷。覆蓋包只當綜合報告白名單來源，在現行 pipeline 實作下**近乎多餘**。
- **使用者裁示：視為已知、先不處理；之後討論架構時不把覆蓋包當作 pipeline 一環**。詳見 `.omo/notes/architecture-futures.md`「覆蓋包 coverage pack 現況」。

## M1 成果摘要
- pairwise eval：`overall_verdict: embedding better`（4/6 embedding 贏、1/6 lexical 贏 survey、1/6 平手）。證明 embedding 檢索整體優於 lexical，但有例外。
- 檔案：`pairwise_eval.py` + `test_pairwise_eval.py`（142 tests OK）+ embedding_retriever（cached 版）+ `--api-key-suffix`。

## 卡點
- 無阻塞。M2 完成。

## 大架構完成度（AGENTS.md line 43-47）
- 第 1-4 段（ResearchIdea → SearchPlan → OpenAlex → filter/rank/select）：**SearchPlan 是 rule-based**（`planning.create_rule_based_plan()`，不需 key）；LLM planner 未做（AGENTS.md：「future LLM planner」）。
- 第 5-10 段（PDF extraction → chunk → embedding RCS → assessment → notes → synthesis）：**全完成**。
- **主要缺口**：SearchPlan 目前只是「契約定義」，**未端到端串接**成「idea → plan → search → assess → 選 PDF → synthesis」的完整入口。目前 `search.py`（metadata 閱讀集）與 `pipeline.py`（PDF 證據）是分開的兩條路。

## M3 討論方向（候選，未定案）
- **search plan 端到端串接 / LLM planner**：把 `create_rule_based_plan()` 產生的 SearchPlan 接成完整入口（idea → 報告）。是否改用 LLM 產生 plan（會消耗 key），決定「search plan 是否配獨立 key」。這是大架構剩餘重點之一。
- **key 分配**：若 LLM planner 做起來，search plan 一個 key、RCS 之後的 LLM 流程一個 key；key 不夠可再創專案。（使用者 2026-09-04 提出）
- **輸出形式**：用一個 `main.py` 封裝，接收 query 輸入、輸出報告；參數（模型/top-k/路徑）直接寫死，不用打一堆 argument；可考慮互動式詢問 query。（使用者 2026-09-04 提出）
- **prompt 品質整理**：請 LLM 檢視之前幾次報告輸出有提到一些問題，之後一併調整 prompt（含 report 尾端中文瑕疵）。
- **資料來源策略**：詳見 `.omo/notes/architecture-futures.md`。OA 全文 / arXiv / SS / Unpaywall 來源、自動拿 OA PDF、下載成本、ranking/selection 角色。
- **Grobid vs PyMuPDF**：切 chunk 前濾參考文獻區塊的取捨。

## API Keys（.env）
- `GEMINI_API_KEY`：第一組 key，主要用於小測試。
- `GEMINI_API_KEY_2`：第二組 key，用於完整 smoke / 高消耗場景。`--api-key-suffix 2` flag 支援選擇。
- 註：pipeline 目前**不支援** `--api-key-suffix`（用既有 `GEMINI_API_KEY` 路徑），此為已知。

## 共通常識（勿重問）
- route B：不 spawn subagent、不執行 git、不印/抄 API key、不碰 `.env` 與 `data/papers/` 內容。
- 角色切換不必開新 session；同 session 可用 `@.omo/STATE.md` 重新載入狀態。
- 狀態以本檔 + 計畫檔「下一步行動卡」+ 計畫 amendment 為準。
- 重要步驟一律寫進行動卡/STATE.md，不靠對話記憶（防上下文壓縮）。
- 規劃 agent 只改 `.omo/*.md`，不碰 product code / tests / 其他目錄。
