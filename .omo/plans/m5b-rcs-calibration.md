# M5b: RCS 校準改版(schema 順序 + A9/A10 + 輸入精簡 + 閾值決策)

- slug:`m5b-rcs-calibration`
- date:2026-09-09
- status:已討論(動工前討論 5 項完成拍板:① schema 順序 ② A9 ③ A10 ④ 輸入精簡 ⑤ 閾值)→ 待使用者核准
- 前置:M6 已完成(RCS on Ollama、B=4、校準數據)、(使用者 commit 已完成)

## 動機(5 個病徵,2026-09-09 使用者發現/討論定案)

1. **M4 I 項實作不完整**:prompt 說「先理由後給分」,但 `LlmEvidenceAssessment` 的 schema 欄位順序是 `chunk_id → summary → relevance_score → evidence_quality_score → rationale`(models.py:183-187)——LLM 自迴歸照 schema 生成,實際**先出分數、後補理由**=「先猜答案、再編理由」,被 schema 順序架空。
2. **10 分廉價**:M6 校準 32 chunks 中 **15 個 rel=10**(快一半);Ollama 天花板 9.2 vs Gemini 6.3、include 2 + consider 5(7/10 進池)→ 尺度明顯上移、錨點遵從偏寬。「10 = 直接回答核心」該是稀有等級,現在變常態。
3. **RCS 輸入冗餘**(使用者 2026-09-09 提出):prompt 送 5 欄位(chunk_id/paper_id/page_start/page_end/text)——評分只需 text;LLM 複製長 chunk_id 正是 **B=8 竄改 id 的病根**;Langfuse trace input 記錄完整 `EvidenceRetrievalResponse`(含 rank/score/matched_terms)也是無關資訊。
4. **缺客觀參考訊號**(Claude ②):`Paper.venue`/`Paper.citation_count` 已存在但沒往下傳——引用數/出處是客觀品質訊號,該給評分模型當參考(與主觀相似度分數不同質)。
5. **閾值未隨模型重校**:include/consider = 8/6(M4 依 Gemini 尺度校準);Ollama 尺度下是否過寬需由用戶抽查 + 分布數據決定(M5b 決策點)。

## Scope

- **改**:`models.py`(`LlmEvidenceAssessment` 欄位順序/雙 rationale)、`build_evidence_prompt`(A9 措辭、輸入格式、metadata)、`summarize_and_rerank`(index→id 組合)、pipeline/main(`RCS_BATCH_SIZE` env)、synthesis.py:237(rationale 使用處同步)、Langfuse trace 精簡。
- **不**:planner(Gemini key1)/逐篇筆記/synthesis(Gemini key2)路徑;embedding 檢索與收縮平均公式;閾值數值**動工時不改**——Todo 5 依數據決定並記錄。
- 校準/驗收全走 **Ollama(免費)**;真實 run 排配額恢復後(與 M6 Todo 5 補跑合流)。

## Todo 1 — schema 欄位順序 + A10 雙 rationale(models.py)

- `LlmEvidenceAssessment` 改為:
  ```
  chunk_id: str → summary: str(重述層) → rationale_relevance: str → rationale_quality: str → relevance_score: int → evidence_quality_score: int
  ```
  - 理由在分數前(①)+ 雙維度理由(A10)+ summary 即「用自己的話重述 chunk 核心」段(三層結構「重述→關係→分數」)。
  - **刪除單一 `rationale` 欄位**。
- 下游同步:
  - `EvidenceSummary(LlmEvidenceAssessment)`(models.py:196)繼承自動帶新欄位——檢查所有 `assessment.rationale`/`summary.rationale` 使用處並更新(synthesis.py:237 報告區塊 A;測試斷言)。
  - `LlmPaperSummaryNote`/`PaperAssessment` 等**不受影響**(各有自己的 rationale 欄位,用途不同)。
- 測試:更新凡引用 `rationale` 的斷言;新增「schema 順序」斷言(欄位順序 = 預期順序——用 `model_json_schema()["properties"]` 順序驗證,防回歸)。

## Todo 2 — A9 錨點措辭 + 分帶定義(build_evidence_prompt)

- 錨點措辭中性化 + **分帶定義表**(relevance 與 evidence_quality 各 5 帶、每帶 1-2 句、prompt 內精簡呈現):
  - relevance:9-10 = 直接回答 query 核心(極少數;9 = 直接強相關但非核心中的核心);7-8 = 直接相關、實質討論核心面向;5-6 = 相關但背景層次,不直接回答核心;3-4 = 間接相關(鄰近主題/術語重疊但核心不同);1-2 = 僅順帶提及/邊緣重疊。
  - evidence_quality:9-10 = 具體數值/方法直接支撐主張;7-8 = 有數據或明確方法;5-6 = 論述清楚但一般;3-4 = 模糊、細節少;1-2 = 幾乎無資訊(破碎文字)。理論/框架論文無數字不扣分。
  - **不加「9-10 例外等級」共用規範**(使用者 2026-09-09 裁示:分帶定義已寫清楚,先觀察效果,避免過度引導)——若改後分布仍 9-10 泛濫,再補該句。
