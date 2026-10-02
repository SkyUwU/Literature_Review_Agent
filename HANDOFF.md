# Literature Review Agent — Handoff

## 閱讀順序

1. AGENTS.md：協作與安全規則、正式路徑摘要。
2. .omo/STATE.md：狀態快照、待辦與證據可用性。
3. .omo/plans/<milestone>.md：該里程碑的權威範圍與驗收條件。
4. .omo/evidence/：執行 log／驗收證據；它可能是未追蹤資料，Windows 與 WSL checkout 不保證同步。

本檔只保留接手工作所需的穩定事實與操作注意事項；歷史實作細節請查計畫、Git 歷史及原執行環境的 evidence，不要把未找到的 log 當成未曾存在。

## 專案目標與資料流

專案實作 ADSL summer-project Task 1A：由研究問題檢索論文、擷取 PDF 證據、評估並產生可追溯的文獻綜述與未來方向。

正式端到端路徑：

```text
query → LLM SearchPlan（3–4 個短子查詢；fallback 為 rule-based）
→ Semantic Scholar（空結果／失敗／無 key 時使用 OpenAlex）
→ Semantic Scholar provider 層年份／venue 限制、DOI 摘要補齊、本地年份／venue 後衛、embedding paper ranking
→ bucket sampling + LLM screening（可提出 gap follow-up）
→ OA PDF 下載 → pymupdf4llm 抽取、章節切分
→ 章節分類與 embedding 取樣（scoring: Method/Results 各 1；notes: Abstract/context 1 + 四類各至多 2，最多 9）→ functional scoring
→ per-paper claims → outline/report/directions 三階段 synthesis
→ JSON 報告及 downloaded-papers 清單
```

重要路徑區分：

- literature_review.main 是正式 end-to-end 入口。--dry-run 使用 rule-based planner，只跑至下載，不呼叫 Gemini，也不保存報告；為搜尋可載入 .env 中唯一的 OPENALEX_API_KEY。
- 正式證據路徑以 `functional.sample_formal_chunks_per_paper` 逐篇章節分類與 embedding 取樣為準。Scoring 優先選 Method、Results 各 1 個 chunk，缺少時由其餘候選補足至 2；逐篇 notes 選 Abstract/context 1 個，再從 Method、Evaluation Setup、Results、Limitations/Future Work 各至多 2 個，總數上限 9。References／acknowledgments 排除；Appendix 僅在子章節能歸入五類時保留。每篇 chunks 僅做一次 embedding，向量供兩階段選取共用。corpus-wide embedding／lexical retrieval 與 RCS 是 CLI、比較或 legacy 路徑。
- 報告中的 [claim-N] 連到程式組裝的 claim-to-chunk/paper provenance。生成內容只應依賴供應的 chunks；形式驗證通過不代表學術品質已經人工確認。
- LLM report 先生成全局 outline，再依序生成每個小節；每次只提供該節 claims、原始 query 與全局大綱的 title/purpose。每節的 schema／缺引用／超出該節集合／額外 Markdown heading 共用一次內容 repair，仍失敗即停止。程式組裝標題與順序，report JSON 新增 optional `outline`、`report_sections`（舊 JSON 可讀）；future directions 仍獨立使用全局 claims。K 節正常共 K+2 次 synthesis calls，不含 repair／provider retry。未加入報告 checkpoint；ID 驗證不代表每句有引用或支持證據。
- 逐篇 notes 的 LLM 輸入使用每次 request／batch 內的短代號 `C1`、`C2` 等；claims 與 coverage 的代號通過驗證後，由程式還原完整 chunk ID。未知代號維持一次 repair，仍不合法即該篇失敗。報告與 checkpoint 保存完整 ID，既有成功 notes 可維持原格式；此變更不改善 `--resume-notes` 仍重跑上游階段的限制。
- metadata／abstract 評估不等於全文評估；chunk-based synthesis 也不代表完整審閱每篇論文。
- 正式章節分段目前合併各頁 Markdown 後切分，chunk 的 PDF 頁碼為空；legacy 分頁文字切分可保留頁碼，不能據此宣稱正式路徑已完成頁碼映射。Claim evidence 的 quote 固定取來源 chunk 前 240 字元，可協助辨識來源，不保證直接支持 claim；核對主張仍需閱讀對應 chunk。

## 目前已知研究與操作政策

