# 按大綱小節生成報告與限定 claim 引用

日期：2026-10-02。狀態：計畫草案，尚未核准實作。本輪只撰寫計畫，不改程式、不執行真實 API。

## 問題與目標

目前 outline 後的一次 report request 接收全部 notes；prompt 要求各節只用該節 supporting_claim_ids，但程式只檢查引用是否屬於全局合法 claims。這無法阻止跨節引用，也不能由引用 ID 合法推定引用支持主張。

本里程碑改為按大綱小節逐一生成，每次只提供該節 claims，驗證後由程式組裝全文並保存大綱與各節結果。目的為縮小上下文、落實每節引用範圍與提高診斷能力，不承諾消除混淆或自動完成學術真實性驗證。

2026-10-02 的 papers/report_20261002_001952_120309.json 可作唯讀案例：12 篇下載、7 篇 notes、69 個 claims、正文引用 58 個不同 claims；有主題範圍偏廣與排除原因不透明的疑慮。這些上游問題不由分節生成修復。

## 範圍

包含：正式 LLM synthesis 的分節 report calls、每節引用驗證與有限 repair、確定性全文組裝、report JSON 保存已驗證大綱與分節結果、fake-client 測試及交接文件更新。

不包含：改搜尋／screening／functional scoring／選文配額、刪除作者資訊等 notes claims、PDF 驗證、排除紀錄輸出、notes checkpoint 完整續跑、報告生成 checkpoint、真實 API run、硬式語義 entailment 判斷。Legacy／deterministic 路徑維持既有行為。

## 生成流程

1. 沿用全部納入 notes 生成全局 outline，驗證所有 supporting_claim_ids 存在；既有未知 ID repair 上限維持。
2. 建立全局 claim lookup。每個小節按大綱順序取得其 supporting_claim_ids 對應的 claim text、aspect 與 paper attribution；只傳入包含目標 claims 的 notes，不夾帶同篇其他 claims。
3. 每次 section request 提供原始 research query、完整大綱的 title/purpose（不含其他節 claim 內容）、當前小節及它的 claims。保持全局 claim-N，不重新編號；同一 claim 可有意識地被多節共用。
4. 要求只寫當前小節正文，不產生報告標題、其他小節或 future directions；提示維持術語、避免重述，區分直接研究結果與可借鑑的設計推論。
5. 驗證 JSON 與正文引用後保存該節結果。依序生成，不並行增加 provider 壓力。
6. 程式從原始 query 組裝報告標題、從 outline 組裝小節標題並拼接正文；LLM 不掌控小節順序。原有材料來源清單仍由本地程式組裝。
7. Future directions 維持獨立 request，接收原始 query 與全部可用 claims，避免被報告小節框架侷限。

## 驗證與錯誤處理

- Section schema 驗證正文非空／最低內容要求；正文不可包含額外 Markdown heading，避免自行改動結構。
- 每節至少有一個 claim marker；所有 marker 必須屬於當前小節集合，即使某 ID 在全局合法，跨節使用仍無效。
- 同一節 schema、缺少 marker、未知／跨節 marker 或額外 heading 問題共用最多一次內容 repair，正常加 repair 最多兩次生成呼叫；provider 原有有界 transport retry 另計。需避免既有 schema repair 與 marker repair 疊加。
- Repair 僅提供該節 claims、合法 ID、當前輸出及錯誤；不能憑 ID 清單盲目替換引用而缺少證據文字。
- 有一節無法通過驗證即停止，不寫入宣稱完整的 report，不略過小節、不降級為無引用段落。錯誤標示小節 index/title；未建立報告 checkpoint，因此成功前節不保證可於下一次重用。
- 程式不能僅憑 marker 驗證宣稱每個 factual sentence 都有支持證據；limitations 改用實際保證：引用 ID 經檢查、內容仍需人工核對。
- 不要求所有 claims 都出現在正文；大綱未選 claim 或正文未引用 claim 不等於錯誤。可保留可用／分配／實際引用 IDs 供比較，不填造支持程度分數。

## 輸出與相容性

