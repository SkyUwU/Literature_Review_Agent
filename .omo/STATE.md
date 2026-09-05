# .omo/STATE.md — 專案現況小抄（最後更新：2026-09-05）

> 每個 session 開始自動載入（`opencode.json` instructions）。gate 過後由規劃 agent 更新；執行 agent 只讀不改。
> 可隨時用 `@.omo/STATE.md` 重新載入，避免依賴對話記憶。

## 現況（一句話）
- **M1（pairwise-retrieval-eval）已完整收尾 ✅**：Todo 1-6 全完成、smoke 通過（embedding better）、commit+push、gitignore 整理、skill 建立。
- **M2（embedding-retrieval-adoption）已完整收尾 ✅**：Todo 1-4 全完成（換 embedding + coverage References 低權重 + 文件 + 真實 API smoke 驗收通過）、code/docs 已 commit+push。
- **M3A（LLM planner + query-only SearchPlan）已完整收尾 ✅ 並已 commit**（`2a09f7b` code + `c5414a1` docs，154 tests OK + 真實 smoke 過，`SearchPlan.idea` = `ResearchIdea∣str∣None` 且寫入 query 保留追溯）。
- **M3B（PDF 自動下載器）已驗收通過 ✅ 待使用者 commit**：164 tests OK + 真實 OpenAlex smoke 過（OA 覆蓋率 77.3%、真實下載成功）。
- **M3C（main.py 端到端串接 + Amendment 1）已驗收通過 ✅ 待使用者 commit**：183 tests OK；真實完整 run 過（LLM plan key1 3 queries、11 PDF、key2 綜合報告、11 inline citations、AIza 0 命中）。**LLM planner 為正式/預設**（`use_llm_plan=True`、key1）；rule-based 僅備案（`--rule-based` 逃生門 / LLM 失敗 fallback / `--dry-run` 強制）。環境修正：`gemini-2.5-flash` 已下架 → 預設 model 全部改 `gemini-3.6-flash`。

## 下一步
1. **使用者 commit/push（M3B + M3C 一次收）**（驗收已過，指令見 commit 訊息）。
2. **待討論（使用者 2026-09-05 提出）**：`data/papers/` 論文累積策略——每次 run 清空 / 互動詢問 / 維持現況（另有 `/tmp/m3c-*` 殘留清理）。
3. **下一個 milestone 候選**：詳見下方「優化候選（2026-09-05）」——prompt 品質整理（報告連貫性 + 尾端中文 + 筆記覆蓋）優先。