- 初始規劃、screening 補蒐與 repair 共用通用任務錨定／歧義消解提示：每個 query 保留原始核心任務、研究對象或領域（允許明確同義詞），不可因詞義相近而換成其他任務。跨領域例子僅供說明，不是必用關鍵字。Repair 保留原始研究問題，既有 2–4 words 驗證與一次 repair 預算維持。語義約束目前為 prompt guidance，未加入硬式語義判定、分頁或零命中自動改寫。

- 發表位置與 PDF host 分開：`Paper.publication_venues` 保存 provider、metadata path、source ID/type/link；`pdf_host` 僅為配置的 PDF URL hostname（不代表 redirect 最終 host）。`venue_verification=provider_reported` 表示 provider metadata，不能解讀為正式論文集／OpenReview 接收查證；找不到發表來源時為 `unconfirmed`。
- SS 額外讀取 `publicationVenue`（缺漏時相容舊 `venue`）。OpenAlex 檢查 primary、全部 locations 與 best-OA 的非 repository／非 submittedVersion 來源；本地 whitelist 比對所有記錄的發表來源，PDF 在 arXiv 不構成排除條件，只有 arXiv 則不能推定頂會。Legacy `venue` 欄位保留供舊路徑使用。尚未加入官方 proceedings／OpenReview 補查或 Student Abstract track 排除政策。
- LLM 初始規劃與 screening 補蒐共用短 query 規則：每條 2–4 個空白分隔詞，詳細需求放在 `purpose`／`target_gap`／`reason`。本地驗證不合格時沿用既有一次 repair 預算，不截斷 query、不增加重試。Rule-based dry-run 的原有 query 延伸方式保留。
- 補蒐全數零候選時，不呼叫 screening LLM；記錄缺口仍未補足，保留初始已驗證的 decisions 繼續下載。部分 query 為零時仍篩選實際候選；任何有候選的 screening 驗證失敗仍中止。`priority` schema 明列 `keep`／`maybe`／`reject`。
- `[search] returned_candidates` 是這頁收到的原始 records 數；`total_matches` 是 provider 回報的全部命中數，未提供時為 `unknown`。例如 `returned_candidates=100 total_matches=216 with_abstract=97 skipped=3` 表示只收到 100 筆，97 筆通過 metadata／abstract 處理。`SearchResponse.total_candidates` 統一為實收筆數，新增 optional `total_matches` 保存總命中數；舊 `provider_total` log 名稱已移除。
- 年份預設為最近三年（2026 年即 2024 起），並使用 FilterPolicy 作為 provider 統一後衛。
- 預設 venue whitelist 是硬篩選；Semantic Scholar 搜尋會將已辨識 conference 名稱／alias 與 year range 傳給 provider，之後仍由本地 whitelist 核對。OpenAlex fallback 仍以本地篩選為準。`[search]` 統計列 provider 回傳、abstract 可用、年份後、venue 後及最終數量。未命中可能來自 venue 欄位缺漏／變體、期刊或預印本不在 whitelist；探索性執行可明確指定 `--venues none`，不可把它解讀為原政策下的同一實驗。
- 每個規劃 query 分別搜尋、排名及分配下載目標；跨 query 以 DOI（無 DOI 時 title+year）去重。預設 data/run/ 每次真實執行會清空重建；使用 --dest-dir 指定位置時不清除該位置。
- 只有成功寫入的 PDF 會列在 papers_*.json；每輪報告與 papers JSON 寫入 data/outputs/ 並共用時間戳。dry run 不寫這兩個 JSON。
- 章節分布統計（SD）在 synthesis 時印至 stdout，統計 scoring 的選取結果。Notes 輸入最多 9 chunks；以預設 chunk 大小估計，每篇約 3,000–4,500 prompt tokens，常需約兩個 2,500-token notes batches；實際 token 數以 provider log 為準。

## Gemini 呼叫與失敗處理

