# C2c: 筆記章節餵入 + 報告 claim 標註(雙 Pydantic 分層)

- slug:`c2c-note-section-input-and-claim-citation`
- date:2026-09-11(設計層)→ 2026-09-12(補實、自審、待核准)
- status:✅ **已核准(2026-09-12 使用者)待執行**
- 前置:C2a(章節切分 section/黑名單)✅、C2b(功能性評分/入選機制/E 拿掉 A)✅ **執行完成 + 驗收通過(308 tests 全綠,2026-09-12)**
- 範圍:C2 第三階段 = 逐篇筆記輸入章節化(A6 覆蓋增強自然解法)+ 報告生成 claim 標註(雙 Pydantic 分層)+ A5 材料清單移出 prose + 報告獨立 key3 + **評分線路改 Gemini 與 B=8 落地(2026-09-11 架構裁示①④,見 D 區)**。**不含兩階段生成(A7 關閉,2026-09-11 裁示,未來需要再改)。**

## 動機(背景事實)

1. **筆記輸入截斷破壞涵蓋**(A6):現況 `_bounded_chunks_for_llm`(synthesis.py:362)以 `llm_input_cap=40` + strided 抽樣截斷,**不保證涵蓋全篇**;aspect 是依 `classify_section(text)` 猜的分類(非 metadata 事實)。C2a 之後 chunk 帶 `section` 路徑(`"Title > 1 Introduction > 1.1 Background"`)——**章節資訊是現成的覆蓋軸**。
2. **報告 LLM 標 chunk_id 是錯的粒度**(2026-09-05 ③④ 定案 + 2026-09-12 確認):報告 LLM 只看得懂 claim(語意單位),chunk 對它是 opaque id;句子結尾標 **`[claim-N]`(全域連續短序號,2026-09-12 使用者拍板)**,claim→chunks 由程式機械展開;句子↔claim 歸屬是 LLM 擅長的事、完整性機械保證。**對外呈現 = 變體 A(2026-09-05 定案 + 2026-09-12 確認):正文保留 claim 標註版、不做 chunk 展開版**,附全域唯一 `claim_chunks` 對照表;future_directions 與 report 共用同一張對照表。
3. **報告尾端中文瑕疵**(M2 遺留):prompt 那句「Close the report with 材料來源清單」讓 LLM 在 prose 尾端生成中文跳脫附加段——A5:材料清單移出 prose,由 pipeline 程式組裝。
4. **key 分離**(2026-09-11 裁示):報告生成用獨立 `GEMINI_API_KEY_3`(與 notes 的 key2 分開);**缺 key3 → exit 1(比照 key2「報告無備案」慣例,2026-09-12 確認)**。✅ 使用者已於 2026-09-12 把 key3 加入 `.env`(WSL+Windows)。
5. **評分線路改 Gemini**(2026-09-11 裁示①④):功能性評分正式路徑優先 Gemini(Ollama 不再預設),`FUNCTIONAL_BATCH_SIZE` 預設 5→8(每 call ~2.5-3K tokens、總 calls 3-5 對 Gemini 安全);此數字僅適用 Gemini 線路(M6 實測 Ollama 8B 在 B=8 會漏/竄改 id,若回退 Ollama 維持小批次)。C2b 時評分走 `client_rcs`(Ollama),C2c 落地調整優先序。
6. **功能性分數是入選軸**(C2b 定案):入選 = 配額∩閾值(`select_quota_threshold`,第一輪 2/follow-up 1、≥6.0);報告只吃入選論文的功能性 assessment + 筆記——C2c 承接 C2b 輸入結構。

## 現況 code 事實(2026-09-12 查證,C2b 驗收後;⚠️ 已更新)

