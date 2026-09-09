# M5e: 候選池組成升級(Gemini 維度詞庫 planner + 摘要篩選層 + gap follow-up)

- slug:`m5e-candidate-pool-upgrade`
- date:2026-09-09
- status:計畫草稿(動工前討論剩 4 項 TBD)→ 討論後定稿 → 待使用者核准
- 前置:M5b 完成驗收(此計畫動工排在 M5b 之後;M5b 執行中不阻塞本計畫撰寫)

## 動機

M5a 診斷定案:「1 篇 usable」是**天花板約束**不是閾值約束——K/K2/K3 三輪皆 1 篇,下載池裡的最高分論文就是天花板;降閾值非正解,**候選池組成才是杠杆**。M5e 用兩招打這個杠杆(2026-09-09 使用者發想 + 拍板):

1. **planner 多維度詞庫**:從 3 個互補維度(核心任務名稱/關鍵方法論/評測基準與主流對標)生成關鍵詞,組出可執行 query 集合——取代現況「自由多面向 1-5 條 query + purpose/perspectives」(M5a 已證實重合度 0.444 → 0.077,但詞彙多樣性仍受「同角度重組」限制)。
2. **LLM 摘要篩選層 + 全局 gap 分析**(新 LLM 層,整理在「search/rank 之後、下載之前」):對各 query 的 embedding rank 候選,一次給 LLM 標題/摘要/年份 → ① 逐篇**保留/剔除**(主動剔除,品質保證,剔除理由記錄)② **全局 gap 分析**(這批論文已涵蓋什麼?缺什麼關鍵拼圖——缺 Benchmark/方法?)→ 產 **2-3 條 follow-up queries** → 再搜尋再篩選 → 合併候選池 → 下載。

## 已拍板決策(使用者 2026-09-09)

- 模型:**Gemini 全包**(planner + 摘要過濾層皆 Gemini,品質優先);key 分配建議:摘要過濾接 key1(與 planner 同階段「檢索/選擇層」;key2 維持 notes/synthesis;RCS 維持 Ollama)——**key 細節動工前再確認**。
- 剔除策略:**LLM 剔除 = 主動決策錨**(保留品質好);保守原則(不確定就保留)撤銷,誤殺風險接受。
- 空池機制:候選池空/不足 → **不預先做**,「若出現沒論文的狀況再說」;`download_and_backfill` 的 shortfall 訊號已存在,屆時沿用。
- query 數:planner 第一次輸出 **3-5 條 query**(詞庫當素材、由 planner 內部組合一次輸出最終 query 表;`target_n = ceil(20/query_count)` 自動隨總 query 數含 follow-up 變動);全量 36 條否決(稀釋與成本爆炸迴避)。
- **follow-up = 固定流程的條件式步驟**(使用者 2026-09-09 裁示,非「臨時加點」):每次 run 皆執行 gap 分析,有缺口才產 0-3 條 follow-up;無缺口自然收斂。
- **分桶保多樣性選樣**(使用者 2026-09-09 採納):rank 後、送 LLM 篩選前,每 query 候選依三桶取樣(A 權威經典/B 最新前沿/C 跨領域啟發),避免 embedding top-N 全是同一類型論文(對應 K3 壟斷教訓)。

## Scope

- **改**:`planning.py`(planner prompt 維度模板 + 詞庫 + 3-5 query 產出)、新 `screening.py`(摘要篩選層:LLM 呼叫 + Pydantic 契約)、`main.py`(篩選層接線 + follow-up 迴圈)。M5a 的 `query_overlap` guard 沿用(planner 產出與 follow-up 皆過)。
- **不**:RCS/notes/synthesis 路徑;embedding 檢索與收縮平均;`filter_and_rank` 規則式過濾(保留為便宜前置:無 abstract/min citation/year);閾值;Ollama client。
- 契約:SearchPlan 保持靜態 query-only(見 TBD-3)。

## Todo 1 — planner prompt:維度模板 + 詞庫 + 3-5 query(planning.py)

- 新 prompt 結構(草稿,English):
  > Decompose the user's idea into 3 complementary research dimensions: (1) core task name, (2) key methodology, (3) evaluation benchmarks and mainstream comparisons. For each dimension, generate a keyword pool (short 2-4 word keywords for scholarly search APIs, not long sentences). Then compose 3-5 executable queries from these pools — each query must combine terms from different dimensions, avoid keyword overlap with other queries, and cover the idea from distinct angles.
