# llm-planner — Work Plan (M3A)

## TL;DR (For humans)

**What you'll get:** 兩個一體的事：① 把 `SearchPlan` 輸入改成**只有 query**（像 PaperQA2 拿搜尋字串；把 `idea: ResearchIdea` 改為可選，簡化契約）；② 新增 **LLM 制定搜尋計畫** `create_llm_plan`——給一句 query，LLM 分析關鍵字、視角、產出多個查詢的 `SearchPlan`（仍用同一個 `SearchPlan` Pydantic schema）。下游 search pipeline 的消費方式不變。

**Why this approach:** AGENTS.md 明說「A future LLM planner will return the same SearchPlan contract」。使用者定案：輸入簡化為「只有 query」（embedding 也是 query 對 chunk 算相似度，自洽；PaperQA2 同理）。LLM planner 能從 query 分析該怎麼找。共用既有 `JsonGenerationClient`（Protocol）+ `GeminiJsonClient`，**開發期可用 fake client（不耗 API）測試，最後才用真實 key 驗證**——與 M2 的 fake encoder → 真實 smoke 一致。

**What it will NOT do:** 不換下游 search pipeline；不把 workflow 串成端到端入口（那是之後 `main.py` 的事）；不處理「抓 PDF」（那是 M3B）；不刪除 ResearchIdea 模型的定義（僅 `SearchPlan` 讓 `idea` 改為可選；rule-based 保留為 fallback，其簽名視需要調整）。

**Effort:** Medium-High（schema 契約小改 + 新函式 + prompt + 測試）
**Risk:** Medium — 動到 `SearchPlan` 資料契約 / `create_rule_based_plan` 簽名 / 既有測試；其餘純新增。需跑回歸確認下游無破壞。

**Decisions to sanity-check:**
0. **schema 改造**：`SearchPlan.idea` 由 `ResearchIdea`（必填）改為 **`ResearchIdea | str | None = None`**；`LiteratureReviewReport.idea` 同步改為 `ResearchIdea | str | None = None`（目前都放使用者 query 字串保留追溯，ResearchIdea 型別預留日後升級）。`create_rule_based_plan` / `create_llm_plan` 都把 query 寫入 `idea`（非 None）。模型 `ResearchIdea` 本身保留定義。grep 確認無下游把 `SearchPlan.idea` / `LiteratureReviewReport.idea` 當「必然是 ResearchIdea」讀取（僅 demo.py:53 寫入變數，相容）。
1. 新增函式：`create_llm_plan(query, client, ...) -> SearchPlan`（輸入為 query 字串；`SearchPlan.generated_by="llm"`）。
2. 共用 `JsonGenerationClient` 注入 → 測試注入 fake，驗證 prompt/schema/repair。
3. LLM 回傳結構直接對應 `SearchPlan` + `PlannedQuery`；schema 由 `client.generate_json(prompt, SearchPlan.model_json_schema())` **第二參數**送出（prompt 只描述結構，不把完整 schema 塞進字串）。驗證 `model_validate_json`，未知 ID/格式自動 repair。
4. **共用 LLM-JSON 驗證 helper 要「抽象抽出」**：synthesis 的 `_generate_validated` 綁定 `SynthesisError` + 私有 repair 函式，planning 不該 import synthesis 私有 helper（會造成 planning→synthesis 依賴反向）。計畫做法：把「call once → parse → repair-once」抽成共用函式（放 `llm_evidence.py` 或獨立 util），synthesis 與 planning 都用它；**repair 上限恰 1 次**（首次無效 → repair → 仍無效即拋錯，不無限重試）。
5. `generated_by` 欄位已是 `Literal["rule_based","llm"]` → 不需動（這就是為什麼 schema 改造只動 `idea`，不改 `generated_by`）。
6. 保留 rule-based 當 fallback（若 LLM plan 失敗／無 key）。
7. **新增 planning 自訂錯誤**（`planning.PlanningError`），拋錯類型明確，讓測試可 assert 具體 exception；不沿用 `LlmEvidenceError`（那是 evidence 領域）。真實 smoke 直接用 `GeminiJsonClient()` 注入給 `create_llm_plan`，與 pipeline 的 `--api-key-suffix` 無關。
8. prompt 只依 query 內容（+ 若 idea 有值再多用其欄位）產 plan，不引入外部知識（bounded）。

