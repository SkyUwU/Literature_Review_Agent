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
→ DOI 摘要補齊、年份／venue 篩選、embedding paper ranking
→ bucket sampling + LLM screening（可提出 gap follow-up）
→ OA PDF 下載 → pymupdf4llm 抽取、章節切分
→ 每篇論文內取樣 top-2 chunks → functional scoring
→ per-paper claims → outline/report/directions 三階段 synthesis
→ JSON 報告及 downloaded-papers 清單
```

重要路徑區分：

- literature_review.main 是正式 end-to-end 入口。--dry-run 使用 rule-based planner，只跑至下載，不呼叫 Gemini，也不保存報告；為搜尋可載入 .env 中唯一的 OPENALEX_API_KEY。
- 正式證據路徑以 functional.sample_top_chunks_per_paper 逐篇取樣及 functional scoring 為準；corpus-wide embedding／lexical retrieval 與 RCS 是 CLI、比較或 legacy 路徑，不是正式 synthesis pipeline。
- 報告中的 [claim-N] 連到程式組裝的 claim-to-chunk/paper provenance。生成內容只應依賴供應的 chunks；形式驗證通過不代表學術品質已經人工確認。
- metadata／abstract 評估不等於全文評估；chunk-based synthesis 也不代表完整審閱每篇論文。

## 目前已知研究與操作政策

- 年份預設為最近三年（2026 年即 2024 起），並使用 FilterPolicy 作為 provider 統一後衛。
- 預設 venue whitelist 是硬篩選；舊紀錄指出 "literature review agent" 的候選可能全被此 whitelist 排除。真實 run 前檢查候選／venue 統計；需要探索性執行時可明確指定 --venues none，不要把結果解讀為正式政策下的同一實驗。
- 每個規劃 query 分別搜尋、排名及分配下載目標；跨 query 以 DOI（無 DOI 時 title+year）去重。預設 data/run/ 每次真實執行會清空重建；使用 --dest-dir 指定位置時不清除該位置。
- 只有成功寫入的 PDF 會列在 papers_*.json；每輪報告與 papers JSON 寫入 data/outputs/ 並共用時間戳。dry run 不寫這兩個 JSON。
- 章節分布統計（SD）在 synthesis 時印至 stdout；它可用來判斷是否需要討論 section-aware sampling，不要在取得可查證的實際 run 數據前先改取樣策略。

## Gemini 呼叫與失敗處理

- 沒有 `GROQ_API_KEY` 時，正式流程可分配四個 Gemini key 群組：planner + screening 共用 `--plan-key`（預設 1）、functional scoring 使用 `--scoring-key`（預設沿用 `--notes-key`）、per-paper notes 使用 `--notes-key`（預設 2）、report 使用 `--report-key`（預設 3）。例如 `--plan-key 2 --scoring-key 3 --notes-key 4 --report-key 5` 可分配 Key 2–5；未指定 `--scoring-key` 時維持舊版 scoring/notes 共用同一 key。
- 每把 key 有獨立速率追蹤；預設最多 20 次／process，預設約 13 秒 pacing。stderr 的 [llm] 紀錄包含 stage、key hash、等待時間、呼叫數及狀態，不會輸出完整 key。
- Gemini 503／overload 會在同一模型、同一階段做最多 3 次 exponential backoff 重試（約 15、30、60 秒，另加少量 jitter）；這高於每 key 13 秒的 RPM pacing 間隔。SDK 內層重試設為單次，避免超出應用層次數；每次嘗試都計入本 process 的該 key 呼叫數。重試耗盡後不切換模型或降級：planner、main screening、gap follow-up screening、functional scoring、notes、report 均中止該輪。429 的 quota／retry-after 規則維持獨立，不套用 503 重試。
- 429 依 provider 訊息分類；有 per-minute retry hint 時最多有限重試，日額度耗盡不重試。notes/scoring/report 所指定的 Gemini keys 沒有後續階段的替代降級路徑。
- OpenAlex 503 若訊息指出 anonymous search paused，需使用 OPENALEX_API_KEY；OpenAlex 504 query_timeout 則表示該次搜尋逾時。Dry run 的 rule-based planner 會把輸入 query 原樣延伸為 survey/literature-review/comparison 子查詢，應輸入精簡的主題詞而非長篇研究問題。發生 timeout 時檢查 stdout 的 Search plan，縮短導致錯誤的子查詢後再執行；不要盲目重送同一條過長 query。
- 啟動任何真實 run 前，請使用者確認本次會用到的每把 key 在 https://ai.dev/rate-limit 的剩餘額度。不得假設額度，也不得自動重跑整輪。
- .env 僅為本機秘密設定；不要讀出、貼出或提交 key。Gemini 與 Semantic Scholar key 應按 stage 的實際需求準備。

## Groq/Gemini 混合 provider

- 在 `.env` 設定 `GROQ_API_KEY` 後，Groq 固定模型 `openai/gpt-oss-120b` 負責 planner、分批 screening／全域 gap 彙整、functional scoring 與 report；由 `uv sync` 安裝 Groq 官方 Python SDK，透過 Chat Completions API 呼叫。
- 混合模式下，screening 會依約 3,000 個估計 prompt tokens 的預算切批，逐批產生每篇 keep/maybe/reject 判斷及局部涵蓋／缺漏摘要，再用一次精簡呼叫整合全域 gap 與 follow-up queries。每次 screening 呼叫（包含格式修復）至少間隔 61 秒，為 8K TPM 留出輸出空間；這是保守估算與節流，provider 的實際 tokenization／組織額度仍可能不同。候選若單篇已超出預算會直接停止並報錯，不會截斷摘要或切換模型。per-paper notes 固定用 Gemini（key 由 `--notes-key` 選擇）。任一 screening 或 notes 呼叫失敗時中止，不改由另一 provider 接手。
- 混合模式所需 key：`GROQ_API_KEY` 與 notes 對應的 `GEMINI_API_KEY[_N]`（預設 key2）。沒有 `GROQ_API_KEY` 時沿用原先全 Gemini 的分工。
- Groq 503 在同一模型最多重試三次（約 15、30、60 秒加 jitter），耗盡後停止該輪。Groq 使用 JSON Schema best-effort mode，並以既有 Pydantic 驗證及有限 JSON repair 檢查輸出。文件所列免費額度會變動；完整 run 前查看 Groq Limits 與每把 Gemini key 的 AI Studio rate limit。
- 每次 Gemini／Groq 呼叫會在 `[llm]` log 記錄 `prompt_chars` 與 `prompt_utf8_bytes`，不含 JSON schema 與 API protocol overhead，也不是 token 數估值；Groq 成功回應時另記錄 provider 回報的 `prompt_tokens`。Groq screening 另印出保守估計 tokens 與 TPM 等待秒數；估算不等於 provider tokenizer 結果，遇 413 時以錯誤內容為準。
- `.env` 範例為 `GROQ_API_KEY=<你的 key>`。不要提交 `.env` 或把 key 貼到聊天、log、文件。

## 執行命令

```powershell
# 測試與無 API 的流程檢查
uv sync
uv run python -m unittest discover -s tests -v
uv run python -m literature_review.main --dry-run

# 真實端到端流程（執行前先確認 quota、venue policy、輸出資料夾）
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
- 目前 Windows checkout 在本次盤點中沒有 .omo/evidence/、data/run/ 或 data/outputs/；這只代表此 checkout 看不到它們。使用者表示 evidence 可能留在 WSL OpenCode 環境，應在該環境確認，勿推斷為遺失。
- Summer_Project.pdf 是預期的未追蹤來源檔，不要提交；data/papers/、data/outputs/ 亦不得提交。

## 當前已知狀態（以 .omo/STATE.md 為準）

最近的文件紀錄稱 2026-09-29 完成 rate limiting/key rotation，並稱 504 個測試通過；此數字是歷史紀錄，並非本次 checkout 已重跑驗證。section-stats/ref-filter 文件及程式曾被標成完成；是否已提交應以目前 Git history/status 為準，不沿用舊 handoff 的「待 commit」敘述。

接手真實 run 前，先確認使用者剛才那次 503 的原始 log、key stage、quota、venue 篩選結果與原 WSL 輸出檔是否可取得。不要僅因缺少本機輸出就重跑 API 流程。