- `models.py`:
  - `ChunkReference`(chunk_id/paper_id/page_start/page_end/quote)、`PaperSummaryClaim`(text min 20/aspect min 3/evidence: list[ChunkReference] min 1)、`PaperSummary`(paper_id/claims min 1/coverage_chunk_ids min 1)。
  - `LlmNoteClaim`(text/chunk_ids/aspect)、`LlmPaperSummaryNote`(claims min 1/coverage_chunk_ids)。
  - C2b 新契約:`LlmFunctionalAssessment`(:211,rationale 先於 utility_score、utility 1-10)、`LlmFunctionalAssessmentBatch`(:228)、`EvidenceCitation`(含 utility_score)、`PaperAssessment`(:271,utility_score float 1-10 + recommendation **两分法 include|exclude**,consider 退役)、`FunctionalScoringPolicy`(:284)、`FunctionalPaperScore`(:303)。
  - `LlmSynthesisDirection`(title/rationale/supporting_paper_ids/**supporting_chunk_ids min 1**——C2c 改 `supporting_claim_ids`);`LlmSynthesisBatch`(report min 100/future_directions min 1);`SynthesisResponse`(**C2c 加 `claim_chunks`**)。
  - `CoveragePackPolicy`(max_chunks_per_paper=6、llm_input_cap=40——cap 主要作用於筆記輸入,C2c 改章節導向語意)。
- `synthesis.py`:
  - `_CONVERGENCE_UTILITY_SCORE = 6`(:53)、`_bounded_chunks_for_llm`(:362 cap=40 strided)、`build_paper_notes_prompt`(:381 payload 含 chunk_id/paper_id/page_start/page_end/text + `classify_section(text)` 猜 aspect——**未用 C2a section 欄位**)。
  - `summarize_paper_notes`(:450)→ `PaperSummary`;`_check_unknown_ids` repair。
  - `build_synthesis_prompt`(:511):**C2b 已移除舊 A 區塊(summaries)**,現送「Paper assessments(utility_score/rationale/evidence_chunk_ids)+ Per-paper notes」;仍含「Close the report with 材料來源清單」句(:547)。
  - `_allowed_marker_ids`(:558)= 入選論文評分證據 chunks ∪ 筆記引用 chunks(chunk 級白名單);`synthesize_report`(:577)marker 驗證 + `_MARKER_REGEX`。
- `functional.py`(C2b 新檔):`build_functional_prompt`(只送 index+text+`Paper: {title} | Section: {section}`——**功能性評分已用 section 欄位**)、`score_chunks_functionally`(批次 `FUNCTIONAL_BATCH_SIZE=5` + repair + 完成檢查)、`sample_top_chunks_per_paper`(per-paper top-2,主 query embedding)、`aggregate_functional`(平均無收縮)、`select_quota_threshold`(配額∩閾值)。
- `pipeline.py` `run_synthesis_pipeline`(:170):`min_words=4` 過濾 `_prepare_documents`;`sample_top_chunks_per_paper` → `score_chunks_functionally` → `aggregate_functional` → `select_quota_threshold`(paper_queries/follow_up_queries 配額)→ `summarize_paper_notes`(只對 include)→ `synthesize_report(paper_assessments=..., client=...)`。
- `evidence.py`:chunk 帶 `section`(路徑 `"Title > 1 Introduction > 1.1 Background"`)、chunk_id = `{paper_id}-c{n}`(C2a);`_heading_path` 依 `_HEADER_KEYS`(#/##/### metadata)組路徑。
- `main.py`:planner 用 key1(缺→fallback)、synthesis 用 key2(缺→exit 1)、評分走 `client_rcs`(OLLAMA_BASE_URL 設了才建,缺設→None→fallback Gemini(=client_synth key2),M6 行為);**key3 不存在(需新增)**;評分優先序需改成 Gemini 優先(裁示①)。

## 設計決策(2026-09-05/09-10/09-11/09-12 定案;本計畫無未定裁示點)

### A. 筆記章節餵入(改 `build_paper_notes_prompt` + `_bounded_chunks_for_llm`)

- **輸入**:逐篇筆記仍是單 call(不逐章節 call——成本不變;2026-09-10 定案「按章節餵入」= 輸入結構化,非多 call)。
- **結構化**:chunks 依 `section` 路徑分組,每組帶節標題 + 該節 chunks(順序 = 文件順序);prompt 移除「LLM 自行 classify_section」指示與 `sections_by_aspect` 提示——章節是 metadata 事實,LLM 只從供給的章節清單中選。
- **cap 改章節導向**:`llm_input_cap` 語意改「章節覆蓋優先:全章節先保 header + 每節首段,超 cap 才節內 strided」——細則 Todo 1 定(fake 測試比較 old strided vs 章節導向選樣)。
- **aspect = 頂層章節**(**2026-09-12 修正,取代設計層初版的「路徑末級」**):章節清單取「頂層章節」,抽取規則(**層級無關版**):拆路徑(` > ` split,過濾空段)→ **跳標題位**(僅對首段套判據,命中才丟)→ **取第一個非標題段**。**不依賴 # 層級數量**(`_heading_path` 已把層級壓平成依階層順序的列表,# 層級只影響路徑段數,不影響取段規則):多層 `AutoGen > 2 Related Work > 2.1` → `2 Related Work`;單層 `2 Related Work` → 首段保留直接取;混合(部分章節有小節、部分無)一致取第 1 段;跳完無剩餘 → aspect="other"。**論文標題 vs 章節標題判據(2026-09-12 精確化)**:三重訊號——① `Paper.title` 正規化比對(去空白/大小寫/標點,相等或 paper title 為其開頭(受截斷))② 位置(文檔第一個標題)③ 編號輔助(章節多數數字開頭);**誤判偏向「當章節」(標題誤當章節 = 多一個 aspect,危害小;章節誤當標題跳掉 = 整章消失,危害大)——僅 ①或② 明確命中才跳過**。**prompt 明示「aspect 一律填頂層章節標題,不得使用小節名」**——小節名跨論文不可比,頂層章節才是覆蓋軸。測試以固定 fixture 涵蓋「有標題段/無標題段、## 存在/不存在、連續 # 單層、混合」分支。
- **legacy chunk(無 section)→ aspect="other"**:`_NOTE_ASPECTS` 分類退役(不再作為 aspect 提示/白名單,避免與 marker 白名單混淆);無 section 的 legacy chunk(fixture/無標題段落)的 claim aspect 允許 "other"。
- **claim 產出不變**:`LlmNoteClaim`(text/chunk_ids/aspect)維持——**aspect=頂層章節,chunk_ids 指回實際 chunks = claim→chunks 對照來源(供 B 用)**。

### B. 報告 claim 標註(雙 Pydantic 分層;2026-09-12 全部定案)

- **claim 代號 = 全域連續短序號** `claim-1`…`claim-N`(2026-09-12 使用者拍板):程式在筆記階段依 `paper_summaries` 文件順序給號(每論文 internal 編號 → 全域從 1 連續到 N);跨論文唯一;報告 LLM 不需理解 paper 分組。`claim-{n}` 對應表 = `claim_chunks: dict[str, list[str]]`,由筆記階段已驗證的 claim→evidence chunks 機械展開。
- **第 1 層 LLM 契約**(改 `LlmSynthesisBatch`):`report` prose 含 `[claim-N]` 標註(粒度:A1 句尾聚合預設,子句級由 LLM 自然運用);`future_directions` 每項 `supporting_claim_ids`(min 1)取代 `supporting_chunk_ids`;`supporting_paper_ids` 保留(LLM 給、程式驗證與 claim 的 paper 歸屬一致性)。**LLM 任務最小化**:只「寫報告 + 標 claim 代號」,永不碰 chunk_id。
- **第 2 層程式契約**(`SynthesisResponse` 加 `claim_chunks`):`report` 維持 claim 標註版(**對外呈現 = 變體 A**,不做 chunk 展開版);`future_directions` 的 `supporting_chunk_ids`(對外保留)由 `supporting_claim_ids` 機械展開填。**對外契約變更**:報告 marker 從 `[chunk_id]` 變 `[claim-N]` + 附對照表——STATE/README 同步說明,測試/驗證全面遷移。
- **驗證機制改寫**:
  - marker 驗證:`[claim-N]` 全部 ∈ 供應的 claim 代號集合(usable 論文的 claims)——claim 級白名單(取代 chunk 級 `_allowed_marker_ids`;chunk 由程式組合,LLM 無從幻覺 chunk);
  - 未知 claim 代號 → repair(回餵有效集合,沿用 `_check_unknown_ids` 精神;M5b 竄改教訓引用)。

### C. A5 材料清單移出 prose

- `build_synthesis_prompt` 移除「Close the report with a 材料來源清單…」句;
- 程式層組裝材料清單(入選論文 + 來源 PDF + claim→chunks 對照表——provenance 契約已有 `paper_sources`/assessments,組裝零 LLM)。

### D. 評分線路改 Gemini + B=8(架構裁示①④落地)

- `client_rcs` 優先序改:**Gemini 評分走 key2**(與 notes 同 key——使用者 2026-09-12 裁示,現況 fallback 行為零改動:「Ollama 缺設 → fallback Gemini(=key2)」維持);有 `OLLAMA_BASE_URL` 才用 Ollama(逃生門,維持小批次);`.env` 註解/移除 `OLLAMA_BASE_URL`/`OLLAMA_MODEL` 即時生效(2026-09-11 裁示)。key 配置定案:**key1 = planner/screen、key2 = 評分+notes、key3 = 報告**。**⚠️ key 彈性備案(2026-09-12 使用者)**:若真實 run 統計 key2 calls 超額(429 密集),備案 = **逐篇筆記移 key3(notes+報告同 key3;notes 在報告之前依序執行,同 key 無並行碰撞)**——以 Todo 5 key 統計為決策依據,調整前先告知使用者。
- `FUNCTIONAL_BATCH_SIZE` 預設 5→8(env 覆寫保留);⚠️ 僅 Gemini 線路安全數字。
- RCS legacy 不動拒絕;notes 維持 key2;報告用 key3。

### E. 不做(2026-09-11/09-12 裁示,C2 範圍外)

- **不做兩階段生成**(outline → 逐 section;單階段維持);
- 不做 claim 展開版 report(第 2 層只留 claim 標註版 = 變體 A);
- 不動 `data/papers` 策略;不做 key3 以外的 key 配置調整(評分 key1/notes key2/報告 key3 定案)。

## Todo 大綱(已精修;每項含測試)

- **Todo 0 — 契約(models.py)**:`LlmSynthesisDirection.supporting_chunk_ids` → `supporting_claim_ids`(min 1);`SynthesisResponse` 加 `claim_chunks: dict[str, list[str]]`;claim 代號規則(`claim-{n}`,全域連續)文件化;`llm_input_cap` 語意註記改章節導向。測試:Schema 驗證、legacy 相容(舊欄位移除後舊測試遷移)。
- **Todo 1 — 筆記章節餵入(synthesis.py)**:`_bounded_chunks_for_llm`/`build_paper_notes_prompt` 章節結構化 + cap 章節導向 + **aspect=頂層章節(規則:跳標題位、Section(##)層優先/無 ## 退 # 層 + 連續 # 單層情境註記)+ prompt 明示不得用小節名 + `llm_input_cap` 預設 40→80(使用者 2026-09-12 裁示起始值:不確定太大量對 LLM 輸出不好,寧可先保守;欄位上限 200;fake 測試看 token 規模與輸出品質後再調)**;legacy(無 section)fallback "other"(`_NOTE_ASPECTS` 退役)。測試:章節分組正確、頂層章節抽取(跳標題位、## 優先/無 ## 退 #、四分支 fixture)、cap 行為、legacy fallback、fake e2e 每章節至少一 claim 涵蓋。
- **Todo 2 — claim→chunks 機械展開**:`build_claim_chunks(paper_summaries) -> dict[str, list[str]]`(全域 `claim-1..N` 依文件序給號);directions 展開;測試:編號唯一/連續、每 claim 非空 chunks、報告標註與對照表一致。
- **Todo 3 — 報告 prompt 改版 + key3 + 評分線路**:`build_synthesis_prompt` 送 notes + 入選功能性 assessment 摘要(維持 C2b 結構)、`[claim-N]` 指示、移除材料來源清單句;`_allowed_marker_ids` → claim 代號集合;A5 程式組裝;`main.py` 報告 client 接 `GEMINI_API_KEY_3`(**缺 → 印訊息 + exit 1,比照 key2**);`client_rcs` 維持 Gemini fallback = key2(裁示);`FUNCTIONAL_BATCH_SIZE` 預設 8。測試:marker 驗證(claim 級)、unknown claim repair、缺 key3 exit 1、**key2 缺 → 提早 exit 1(評分與 notes 都無線路,不得跑到 include 全空)**。
- **Todo 4 — 接線(pipeline.py/main.py)+ 測試遷移**:`run_synthesis_pipeline` 產 `claim_chunks`;`synthesize_report` 新驗證;舊測試遷移(contract 欄位變更:`supporting_chunk_ids`→`supporting_claim_ids`、marker 格式)。
- **Todo 5 — 測試/驗收 + 真實 run 掛帳**:既有套件全綠(C2a/C2b 數量不減為原則);fake e2e 全鏈路(無 key);**真實 run 掛帳 = C2b Todo 5 的 run 一併執行**(最終線路:評分 Gemini+B=8、notes key2、報告 key3;收集:C2b 項[功能性分數分布/入選 vs 配額/keep 但 exclude 衝突抽查/閾值 6.0 校準建議] + C2c 項[claim marker 正確率、材料清單渲染、報告尾端中文瑕疵消失確認、claim→chunks 完整性] + **各 key call 次數統計(Langfuse,供 key 分配調整——2026-09-12 使用者要求)**)。

## Must NOT

- **不做兩階段生成**(單階段——2026-09-11 裁示)。
- 不刪 legacy(deterministic 報告路徑保留相容;RCS legacy 依 C2b 現況);不動 `_NOTE_ASPECTS` 之外的既有分類契約(遷移以測試為準)。
- 不 commit(使用者親做);不碰 `.env`/`data/papers/`;不 spawn;不印 key。
- 報告 marker 對外契約變更(`[claim-N]` + 對照表)── 驗證/測試/README/STATE 同步,不得混用舊 `[chunk_id]` 斷言。

## Success criteria

1. 筆記輸入章節化:單 call/論文不變、aspect=頂層章節(prompt 明示不得小節名)、每章節涵蓋由結構保證(測試斷言)、legacy chunk fallback "other"。
2. 報告 LLM 只產 `[claim-N]`(全域連續短序號),程式機械展開 claim→chunks;claim 級白名單驗證全綠(未知 claim 修復)。
3. A5 落地:prose 無材料清單、程式組裝清單輸出;報告尾端中文瑕疵消失(fake/真實 run 觀察)。
4. key3 獨立:報告生成走 `GEMINI_API_KEY_3`、缺 → exit 1(測試覆蓋);**評分走 Gemini key2(= notes 同 key,現況 fallback 零改動)**;planner/screen 維持 key1。
5. 真實 run 掛帳(最終線路一次跑,涵蓋 C2b+C2c 收集項),回饋納入後關閉 C2b Todo 5。

## Commit strategy(使用者親做;確切檔案以 git status 為準)

```powershell
git add literature_review/models.py literature_review/synthesis.py literature_review/pipeline.py literature_review/main.py literature_review/functional.py tests/ HANDOFF.md
git commit -m "feat: C2c section-aware notes input and claim-cited synthesis"
git add .omo/STATE.md .omo/plans/c2c-note-section-input-and-claim-citation.md .omo/plans/c2b-functional-scoring.md
git commit -m "docs: record C2c plan approval and project state"
git push
```

## 風險自審(2026-09-12 補實版;P1-P7 對照設計層初版)

| # | 風險 | 對策 | 殘餘 |
|---|---|---|---|
| P1 | ~~C2b 契約與假設不符~~ → 已查證對齊(C2b 驗收 2026-09-12) | code 事實區已更新;Todo 以現況為準 | 已關閉 |
| P2 | 章節餵入讓筆記 call 變長 → token 成本↑ | cap 章節導向(節內 strided 保留);fake 測試規模;真實 run 觀察 | 中 |
| P3 | `[claim-N]` 與舊 `[chunk_id]` marker 驗證混淆/遷移漏 | 驗證邏輯集中(單一函式)、全測試遷移、README/STATE 同步 | 中 |
| P4 | LLM 誤造 claim 代號(claim-99)/漏標 | repair 回餵有效集合;M5b 竄改教訓(index 制 + 完整集合檢查) | 低-中 |
| P5 | A5 程式組裝清單格式與對外契約漂移 | 保留 paper_sources 契約;格式寫入 STATE/README | 低 |
| P6 | ~~key3 缺設行為未定~~ → **已定案:exit 1(比照 key2 慣例,2026-09-12)** | Todo 3 測試覆蓋 | 已關閉 |
| P7 | claim_chunks 對照表巨大 → 輸出膨脹 | 對照表只進第 2 層(程式側),不進 LLM prompt;輸出按 paper 分組 | 低 |
| P8 | **頂層章節抽取規則模糊**(路徑首段可能是論文標題,直接取第一段會把 `Title` 當章節) | 精確規則(見 A 區):跳過標題位(與 paper title 相同/`Title`/`Abstract` 的首段)→ 取第一個實質章節;## 層優先,無 ## 退回 # 層;fixture 測試四分支 | 低 |
| P9 | **評分走 key2 與 notes 共 key → 429 碰撞(評分 3-5 calls + notes 每篇 1 call)** | key2 本就是「高消耗場景」key(M3C 配置);`NOTES_PACING_SECONDS` 慣例沿用、retry、Langfuse 觀察;key2 缺 → 既有 `_build_clients` exit 1(評分/notes 無線路,不得跑到 include 全空) | 中 |
| P10 | **章節餵入後 claim 的 aspect 失去舊分類語意**(contribution/method 等)→ 下游/報告依賴 aspect 分類 | C2c 範圍內報告只吃 claim text + 引用,不依 aspect 分組(fake 測試確認);README terminlogy 更新 | 低 |

## 下一步行動卡

1. ~~C2b 執行/驗收~~ ✅ → 本計畫補實(2026-09-12)✅ → **本次交付:交使用者核准**。
2. 核准後:使用者貼執行指令包 → 執行代理(同 C2a/C2b session RESUME)→ 回報 → 規劃驗收 → 使用者 commit(code 1 筆 + docs 1 筆 + push)。
3. **C2c 執行指令包交付時明示:「C2b Todo 5 真實 run 延後,本次不做」**——真實 run 一律等 C2c 完成後、以最終線路(評分 Gemini + B=8 + notes key2 + 報告 key3)一次跑,涵蓋兩邊收集項(2026-09-11 裁示)。
4. key3 已加入 `.env`(2026-09-12 使用者)✅;執行前不需再提醒,但執行代理指令包註記「勿動 .env」。
5. key 配置已定案(2026-09-12):key1 = planner/screen、key2 = 評分+notes、key3 = 報告——執行代理指令包註記,不得再動。