- 保留「Write the rationale first, then assign scores consistent with it」指示(與新 schema 順序一致,不再是空話)。

## Todo 3 — RCS 輸入精簡(index + text + metadata;index→id 程式組合)

- `build_evidence_prompt(response, chunk_ids=None, paper_meta=None)` 輸入格式:
  ```
  ## Chunk 1
  Citations: 87 | Venue: ACL 2025
  {text}

  ## Chunk 2
  ...
  ```
  - **每 chunk 只給 index + (metadata) + text**;不再送 chunk_id/paper_id/page_start/page_end。
  - `paper_meta: dict[paper_id, (citation_count|None, venue|None)]` 由 pipeline 從 `Paper`(models.py:53-54 欄位已存在)組裝傳入;缺 metadata 的論文印 `(Citations: n/a | Venue: n/a)` 不省略。
- **index→id 組合**:`summarize_and_rerank` 建 `index_map = {str(i+1): chunk_id}`;LLM 回傳的 `chunk_id` 欄位填 index 字串;合併後程式查表轉回真 chunk_id(prompt/repair/`expected_chunk_ids` 全部用 index 形式——**repair prompt 的期望 id 必須是 index**,模型只見過 index)。
- 完整性檢查(每 chunk 恰一次)在轉換後做,行為不變。
- **Langfuse trace 精簡**:`summarize_and_rerank` 的 `@observe` 不再記錄完整 `EvidenceRetrievalResponse`(改用 query + chunk_ids 摘要輸入;用 Langfuse 提供的 capture 控制,若不可行則在記錄前複製精簡物件)。
- 測試:prompt 格式斷言(無 chunk_id/paper_id/頁碼字樣、有 Citations/Venue、有 ## Chunk N);index 組合測試(回傳 index → 轉回真 id);repair 期望 id 用 index。

## Todo 4 — RCS_BATCH_SIZE 環境變數(pipeline/main)

- `summarize_and_rerank(..., batch_size=RCS_BATCH_SIZE)` 維持;pipeline.py 讀 `os.getenv("RCS_BATCH_SIZE", "1")`(int 轉換、非法值 fallback 預設)傳入;main.py 的 run_end_to_end 可加參數或直接由 pipeline 讀 env。
- **預設 B=1(逐 chunk,使用者 2026-09-09 裁示試看看)**:真·絕對評分、無竄改 id 問題、與分帶定義同邏輯;Ollama 免費無 429,成本僅推理時間。B=4 保留為 env 可調對照組(比對見 Todo 5)。
- 測試:env 設定 → 呼叫次數變化;非法值 → 預設。

## Todo 5 — 校準重跑 + 閾值決策(免費,全 Ollama)

- 同 M6 校準設定(data/papers 21 檔基準、query「literature review agent」、top32)重跑 RCS(新 prompt/schema)。
- **B=1 vs B=4 免費對比**(同批 chunks 各跑一次):比較 10 分比例、rel/qual 分布、論文層天花板、repairs——決定預設 B(B=1 若有「無參照漂移」→ 分布異常 → 回 B=4;否則 B=1 定案)。
- 產出對照:
  - 改前(M6:m6-calibration.json)vs 改後:10 分比例(15/32 → ?)、rel/qual 分布、論文層天花板、進池數、include/consider 數。
  - **使用者 5 樣本抽查(比照 K3c)**:拿 M6 的 5 樣本(m6-calibration.md:46-64)+ 新 prompt 對同 5 chunks 的重評,三方對照(使用者 vs 舊 Ollama vs 新 Ollama)。樣本 3(AIDE 10/10)為關鍵判準。
- **閾值決策(記錄理由)**:
  - 若新 prompt 後 10 分收回、分布收斂且抽查一致 → 維持 8/6(或依分布微調);
  - 若仍偏寬 → 上調(如 include 9/7)並記錄「Ollama 尺度」適用;
  - 決策與數據寫入 `.omo/evidence/m5b-calibration-*.md/.json`。
- 產物:`.omo/evidence/m5b-calibration.md/.json/.log`。

## Todo 6 — 真實 run 驗收(配額恢復後;與 M6 Todo 5、M5a 下游合併關閉——只跑一次)

- **只跑一次(M5b 改後 code)**:M5b 只改 RCS 內部,plan/notes/synthesis 路徑不變 → 改後 run 同時涵蓋:M5b 端到端驗收 + **M6 Todo 5 掛帳關閉**(RCS on Ollama 端到端)+ **M5a 下游數據**(進評分池組成/usable/天花板 rel,對比 K3=9 篇)。不另跑舊 code baseline(改前對照用 m6-calibration.json 校準數據,同資料集、免費)。
- 指令:`printf 'literature review agent\n' | uv run --env-file .env python -m literature_review.main`;run 後 `data/papers/` 還原、log 存 `.omo/evidence/m5b-real-final.log`。
- 記錄:進評分池組成(vs K3=9 篇 / 校準池 10 篇)、usable、天花板 rel、Gemini 429 次數、report JSON。
- **配額守則(使用者 2026-09-09 裁示:額度不夠時先暫停、等額度,不硬撞)**:
  - 動 Todo 6 前先確認今天的 Gemini 配額可用(輕量 probe call 或前次 run 的 429 記錄);日配額不足 → **暫停 Todo 6,等配額恢復(如隔日)再跑**,先完成/回報 Todo 1-5(全免費)。
  - 跑中撞 429:若訊息含「retry in Xs」(每分鐘 20 次的滾動限制)→ sleep X+10 後重試一次,仍 429 則再等一次;若為 daily quota 字樣 → **立即停止,不重試硬撞**,掛帳回報。
  - Todo 6 至多 1 次嘗試 + 1 次 retry(比照 K3 先例);超限即掛帳,不阻塞驗收。

## Must NOT

- 不動 planner(Gemini key1)/逐篇筆記/synthesis 路徑與契約(除非 synthesis.py:237 的 rationale 欄位名同步——那是 schema 變更的最小必要同步)。
- 不動 embedding 檢索、收縮平均公式、`EvidenceRetrievalPolicy`。
- 閾值數值**不得在 Todo 5 前**改動。
- 不 commit;不碰 `.env`(不讀不印 key)、`data/papers/`、`.omo/drafts/`;不 spawn。
- 不跑額外真實 run(Todo 6 限一次 + 一次 retry,比照 K3 先例)。

## Success criteria

1. tests 全綠(schema 順序斷言、prompt 格式斷言、index 組合、repair index、B env 全過)。
2. Todo 5 校準重跑完成:10 分比例下降(對照 M6 的 15/32)、分布收斂、使用者 5 樣本抽查完成、**閾值決策記錄理由**。
3. Todo 6 真實 run(配額允許時)一次完成:M5b 端到端 + M6 掛帳關閉 + M5a 下游數據;或明確掛帳。
4. `data/papers/` 還原基準。

## Commit strategy(使用者親做)

```powershell
git add literature_review/models.py literature_review/llm_evidence.py literature_review/pipeline.py literature_review/main.py literature_review/synthesis.py tests/ HANDOFF.md
git commit -m "feat: M5b recalibrate RCS scoring schema order, dual rationales, minimal chunk input, and batch size env"
git add .omo/STATE.md .omo/plans/m5b-rcs-calibration.md
git commit -m "docs: record M5b RCS calibration plan and project state"
git push
```

(確切檔案以 `git status` 為準)

## 風險自審

| # | 風險 | 對策 | 殘餘 |
|---|---|---|---|
| P1 | index→id 組合與 repair 不一致(repair 期望真 id、模型只見過 index) | Todo 3 明令 repair/expected_chunk_ids 全用 index 形式 + 測試覆蓋 | 低 |
| P2 | `rationale` 移除導致下游漏改(compile 層面抓不到的字串用法) | grep 全專案 `\.rationale` 使用處(synthesis.py:237、測試)逐一更新 + 測試保護 | 低 |
| P3 | paper_meta 來源:pipeline 的 documents 結構是否帶 `Paper`(venue/citation_count) | 執行代理先讀 pipeline.py 文件組織;缺則從 document 的 paper 欄位組裝;測試用 fixture | 中低 |
| P4 | 「10 分稀有」措辭過強 → 矯枉過正(反向壓低) | 中性措辭草案已寫(「應散布在 4-7」非「不得給 10」);改後分布對照檢驗 | 中低 |
| P5 | 改前後對比受 Gemini 配額限制(真實 run) | 校準對比全 Ollama 免費;**配額守則**(Todo 6):日配額不足先暫停等恢復、短 429 sleep 重試一次、daily quota 立即停不硬撞、至多 1+1 次嘗試 | 中 |
| P6 | 雙 rationale + 順序一次改太多 → schema 形狀出錯 | Todo 1 先定最終 schema 形狀(model_json_schema 順序斷言防回歸);Todo 2/3 不動 schema | 低 |
| P7 | 使用者 5 樣本抽查未完成 → 閾值缺人證 | 若無法親測:「10 分稀有」規則性修正仍可驗證(分布層證據),閾值決策延 M6 Todo 5 後 | 中 |
| P8 | 區塊 A(summaries)格式變動影響報告 prompt | 只影響欄位名/內容呈現(rationale → 兩欄位);報告驗證契約(≥1 inline marker/無未知 chunk_id)不變 | 低 |