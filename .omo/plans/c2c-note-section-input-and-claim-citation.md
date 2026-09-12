# C2c: 筆記章節餵入 + 報告 claim 標註(雙 Pydantic 分層)

- slug:`c2c-note-section-input-and-claim-citation`
- date:2026-09-11
- status:🟡 設計層已寫(2026-09-11 使用者裁示:單階段報告、報告獨立 key3、先寫可設計的部分);code 事實待 C2b 執行後補實、補完交使用者核准
- 前置:C2a(章節切分 section/黑名單)✅、C2b(功能性評分/入選機制/E 拿掉 A)——**C2b 執行回報後本計畫 code 事實區更新、Todo 精修,再核准**
- 範圍:C2 第三階段 = 逐篇筆記輸入章節化(A6 覆蓋增強自然解法)+ 報告生成 claim 標註(雙 Pydantic 分層)+ A5 材料清單移出 prose + 報告獨立 key3。**不含兩階段生成(2026-09-11 使用者裁示:A7 關閉,未來需要再改,現在不做複雜化)**

## 動機(背景事實)

1. **筆記輸入截斷破壞涵蓋**(A6):現況 `_bounded_chunks_for_llm`(synthesis.py:362)以 `llm_input_cap=40` + strided 抽樣截斷,**不保證涵蓋全篇**;aspect 是 LLM 自由分類(白名單 `_NOTE_ASPECTS`),並無結構保證。C2a 之後 chunk 帶 `section` 路徑(`"Title > 1 Introduction > 1.1 Background"`)——**章節資訊是現成的覆蓋軸**。
2. **報告 LLM 標 chunk_id 是錯的粒度**(使用者 2026-09-05 ③④ 定案):報告 LLM 只看得懂 claim(語意單位),chunk 對它是 opaque id;句子結尾標 `[claim-N]`,claim→chunks 由程式機械展開(變體 B:程式展開替換回 `[c3][c7][c12]` 對外維持現有 marker 契約)→ 句子↔claim 歸屬是 LLM 擅長的事、完整性機械保證、正文不噪音。**第 2 層只保留 claim 標註版(不做 chunk 展開版)**,future_directions 與 report 共用同一張 `claim_chunks` 對照表(全域唯一,2026-09-05 再定案)。
3. **報告尾端中文瑕疵**(M2 遺留):prompt 那句「Close the report with 材料來源清單」讓 LLM 在 prose 尾端生成中文跳脫附加段——A5:材料清單移出 prose,由 pipeline 程式組裝。
4. **key 分離**(2026-09-11 使用者裁示):報告生成用獨立 `GEMINI_API_KEY_3`(與 notes 的 key2 分開;C2b 已定功能性評分走 client_rcs 線路)。**待辦提醒:使用者需把 key3 加入 `.env`(C2c 執行前提醒)。**
5. **功能性分數是入選軸**(C2b 定案):入選 = 配額∩閾值;報告只吃入選論文的功能性 assessment + 筆記——C2c 承接 C2b 的 `PaperAssessment`(utility_score、include|exclude)與「報告拿掉 A 區塊」落地後的 synthesis 輸入結構。

## 現況 code 事實(2026-09-11 查證,C2a 後;⚠️ C2b 執行後更新本區)

