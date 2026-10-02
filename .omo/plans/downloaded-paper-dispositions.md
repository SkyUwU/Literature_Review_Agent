# 已下載論文的處理紀錄與 papers JSON 診斷輸出

日期：2026-10-02。狀態：已授權撰寫計畫，尚未開始實作；新對話依本計畫確認範圍後執行。只處理一個有界里程碑，不使用子代理，不跑真實 API。

## 問題與目標

目前 papers JSON 保留成功下載論文，但看不到它們為何未進入 synthesis。Report 只留下納入者的 assessments；functional selection 的原因把 quota／threshold 合併描述，且無 scores 的論文在 legacy assessment 中填 utility_score=1.0。這個預設值不能在診斷中冒充已執行的評分。

另外 papers JSON 在 report 成功後才保存；notes 或 report 失敗時，當輪下載與處理結果可能沒有輸出。目標是在保留 papers 清單語義及研究政策的前提下，讓每篇成功下載論文都有處理狀態、原因和實際已有分數；失敗 run 仍留下可辨識的診斷檔。

案例：papers/report_20261002_001952_120309.json 有 12 篇下載但只有 7 篇 notes，需查明另外 5 篇的實際排除原因。不得從題名或舊 report 回推、補造分數；完整診斷由實作後的新 run 取得。

## 範圍與介面

- PapersOutput 新增 `paper_dispositions`，預設空列表以相容舊 JSON；現有 `papers` 仍只列成功寫入的 PDF，既有 metadata／query／local_path 保留。
- 每個唯一 downloaded paper ID 對應一筆 disposition，引用 papers 中 metadata，不重複嵌入全文或秘密。至少含 paper_id、最後處理 stage/status、selection_status、reason_codes、reason、nullable functional_score、assessment、scored_chunk_ids、notes_status 與 sanitized error。
- Stage/status／reason 使用 Pydantic 與明確 enum。採樣／selection 依已執行階段生成診斷，不解析英文 rationale 字串猜原因。
- Selection 原因允許列表：同時不過 threshold 與 quota 可同時標記。保存實際 threshold、query group、quota、group rank 與 n_samples 供解釋；未執行或未知欄位使用 null，不拿預設值補空。
- `run` 增加 run_id、status（in_progress/completed/failed）、failed_stage、sanitized error 與 notes_checkpoint_id（如有）。Report 與 papers 成功輸出仍共用時間戳；run_id 只用於關聯，並不代表新增 resume 功能。

## 狀態語義

| 情況 | 記錄方式 |
| --- | --- |
| 已下載，後續尚未開始 | pending；其他 stage 尚未評估 |
| PDF 抽取失敗 | extraction failed + 原因；functional_score=null |
| 無可用 scoring chunks | no_usable_chunks；實際分數=null，legacy assessment 的 1.0 不算真實評分 |
| functional 已評分但未選取 | excluded；threshold_not_met／quota_not_selected；保存實際分數與來源 chunk IDs |
| 選入但未有可用 notes chunks | notes no_usable_chunks；不謊稱成功或研究不相關 |
| notes 未執行／生成失敗／成功或 checkpoint 重用 | pending／failed／completed／reused；保存真實狀態與錯誤 |
| 已參與 synthesis | selection included + notes 成功；不能由此推定 report 已成功 |
| report section／directions 失敗 | run failed；既有成功 notes 仍成功，不把所有論文改成 excluded |

研究排除、處理失敗與尚未評估分開；notes 失敗也不能宣稱該篇品質不合格。引用驗證結果與語義品質判定不可混淆。

## 實作順序

1. 在 models.py 定義 disposition／run 診斷 schema；補舊 JSON 讀取及序列化測試。
2. 為 functional selection 建立結構化診斷，沿用原先 rank／quota／threshold 計算，不改 include/exclude 結果或 legacy public assessment 契約。無 scores 時在新診斷寫 null。
3. 透過 caller 持有的 typed run diagnostics collector，把 extraction、sampling、selection 與 notes 狀態跨 main／pipeline 保留；可用 optional observer/callback 傳遞，避免改變現有 synthesis return type。既有 direct callers 不需提供 collector，原功能仍可使用。實作時檢查缺少 scores、prepared document 被排除及 skipped notes 的邊界。
4. 下載完成後建立完整 PapersOutput 與 pending dispositions。主入口先保存同一時間戳的診斷快照，後續在抽取／selection 完成及逐篇 notes 狀態變動時更新；使用暫存檔＋atomic replace，避免寫入一半的 JSON。
5. 成功時更新 run completed 並保存 report；受控例外時更新 run failed、保存已有結果，維持原本非零 exit 與錯誤，不吞例外或生成不完整 report。若診斷寫檔也失敗，明示寫檔錯誤且保留原始 pipeline 錯誤。不承諾硬關機、強制 kill 或磁碟故障都能保存完整資料。
6. Dry run 不新增持久化 papers／report，保留現有契約。真實 CLI 與直接函式呼叫的輸出責任需明確，不讓測試或 library caller 無意寫入全局 data/outputs。

## 排除事項

不記錄未下載候選的年份／venue／screening／去重／OA／network 排除事件，這些留待 selection_events。也不改 provider、搜尋政策、取樣、評分、配額、分節報告、PDF 下載有效性檢查、notes manifest 或完整續跑。不刪除既有 PDF、report 或 checkpoint；不從舊資料填造原因；不增加 LLM 呼叫。

## 預計變更與驗收

預計 models.py、functional.py、pipeline.py、main.py、相關 focused tests、HANDOFF.md、STATE.md；必要時小型本地診斷模組集中 collector／atomic writer，保持職責清楚。若現況與計畫衝突，停止確認。

離線 fake-client 驗收：

1. 下載 N 篇則 dispositions 精確覆蓋 N 個唯一 paper IDs，沒有未下載候選混入 papers。
2. Threshold、quota、雙重原因與 included 被正確區分；判定結果與改動前一致。
3. 無可用 chunks／抽取失敗時 score=null，已評分者保留真實分數與 chunk IDs；不暴露假 1.0。
4. Notes 失敗後其他 paper 成功／checkpoint 重用狀態正確，run failed 且沒有完整 report，papers 診斷仍保存。
5. Scoring、manifest mismatch、report section／directions 失敗時保存已有狀態，未執行項保持 pending，不捏造處理結果；collector 有提供與未提供均測試。
6. 成功 run 的 report／papers 同時間戳、run status 正確；dry-run 不寫持久檔；舊 JSON 可讀。
7. Sanitized errors 不含 keys／headers／完整 provider request；mock 寫檔失敗時明確報錯，atomic 更新不留下半份有效檔。
8. Focused suites 與 git diff --check、key-pattern scan；不載入秘密、不跑真實 API。完整 suite 的既有限制與本輪驗證分開交代。

## 一般唯讀自審與限制

Codex 未使用另一環境的 plan-review，本節是一般自審。計畫保持下載後單一里程碑，區分研究排除與錯誤，保留政策／舊介面並讓失敗 run 留下診斷。主要風險為跨階段 collector 狀態一致性、例外路徑與檔案寫入錯誤；以 fake flow 和中斷點測試驗收，不把 schema 通過當學術品質驗證。診斷輸出不等於可續跑 checkpoint。
