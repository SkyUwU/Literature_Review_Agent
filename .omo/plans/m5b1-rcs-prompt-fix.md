# M5b.1: RCS prompt 小修(子主題定位 + 反污染 + 主題混雜 + 獨立性 + metadata 用法)

- date: 2026-09-10
- 前置:M5b 驗收通過(230 tests)、使用者 5 樣本人類基準已產出(①5/6 ②8/8 ③2/9 ④8/8 ⑤4/7)
- 目的:修正 5 樣本抽查揭露的 rel 系統性錯誤——③ 資料豐富誘導高估(AIDE 8-10 vs 人類 2)、⑤ 背景/方法被當核心(protocol B4=10 vs 人類 4)、④ 破碎表格保守(B4=6 vs 人類 8)、主題混雜誤判——讓 LLM 分數貼近人類基準
- 性質:小修(只動 `llm_evidence.py` prompt 文字 + 測試 + 校準重跑),不動 schema/流程

## 改動 Todo

### Todo 1 — `build_evidence_prompt` 加 5 段指示(英文原文,語義不可改,措辭可微調)

於 items 說明段之後、Scoring guide 之前(或內嵌於 scoring guide,依現有結構安放,語義優先):

1. **SUB-QUESTION(核心子主題定位,對應⑤)**
   `First state, in one sentence, the specific sub-question the query is asking. Then judge whether the chunk's main content — not just overlapping keywords — actually addresses that sub-question.`
2. **ANTI-POLLUTION(反污染,對應⑥)**
   `A chunk with rich data, tables, or strong methodology is NOT automatically relevant. Score relevance solely on whether the chunk's core content addresses the query's specific question. Relevance and evidence quality are independent; a high-quality chunk must not inflate relevance.`
3. **MIXED-TOPIC(主題混雜,對應⑦)**
   `If a chunk mixes topics (e.g., a brief transitional sentence followed by unrelated content), base the relevance score on what the majority of the chunk substantively discusses, not on isolated overlapping phrases.`
4. **INDEPENDENCE(兩分數獨立,對應②)**
   `Score the two dimensions independently: high relevance does not imply high evidence quality, and vice versa.`
5. **METADATA-USE(metadata 用法,對應③)**
   `Citation count and venue are secondary supporting signals only; never lower evidence quality solely for low citations when the text itself is concrete. Use them as context, not as a primary criterion.`

### Todo 2 — 測試同步

- 找出 `build_evidence_prompt` 相關的既有 assert(prompt 字串斷言),隨變更同步更新
- 新增測試:斷言 5 句關鍵片語(sub-question / NOT automatically relevant / majority of the chunk / independently / secondary supporting signals)存在於 prompt
- 全測試綠(預期 230+)

### Todo 3 — 校準重跑(免費、Ollama 本地,不燒 Gemini key)

- 設定與 M5b **完全一致**:同 query「literature review agent」、`data/papers/` 21 檔基準、embedding top_k=32、Ollama qwen3:8b、`RCS_BATCH_SIZE` **B=4 與 B=1 兩批都跑**(沿用 /tmp/m5b_calibrate.py 或複製,產出到 `.omo/evidence/m5b1-calibration.{json,log,md}`)
- 記錄:10 分比例 / rel 分布 / 論文層天花板 / include-consider-exclude / calls+repairs

### Todo 4 — 對照表與建議(只產數據 + 兩案,不拍板)

- 5 樣本人類基準 vs 新 B=1 與 B=4 的 |Δrel|、|Δqual| 平均誤差
- 全量統計 vs M5b 校準(10 分比例、天花板、repairs)
- 特別檢查:樣本③ AIDE rel 是否顯著下降(目標 2-5 區間)、樣本⑤ protocol rel 是否下降(目標 3-6)
- B 決策建議:若 B1 vs B4 平均誤差差距 <0.5 且 B4 repairs 顯著少 → 建議維持 B=4;否則列出兩案
- 閾值建議:新天花板 < 7 → include=7 形同虛設,列出選項(降 consider / 接受 include 0 靠 M5e 候選池 / 其他);≥ 7 → 建議維持 b(include 7 / consider 6)

## Must NOT

- 不動 `LlmEvidenceAssessment` schema 欄位;不動 `RCS_BATCH_SIZE`(維持 4);不動 pipeline/main/planning/synthesis/embedding
- 不做 id 制改革(複製制 / 順序 zip 排 M5b.1 之後,動工前另討論)
- **不跑 M5b Todo 6 補跑**(等 M5b.1 驗收 + commit 後,用最終 code 跑,指令見計畫 m5b-rcs-calibration.md)
- 不 commit(使用者親做);不碰 `.env`(不讀不印 key)、`data/papers/`(21 檔基準)、`.omo/drafts/`;不 spawn 子代理
- 校準一律 Ollama 本地;不自行拍板 B/閾值(產數據 + 兩案建議即可)

## Success criteria

1. tests 全綠(230+),5 句新指示都在 prompt 中(測試斷言)
2. 校準統計產出且可與 M5b 對比;10 分比例維持 ≤10%
3. 5 樣本 |Δrel| 對照表 + B 兩案 + 閾值建議(含天花板判斷)產出
4. 樣本③ AIDE rel 較 M5b(B1 8 / B4 9)顯著下降;若無,記錄檢討
5. `data/papers/` 21 檔基準未動

## Commit strategy(使用者親做,commit 指令見執行指令包)

- 分兩筆:code+測試+HANDOFF / STATE+計畫;push 視狀態