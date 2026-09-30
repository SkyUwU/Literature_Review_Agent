# S1-S5 評分線 + 輸出線調整計畫

- status:✅ **已核准(2026-09-12 使用者)待執行**(執行指令包 2026-09-13 交付)
- 唯一權威:本檔。⚠️ 執行期間不改計畫檔;裁示記於 STATE 架構裁示區。

## 動機(全部來自 2026-09-12 與使用者討論,決策均已確認)

1. **S1 輸出層統一 claim + 對照表**:C2c 實作時 `FutureDirection`(外部契約)保留 `supporting_chunk_ids`(由 `_expand_claim_ids` 從 claim 機械展開回 chunk)——使用者裁示:對外輸出一律 claim 標註 + 依賴 response 層 `claim_chunks` 對照表(「變體 A 完整版」)。
2. **S2 top-2 取樣改用 sub-query**:使用者輸入主 query 較籠統,embedding 難以抓「該論文在該搜尋詞面向最有用的 chunk」;改為用「下載該論文的 sub-query」做取樣 embedding。**評分仍用主 query**(LLM 理解更深,直接對主 query 評——使用者確認)。
3. **S3 論文分數 0.7×max + 0.3×mean**:純平均會讓「單段超硬證據」被普通 chunk 拉低(候選 Claude ③ 長期討論);加權 = 平滑版 max 採納(0.3 平均壓制單一 outlier 灌水)。零額外 LLM calls。
4. **S4 功能性 prompt 強化**:三類支持檢查清單(問題定義/痛點、技術機制、實驗評測)= **舉例性質不限於此**(使用者確認——避免窮舉綁死 LLM)+ **0 分錨點**(純常識/泛泛而談/無關描述)。
5. **S5 aspect 碎片化**:層級壓平(全部 #)時小節標題變 aspect → **確定性編號合併**(`X.Y…` → 頂層編號 `X`)先做;**LLM 歸類為後備**(觸發式標題→頂層章節對應表 +1 call,本次不做,觀察後再議——使用者確認分法);**觀察點已掛真實 run 收集項**。

## code 事實區(2026-09-12 已核對)

- `FutureDirection`(models.py:354-360):`supporting_chunk_ids: list[str]`(外部契約,由 `_expand_claim_ids` 展開);LLM 契約 `LlmSynthesisDirection.supporting_claim_ids`(models.py:426)。展開發生在 synthesis.py:833(`supporting_chunk_ids=_expand_claim_ids(...)`);`_expand_claim_ids` 定義 synthesis.py:142;使用處 grep 全掃:synthesis.py:129/149/193/833、models.py:360/420/463。
- `utility_score` 共 4 處 `Field(ge=1, le=10)`(models.py:225 `LlmFunctionalAssessment`、:265、:278 `EvidenceCitation`、:313 `FunctionalPaperScore`)——0 分需全部放寬 `ge=0`(LLM 可給 0 → 下游 citation/paper score 可含 0)。
- `sample_top_chunks_per_paper`(functional.py:199-231):per-paper 取樣,`query` 為單一參數;pipeline.py:214-219 傳主 `query`(pipeline `query` 參數 = 使用者輸入,非 sub-query)。
- `aggregate_functional`(functional.py:234-282):論文分數 = 純 mean(`round(...,1)`);`FunctionalScoringPolicy`(models.py:284 區)。
- `build_functional_prompt`(functional.py:46-87):任務句(utility for research idea)+ 反 hallucination + JSON 格式 + 先 rationale 後 score + utility≠query 字面 + 錨點(10/7-9/5-6/3-4/1-2)+ research idea + chunks;無三類支持清單、無 0 分。
- `top_level_section`(synthesis.py:434-451)層級無關版 + `_is_title_segment`(:459)三重判據;`_group_chunks_by_section`(:480)產 aspect 清單;無編號合併。
- 評分線路現況:`score_chunks_functionally` Gemini key2(Ollama 逃生門)、`FUNCTIONAL_BATCH_SIZE=8`、取樣 top-2(C2b/C2c 已驗收)。

## 設計決策

### A. S1 輸出層統一 claim + 對照表
- `FutureDirection` 新增/調整:`supporting_claim_ids: list[str] | None = Field(default=None)`(LLM 路徑填,不展開)。
- **LLM 路徑**(synthesis.py:833):移除 `_expand_claim_ids` 展開,改填 `supporting_claim_ids`(直接由 LLM 方向物件的 `supporting_claim_ids` 過渡);`_expand_claim_ids` 若無其他使用處即刪除(含 docstring 範例 :129/:149 同步)。
- **deterministic 路徑**(synthesis.py:175-194 `_make_direction`):**不變**——逃生路徑無 LLM、無 claim 系統,維持填 `supporting_chunk_ids`(這是先前「透明偏離②」的正規化:不是展開,是雙路徑各填各的欄位;`supporting_claim_ids` 留 None)。
- 因此 `FutureDirection` 兩欄位並存:`supporting_claim_ids`(LLM 路徑)+ `supporting_chunk_ids`(deterministic 路徑);chunk 追溯靠 response 層 `claim_chunks`(全域唯一,已存在)——LLM 產出的方向**不再出現 chunk 展開**。
- 影響掃描:`supporting_chunk_ids` grep 全項目(含 tests/README/HANDOFF)確認兩路徑語意;`supporting_claim_ids` 新增後 validation 同步(至少一欄非空)。

### B. S2 top-2 取樣用 sub-query(query_map)
- `sample_top_chunks_per_paper(..., query_map: dict[str, str] | None = None)`:per-paper 取樣 query = **`query_map.get(paper_id) or query`**(缺省與**空字串**都 fallback 主 query——pipeline 舊路 `paper_queries=None` 會產生 `{paper_id: ""}`,空字串必須防護;無 map 時行為與現況完全相同,舊測試不破)。
- pipeline.py:214-219 傳入現有 `paper_queries`(該論文由哪個 query 下載的映射,已存在)。
- **評分不變**:`score_chunks_functionally(query, ...)` 仍主 query(pipeline.py:221-227 零改動)。
- 設計註記:取樣 query 淺(embedding)需要 sub-query 協助定位面向;評分 query 深(LLM)直接對 idea——兩者分離是關鍵,不打破「功能性分數跨 query 同尺」。

### C. S3 論文分數 0.7×max + 0.3×mean
- `FunctionalScoringPolicy` 加 `max_weight: float = Field(default=0.7, ge=0, le=1)`;`mean_weight = 1 - max_weight`。
- `aggregate_functional`:單篇論文分數 = **`max_weight × max(scores) + (1 - max_weight) × mean(scores)`**,`round(...,1)` 維持;`n=1` 時 max=mean=該值(不變)。
- 零額外 LLM calls(純離線聚合)。0.7/0.3 為起始值,真實 run 數據後可調(policy 欄位,改一行)。
- **證據要求**:K3b 式離線重算對照——執行代理用代表性樣本(暫存測試資料)輸出 mean 版 vs 加權版「每篇分數 + 入選變化」對照表(存 `.omo/evidence/s3-aggregation-compare.md`)+ **threshold 6.0 校準建議**(加權後幾篇過 6.0、是否建議調)。

### D. S4 功能性 prompt 強化
- `build_functional_prompt` Scoring guide 重寫:
  1. **三類支持檢查清單(舉例性質)**:評分前「檢查該 chunk 是否至少提供下列任一類具體支持(**以下為舉例,不限於此**:① 問題定義/痛點支持——證實某個問題確實存在且未被妥善解決;② 技術機制可借鑒——具體演算法/架構/實作細節;③ 實驗與評測依據——可對標的基準數據集/Baseline/評估指標)」。
  2. **0 分錨點**:「純常識、泛泛而談或與該研究無關的描述 → 0 分」;1-2 分帶 = 「幾乎無貢獻(若無 0 分的噪音但貢獻極微)」。錨點更新:10 = 直接提供三類之一且填補缺口;7-9 = 實質推進核心面向;5-6 = 有用的背景(間接支持,含綜述/定位類);3-4 = 邊緣相關;1-2 = 極微貢獻;0 = 純常識/泛泛而談/無關。
- `utility_score` 4 處 `ge=1` → `ge=0`(models.py:225/:265/:278/:313)。
- rationale 指示同步:「若給 0 分,rationale 說明為何是常識/無關」。

### E. S5 aspect 碎片化 — 確定性編號合併
- 新函式 `merge_numbered_section(aspect: str) -> str`(synthesis.py,與 `top_level_section` 同區):aspect 開頭匹配 `^\d+(\.\d+)+`(多段編號)→ 取首段數字 `^\d+`(例 `2.1 Multi-Agent` → `2`;`2.1.3 X` → `2`);單段編號(`1 Introduction`)/無編號(`Background`)→ **原樣保留**。
- 套用點:`_group_chunks_by_section` 產 aspect 時正規化(每 chunk 的 aspect 都過 `merge_numbered_section`)——清單層與 claim aspect 一致。
- 產物是裸數字 aspect(如 `2`)——可接受(跨論文「第 2 章」位置可比、碎片最少);**可讀性觀察 **掛真實 run。
- **LLM 歸類後備本次不做**:若真實 run 觀察顯示合併後仍碎片化(無編號大綱式小節過多)→ 下個里程碑(觸發式標題→頂層章節對應表,單一 call)。

## Todo

- **Todo 0 — 契約改版(S1+D)**:models.py(`FutureDirection.supporting_chunk_ids`→`supporting_claim_ids`、`utility_score` 4 處 ge=1→ge=0、`FunctionalScoringPolicy.max_weight=.7`)+ synthesis.py(S1 展開移除、`_expand_claim_ids` 刪除/同步 docstring);tests 遷移(test_models/test_synthesis 等,`supporting_chunk_ids` 零殘留 grep)。
- **Todo 1 — S2 取樣 query_map**:functional.py `sample_top_chunks_per_paper(..., query_map=None)` + pipeline.py 傳 `paper_queries`;測試:有 map(不同 sub-query → 不同取樣結果)/無 map(行為與現況一致)。
- **Todo 2 — S3 聚合加權**:functional.py `aggregate_functional` 公式 `max_weight×max+(1-max_weight)×mean`;測試([8,3]→7.25、n=1 不變、policy 可調)。
- **Todo 3 — S4 prompt 強化**:functional.py `build_functional_prompt`(三類舉例檢查清單 + 0 分錨點 + rationale 指示);測試(prompt 文字含三類/0 分、model `utility_score=0` 驗證通過)。
- **Todo 4 — S5 編號合併**:synthesis.py `merge_numbered_section` + `_group_chunks_by_section` 套用;測試(`2.1 X`→`2`、`2.1.3 X`→`2`、`1 Introduction` 不變、`Background` 不變、aspect 分組一致性)。
- **Todo 5 — 測試/驗收 + 證據**:全套件全綠(log `.omo/evidence/s1s5-tests.log`);S3 離線重算對照 `.omo/evidence/s3-aggregation-compare.md` + threshold 6.0 校準建議;fake e2e 全鏈路(無 key);真實 run 掛帳不變(含 aspect 碎片化觀察點)。

## Must NOT

- 不改變評分 prompt 的「utility for research idea / 非 query 字面」核心語意(只強化錨點與檢查清單)。
- 不改 notes prompt / claim-N / 章節餵入(C2c 已驗收行為)。
- 不加 LLM 歸類後備(S5b)於本次——只做確定性合併 + 觀察。
- 不跑真實 run(掛帳);不碰 .env / data/papers/。

## Success criteria(驗收 1-6 全過)

1. S1:LLM 路徑的 `FutureDirection` 只填 `supporting_claim_ids`(不展開);`_expand_claim_ids` 不再被使用;deterministic 路徑維持 `supporting_chunk_ids`;兩欄至少一欄非空(測試 + grep 驗證)。
2. S2:取樣用 query_map 的 sub-query、評分仍主 query;無 map 時行為同現況(測試)。
3. S3:聚合 = `0.7×max+0.3×mean`(policy 可調)+ 離線重算對照 md + threshold 校準建議(證據)。
4. S4:prompt 含三類支持(舉例不限)+ 0 分錨點;`utility_score` ge=0 驗證通過(測試)。
5. S5:`2.1 X`→`2` 等合併規則正確(測試);aspect 碎片化觀察掛真實 run 收集項。
6. 測試數增加、既有全綠(335 → N);`_expand_claim_ids` 死亡碼零殘留;兩路徑(LLM claim / deterministic chunk)各一測試。

## 風險表

| # | 風險 | 對策 |
|---|---|---|
| P0 | **S1 契約改動誤傷 deterministic 路徑**(逃生路徑無 claim 系統,直接填 chunk) | 雙欄位並存:supporting_claim_ids(LLM 路徑)+ supporting_chunk_ids(deterministic 路徑);至少一欄非空驗證 |
| P1 | S1 契約變更波及下游(supporting_chunk_ids 使用處散落) | Todo 0 全項目 grep 掃描 + 驗證兩路徑語意 + 至少一欄非空 validation |
| P2 | S2 query_map 影響舊測試 / 空字串 query | 缺省與空字串都 fallback 主 query(`q or query`)、無 map 行為不變;測試覆蓋兩路 + 空字串路 |
| P3 | S3 0.7 權重過度拉抬單一高分 chunk | 0.3 mean 壓制 + 離線重算看入選變化;0.7 為起始值(policy 可調) |
| P4 | S4 0 分拉扯 mean | 合理(純噪音本該拉低);rationale 強制說明;離線重算含 0 分樣本 |
| P5 | S5 裸數字 aspect 可讀性/合一性不達預期 | 先觀察(真實 run 收集項);不達 → LLM 後備(下個里程碑) |
| P6 | 真實 run 未跑、實際分數分布未知 | S3 離線重算以代表性樣本模擬;threshold 建議標「待真實 run 複核」 |

## Commit strategy(使用者親做;確切檔案以 git status 為準)

```powershell
git add literature_review/models.py literature_review/synthesis.py literature_review/functional.py literature_review/pipeline.py tests/ HANDOFF.md README.md
git commit -m "feat: S1-S5 scoring line and output line adjustments"
git add .omo/STATE.md .omo/plans/s1-s5-scoring-and-output-line.md
git commit -m "docs: record S1-S5 plan approval and project state"
git push
```

## 下一步行動卡

1. 使用者核准本計畫 → 規劃 agent 交付執行指令包(route B,RESUME 同執行代理 session `ses_fa2630750ffesFrcrOjP3fJ5RT`;明示真實 run 掛帳、勿動 .env)。
2. 執行代理照 Todo 0-5 → 回報 → 規劃 agent 驗收(criteria 1-6)。
3. 核准後使用者 commit(上述兩筆 + push)。
4. 真實 run(commit 後)最終線路一次跑:評分 Gemini key2+B=8、notes key2、報告 key3;收集:C2b 項 + C2c 項 + **key call 統計 + aspect 碎片化觀察(S5 驗收依據)**。