- `models.py`:
  - `ChunkReference`(chunk_id/paper_id/page_start/page_end/quote)、`PaperSummaryClaim`(text min 20/aspect min 3/evidence: list[ChunkReference] min 1)、`PaperSummary`(paper_id/claims min 1/coverage_chunk_ids min 1)。
  - `LlmNoteClaim`(text/chunk_ids/aspect)、`LlmPaperSummaryNote`(claims min 1/coverage_chunk_ids)。
  - `LlmSynthesisDirection`(title min 3/rationale min 20/supporting_paper_ids min 1/**supporting_chunk_ids min 1**);`LlmSynthesisBatch`(report min 100/future_directions min 1)。
  - `FutureDirection`(title/rationale/supporting_paper_ids/**supporting_chunk_ids**);`SynthesisResponse`(paper_sources/evidence_assessment_response/paper_summaries/report/future_directions/limitations/generated_by)。
  - `CoveragePackPolicy`(max_chunks_per_paper=6、llm_input_cap=40)——cap 主要作用於筆記輸入。
- `synthesis.py`:
  - `_bounded_chunks_for_llm`(:362,cap=40、strided 抽樣、appendix 邊界優先)→ 筆記輸入截斷現況。
  - `build_paper_notes_prompt`(:381):payload 含 chunk_id/paper_id/page_start/page_end/text;**也用 `classify_section`(chunk.text)把 chunk 分類成 aspect 當提示**(sections_by_aspect)——已有章節分類雛形,但以 text 分類非 metadata section;**未使用 C2a section 欄位**。
  - `summarize_paper_notes`(:450):`LlmPaperSummaryNote` → `PaperSummary`;`_check_unknown_ids` repair。
  - `build_synthesis_prompt`(:492):A 區塊(usable 論文的 summaries:rel/qual/summary 摘要)+ B 區塊(notes claims text/aspect/chunk_ids);指示「句尾 [chunk_id] marker」+「Close the report with 材料來源清單」(C2b 後 A 區塊移除)。
  - `_allowed_marker_ids`(:542):summaries ∪ notes coverage。`synthesize_report`(:560):marker/方向 id 驗證、`LlmSynthesisBatch` 產出、`_EXCLUDED_SUMMARIES_LIMITATION`。
- `pipeline.py` `run_synthesis_pipeline`(:140):notes 只給 usable 論文、`coverage_policy` 傳入 `summarize_paper_notes`;`synthesize_report(assessment_response, coverage_packs, paper_summaries, client)`(C2b 後簽名與輸入結構會變)。
- `evidence.py`:chunk 帶 `section`(如 `"Title > 1 Introduction > 1.1 Background"`)、頁碼;chunk_id = `{paper_id}-c{n}`(C2a)。
- `main.py`:synthesis 用 key2(client_synth);key3 不存在(需新增 env + client 接線)。

## 設計決策(2026-09-05/09-10/09-11 定案 + 本計畫裁示點)

### A. 筆記章節餵入(改 `build_paper_notes_prompt` + `_bounded_chunks_for_llm`)

- **輸入**:逐篇筆記仍是單 call(不逐章節 call——成本不變;2026-09-10 定案「按章節餵入」= 輸入結構化,非多 call)。
- **結構化**:chunks 依 `section` 路徑分組,每組帶章節標題 + 該節 chunks(順序 = 文件順序);prompt 移除「LLM 自行 classify_section」的指示與 `sections_by_aspect` 提示——章節是 metadata 事實,LLM 只需從供給的章節清單中**選章節當 aspect**。
- **cap 改章節導向**:`llm_input_cap` 語意改「每章節內 cap」或「章節覆蓋優先:全章節先保 header + 每節首段,超 cap 才節內 strided」——細則 Todo 1 定(fake 測試比較 strided vs 章節導向選樣)。
- **aspect 白名單**:從「自由分類到 `_NOTE_ASPECTS`」改「侷限於該論文實際章節路徑末級 + 既有 `_NOTE_ASPECTS` 保留當 fallback(無 section 的 legacy chunk)」——A6 覆蓋由結構保證(C2b 後 real-run 觀察 P9 是否緩解)。
- **claim 產出不變**:`LlmNoteClaim`(text/chunk_ids/aspect)維持——**aspect=章節,chunk_ids 仍指回實際 chunks(claim→chunks 對照的來源,供 B 用)**。

### B. 報告 claim 標註(雙 Pydantic 分層)

- **第 1 層 LLM 契約**(新 `LlmSynthesisReport` 或改 `LlmSynthesisBatch`):
  - `report`:prose 含 `[claim-N]` 標註(句子/子句級,粒度=A1 句尾聚合預設,A2 子句級由 LLM 自然運用,不強制);
  - `future_directions`:`LlmSynthesisDirection` 的 `supporting_chunk_ids` → **`supporting_claim_ids`(min 1)**,`supporting_paper_ids` 保留(claim 本身帶 paper 歸屬,程式可推得——paper_ids 是否也由程式推?裁示點:保留 LLM 給,程式驗證一致性)。
  - **LLM 任務最小化**:只「寫報告 + 標 claim 代號」,不做 chunk 對應。
- **第 2 層程式契約**(`SynthesisResult` 或 `SynthesisResponse` 內新欄位):
  - `claim_chunks`:全域唯一對照表 `{claim_id(如 "1"… 或 "claim-1"): list[chunk_id]}`——**由筆記階段已驗證的 claim→evidence chunks 機械展開**,不再讓 LLM 碰 chunk_id。
  - `report`:維持 claim 標註版(不做 chunk 展開版;2026-09-05 定案)。
  - `future_directions`:`FutureDirection.supporting_chunk_ids` → 由 `supporting_claim_ids` 機械展開填(對外 chunk 層級不變,`SynthesisResponse` 對外契機可能微調)。
- **驗證機制改寫**:
  - marker 驗證:`[claim-N]` 全部 ∈ 供應的 claim 代號集合(usable 論文的 claims);
  - 未知 claim 代號 → repair/raise(沿用 `_check_unknown_ids` 精神);
  - directions:claim-N 存在 + paper 歸屬一致。

### C. A5 材料清單移出 prose

- `build_synthesis_prompt` 移除「Close the report with a 材料來源清單…」句;
- 程式(pipeline 或 synthesis)在 `SynthesisResponse`/report 輸出層組裝材料清單(入選論文 + 來源 PDF + 證據 chunks 清單——provenance 契約本來就有 `paper_sources`/assessments,組裝零 LLM)。

### D. 報告獨立 key3

- synthesis client 改用 `GEMINI_API_KEY_3`(新 env;`main.py` client_synth 接線 + 缺 key 處理比照 key2→exit 1?裁示點:預設缺 key3 時 fallback key2 或 exit);
- **使用者待辦提醒(C2c 執行前)**:`GEMINI_API_KEY_3` 加入 `.env`(WSL 與 Windows 兩端);
- notes 維持 key2、功能性評分維持 client_rcs(現況參數名,語意 C2b 已定)。

### E. 不做(2026-09-11 裁示 / C2 範圍外)

- **不做兩階段生成**(outline → 逐 section;單階段維持);
- 不做 claim 展開版 report(第 2 層只留 claim 標註版);
- 不動 `data/papers` 策略;不做第三 key 以外的 key 配置調整。

## Todo 大綱(細則待 C2b 執行後精修;每項含測試)

- **Todo 0 — 契約(models.py)**:`LlmSynthesisDirection.supporting_claim_ids`(取代 supporting_chunk_ids);新 `LlmSynthesisReport`(report 含 `[claim-N]` 語意註記、future_directions);`SynthesisResponse` 加 `claim_chunks: dict[str, list[str]]`(或 `SynthesisResult` 分層);`GEMINI_API_KEY_3` 相關 client 契約(若接線點在 models 外則移至 Todo 3);`llm_input_cap` 語意註記更新。測試:Schema 驗證、legacy 相容。
- **Todo 1 — 筆記章節餵入(synthesis.py)**:`_bounded_chunks_for_llm`/`build_paper_notes_prompt` 章節結構化;cap 章節導向;aspect 改章節清單。測試:章節分組正確、cap 行為、fake e2e 涵蓋(每 section 至少一 claim 的驗證點?裁示點)、legacy chunk(無 section)fallback。
- **Todo 2 — claim→chunks 機械展開**:`build_claim_chunks(paper_summaries) -> dict[str, list[str]]`;directions 展開;`claim_id` 命名規則(如 `"claim-{paper}... "`?裁示點:全域唯一即可,建議 `{paper_id}-{index}` 或簡短序號,對外標註友善取短)。測試:完整性(每 claim 對應非空 chunks)、紙上驗證「句子↔claim 歸屬是 LLM 職責」。
- **Todo 3 — 報告 prompt 改版 + key3 接線**:`build_synthesis_prompt` 只送 notes(B)+ 入選論文功能性 assessment 摘要;`[claim-N]` 指示;移除 材料來源清單句;synthesis client 接 `GEMINI_API_KEY_3`(+ fallback 決策);`_allowed_marker_ids` 改 claims 代號;A5 程式組裝。測試:marker 驗證新邏輯、unknown claim repair、缺 key3 行為。
- **Todo 4 — 接線(pipeline.py/main.py)+ 測試遷移**:`run_synthesis_pipeline` 新輸入;`SynthesisResponse.claim_chunks` 產出;舊測試遷移(contract 欄位變更)。
- **Todo 5 — 測試/驗收**:既有套件全綠(含 C2a/C2b 數量不減為原則);fake e2e 全鏈路(無 key);**真實 run 掛帳**(修改清單清空後整條;收集:字形 marker 正確率、材料清單渲染、報告尾端中文瑕疵確認消失、claim→chunks 完整性統計)。回報格式:測試數、檔案變更、證據路徑、驗收對照。

## Must NOT

- **不做兩階段生成**(單階段,未來需要再改——使用者 2026-09-11 裁示)。
- 不刪 legacy(deterministic 報告路徑保留相容;功能性/RCS legacy 依 C2b 現況)。
- 不 commit(使用者親做);不碰 `.env`/`data/papers/`;不 spawn;不印 key。
- 報告 marker 對外契約若維持 `[chunk_id]`(變體 B 展開)不得與 claim 代號混淆——若採 claim 代號對外呈現,需在 STATE/README 同步說明(裁示點)。

## Success criteria

1. 筆記輸入章節化:單 call/論文不變、每章節涵蓋由結構保證(測試斷言)、aspect=章節。
2. 報告 LLM 只產 claim 代號標註,程式機械展開 claim→chunks;驗證全綠(未知 claim 修復)。
3. A5 落地:prose 無材料清單、程式組裝清單輸出;報告尾端中文瑕疵消失(fake/真實 run 觀察)。
4. key3 獨立:報告生成走 `GEMINI_API_KEY_3`,缺 key 行為有明確定義與測試。
5. C2b 真實 run 回饋納入(功能性分數分布、P9 筆記涵蓋)後,核准本計畫並掛真實 run。

## Commit strategy(使用者親做;確切檔案以 git status 為準)

```powershell
git add literature_review/models.py literature_review/synthesis.py literature_review/pipeline.py literature_review/main.py tests/ HANDOFF.md
git commit -m "feat: C2c section-aware notes input and claim-cited synthesis"
git add .omo/STATE.md .omo/plans/c2c-note-section-input-and-claim-citation.md
git commit -m "docs: record C2c plan and project state"
git push
```

## 風險自審(設計層初版,C2b 後補實)

| # | 風險 | 對策 | 殘餘 |
|---|---|---|---|
| P1 | C2b 實際契約與本計畫假設不符(synthesis 輸入結構、PaperAssessment 欄位) | code 事實區標 C2b 後更新;核准前重讀 C2b 回報比對 | 低(設計層不鎖死細節) |
| P2 | 章節餵入讓筆記 call 變長(整節 chunks vs strided 抽樣)→ token 成本↑ | cap 章節導向(節內 strided 保留);先 fake 測試規模;真實驗收觀察 | 中 |
| P3 | `[claim-N]` 代號與既有 `[chunk_id]` marker 驗證區塊混淆/遷移漏 | 驗證邏輯改寫集中(單一函式)、全測試遷移;契約註記 | 中 |
| P4 | LLM 對「支援 claim 代號」遵從弱(誤造 claim-99) | 程式 repair(unknown claim 回餵有效集合);B=1 竄改教訓引用(M5b) | 低-中 |
| P5 | A5 材料清單由程式組裝的格式與 README/對外契約漂移 | 保留 paper_sources 契約;格式變更寫入 STATE/README | 低 |
| P6 | key3 缺設時行為未定 → 使用者跑真實 run 才發現 | Todo 3 明訂 fallback 決策(建議:缺 key3 → 警告 + fallback key2,比照 client_rcs 先例);測試覆蓋 | 低 |
| P7 | claim_chunks 對照表巨大(每 claim 多 chunks × 多論文)→ prompt/輸出膨脹 | 對照表只進第 2 層(程式側),不進 LLM prompt;輸出按 paper 分組 | 低 |

## 下一步行動卡

1. **C2b 執行並回報** → 規劃驗收 → 本計畫更新 code 事實區 + Todo 精修 → 交使用者核准(含待議點)。
2. 待議點(核准時一起定):① 缺 key3 行為(fallback key2 vs exit);② claim 代號格式(短序號 vs paper 前綴);③ aspect=章節時 `_NOTE_ASPECTS` 白名單的取捨(全放開 vs 保留 fallback);④ 對外 marker 呈現(claim 代號 vs 展開 chunk_id)。
3. **提醒使用者:C2c 執行前把 `GEMINI_API_KEY_3` 加入 `.env`(WSL + Windows 兩端)**。
4. **C2c 執行指令包交付時,同時明示執行代理:「C2b Todo 5 真實 run 延後,本次不做」**——真實 run 一律等 C2c 全部完成後、以最終線路(評分 Gemini + `FUNCTIONAL_BATCH_SIZE=8`)一次跑(2026-09-11 使用者定案)。
5. 核准後使用者貼執行指令包 → 執行代理(同 C2a/C2b session RESUME)→ 回報 → 驗收 → commit。