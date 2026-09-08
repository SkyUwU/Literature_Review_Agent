# M5a:Planner prompt 多面向(query 面向多樣 + 關鍵詞多樣)

- slug:`m5a-planner-multifaceted`
- date:2026-09-08
- status:approved(使用者 2026-09-08 核准寫計畫;執行前需核可指令包)
- milestone:候選 L(planner prompt 改進);M5b(RCS 校準)接在其後

## 動機與診斷(2026-09-08 定案)

- **「1 篇 usable」是天花板約束,不是閾值約束**:K/K2/K3 三輪皆 1 usable——當輪下載池最高分論文 6.2-6.8 就是天花板;K3 論文層 rel 分布 2.1-6.5,consider 6→5 對該批 +0 篇、降到 4.5 才 +2 → **閾值不是杠杆,候選池組成才是**。
- **病根**:`build_llm_plan_prompt`(planning.py:51-66)只要求 1-5 條 query + purpose + perspectives,無「面向互補/詞彙多樣」指示 → LLM 子查詢語意高度重疊(真實例:「literature review agent」拆出的三條都是 lit-review+AI 詞組重組,無實質面向差異)→ OpenAlex(字面匹配搜尋)撈回同一批論文 → 池子窄。
- **解法是兩件事都要**:①**面向多樣**——各子查詢覆蓋不同角度(AI 文獻回顧工具 / LLM 研究助理 agents / 回顧與證據合成方法 / 評估框架),讓池子涵蓋不同主題區塊;②**關鍵詞多樣**——每個面向用同義詞/上下位/不同表述(因 OpenAlex 認字面,換詞才撈得到不同論文)。
- 現況 schema 已具備記錄欄位:`PlannedQuery.purpose`(models.py:24)與 `SearchPlan.perspectives`(models.py:39)——缺 prompt 指示、log 記錄與重合度驗證。

## Scope

- 只改:planning.py(prompt + 重合度驗證)+ main.py 或 run_end_to_end(log 記錄)+ tests。
- 不:碰 RCS/synthesis prompt、不調閾值/prior/retrieval policy、不改 schema(欄位齊備)。

## Todo 1 — prompt 改版 + 測試同步

- `build_llm_plan_prompt` 加指示(英文、精簡):
  - 各子查詢必須**覆蓋不同面向/角度**(explicit: avoid near-duplicate queries; each query should target a distinct facet of the researcher query);
  - 各子查詢用**不同關鍵詞/同義詞/上下位概念**表述(avoid reusing the same head terms across queries; prefer synonyms, hyponyms, and alternative phrasings);
  - purpose 要說明「該面向的資訊需求」;
  - **每個子查詢仍須是原始 query 的合理子面向**(stay on-topic——防過度發散拆出離題 query)。
- 同步 `tests/test_planning.py`:新增斷言(prompt 含新指示字樣);既有 prompt/plan 測試維持綠。

## Todo 2 — 兩兩 query 字面重合度驗證

- 新純函式(planning.py):`query_overlap(a, b) -> float`——lowercase + 去非字母數字 token 後的 Jaccard overlap(∩/∪);`max_query_overlap(queries) -> float`。
- **設計意圖(勿改用 embedding)**:目標是檢查「關鍵詞重疊」——OpenAlex 是字面搜尋引擎、認字面,所以驗證也用字面(確定性、零成本)。語言相似的 query(如「literature review agent」vs「systematic review automation」)語意可能高度相關,但那正是我們**想要**的「同義詞改寫」,不得被 flag;embedding 語意相似度用於論文 ranking 層,不用於此。
- LLM 產出 plan 後驗證:任兩兩 overlap > 0.5 → **重試一次**(repair 呼叫:要求子查詢覆寫為不同面向與詞彙);**重試後仍超標 → 接受第一版(LLM 原本產出的 plan)+ 紀錄重疊度警告**——不降級 rule-based、不採重寫版(重疊是品質問題不是可用性問題,plan 照用、流程不卡死);驗證的價值 = 診斷數據(評估 prompt 是否真的降低重疊)。
- **實作註記**:若 `generate_validated` 不支援自訂驗證條件(它只處理 schema 錯誤),則在 `planning.create_llm_plan` 包一層 retry 迴圈(最多次 = 1;重試前先保存第一版,重試後仍超標 → 回傳第一版)——**不得改動 `generate_validated` 本身**(共用函式,其他 caller 依賴其行為)。
- **閾值校準**:overlap 0.5 先以真實案例驗證——已知病例「literature review agent AI」vs「AI literature review agent tools」須被 flag(∩4/∪5=0.8);理想改寫「literature review agent」vs「systematic review automation」須不 flag(∩1/∪6≈0.17)。測出誤報/漏報時記錄並微調(0.4-0.6 範圍)。
- 測試:`test_planning.py` 加 overlap 函式單元測試 + 重合度高時觸發 repair 的 fake-client 測試。
- 註:rule-based fallback 不套用驗證(其查詢本來就同字根,屬備案)。

## Todo 3 — log 記錄每 query 的 purpose/perspectives

- `run_end_to_end`(main.py)選定 plan 後打印/寫入含每條 `query` + `purpose` 的清單與 `perspectives`(現況 log 只記 query 字串);真實 run 的 stdout/Log 需可追溯面向。
- 測試:現有 fake e2e 測試若斷言 log 格式,同步更新。

## Todo 4 — 真實 run 驗收(對比 K3 run)

- 同一 query「literature review agent」一次完整 run(LLM planner、key1/key2 既有配置)。
- **對比 K3 run**(`.omo/evidence/k3-after-real.log`)：
  - 子查詢字面重合度(本 run vs K3 三條的重合度);
  - 進評分池組成(K3:9 篇)——新論文比例、重疊論文;
  - usable 數(期望:天花板上升機會;不強求 >1,如實記錄)。
- 產物 `.omo/evidence/m5a-*.log/.json`;Langfuse health 200 前置確認;下載目標 temp(比照先例,data/papers 零變更)。
- 前置(執行代理):`curl http://localhost:3000/api/public/health` → 200 才跑。

## Must NOT

- 不調 include/consider/prior/top_k/max_chunks_per_paper 等任何 policy 值。
- **不因 query 重疊而降級 rule-based / 拋錯**——重疊時重試一次,仍重疊則接受第一版 + 紀錄(rule-based 僅在 LLM 呼叫失敗時使用,維持 M3A 原義)。
- 不改 LlmEvidenceAssessment/報告 prompt/schema。
- 不 commit;不碰 `.env`、`data/papers/`、`.omo/drafts/`;不 spawn;不跑額外 run。
- 不更動 rule-based fallback 行為(Todo 2 驗證僅套 LLM plan)。

## Success criteria

1. tests 全綠(新增 overlap + repair 測試)。
2. Todo 4 真實 run:子查詢重合度數據 < K3(明顯改善);進評分池組成記錄完整;usable 數如實(優先看天花板 rel,不看絕對數)。
3. 變更集僅限計畫內檔案;無 key 洩漏、無 `Pipeline failed`。

## Commit strategy(使用者親做)

```powershell
git add literature_review/planning.py literature_review/main.py tests/test_planning.py HANDOFF.md .omo/plans/m5a-planner-multifaceted.md .omo/STATE.md
git commit -m "feat: M5a multifaceted planner prompt with query-overlap validation and purpose logging"
git push
```