- Gemini provider mode 可分配四個 Gemini key 群組：planner + screening 共用 `--plan-key`（預設 1）、functional scoring 使用 `--scoring-key`（預設沿用 `--notes-key`）、per-paper notes 使用 `--notes-key`（預設 2）、report 使用 `--report-key`（預設 3）。例如 `--plan-key 2 --scoring-key 3 --notes-key 4 --report-key 5` 可分配 Key 2–5；未指定 `--scoring-key` 時維持舊版 scoring/notes 共用同一 key。
- 每把 key 有獨立速率追蹤；預設最多 20 次／process，預設約 13 秒 pacing。stderr 的 [llm] 紀錄包含 stage、key hash、等待時間、呼叫數及狀態，不會輸出完整 key。
- Gemini 503／overload 會在同一模型、同一階段做最多 3 次 exponential backoff 重試（約 15、30、60 秒，另加少量 jitter）；SDK 內層重試設為單次，避免超出應用層次數。429 依 provider 訊息分類；有 per-minute retry hint 時最多有限重試，日額度耗盡不重試。planner、screening、scoring、report 重試耗盡仍中止階段；per-paper notes 會記下失敗 paper，繼續處理其他論文並使用 checkpoint 等待續跑，不產生不完整 report。
- OpenAlex 503 若訊息指出 anonymous search paused，需使用 OPENALEX_API_KEY；OpenAlex 504 query_timeout 則表示該次搜尋逾時。Dry run 的 rule-based planner 會把輸入 query 原樣延伸為 survey/literature-review/comparison 子查詢，應輸入精簡的主題詞而非長篇研究問題。發生 timeout 時檢查 stdout 的 Search plan，縮短導致錯誤的子查詢後再執行；不要盲目重送同一條過長 query。
- 啟動任何真實 run 前，請使用者確認本次會用到的每把 key 在 https://ai.dev/rate-limit 的剩餘額度。不得假設額度，也不得自動重跑整輪。
- .env 僅為本機秘密設定；不要讀出、貼出或提交 key。Gemini 與 Semantic Scholar key 應按 stage 的實際需求準備。

## Groq provider 與 notes checkpoint

- 明確設定 `LLM_PROVIDER=groq`，或未指定 `LLM_PROVIDER` 且有 `GROQ_API_KEY` 時，Groq 負責 planner、分批 screening／全域 gap 彙整、functional scoring、per-paper notes 與 report；由 `uv sync` 安裝 Groq 官方 Python SDK，透過 Chat Completions API 呼叫。模型預設為 `openai/gpt-oss-120b`；`GROQ_MODEL` 可統一覆寫，`GROQ_MODEL_PLAN`、`GROQ_MODEL_SCREENING`、`GROQ_MODEL_SCORING`、`GROQ_MODEL_NOTES`、`GROQ_MODEL_REPORT` 可分別覆寫階段模型，階段值優先於全域值。使用模型前請先確認 Groq Console 中的 model ID、JSON Schema 支援與額度。
- 混合模式下，screening 依約 3,000 個估計 prompt tokens 的預算切批；notes 依 section 切批，預設每批約 2,500 個估計 prompt tokens，可用 `GROQ_NOTES_BATCH_TOKENS` 調整。所有 Groq 階段共用 process 內 TPM tracker：依歷史 usage 校準估值、預留輸出額度、追蹤實際 tokens，並採用 provider remaining/reset headers。有 retry-after 的 TPM 429 與 503 都有界重試；日額度錯誤不盲目重送。Notes 批次輸出經 Pydantic 與 chunk ID 驗證，再保留來源 ID 本地合併，不額外呼叫 LLM 整合。任一 paper notes 失敗時先完成其他論文，checkpoint 記錄 pending paper IDs；有未完成項時不產生 report。
- 每輪真實流程會印出 `[notes] checkpoint_id=<run-id>`，checkpoint 位於 `data/outputs/notes_checkpoints/<run-id>/`。續跑命令：`uv run --env-file .env python -m literature_review.main --resume-notes <run-id>`，並輸入相同 research query；續跑仍會重做搜尋、下載、抽取與 scoring，已完成的 notes 會重用，只補缺漏 notes。Manifest 比對 query、入選 paper、provider、policy 與來源內容，不相符即拒絕續用。Gemini provider mode 的 notes 使用 `--notes-key`；OpenAI mode 則使用 OpenAI。
- Groq 503 在同一模型最多重試三次（約 15、30、60 秒加 jitter），耗盡後停止該輪。Groq 使用 JSON Schema best-effort mode，並以既有 Pydantic 驗證及有限 JSON repair 檢查輸出。文件所列免費額度會變動；完整 run 前確認所選 provider 的用量／權限；只有實際使用 Gemini 時才檢查對應 key 的 AI Studio rate limit。
- 每次 Gemini／Groq 呼叫會在 `[llm]` log 記錄 `prompt_chars` 與 `prompt_utf8_bytes`，不含 JSON schema 與 API protocol overhead。Groq 呼叫另記錄 `prompt_tokens`、`completion_tokens`、`total_tokens`（provider usage）；送出前的 token 數仍是估值，成功回應及錯誤的 rate-limit headers／retry-after 用於調整下一次等待。
- `.env` 範例為 `GROQ_API_KEY=<你的 key>`；`GROQ_NOTES_BATCH_TOKENS` 預設 2500。模型覆寫設定見 `.env.example`。不要提交 `.env` 或把 key 貼到聊天、log、文件。