## 優化候選（2026-09-05 使用者提出，未動工）
- **A. prompt 品質整理（最先進）**：報告連貫性四大改法（已評估**全部與現有驗證契約相容**——synthesize_report 只查「≥1 inline marker」與「無未知 chunk_id」，不管句數/標註位置/段落結構）：
  1. 單句多標註：2-3 chunks 支持同一點 → 組複合句、句尾 `[id1][id2]`。（**2026-09-05 使用者主張升級為「引用完整性」**：報告 LLM 看不到 claim 的 chunk 原文、只依據 claim text 生成——「用了某 claim 的事實就應標駐該 claim 全部引用 chunks」；現況只標單一 marker＝缺陷。取捨：多 claim 合成句的全標噪音、語意歸屬判定、部分採用時的全標過度——M4 動工前確認精準標 vs 完整標。）**③ 2026-09-05 使用者再提「claim 標註方案」**：報告 LLM 只看得懂 claim（語意單位），chunk 對它是 opaque id——句子結尾標 `[claim-N]`，claim→chunks 由程式機械展開（變體 A：正文保留 `[claim-N]` + 附 claim 索引；變體 B：程式展開替換回 `[c3][c7][c12]`，對外維持現有 chunk marker 契約、驗證不重寫——建議 B）。優點：句子↔claim 歸屬是 LLM 擅長的事、完整性變機械保證、正文不噪音；代價：報告 marker 內部契約變更（prompt/模型/驗證/測試）。**④ 使用者 2026-09-05 定案「雙 Pydantic 分層」**：第 1 層 LLM 契約（`SynthesisReport`：report 含 `[claim-N]`、future_directions）只做「寫報告+標 claim 代號」；第 2 層程式契約（`SynthesisResult`：與第 1 層類似 + 新欄位 `claim_chunks`＝各 claim 代號→引用 chunks 對照，future_directions 由 claim 展開），程式把第 1 層機械展開產生第 2 層。特性：LLM 任務最小化、完整性機械保證（claim→chunks 在筆記階段已驗證）、兩層隔離（改對外格式不動 LLM 契約）、驗證分層。**2026-09-05 使用者再定案**：第 2 層 report **只保留 claim 標註版、不做 chunk 展開版**（需要時再議，不做冗餘）；future_directions 同以 claim 為輸入（`supporting_claim_ids` 取代 `supporting_chunk_ids`），與 report **共用同一張 `claim_chunks` 對照表**（全域唯一）。
  2. 句中子句標註：marker 可放子句/逗號後。（**2026-09-05 使用者確認**：與 A1 同機制——多 claim 並排各自標＝子句級（A2）；多 claim 疊加支持同一點＝句尾聚合（A1）。展開行為相同，僅標註位置不同，M4 時確認粒度。）
  3. 強制邏輯轉折詞：However / In addition / Specifically / Consequently 等，措辭要「適量自然、避免機械重複」。
  4. 主題式合成：報告歸納為 2-3 主題段落（背景與範疇 → 現有工具與機制 → 瓶頸與未來挑戰），不要照清單宣科。
  5. **材料來源清單移出 prose**：prompt 那句「Close the report with 材料來源清單」移除，改由 pipeline 層程式組裝——一次收掉報告尾端中文瑕疵。
  6. **逐篇筆記覆蓋增強**（使用者 2026-09-05 提出）：現況 claim 的 aspect 是 LLM 自由分類（白名單 contribution/method/experiments/results/limitations/other），輸入被 `llm_input_cap=40` 截斷（超過則 strided 抽樣），**不保證涵蓋全篇**。改法：要求每個 aspect 至少一條 claim / 驗證主要段落都被引用 / 輸入截斷策略改進。**已查證（2026-09-05）**：`gemini-3.6-flash` context window = **1M tokens**（$1.5/M input）——一篇論文全文 ~20-40K tokens（<context 4%、單篇筆記成本 ~$0.02-0.05），**cap 40 只用 ~2% context，屬早期保守設計**，可提高 `llm_input_cap`（使用者 2026-09-05 裁示：**不必全量**——大幅提高上限即可，小篇論文等於全量、大篇才截斷；實際數量後議，欄位現支援 1-200）；只有超長 survey 論文才需做法 B 分段滾動摘要（多 call）。claim 品質另可要求 who/what/how/why + 數值 + 頁碼（自含式 claim；執行注意：頁碼已由 `ChunkReference` 自動附帶，**不需叫 LLM 寫**；可選強化：程式驗證每 claim text 含 ≥1 數字，先看純 prompt 效果再上）。**措辭需 aspect-aware**（2026-09-05 使用者提醒）：硬套「state the method」會誤導 limitation/contribution claim——應依 aspect 對應細節（method→做法步驟、results→數值/資料集/比較、limitations→限制情境），並要求「只報告 chunk 有的數值，不得發明」。**筆記平行化不列為候選**：使用者 2026-09-05 裁示暫不考慮。
  7. **兩階段生成（A1-A4 仍不夠連貫時的升級路徑）**：先讓 LLM 產出大綱（outline / section plan），再逐 section 生成並合併（多 call、成本上升）；僅當單次 prompt 改進不足才考慮。⚠️ **使用者 2026-09-05 提醒**：這部分（以及報告生成相關設計）她有一些不同想法要修改——**M4 實行前必須先與使用者討論確認，不得照候選直接執行**。
  8. **M4 驗收方式**：同一 query 跑改前/改後真實 run，對比報告品質（連貫性、per-aspect 涵蓋、citation 正確率），人工評分留證據。
- **B. PDF 檔頭驗證（M3B 殘留）**：`download_pdf` 下載後檢查 magic bytes `%PDF-`，非 PDF 視同失敗 → 自動遞補（避免 HTML 偽 PDF 佔名額，如真實 run 的 W4205941964）。
- **C. 完整報告存檔**：`main.py` 支援把完整報告輸出成 JSON 檔（真實 run 只留了 report_shape，完整 prose 沒留底）。
- **D. Unpaywall 補查**：背景見「M3 方向」段落。
- **E. 報告輸入瘦身（Q2，2026-08-30 已決策、未實作）**：報告 prompt 現況送兩區塊——A「逐 chunk 證據摘要」（`EvidenceSummary`，RCS 產物，每篇配一份全文摘要的常數倍數）+ B「逐篇筆記」（claims）。**方向已定：拿掉 A、輸入只剩最瘦的 B（逐篇筆記）**，源於 `corpus-single-rerank-call.md`（Q2 另案，:11/:17/:32/:112）。未實作原因：當時決策條件是「先看 corpus-wide RCS 的實測 A 筆數再決定」。⚠️ 動工前須與使用者確認（她對報告生成設計有主導權，且 2026-09-05 明示實行前先討論）。
3. M1 遺留：opencode 尚未重啟（skill 需重啟才生效）；Windows 端尚未 pull 同步。
4. **skill 待辦**：計畫驗證做成 `review-plan` skill（先有 checklist：`.omo/notes/plan-review-checklist.md`；skill 之後交執行代理建立，重啟生效）。