- 產出契約不變:`SearchPlan.queries`(每 query 帶 purpose/perspectives);「詞庫」為 prompt 內部步驟(組合出最終 query 表後不另行輸出——TBD-1 定案)。
- `TOTAL_TARGET=20` 不變;`target_n = ceil(20/總 query 數)`(總 query 數 = plan.queries + follow-up,見 Todo 3)。
- M5a 重合度 guard 沿用;失敗重試/回第一版的規則不變。
- 測試:prompt 格式斷言(含 3 維度指示)、fake client 產 3-5 query 的驗證、重合度 guard 整合、`_print_plan` log 記錄詞庫摘要。

## Todo 2 — 摘要篩選層(screening.py + Pydantic 契約)

- 新模組 `literature_review/screening.py`:
  - `ScreenDecision(BaseModel)`:`paper_id`、`keep: bool`、`reason: str`(剔除必須有理由)、`paper_title`(追溯輔助,由程式填回)。
  - `GapAnalysis(BaseModel)`:`covered_areas: list[str]`、`missing_pieces: list[str]`、`follow_up_queries: list[FollowUpQuery]`(≤3 條;`query`、`target_gap`、`reason`)。gap 分析認為已充分時 `follow_up_queries=[]`(迴圈自然收斂,見 TBD-4)。
  - `screen_candidates(plan_queries_results, client)` → `ScreeningResult(decisions: dict[query_str, list[ScreenDecision]], gap: GapAnalysis, screened_at)`;call-once-parse-repair 共用 `llm_evidence.generate_validated`;模型 = key1 client(建議)。
  - prompt 輸入:每 query 的候選論文 `## Query {i}` 下逐篇 `### [{index}] {year} {title}\n{abstract}`——**只給標題/摘要/年份,不給 rank/分數**(避免誘導相對評分,比照 RCS 輸入精簡精神);LLM 回傳 index,程式查表填回真 paper_id + title。
  - **分桶選樣(先於 prompt)**:rank 後候選依三桶取樣再送 LLM(定案紀錄 TBD-2):
    - Bucket A 權威經典(~25%):排名前段(如 top 20%)且引用數極高 → 背景/經典 baseline。
    - Bucket B 最新前沿(~50%):排名前段且年份近(近 1-2 年)→ 最新術語/Benchmark。
    - Bucket C 跨領域啟發(~25%):排名中段(分位 40-70%)但引用數在前 30% → 相鄰領域交叉方法。
    - 桶定義用**排名分位 + 引用/年份條件**(不用絕對 embedding 值——不同 query 尺度不同);每 query 送 LLM 總量 ≈ 20-30 篇(tokens 估 ~100-150K/run,flash 一次 call);候選不足則全送。
- 測試:prompt 格式、fake client、index→paper_id 組合、剔除理由必填、follow-up ≤3 條驗證、gap 空清單收斂。

## Todo 3 — main.py 接線:篩選 + follow-up 迴圈(固定流程,至多 1 輪)

- plan → 每 query search/rank → 分桶選樣 → **篩選層**(保留/剔除 + gap 分析)→ 合併保留論文 → `download_and_backfill`(見下)→ 原 pipeline。
- **download_and_backfill 針對全部 query(原 3-5 條 + follow-up),一輪收**:所有 query 的「LLM 保留池」合併、共享去重(`already_downloaded` 既有 m3c 行為)、總下載 target = TOTAL_TARGET=20;`target_n` 依「總 query 數(含 follow-up)」計算——**使用者 2026-09-09 確認**。
- follow-up 迴圈(固定流程的條件式步驟,至多 1 輪):gap 分析產出 0-3 條 → 每條執行 search/rank → 分桶選樣 → 再篩選(同一 screening 函式)→ 保留論文併入合併池。gap 輸出空清單 → 迴圈自然收斂。
- follow-up 的 provenance:記錄於回傳物件/log(`generated_by="gap-follow-up"`、`reason=gap 內容`);**SearchPlan 契約不動**(物件保持 planner 輸出原樣)。
- dry-run/rule-based 模式:篩選層無 key 時跳過(候選直接進 download_and_backfill,維持現況行為)。

## Todo 4 — 測試與 smoke

- 單元測試全綠(? 個,比照先例);fake 端到端(dry-run 路徑不含 LLM);真實 smoke 視配額(使用者裁示的配額守則)。
- M5b 的 RCS B=1 預設、分帶定義等不受影響(本計畫不動 RCS)。

## Todo 5 — 真實 run 驗收(配額守則沿用)