## OpenAI provider

- 在 `.env` 設定 `LLM_PROVIDER=openai` 與 `OPENAI_API_KEY`，正式流程的 planner、screening、scoring、notes、report 全部使用 OpenAI 官方 SDK；SDK 預設使用 OpenAI API endpoint，不需設定 Base URL。只設 `OPENAI_API_KEY` 不會改變路由，避免同時設定多家 provider 時選錯。
- 預設模型為 `gpt-5.6-luna`；`OPENAI_MODEL` 可統一覆寫，`OPENAI_MODEL_PLAN`、`OPENAI_MODEL_SCREENING`、`OPENAI_MODEL_SCORING`、`OPENAI_MODEL_NOTES`、`OPENAI_MODEL_REPORT` 可個別覆寫階段。OpenAI client 透過 Chat Completions JSON Schema 輸出並保留本地 Pydantic 驗證；log 記錄 provider 回報的 input/output/total tokens。`uv sync` 安裝 OpenAI SDK。
- 若同時設有多個 provider key，`LLM_PROVIDER` 明確指定 `openai`、`groq` 或 `gemini`；若未設定，保留舊行為（有 `GROQ_API_KEY` 選 Groq，否則走 Gemini）。執行前在 OpenAI API Usage Dashboard 選對 organization/project 並查看用量、模型權限與 rate limits；Usage Dashboard 權限可能由 organization 管理者控制。不要將 `.env` 或完整 key 傳出。

## 執行命令

```powershell
# 安裝與測試（測試需隔離本機 provider keys／外部請求）
uv sync
uv run python -m unittest discover -s tests -v
# Dry run：會搜尋及下載，涉及外部 API／網路，但不呼叫 LLM
uv run python -m literature_review.main --dry-run

# 真實端到端流程（執行前先確認 quota、venue policy、輸出資料夾）
uv run --env-file .env python -m literature_review.main

# 選擇 OpenAI（需在 .env 設定 LLM_PROVIDER、OPENAI_API_KEY）
uv run --env-file .env python -m literature_review.main

# 例：明確停用 venue whitelist；只應作為有意識的一次性設定
uv run --env-file .env python -m literature_review.main --venues none
```

完成後檢查 data/outputs/report_*.json、data/outputs/papers_*.json、stdout 的 SD 統計及 Langfuse trace（若已設定且服務可用）。目前程式會重建預設 data/run/；確認可覆寫後再啟動，避免覆蓋需要保留的下載檔。

## 503 與輸出品質的判讀

HTTP 503 顯示該次 provider 呼叫失敗／服務過載，不足以單獨證明程式錯誤，也不能據此評價已生成內容。先由 [llm] label=... status=... 找出階段，再查該 run 是否有完整 report/papers JSON。品質檢查至少需看：納入論文與 query 的關聯、PDF 抽取是否成功、functional scores 與逐篇 note 的證據一致性、claim marker 是否能回溯 chunk，以及方法／結果等章節的 SD 分布。Schema／citation 驗證只保證格式與引用 ID 可解析，不保證每個學術主張都正確。

## Windows / WSL 同步

- Windows 與 WSL 是獨立 working copies；透過共享 Git remote 同步，避免兩邊同時改同一功能。開始工作先檢查 git status，只 stage 本次有意提交的檔案。
- .env 與 PDF／執行資料依 .gitignore 規則不會隨 Git 傳送。WSL 的未追蹤 evidence、log、data/run/、data/outputs/ 不會自動出現在 Windows clone；需要時在原 WSL checkout 查閱，或明確匯出非秘密的紀錄。
- 本機資料是否存在是時間相關快照，以 .omo/STATE.md 最近盤點為準。WSL evidence 的可用性仍需在原環境確認，不能由 Windows 缺少檔案推斷為遺失。
- Summer_Project.pdf 是預期的未追蹤來源檔，不要提交；data/papers/、data/outputs/ 亦不得提交。

## 當前已知狀態（以 .omo/STATE.md 為準）

最新完成項目、歷史測試紀錄、完整 suite 限制與候選方向集中於 .omo/STATE.md。歷史 focused／mock 測試通過不代表目前完整 suite 或真實搜尋品質已驗收；不要僅因缺少本機輸出就重跑 API 流程。`plan-review` 屬另一執行環境，Codex 本輪未使用該 skill。