Your next move: 執行代理在獨立 session 實作（使用者貼指令包）；不做 `/start-work`。

---

## 下一步行動卡 (ACTION CARD — 執行 agent 每次開 session 第一件先看這塊)

> **單一事實來源。目前要做哪個 Todo、讀哪個指令包。避免轉述長命令。**
>
> **當前狀態：Todo 0（schema 改 query-only）→ Todo 1（新增 LLM planner 函式 + prompt）→ Todo 2（測試）→ Todo 3（文件）→ Todo 4（無 key 驗證 + 真實 smoke）**
>
> **⚠️ Commit 紀律：執行代理不執行任何 git commit/push——commit 一律由使用者（本人）親自做。** 你只改 code、跑測試、存 log、回報。
>
> 建立後續 Todo 細節如下。

---

## Todo 0: SearchPlan schema 改為 query-only（`idea` 改 `ResearchIdea | str | None`）

**What to do:**
- `models.py`：`SearchPlan.idea` 由必填改為 **`ResearchIdea | str | None = None`**；`LiteratureReviewReport.idea` 同步改為 `ResearchIdea | str | None = None`。確認 `queries`/`perspectives`/`rationale`/`generated_by` 其餘欄位不變。
- `planning.py`：`create_rule_based_plan(query)` / `create_llm_plan(query, client)` 都把 `idea=query`（使用者 query 字串，保留追溯來源）。保留 rule-based 為 fallback。
- 更新 `tests/test_planning.py`：既有 `assertIsNone(plan.idea)` 改為 `assertEqual(plan.idea, <query字串>)`。
- 檢查下游有無地方依賴「`SearchPlan.idea` / `LiteratureReviewReport.idea` 必填或必然 ResearchIdea」：grep `.idea` / `idea=` 使用（已確認僅 demo.py:53 寫入變數，相容；無以 `.title` 等讀取）。跑全套件回歸。

**Must NOT:**
- 不刪除 `ResearchIdea` 模型定義（型別預留）。
- 不改 `generated_by`（已是 `Literal["rule_based","llm"]`）。
- 不改下游 search/其他模組（除受影響簽名/測試）。

**Evidence of completion:**
- `grep -n "idea: ResearchIdea" literature_review/models.py` 顯示 `| None = None`。
- `grep -n "def create_rule_based_plan" literature_review/planning.py` 簽名已含 query。
- 全套件回歸綠（log 存 Todo 4）。

---

## Todo 1: 新增 LLM planner 函式 + prompt

**What to do:**
- 在 `literature_review/planning.py` 新增 `create_llm_plan(query: str, client: JsonGenerationClient) -> SearchPlan`（輸入為 query 字串）。
- 新增 `planning.PlanningError`（規劃領域自訂例外），`create_llm_plan` 失敗時拋它（非借用 `LlmEvidenceError`）。
- 新增 `build_llm_plan_prompt(query: str) -> str`：叫 LLM「只依這個 query 分析關鍵字與怎麼找、產出搜尋計畫」，要求 JSON 結構（queries: [{query, purpose}], perspectives, rationale），`generated_by="llm"`（若 `idea` 可選欄位有值再附上其欄位）。prompt 描述結構即可，**不把完整 JSON schema 塞進字串**——schema 由 `client.generate_json(prompt, SearchPlan.model_json_schema())` 第二參數送出。
- **抽出共用 LLM-JSON 驗證 helper**：把 synthesis 的「call once → parse → repair-once（上限恰 1 次）」邏輯抽成共用函式（放 `llm_evidence.py` 或獨立 util），synthesis 與 planning 共用——**不要**讓 planning import synthesis 的私有 `_generate_validated`/`_parse_llm_model`（避免 planning→synthesis 依賴反向）。
- `SearchPlan` 驗證：queries ≥1、perspectives ≥1、rationale ≥20 字（Pydantic 已約束）。首次解析失敗 → 送 1 次 repair prompt → 仍失敗 → 拋 `PlanningError`（不無限重試）。

**Must NOT:**
- 不改 `create_rule_based_plan` 的「fallback 角色」（保留；其簽名在 Todo 0 已調整）。
- 不刪 `ResearchIdea` 模型（保留供日後）。
- 不接進 pipeline / main（串接是之後的事）。
- 不 import synthesis 私有 helper 進 planning。