## M3 方向（使用者偏好，2026-09-04 定）
- **先做部件、最後用 main.py 串接**（不是先串骨架）。部件順序：① LLM planner（M3A，進行中）→ ② 抓 PDF → ③ main.py 串接。
- **M3A = LLM planner ✅（已 commit）**：`SearchPlan.idea` 改 `ResearchIdea∣str∣None`（query-only，寫入 query 保留追溯）、新增 `create_llm_plan(query, client)` 用 LLM 生成 `SearchPlan`（`generated_by="llm"`），共用 `llm_evidence.generate_validated`，開發期 fake client → 最後真實 smoke。`create_rule_based_plan(query)` 保留為 fallback。計畫：`.omo/plans/llm-planner.md`。commit：`2a09f7b`（code）+ `c5414a1`（docs）。
- **抓 PDF（M3B）✅ 已完成**：`Paper.open_access_pdf_url` + OpenAlex `best_oa_location.pdf_url`、`pdf_downloader.py`（`download_pdf` / `download_and_backfill`）、ranking 三成分標準化 0~1 等權（`(3*title+abstract)/(4*|terms|)` + citation + recency）、OA 覆蓋率統計。真實 smoke OA 覆蓋率 77.3%。
- **main.py 串接**（M3C 候選）：idea → plan → search → 抓 PDF → pipeline → 報告 的完整入口，參數寫死 + 可互動式問 query。
- **Unpaywall 補查（M3C 候選，2026-09-05 使用者定）**：對 `failed_no_oa` 的論文用 DOI 打 Unpaywall 補查一次（免費無 key）。先只做 Unpaywall（不做 arXiv），smoke 統計補到幾篇，數據決定是否還需要 arXiv 補查。**背景**：OpenAlex 的 OA 資料來源之一就是 Unpaywall，預期補到位比例偏低；執行代理 Q2 也證實 OpenAlex 給的 OA 連結很多本來就是 arXiv —— arXiv 補查邊際效益更低。

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
- **commit 指令只列「會被追蹤且該進 git」的檔案**（2026-09-05 使用者提醒）：`.omo/notes/`、`.omo/drafts/`、`.omo/evidence/` 在 .gitignore（內部工作產物，不進 repo）；使用者 `git add` 時被忽略屬正常，**不要用 `-f` 強加**。決策紀錄以 `.omo/plans/` + `.omo/STATE.md` 進 git。

## M3A（LLM planner）驗收現況（2026-09-04）
- **功能已驗收通過 ✅**：154 tests OK（`test-suite-m3a-llm-planner.log`）；無 key fake 驗證 + 真實 Gemini smoke（`smoke-m3a-llm-planner.log`）都過，`generated_by=llm`、`idea=None`、無 key 洩漏。
- **code 實作確認為 query-only**：`models.py` `SearchPlan.idea` 可選（`None`）、`create_llm_plan(query, client)`、`planning.PlanningError`、共用 `llm_evidence.generate_validated` 抽出且 synthesis 已遷移、`create_rule_based_plan(query)` fallback。ResearchIdea 模型保留。
- **待辦**：commit/push **由使用者做**（計畫檔 Commit strategy 有分兩次指令）。

## M3B（PDF 自動下載器）驗收現況（2026-09-05）
- **功能已驗收通過 ✅**：164 tests OK（`test-suite-m3b.log`）；無 key fake e2e（`smoke-m3b-fake-e2e.log`）+ 真實 OpenAlex smoke（`smoke-m3b-real-openalex-v4.log`）都過。
- **code 實作確認**：`models.py` `Paper.open_access_pdf_url`（`HttpUrl | None`）、`search.py` `REQUESTED_FIELDS` 加 `best_oa_location` 且 `paper_from_openalex` 填值、`ranking.py` lexical 標準化 0~1（標題 3x 權重保留）+ 三成分等權相加（總分 0~3）、`pdf_downloader.py` `download_pdf(paper, dest_dir, *, fetcher)`（可注入、dest_dir 由呼叫者決定）與 `download_and_backfill`（遞補 + `DownloadStats` 兩比例 + `shortfall` + `stats_path` JSON）。
- **真實 smoke 數據**：22 候選 → 17 有 OA 連結（`oa_ratio_candidates=0.773`）→ 成功下載 2 篇（277KB + 5.9MB）、1 篇無 OA 記錄。**結論：OA 覆蓋率 77.3% 不低，暫不需加 arXiv/Unpaywall 備援**（留待日後數據判斷）。
- **已知非阻塞小瑕疵**：HANDOFF.md 寫「163 tests」但實際 164（文件數字小出入）；真實 run 的 report 尾端中文（unicode 跳脫）附加段落——都記下，之後一併處理。
- **待辦**：commit/push **由使用者做**（指令見下方 commit 訊息）。

