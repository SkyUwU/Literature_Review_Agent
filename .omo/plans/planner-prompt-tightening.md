# planner-prompt-tightening — LLM planner 子查詢收緊（3-4 條、Keyword Length、repair 預算化）

## TL;DR (For humans)

外部 AI 對 `build_llm_plan_prompt` 提出改進；使用者與我在 2026-09-28 逐項討論後定案下列變更（**全部已在計畫前討論確認**）：

1. **`SearchPlan.queries` 收緊為 `min_length=3, max_length=4`**（`models.py:38`，現為 1..10）。schema 自己握上下限，planner 不再傳 `max_queries`。
2. **prompt 新增 Keyword Length (Strict)**：每個 sub-query 必須是 2-4 個關鍵字的短詞組；**範例明示 `For example (good): ...` / `For example (bad): ...`**（使用者要求標明是範例）。理由用保守說法「scholarly search APIs match short keyword phrases more precisely than verbose sentences」——**不採**外部 AI「長句會讓 boolean 匹配失敗」的未驗證宣稱。
3. **`"queries"` 指示改 `exactly 3 to 4 items`**；既有斷言句**全部保留原地**（tests 依賴）。
4. **repair 預算化**：`create_llm_plan(..., enable_overlap_repair=True)`；整個 planning 階段**最多 2 次 LLM 呼叫**（正常 1 次 + 至多 1 次 repair，schema repair 與 overlap repair 合計）；用 counting wrapper 實現；schema repair 吃滿預算 → overlap repair 直接跳過。
5. **overlap repair 接受標準放寬**：改寫後 ≤0.5 **或** max overlap 比原案**嚴格下降**即接受；否則保留原案＋warning。**`QUERY_OVERLAP_THRESHOLD` 維持 0.5**（0.4 會常態觸發、0.55 幾乎無差異——已用 Jaccard 值域論證，不改）。
6. **`create_rule_based_plan` 加下界防護**：`max_queries < 3` → `ValueError`；候選 4 條不變。

純 code+test+docs 里程碑，零 Gemini（測試全 fake）。**contract 變更**＝`SearchPlan.queries` 下限 1→3（rule-based 4 條不加即合規）；`_LlmScreeningOutput` 等一律不動。

**不會做**：改 `QUERY_OVERLAP_THRESHOLD`、改 overlap repair prompt 內容本文、動 reporter/screening/functional、真實 run、`create_llm_plan` 以外的 planning API（M5d plan-review loop 列候選不入此）。

**執行偏離（2026-09-28，已記錄於 HANDOFF Follow-up）**：計畫假設 code+tests 只動 `models.py`/`planning.py`/`test_planning.py`；Todo 4 實行時 schema 收緊使 `tests/test_main.py` 的 `FakePlanClient(2)` 產出無效 plan → 6 支測試改用 `FakePlanClient(3)`／調整 follow-up payloads／`test_target_n_follows_ceil_formula` pairs 改 `(3,7),(4,5)`。commit 指令因此多含 `tests/test_main.py`（見下）。除此之外無其他偏離。

---

## Scope

**In**
- `literature_review/models.py`：`SearchPlan.queries` → `Field(min_length=3, max_length=4)`（:38）；docstring 補「sub-queries 3-4 + short keyword phrases」。
- `literature_review/planning.py`：
  - `create_rule_based_plan(query, max_queries=5)`：加 `if max_queries < 3: raise ValueError(...)`（:48 前段）。
  - `build_llm_plan_prompt(query)`：**移除 `max_queries` 參數**（:84）；加 Keyword Length (Strict) 段（含標明 good/bad 範例）；`"queries": exactly 3 to 4 items`（:106-108 改寫）；保留 :91-104 既有斷言句逐字；保守理由句。
  - `create_llm_plan(query, client, *, enable_overlap_repair=True)`：**移除 `max_queries`**（:161-162）；加 `_BudgetedClient`（count `generate_json`，超過 2 次 raise `PlanningError`）；first call 後檢查 `calls` 已滿 → 跳過 overlap；overlap repair 接受＝≤0.5 **或** strict 改善；否則保留原案＋warning；docstring 更新（threshold 定位 = safety-net）。
