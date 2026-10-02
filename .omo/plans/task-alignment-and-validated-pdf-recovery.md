# 任務一致性與有效 PDF 取得：里程碑計畫

日期：2026-10-02。狀態：規劃與主代理自審完成；未實作、未執行真實 API。

## 目標與依據

針對 papers/report_20261002_112526_746501：原始 query 正確，但模糊 sub-query 引入 peer review，scoring 將其誤認為直接支持 literature review；兩個下載檔實為 HTML（一個 gzip HTML），仍被計為 PDF 成功。只修 prompt 或只調聚合權重不足以處理兩類問題。

已核對入口：planning.py/build_llm_plan_prompt、query_policy.py/SHORT_QUERY_GUIDANCE、screening.py/build_screening_prompt、ranking.py/rank_papers_embedding、main.py/_search_and_rank 與下載組裝、functional.py/sample_formal_chunks_per_paper 與 score_chunks_functionally、synthesis.py 的 notes/outline/section/directions prompts、pdf_downloader.py、diagnostics.py。

重要訂正：scoring LLM 使用原始 query；正式 chunk embedding 取樣目前使用 query_map 的來源 sub-query，缺少才 fallback 原始 query。

## 里程碑順序

完整方向分三個可獨立驗收的里程碑，依 M1 → M2 → M3 執行；每輪實作僅完成一個。此文件不是實作授權。

### M1：任務理解與跨階段歧義處理

1. 新增 Pydantic TaskInterpretation：task、research_object、expected_output 為簡短文字，scope_boundaries 為清單。這是對使用者輸入的解讀摘要，不是詳細 CoT，也不是憑空增加研究要求。未指定項目明示 unspecified，邊界可為空。
2. SearchPlan 新增 optional task_interpretation，舊 JSON 保持可讀。新 LLM planning 成功結果必須提供有效摘要與原有 queries；同一呼叫完成，保留既有內容 repair 預算，不新增理解階段 API call。Rule-based dry run 不呼叫 LLM、不假造模型理解。
3. 原始 idea 由程式保存，不能被摘要或 follow-up 替換。每個下游判斷同時保留原始 idea；摘要與原文衝突時以原文為準，不靜默擴張任務。
4. 共用通用歧義 guidance，包含至少兩個跨領域對照例：idea → 模糊 query → 修正 query → 原因（literature/peer review 與 MoE/network routing）。明示例子只是方法示範，不是固定領域、關鍵字或排除清單。
5. Planner 允許任務錨點重複；詞彙多樣性集中在方法、評估與其他面向。保留 2–4 詞與 3–4 queries，不為多樣性犧牲任務語義。
6. Screening 的逐篇 decision guidance 加入任務比較，不只在 follow-up 區塊提到歧義。Reason 說明直接支持、可轉用或同詞異義；不同任務不因單一字詞相似被視為核心論文，也不一律排除有具體轉用價值的工作。
7. Functional scoring 明確辨認原始任務與證據任務；直接支持高分需與原始任務一致。不同任務的機制若可轉用，rationale 必須說明機制、對應階段及限制，不可把 peer-review 結果當成 literature-review 已驗證結果。不設定字面關鍵詞黑名單或所有跨任務工作的固定分數上限。
8. Notes 不接收 idea 或任務摘要；忠實保留論文自身任務、研究對象與條件，不為適配研究題目改寫結果，不將網站/書目 boilerplate 強制生成研究 claims。保留 claim ID/來源驗證，不虛構缺少的研究資訊。
9. Outline、section report、directions 及 repair 接收原始 idea 與 optional 任務摘要。明確區分原論文研究發現與轉用建議；建議需要條件與待驗證限制，不呈現為目標任務已證實效果。
10. papers.run 保存原始 query、完整 validated SearchPlan/任務摘要（新增欄位，不刪舊欄位）。摘要傳遞採 optional 參數以相容現有 library callers。Notes checkpoint 不因只新增 report/planning 上下文而無理由失效；如果 notes prompt 政策變更，應更新 notes policy fingerprint，拒絕混用舊政策 notes。舊資料保留。
11. 保留 0.7 max + 0.3 mean、threshold、quota。修正輸出 Mean 為 Aggregated functional utility，呈現 max/mean/max_weight/n_samples；聚合與診斷用同一資料來源，不重算為不同公式。