**Evidence of completion:**
- `grep -n "create_llm_plan" literature_review/planning.py` 命中。
- `grep -n "def create_llm_plan\|def build_llm_plan_prompt\|class PlanningError\|def <共用helper>" literature_review/planning.py`（或對應檔案）命中。
- 測試可注入 fake client 通過（見 Todo 2）。

---

## Todo 2: 測試（無 key）

**What to do:**
- 在 `tests/` 擴充 `test_planning.py`：
  - **T-llm-1** fake client 回傳合法 SearchPlan JSON → `create_llm_plan` 回傳 `generated_by="llm"`、queries/perspectives/rationale 正確、`plan.idea == query字串`（追溯來源）。
  - **T-llm-2** fake client 第一筆格式錯誤（缺 rationale / queries 空）→ 觸發 1 次 repair；隨後 fake client 第二筆回傳合法 → 仍回傳合法 plan（證明 repair-once 生效）。
  - **T-llm-3** fake client 連續兩筆皆無效 → 拋 `PlanningError`，且 fake client 恰被呼叫 2 次（證明「repair 上限恰 1 次、不無限重試」）。
  - **T-llm-4** prompt 符合 bounded：`build_llm_plan_prompt(query)` 的字串**包含** query 文字，且不包含任何未提供的外部查詢字串（驗證只依 query）。
  - **回歸**：既有 rule-based 測試（`test_planning.py` 現有 2 條，`idea` 斷言改為 `== query`）仍全綠。
- 用 fake client（不需 key）。

**Evidence of completion:**
- 全套件測試綠；log 存 `.omo/evidence/test-suite-m3a-llm-planner.log`（執行代理跑）。

---

## Todo 3: 文件

**What to do:**
- `AGENTS.md`：架構「future LLM planner」段落更新為「LLM planner 已實作，rule-based 保留為 fallback」；Commands 若有需要補一行。
- `HANDOFF.md`：加「Latest milestone: LLM planner (M3A)」。
- `README.md`：更新 planning 描述（若提及 rule-based-only）。

**Evidence of completion:**
- `grep -n "create_llm_plan\|LLM planner" AGENTS.md / HANDOFF.md / README.md` 命中更新處（避免用裸 `llm\|planner` 誤命中其它 llm 字詞）。

---

## Todo 4: 無 key 驗證 + 真實 API smoke

**What to do:**
- (a) 全套件 `uv run python -m unittest discover -s tests -v` 全綠（含 **Todo 0 的 schema 改造回歸**；log 存 `.omo/evidence/test-suite-m3a-llm-planner.log`）。
- (b) 無 key：fake client 跑範例 query 字串 → 驗證產生合法 `SearchPlan`（`generated_by="llm"`）。
- (c) 真實 API smoke（需 key）：new 一個 `GeminiJsonClient(model=...)`，注入 `create_llm_plan(query, client)` 對一個真實 query（例如 `"literature review agent"`）跑一次 → 看實際 LLM 產的 SearchPlan 是否合理、是否通過 schema 驗證、`generated_by="llm"`。
  - **注意**：LLM planner 的 client 由呼叫者注入自身 key 載入路徑（`GeminiJsonClient` 讀 `GEMINI_API_KEY`），**與 pipeline 的 `--api-key-suffix` flag 完全無關**——不需提 suffix。
- 記錄結果：報告產出合法 plan、無 `PlanningError`、無 key 洩漏（輸出無 key 字串）。

**Must NOT:** smoke 期間不改 code；不 commit 產物；不印 key。

**Evidence of completion:**
- `.omo/evidence/smoke-m3a-llm-planner.log`（執行代理跑存）。

---

## Commit strategy（由使用者執行，執行代理不 commit）
- 執行代理**不執行任何 git commit**；只改 code、跑測試、存 log、回報。
- 使用者（本人）驗收後親自 commit/push：
  - code commit：`git add literature_review/models.py literature_review/planning.py literature_review/llm_evidence.py tests/` → `feat(planning): query-only SearchPlan + LLM search-plan planner with injected client`
  - docs commit：`git add AGENTS.md HANDOFF.md README.md` → `docs: document LLM planner milestone`
- 注意：`.omo/evidence/` 被 gitignore，不 commit。

## 執行方式（route B）
- 不用 `/start-work`。使用者手動把本檔案 Todo 指令包貼給執行代理；執行代理改 code、跑測試存 log、回報 → 規劃 agent 驗收。smoke 記錄由執行代理存 `.omo/evidence/`。