- `tests/test_planning.py`：
  - `valid_plan()` / `overlapping_plan()` fixture 改 3 條（各行需越過 schema min 3）。
  - `test_plan_respects_max_queries_and_keeps_them_unique`（:24-29）`max_queries=2` → **3**，並新增 `max_queries=2 → ValueError` 測試。
  - `LlmPlanOverlapRepairTests` / `LlmPlanTests` 全數配合新 fixture 與新接受規則；新增：改善未達標→接受、(d) flag 關閉→1 call、(e) schema repair 吃滿→overlap 跳過、(f) 2-query 回覆→一次 schema repair、prompt 文字（3 to 4 / Keyword Length / For example (good)/(bad)）。
- 文件：AGENTS.md（規劃段一句）、HANDOFF.md（里程碑列＋follow-up 節）、`.omo/STATE.md`（里程碑表＋測試數）。

**Out**
- `QUERY_OVERLAP_THRESHOLD` 數值（維持 0.5，只補 docstring/framing）。
- `_build_overlap_repair_prompt` 內容本文（保留 :146-158 現文）。
- `SearchPlan.idea`／`perspectives`／`rationale` schema、`PlannedQuery` schema。
- M5d plan-review loop、任何搜尋/篩選/評分/抽取/下載/報告層。
- 真實 run、Gemini 驗證（F4 仍擱置；本里程碑後 F4 可用收緊後的 planner）。

---

## Verification strategy

每 Todo＝改檔＋對應測試；最終完整 suite 全綠（455 為底線、以實測為準）、`git status --short` 只含預期檔、無 key 洩漏。額外的 prompt 檢查以 `python -c` 建立 `build_llm_plan_prompt` 字串 grep 斷言句（不燒網路）。**過渡紅**：Todo 1-3 動 schema/fixture，suite 完整回綠要到 Todo 4——勿中途判敗。

---

## Execution strategy

```
Todo 0（預檢）→ Todo 1（schema + rule-based 防護）→ Todo 2（prompt 重寫）
→ Todo 3（create_llm_plan budget 化）→ Todo 4（測試）→ Todo 5（文件 + 最終驗收 + commit）
```

**Rollback**：變更僅限 `models.py`、`planning.py`、`test_planning.py`，`git checkout -- <這三檔>` 即可完整復原；無遷移資料。

---

## Todos

- [ ] 0. **預檢**

  **References**
  - `.omo/STATE.md:8`（455 tests 全綠）
  - `git status --short`（預期乾淨；後續由使用者 commit）

  **Implementation**
  1. `git status --short` 記錄底線。
  2. 跑完整 suite 至 `.omo/evidence/planner-prompt-tightening-todo0.log`，記錄基準測試數（預期 455）。

  **Acceptance**
  - 底線記錄完整；測試數以實測為準。

- [ ] 1. **schema 收緊 + rule-based 下界防護**

  **References**
  - `models.py:38` `queries: list[PlannedQuery] = Field(min_length=1, max_length=10)`
  - `models.py:30-35` SearchPlan docstring
  - `planning.py:48-81` `create_rule_based_plan`（:63-70 迴圈 / :69 `len(queries) == max_queries` 斷）
  - `tests/test_planning.py:24-29`（`max_queries=2`）

  **Implementation**
  1. `models.py:38` → `Field(min_length=3, max_length=4)`；SearchPlan docstring 補一句「sub-queries must be 3-4 short keyword phrases」。
  2. `planning.py` `create_rule_based_plan`：函式開頭加
     ```python
     if max_queries < 3:
         raise ValueError("max_queries must be >= 3 to satisfy the SearchPlan lower bound.")
     ```
  3. 不變：候選 4 條（:55-60）、預設 `max_queries=5`、去重 :63-67。

  **Acceptance**
  - `SearchPlan(queries=...)` 少於 3 條 raise；rule-based 4 條不加即合規；`max_queries=2` → `ValueError`。
  - `test_plan_respects_max_queries_and_keeps_them_unique` 改 3 後綠；新增 ValueError 測試。

  **QA**
  - happy：`create_rule_based_plan(q)` 維持 4 條；`max_queries=3` 恰 3 條。failure：`max_queries=2`→ValueError、`max_queries=1`→ValueError；空/短 query 既有行為不變。已知限制：agent-code 之外沒有其他 callers 傳 <3（`main.py:114` 與 tests 均只用預設或 ≥3）。