驗收：fake clients 確認單次 planning 與既有一次 repair、摘要缺失/格式錯誤處理、原文保存；初始/補蒐/repair 都包含通用 guidance；screening/scoring/report 有對應階段要求；notes 無 idea 且保留原始任務；舊 SearchPlan/報告/呼叫可讀；[0,10] 仍為 8.5 且描述正確。使用 literature review、MoE 及未出現在 prompt 的第三領域反例，避免只驗固定例子。Prompt mock tests 只證明傳遞與契約，不能宣稱真實語義品質已通過。

### M2：原始任務與子查詢共同參與 embedding

1. 候選 title+abstract 向量只編碼一次；原始 idea 與 sub-query 各編碼，原始 idea 向量在 run 內共用。
2. semantic_score = 0.5 * similarity_to_idea + 0.5 * similarity_to_subquery，0.5 是可配置的初始假設，不是驗證最佳值。沒有 main idea 的舊呼叫保持原行為；兩個 query 相同時結果等同原相似度。
3. 候選排名先保留 citation/recency 計算，避免同輪混入另一組權重改動；保存 idea/sub-query/semantic/citation/recency 分項與權重，評估是否淹沒語義貢獻。
4. 正式 scoring 與 notes 的 chunk 取樣也以兩種相似度組合；維持章節分類、Method/Results 優先、最多 2/9 chunks、一次 document embedding 與噪音排除政策。
5. Embedding 排名不作語義真值或 hard rejection；screening 仍負責任務判断。不得宣稱 embedding 必定區分 peer/literature review。
6. 新取樣規則/權重必須納入 notes checkpoint policy fingerprint，避免重用不同證據選取規則的 notes。原始與來源 query 均保持 provenance。

驗收：合成向量測試能區分符合 sub-query 但偏離 idea 的案例；檢查缺 main idea 的相容行為、向量重用、來源 query 不覆蓋原文、章節取樣與上限不變。以本次候選及跨任務案例做離線排序比較，記錄 α=0、0.5、1 對核心文獻和可轉用文獻的影響，不為符合單篇預期排序硬調權重。使用本地 encoder cache；需要下載模型時先明示網路需求。未完成實際 encoder 比較時清楚報告限制，不將 fake vector 測試稱語義驗收。

### M3：PDF 驗證、HTML 補救與 Unpaywall OA 補齊

1. 新增結構化 FetchResult 保存 original/final URL、HTTP status、content type/encoding 與 bytes；相容既有 bytes fetcher 注入介面，未知 metadata 保持空值，不捏造。
2. 檢查解壓後檔頭與 PDF parser 可開啟、至少一頁；content-type 只作提示。gzip 回應有界解壓，限制原始/解壓 bytes 與 timeout，避免無界記憶體消耗。採現有 PDF runtime，不為本項安裝另一套解析器。
3. HTML 不直接進全文管線。解析 citation_pdf_url、明確 PDF link 與 ACL/AAAI 已知頁面規則，解析相對 URL；不全面爬站、不使用 LLM 找 URL、不繞過登入/paywall。
4. 原始 provider URL 無效、沒有 OA URL，或頁面找不到有效 PDF 時，對已 screening keep/maybe 且有 DOI 的 paper 使用 Unpaywall GET /v2/{doi}。以 UNPAYWALL_EMAIL 本機設定啟用，未設定明示 skipped；本文件不需真實 email。保留 best_oa_location 與 oa_locations 的 url_for_pdf、host_type、version、license/source provenance。優先 published，再 accepted，再 submitted；版本明示，不改 venue 接收/whitelist 判斷。
5. 每篇最多一次 Unpaywall lookup（run 內 DOI cache），最多三個額外文獻 URL 嘗試，包含 HTML 導出的 PDF 與 Unpaywall 替代 location；不對新 landing page 再遞迴找更多 link。單次 fetch timeout 30 秒，最多五個 redirects；服務錯誤記錄後嘗試已有候選，不自動重跑整輪。沿用 keep-first/maybe-backfill、目標 20，不加入 reject 或突破年份/venue 政策湊數。
6. 取得 PDF 後核對 DOI/title：metadata/首段文本能確認則記 confirmed，明確不符則拒絕；缺少可核对身分證據則記 unconfirmed，不進正式全文管線。避免把任意 PDF 當成本篇；辨識規則需能容忍標題格式與連字差異。
7. 有效且身分確認的補救 PDF 以 atomic write 存入正常下載路徑，與正常 PDF 使用同一抽取/scoring/notes/report 流程。只有這種成功計入 downloaded；無效 bytes 不佔正常 .pdf 路徑。HTML 診斷可選保存在獨立位置並設大小上限。
8. 新增 optional download_attempts（Pydantic records）在 PapersOutput，涵蓋已 screening 但未成功取得 PDF 的候選。現有 papers/paper_dispositions 仍只對有效下載建立，不能直接依賴 diagnostics.update（它會忽略未下載候選）。紀錄每篇/每次 attempt 的來源、階段、格式、reason_code、恢復成功/失敗、PDF 版本與成功路徑。下載期間與結束持久化 attempts，避免只剩總數；保留舊欄位且統計嚴格區分候選數、HTTP attempts、有效 PDF 數、recovered 數、缺口。
9. URL 診斷移除 email/API-key 等 query secrets；不輸出 .env 或 provider headers。原始 OA metadata 與實際取得的 PDF URL 分開保存，不能覆蓋原始 provider provenance。
10. 舊 20261002 輸出與下載檔全部保留供比較，不自動改寫、刪除或重跑。Dry run 不啟用 Unpaywall 或新補救網路操作；保留原 dry-run 階段界線。