- SynthesisResponse 新增 optional outline 與預設空列表 report_sections，讓舊 JSON 仍可讀。
- 分節結果由程式填入 section index、outline title、正文、allowed_claim_ids、實際 cited_claim_ids；不讓 LLM 回傳這些可由程式推導的 metadata。
- 原有 report 字串、claim_chunks、paper_summaries、future_directions 與來源格式保留；notes checkpoint 不改格式。
- 若下游自訂 client 依 prompt 或呼叫數回應，需更新 fake-client 契約與明確文件；既有報告 LLM 呼叫數由 3 變成 2 + 小節數（不含 repair／transport retry）。小節數為 K 時，內容生成含 repair 的上限為 2K；outline/directions 沿用既有預算。增加 request 數與重複上下文，不保證更便宜。

## 預計檔案與驗收

預計修改 models.py、synthesis.py 與受影響的 synthesis/pipeline/main fake-client tests；依程式介面需要同步 pipeline.py，文件更新 HANDOFF.md、STATE.md。實作前再確認現況與必要範圍。

離線驗收：

1. 各節只取得本節 claims；原始 query 與大綱結構存在，其他節 claim 文字不存在。
2. 多篇 claims、跨節共用 claim、同篇部分 claims 的映射正確；全局 ID 與來源 attribution 保持不變。
3. 全局合法但該節未允許的 marker 被拒絕；缺 marker、未知 marker、額外 heading 與 schema 錯誤共用一次 repair，仍無效就停止。
4. 組裝順序、outline/section JSON 與引用 metadata 正確，舊 JSON／deterministic 路徑可讀。
5. Future directions 仍取得全局 claims；任一節失敗不產生完整 report。
6. 使用 fake clients，不載入本機 provider secrets、不呼叫外部服務；focused suites、git diff --check 與 key-pattern scan。真實品質評估需另經授權，不由 mock 通過推定。

## 另一個候選里程碑：papers JSON 排除資訊（尚未納入實作）

可以放在 papers JSON；建議第一階段只記錄「已下載論文的後續 disposition」，保留 papers 為成功下載清單，在 root 新增預設空列表 paper_dispositions。按 paper ID 關聯，內容包括 stage、status、reason_code、reason 與實際取得的 assessment／scores／chunk IDs；缺值以 null 表示。

區分 functional threshold 排除、quota 未選取、無可用 chunks、抽取失敗、notes pending/failed、included；quota 淘汰不可誤寫為品質不合格，抽取或 notes 失敗也不可寫成研究不相關。納入結果與處理錯誤分開，保存原因而非僅用 excluded 布林。

第二階段才擴展至搜尋的年份／venue／去重、screening、OA 缺失與下載失敗事件。這些候選不一定有 PDF 或分數，應另設 selection_events，保存已有 metadata、query、provider、階段、原因及已執行的分數，不塞進 papers 或填假評分。各階段可能重複遇到同篇，需保留 query 與事件而不是盲目以 paper ID 覆蓋。

目前 papers JSON 在 synthesis 成功後組裝，notes/report 失敗可能沒有當輪 papers JSON。排除資訊里程碑應討論將 papers 診斷輸出與 report 成功解耦、用相同 run ID 連結並明列 run status；未完成結果不可冒充完整 report。此部分先提案，另行核准範圍後才實作。

## 唯讀自審

Codex 沒有使用另一環境的 plan-review skill；本節是一般唯讀自審，不宣稱完成該流程。

- 範圍集中在分節報告，排除資訊、PDF 與 checkpoint 為獨立工作，避免一次改動多階段。
- 保留全局 provenance 與獨立 directions；收緊節內引用但不將格式驗證當成學術品質驗收。
- 明列內容 repair 預算、失敗停止、舊 JSON 相容與成本影響。
- 風險：大綱選錯 claims、上游主題偏移、跨節重複與不自然銜接仍可能存在；全文組裝不自動解決這些問題。首版以 query／完整大綱提供上下文，人工評估後再考慮全局潤稿，避免新增無界 LLM 呼叫。