- [ ] 2. **`build_llm_plan_prompt` 重寫（移除 max_queries + Keyword Length + 3-4 items）**

  **References**
  - `planning.py:84-114` 現行 prompt（:91-94 三維度、:98-104 斷言句、:106-108 queries 指示）
  - `tests/test_planning.py:120-137` 兩個 prompt 測試（字串斷言清單）

  **Implementation**
  1. 簽名 → `def build_llm_plan_prompt(query: str) -> str`。
  2. `:177`（`create_llm_plan` 內部）呼叫改 `build_llm_plan_prompt(query)`。
  3. 重寫 prompt，**逐字保留既有下列字串**（tests/回歸依賴，不得改詞）：
     - `"Each sub-query must target a distinct facet"`
     - `"avoid near-duplicate queries"`
     - `"do not reuse the same head terms"`
     - `"synonyms, hyponyms, and alternative phrasings"`
     - `"Stay on-topic"`
     - `"3 complementary research dimensions"`、`"(1) the core task name"`、`"(2) key methodology"`、`"(3) evaluation benchmarks"`
     - `"keyword pool for each dimension"`、`"combine terms taken from different dimensions"`
  4. 新增 Keyword Length (Strict) 段（插在三維度規則後）：
     ```text
     - Keyword Length (Strict): every sub-query must be a short keyword phrase of 2-4
       keywords - scholarly search APIs match short keyword phrases more precisely than
       verbose sentences. For example (good): "multi-agent retrieval planning";
       For example (bad): "how can we build a literature review agent that uses retrieval
       augmentation to write a survey for users".
     ```
  5. `:106-108` 改：
     ```text
     - "queries": exactly 3 to 4 items, each with a "query" string (at least 3
       characters) and a "purpose" string (at least 10 characters explaining the
       information need that query covers);
     ```
  6. 傾印：`python -c "from literature_review.planning import build_llm_plan_prompt; print(build_llm_plan_prompt('literature review agent'))"` 存 `.omo/evidence/planner-prompt-sample-todo2-<timestamp>.log`，人工對照斷言句與段落順序。

  **Acceptance**
  - prompt 含 3-4 items 指示、`Keyword Length (Strict)`、`For example (good):` ／ `For example (bad):`；既有斷言句逐字在；`max_queries` 不再出現（`grep -n max_queries planning.py` 僅 rule-based 簽名與 guard）。

  **QA**
  - happy：短 query、長 query 皆正常組 prompt。failure：斷言句遺漏→兩個既有 prompt 測試紅。已知限制：prompt 本文即權威、傾印 log 僅供對照（比照 screening-prompt 先例）。