驗收：離線 fixture 覆蓋 direct PDF、HTML→相對 PDF、gzip HTML→PDF、缺 OA→Unpaywall、多 locations 第一個無效第二個有效、無 DOI/email、404/限流/timeout、URL loop、過大內容、偽 PDF/損壞 PDF、身分錯誤/未知、修復成功進後續、全部失敗留理由且不進全文、keep/maybe backfill 與成功數。Fake flow 驗證失敗未下載項持久化與舊 JSON 相容。真實 Unpaywall smoke 需本機 email 與使用者明確選擇；不包含完整 LLM rerun。真實 PDF smoke 使用獨立目錄，不覆寫既有 run。

## 外部文件

- Unpaywall API：https://data.unpaywall.org/products/api （DOI endpoint；需要 email）。
- Unpaywall schema：https://unpaywall.org/data-format （url_for_pdf 可為 null；url 可只是 landing page；versions 必須區分）。

## 共同驗收與排除

依里程碑跑相關 focused tests、自審 diff、git diff --check；M1/M3 可能觸及設定/URL，變更前後做 AGENTS key-pattern scan。測試隔離 .env 與所有真實 providers，使用 fake transports，不重現舊 suite 誤連外情況。完成三個里程碑後安排一次隔離的全套測試，分開記錄既存 failures。真實模型品質評估另需額度/權限確認；不以 mock tests 取代。

不調聚合權重、quota、threshold、年份或 venue；不實作 quote 支持句/頁碼、搜尋分頁、官方 venue/track 查證、完整 checkpoint continuation。M3 只記錄下載階段，不擴張為所有檢索排除紀錄。API 錯誤不略過 screening，不切换 LLM provider。

## 主代理自審

- 修正前輪錯述：chunk sampling 優先來源 query；M2 同時覆盖候選與 chunk。
- Screening guidance 原在 query-output 區塊；M1 必須新增逐篇判断規則。
- 任務摘要不能取代原文或憑空加入要求；原文保留並優先。
- Notes 不收到 idea，防止摘要被目標任務同化；report/directions 才交代轉用。
- 改 sampling 會影響 checkpoint；M2 更新 policy fingerprint，拒絕錯誤復用。
- HTML 誤下載是格式驗證缺口；不能只改副檔名或抽取成功判斷。
- 未下載失敗不在現有 dispositions；M3 必須增加獨立 attempts 契約。
- Unpaywall OA copy 版本不等於發表位置；不改原 venue policy。
- 格式有效不等於同一篇；M3 同時設身分檢查與未知狀態。
- embedding/語義 prompt 的成效尚未真實驗證；不承諾必能抓出 review 歧義。

結論：M1 可執行，M2/M3 可按上述初始參數與保守品質門檻執行；0.5 權重、URL budget 與身分辨識規則為明示設計假設。Email 只阻擋真實 Unpaywall request，不阻擋實作與 mock 驗收。實際模型語義品質及 PDF 取得率尚未驗證。