- 對照基準:**m5b-real-final.log**(M5b Todo 6 的改後 run,免費,不另跑 baseline)。
- 指標:M5a 同套——子查詢重合度、進評分池組成(對比 m5b run)、usable、天花板 rel;新增——**follow-up 論文進池率**(follow-up 帶進的論文佔下載池比例)、剔除統計(剔除篇數/理由分布)、Gemini 429 次數。
- 指令:`printf 'literature review agent\n' | uv run --env-file .env python -m literature_review.main`;run 後 `data/papers/` 還原、log 存 `.omo/evidence/m5e-real-final.log`。
- 判定:usable ≥ 2(打破 1 篇天花板)且天花板 rel > m5b run 即通過;低於則記錄並決定後續(閾值/M5c 多來源)。

## 動工前討論定案紀錄(2026-09-09 全部拍板)

- **TBD-1 詞庫位置**:planner 一次輸出最終 **3-5 條 query**(詞庫為 prompt 內部步驟,不另行輸出;契約不動)。
- **TBD-2 篩選層輸入量**:分桶選樣(A~25%/B~50%/C~25%)後每 query 送 ~20-30 篇;**桶定義用排名分位 + 引用/年份條件,不採絕對 embedding 門檻**(跨 query 尺度不同);token 估 ~100-150K/run(flash 1M context 一次 call 內可容)。
- **TBD-3 follow-up 契約**:SearchPlan 保持靜態、follow-up 為執行期補充流(記錄 reason/provenance,契約純淨);使用者裁示 follow-up 是**固定流程的條件式步驟**(非「臨時」),只是不一定每次需要補。
- **TBD-4 迴圈輪數**:至多 1 輪(gap 分析輸出空清單即收斂)。
- **download_and_backfill**:針對**全部 query(原 + follow-up)一輪收**,合併保留池 + 共享去重,總 target = 20(使用者 2026-09-09 確認)。

## Must NOT

- 不動 RCS/notes/synthesis/embedding/閾值/Ollama client。
- 不動 SearchPlan 契約(定案:follow-up 為執行期補充流,不寫回物件)。
- 36 條 query 全跑(否決)。
- 不 commit;不碰 `.env`(不讀不印 key)、`data/papers/`(真實 run 落 temp/還原)、`.omo/drafts/`;不 spawn。
- 配額守則(M5b 計畫同款):日配額不足先暫停、短 429 sleep 重試一次、daily quota 立即停。

## Success criteria

1. planner 產 3-5 條跨維度 query + 重合度 guard 通過(測試 + log 證據)。
2. 摘要篩選層契約/測試全綠;fake smoke 通過;剔除理由完整。
3. 真實 run:M5a 同套指標 + follow-up 進池率;usable ≥ 2 且天花板 rel > m5b run 為通過(或如實記錄未達並給後續建議)。
4. `data/papers/` 還原。

## Commit strategy(使用者親做)

```powershell
git add literature_review/planning.py literature_review/screening.py literature_review/main.py tests/ HANDOFF.md
git commit -m "feat: M5e candidate pool upgrade with dimension planner, gemini screening layer, and gap follow-up"
git add .omo/STATE.md .omo/plans/m5e-candidate-pool-upgrade.md
git commit -m "docs: record M5e candidate pool upgrade plan and project state"
git push
```

(確切檔案以 `git status` 為準)

## 風險自審

| # | 風險 | 對策 | 殘餘 |
|---|---|---|---|
| P1 | 剔除誤殺(摘要差但全文好→ 進不了全文層) | 使用者裁示接受(剔除錨);驗收統計剔除理由分布 + follow-up 補救;「候選不足」走 shortfall 訊號 | 中 |
| P2 | 詞庫→query 組合讓 planner 輸出偏離契約(格式壞) | prompt 結構化 + `generate_validated` 驗證 + 測試(定案後鎖定) | 低 |
| P3 | 篩選層 token 成本/呼叫數超預期(free-tier 429) | 分桶選樣截斷(每 query ~20-30 篇)+ 每 query 一次 call + 配額守則;flash 1M context 單 call 容納 | 中低 |
| P4 | follow-up 與原 query 重合(重搜浪費) | M5a 重合度 guard 套用 + 共享去重池(already_downloaded) | 低 |
| P5 | 新 LLM 層讓 run 時間暴增(3-5 query + 2-3 follow-up × 搜尋/排序/篩選) | 每步皆現有組件;篩選一次 call;可接受為 milestone 成本 | 中低 |
| P6 | 驗收對照基準(m5b-real-final.log)未產出(Todo 6 掛帳中) | 若 M5b Todo 6 仍掛帳,M5e 驗收改用 m6-calibration 或延後對照 | 中 |
| P7 | 剔除太狠 → 可下載論文不足(distribution 空) | shortfall 訊號 + 使用者「再說」裁示;記錄統計供後續決策 | 中 |