- [ ] 3. **`create_llm_plan` budget 化（≤2 calls、改善接受、flag）**

  **References**
  - `planning.py:161-216` `create_llm_plan`（first :183-192、overlap 檢查 :193-194、repair :196-216）
  - `llm_evidence.py:207-227` `generate_validated`（:223 initial、:226-227 單次 repair；repair call 若 raise 會向上傳）
  - `planning.py:19-21` `PlanningError`、`:128-133` `_parse_plan`、`:146-158` `_build_overlap_repair_prompt`

  **Implementation**
  1. 簽名 → `def create_llm_plan(query: str, client: JsonGenerationClient, *, enable_overlap_repair: bool = True) -> SearchPlan`；移除 `max_queries`。
  2. 新增私有 budget client：
     ```python
     class _BudgetedClient:
         """Wrap a planning client so the whole plan stage costs at most ``budget`` calls."""
         def __init__(self, client: JsonGenerationClient, budget: int) -> None:
             self.client = client
             self.budget = budget
             self.calls = 0
         def generate_json(self, prompt: str, schema: dict | None = None) -> str:
             if self.calls >= self.budget:
                 raise PlanningError("Planning LLM call budget exhausted after 2 calls.")
             self.calls += 1
             return self.client.generate_json(prompt, schema)
     ```
  3. `create_llm_plan` 內改：
     - `budgeted = _BudgetedClient(client, 2)`；`first` 與 overlap repair 的 `generate_validated` 一律吃 `budgeted`。
     - `first` 後：`if max_query_overlap(...) <= QUERY_OVERLAP_THRESHOLD or budgeted.calls >= 2 or not enable_overlap_repair: return first`（先跳過，避免不必要呼叫）。
     - overlap repair 包 `try/except PlanningError`（`_BudgetedClient` 爆預算 ／ repair parse 失敗皆落此 → keep first + warning）。
     - 接受標準：`new = max_query_overlap(repaired)`；`new <= QUERY_OVERLAP_THRESHOLD or new < max_query_overlap(first)` → 回 repaired；否則 keep first + warning（沿用 :212-215 語氣）。
  4. docstring：改寫 repair 段落——schema 與 overlap repair「合計」≤1 次、≤2 calls；`QUERY_OVERLAP_THRESHOLD` 為 safety-net（數值維持 0.5，0.4 會過度觸發、0.55 對 Jaccard 值域幾乎無差）；接受含「stricly improved」。
  5. `:16` `QUERY_OVERLAP_THRESHOLD = 0.5` 補 docstring 註解（safety-net framing，不改數值）。

  **Acceptance**
  - 單測可證：正常 plan =1 call；schema repair 吃掉預算後 overlap 不再發（總 2）；overlap repair 成功 ≤2；flag=False 時絕不發 overlap call。
  - 回歸：`test_t_llm_3/4`（:159-171）行為不變。

  **QA**
  - happy：valid plan 立即 return（1 call）；overlap 有改善但未達標 → reparired（2 calls）；無改善 → first（2 calls）。failure：第一次輸出壞 JSON→ repair（2 calls）→ overlap 跳過；repair 起 call 爆預算 → PlanningError → keep first。已知限制：budget 是硬上限，不是 soft（爆預算即放棄 repair，符合「合計 ≤1 repair」定案）。

- [ ] 4. **測試更新 + 新增**

  **References**
  - `tests/test_planning.py:44-55` `valid_plan`（1 條，需改 3）
  - `tests/test_planning.py:79-88` `overlapping_plan`（2 條，需改 3）
  - `tests/test_planning.py:91-116` `LlmPlanOverlapRepairTests`
  - `tests/test_planning.py:139-171` `LlmPlanTests`

  **Implementation**
  1. `valid_plan()` → 3 條互不重疊（max overlap 0）：
     ```python
     {"query": "literature review agent", "purpose": "Find core papers on literature review agents."},
     {"query": "systematic survey automation tools", "purpose": "Find automation tooling for systematic surveys."},
     {"query": "comparative evaluation benchmarks", "purpose": "Find evaluation benchmarks for comparative surveys."},
     ```
  2. `overlapping_plan()` → 3 條、max overlap 0.8（>0.5）：
     ```python
     {"query": "literature review agent AI", ...},
     {"query": "AI literature review agent tools", ...},
     {"query": "agent-based literature review systems", ...},
     ```
  3. repaired（達標）查詢組＝兩兩 max ≤0.5（例：`"AI systematic review automation"`、`"LLM research assistant agents"`、`"survey quality evaluation benchmarks"`）。
  4. `test_plan_respects_max_queries_and_keeps_them_unique`：`max_queries=3`；新增 `with self.assertRaises(ValueError): create_rule_based_plan(..., max_queries=2)`。
  5. `LlmPlanOverlapRepairTests` 更新/新增：
     - 達標接受（既有 :91-103 改 3 條 fixture，仍 assert ≤2 prompts + max ≤0.5）。
     - 無改善保留原案（既有 :105-116；repaired 用三條、其 max_overlap **必須 ≥ 原案 0.8**（等值或更糟），否則會在新「strictly improved」接受規則下被誤判成改善而接受 → `plan.queries[0].query` 仍為原案首條、2 calls）。
     - 新增「改善未達標仍接受」：repaired 查詢組 max overlap 0.6（>0.5 但 < original 0.8），assert `plan is repaired`（以 queries 首條判定）+ 2 calls。
     - 新增「enable_overlap_repair=False」：僅 1 call、overlap 不修。
     - 新增「schema repair 吃滿預算 → overlap 跳過」：responses = [壞 JSON, valid_plan()]，assert 2 calls、回傳 valid plan、queries=3。
     - 新增「2-query 回覆 → 一次 schema repair」：responses = [2 條 plan（invalid schema）、valid_plan()]，2 calls。
  6. `LlmPlanTests`：`test_t_llm_1` 維持 `plan.queries[0].query == "literature review agent"`（fixture 首條不變，:147 既有斷言）；prompt 測試新增三斷言：`"exactly 3 to 4 items"`、`Keyword Length (Strict)`、`For example (good):` / `For example (bad):`。
  7. `tests/test_main.py`（偏離，見頂部紀錄）：`FakePlanClient(2)` → `(3)`（6 支：dry-run-with-screen、no-screen legacy merging、papers collect metadata、screen full run、default planner is llm…）、follow-up payloads 插空結果、`test_target_n_follows_ceil_formula` → `(3,7),(4,5)` 子測且僅整除時斷言總數 20。
  8. 完整 suite 至 `.omo/evidence/planner-prompt-tightening-todo4.log`，記錄測試數（預期 >455）。

  **Acceptance**
  - 上述情境逐項綠；既有兩次 repair 斷言數（`len(prompts) == 2`）全數維持；suite 全綠且測試數>455。