## M3C（main.py 端到端串接）驗收現況（2026-09-05）
- **功能已驗收通過 ✅**：183 tests OK（`test-suite-m3c-amendment1.log`）；fake e2e（`smoke-m3c-fake-e2e-amendment1.log`）、無 key 真實 dry-run（`smoke-m3c-real-dryrun-amendment1.log`）、**真實完整 run**（`smoke-m3c-real-full.log`，291s）全過。
- **真實完整 run 證據**：`plan.generated_by="llm"`（key1 產 3 queries）、真 OpenAlex 11 PDF 到 temp、`failed_extractions=[W4205941964]`（HTML 偽 PDF 容錯跳過）、報告含 2 篇 include/consider 論文、2 條附 `supporting_chunk_ids` 的 future directions、**11 個 inline citation markers**、Langfuse flush、AIza 0 命中、`data/papers/` 未碰。
- **Amendment 1 實作確認**：`use_llm_plan=True` 預設（key1）；`--rule-based` 逃生門；dry-run 強制 rule-based 零 key；key1 缺 → 警告 + fallback 不 exit / key2 缺 → exit 1（報告無備案）。跨文件口徑 grep（`llm-plan`/`rule-based by default`）零命中。
- **環境修正（外部 API 下架）**：`gemini-2.5-flash` 404 → 全專案預設 model 改 `gemini-3.6-flash`（llm_evidence/pipeline/pairwise_eval/retrieval_eval + 測試 assert）。
- **待辦**：commit/push **由使用者做**（指令見 commit 訊息）。

## 架構已知現況（覆蓋包 coverage pack）
- 覆蓋包（`max_chunks_per_paper=6` + References 低權重）目前**沒有**作為逐篇筆記輸入；逐篇筆記輸入 = 該論文全部 chunk 用 `llm_input_cap=40` 截斷。覆蓋包只當綜合報告白名單來源，在現行 pipeline 實作下**近乎多餘**。
- **使用者裁示：視為已知、先不處理；之後討論架構時不把覆蓋包當作 pipeline 一環**。詳見 `.omo/notes/architecture-futures.md`「覆蓋包 coverage pack 現況」。
- **補充查證（2026-09-05）**：`build_synthesis_prompt` 的筆記區塊與 `_allowed_marker_ids` 用的 `coverage_chunk_ids` 其實是「claims 實際引用的 chunk 集合」（synthesis.py:101 `coverage_chunk_ids=sorted(referenced)`），**不是 coverage.py 的覆蓋包**；報告 allowed 白名單 = RCS summaries（usable 論文）∪ 筆記引用集合，覆蓋包未進入。報告 inline marker 驗證只查「存在於供應清單」（synthesis.py:582-590），**不驗證句子↔chunk 語意對應**——這是 A6 自含性重要的原因。

## M1 成果摘要
- pairwise eval：`overall_verdict: embedding better`（4/6 embedding 贏、1/6 lexical 贏 survey、1/6 平手）。證明 embedding 檢索整體優於 lexical，但有例外。
- 檔案：`pairwise_eval.py` + `test_pairwise_eval.py`（142 tests OK）+ embedding_retriever（cached 版）+ `--api-key-suffix`。

## 卡點
- 無阻塞。M2 完成。

## 大架構完成度（AGENTS.md line 43-47）
- 第 1-4 段（ResearchIdea → SearchPlan → OpenAlex → filter/rank/select）：**SearchPlan 產生：LLM 為正式/預設**（`planning.create_llm_plan`，M3C 起完整 run 預設走 key1）；rule-based 為**備案**（`create_rule_based_plan`：`--dry-run` 強制使用 / LLM 失敗時 fallback）。⚠️ 2026-09-05 修正紀錄：曾誤寫「預設 rule-based」，與 9/2 roadmap「M3 = LLM 取代 rule-based」方向相反，已於 M3C 計畫 Amendment 1 修正。
- 第 5-10 段（PDF extraction → chunk → embedding RCS → assessment → notes → synthesis）：**全完成**。
- **主要缺口（M3C 已關閉）**：完整入口已由 `main.py` 串接（query → SearchPlan{LLM 預設} → 每 query 各自 search/rank/download（共享去重）→ 抽全文 → 綜合報告）。**剩餘候選**：Unpaywall 補查、prompt 品質整理（報告尾端中文瑕疵）、`data/papers/` 累積策略。

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