- [ ] 5. **文件同步 + 最終驗收 + commit 指令**

  **References**
  - `AGENTS.md`「Live vs legacy path map」規劃段／「Current architecture」SearchPlan 敘述
  - `HANDOFF.md` 里程碑表（末列 screening-prompt 之後）＋ Follow-up 節
  - `.omo/STATE.md` 現況快照（<8）＋里程碑表尾

  **Implementation**
  1. AGENTS.md：planner 敘述一句補「queries 3-4 短關鍵詞、Keyword Length (Strict)、planning 全段 ≤2 LLM calls」。
  2. HANDOFF.md：里程碑表加列 `Planner prompt tightening`；加 Follow-up 節（2026-09-28）。
  3. STATE.md：現況快照測試數（Todo 4 實測）＋里程碑表加列。
  4. 最終驗收：完整 suite（`.omo/evidence/planner-prompt-tightening-final.log`）、`git status --short`、`git grep -n "AIza"` 無 key 洩漏、prompt 傾印對照。

  **Acceptance**
  - suite 全綠；git status 只含預期檔（models.py/planning.py/test_planning.py/test_main.py/AGENTS/HANDOFF/STATE/.omo/plans/.omo/evidence）；無 key 洩漏；commit 指令一次列齊。

---

## Commit strategy（使用者親做）

```bash
# code+tests 一筆
git add literature_review/models.py literature_review/planning.py tests/test_planning.py tests/test_main.py
git commit -m "feat(planning): tighten planner to 3-4 short keyword sub-queries with a 2-call repair budget"
# docs 一筆
git add AGENTS.md HANDOFF.md .omo/STATE.md .omo/plans/planner-prompt-tightening.md
git commit -m "docs: sync AGENTS/HANDOFF/STATE with planner prompt tightening milestone"
git push
```

---

## Success criteria

- `SearchPlan.queries` = 3..4；rule-based 4 條合規、`max_queries<3`→ValueError。
- prompt：Keyword Length (Strict) 2-4 關鍵字＋標明 good/bad 範例、`"queries": exactly 3 to 4 items`；既有斷言句逐字保留；無 `max_queries`。
- `create_llm_plan` 全程 ≤2 LLM calls（schema+overlap repair 合計 ≤1）；overlap repair 接受＝≤0.5 或 strict 改善；`enable_overlap_repair=False` 零額外呼叫；預算爆掉 keep first＋warning。
- thresholds 0.5 不動（只補 framing 註解）。
- 455 起全套 tests 全綠（新增數以實測為準）；AGENTS/HANDOFF/STATE 同步；無 key 洩漏；零 Gemini 開銷。