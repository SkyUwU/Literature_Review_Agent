# 專案狀態（盤點日期：2026-10-01）

## 2026-10-01：OpenAI provider 路由

- 已新增明確的 `LLM_PROVIDER=openai` 路由；OpenAI 官方 SDK 可供 plan、screening、functional scoring、per-paper notes 與 report 使用，預設模型 `gpt-5.6-luna`，支援全域及階段模型覆寫。
- 未設定 provider selector 時維持舊路由；只放 `OPENAI_API_KEY` 不會改變既有 Groq/Gemini 選擇。OpenAI requests 使用 Chat Completions JSON Schema、SDK 有界 retry 與本地 Pydantic 驗證；程式只測 mock，沒有用實驗室 key 發 API request。
- 驗證：OpenAI provider、notes、functional scoring 與 section stats focused tests 共 140 tests 通過；`git diff --check` 通過，key-pattern scan 無命中。未執行完整 suite 或真實 API/run。

## 2026-10-01：章節分類與逐篇證據取樣

- 正式 pipeline 現依 section path 分成 context、method、evaluation_setup、results、limitations_future 五類；References／acknowledgments 排除，Appendix 僅保留能歸入上述類別的子章節。
- Functional scoring 優先取 Method 與 Results 各一個 chunk；notes 取 Abstract/context 一個，再從 Method、Evaluation Setup、Results、Limitations/Future Work 各至多兩個，最多九個。每篇候選只做一次 embedding，向量共用於兩種取樣。
- Focused 驗證 78 tests 通過。全套測試因 `test_main` 載入本機 `.env` 後發出真實 Semantic Scholar 與 Groq 請求而中止；沒有繼續重試。這些請求不是本次變更的預期驗證步驟。
- 未執行端到端真實 literature review run；未追蹤的 `Summer_Project.pdf` 保持原狀。

## 2026-10-01：逐篇 notes checkpoint 與 Semantic Scholar 篩選

- 已完成 Groq per-paper notes：按 section 和 `GROQ_NOTES_BATCH_TOKENS`（預設 2500）切批，逐批 Pydantic/chunk ID 驗證後本地合併。Groq notes 與其他 Groq stages 共用 TPM tracker；Gemini-only 分工不變。
- Notes 每篇成功後寫入 `data/outputs/notes_checkpoints/<run-id>/`；`state.json` 留下 pending/completed paper IDs。失敗項會讓流程繼續處理其他論文並停止最終報告；用 `--resume-notes <run-id>` 重跑，會驗證 manifest、重用成功項並補缺漏項。續跑仍會重做搜尋、下載、抽取與 scoring。
- Semantic Scholar request 現傳年份與 venue 名稱；本地年份上／下界及 venue whitelist 仍是最後把關。`[search]` log 現列 provider 回傳、abstract 可用、年份後、venue 後與最終候選數。
- 驗證：受影響的 notes/checkpoint/search/provider-routing focused tests 通過（最新 focused run exit 0；先前同組合計 111 tests 通過）。完整 suite `Ran 511 tests`，有 1 failure、3 errors：`test_functional_scoring_policy_defaults` 期待 batch size 8、程式為 4；兩個 screening tests 預期 429/503 退回未篩選候選、目前正式程式明確中止；Gemini 503 fake SDK 測試的互動腳本耗盡。這些位置未由本次變更修改。
- 測試需清空 `GROQ_API_KEY`，避免 `load_local_env()` 從本機 `.env` 載入 key。第一次完整 suite 未清空時，舊測試實際呼叫一次 Groq planner，發現後立即中止；無 key 值輸出。後續 suite 在 Groq disabled 環境中執行。
- 本次沒有執行真實 literature review run；未追蹤 `Summer_Project.pdf` 保持原狀。

## 本次 checkout 可確認的狀態

- 本次文件整理前，Windows working copy 的 Git status 僅有預期中的未追蹤 Summer_Project.pdf；未見 tracked file 變更。
- 本機沒有 .omo/evidence/、data/run/ 或 data/outputs/。這些可能是 WSL OpenCode checkout 的未追蹤執行資料，Git 不會同步；請在原 WSL 環境查證，不代表紀錄遺失。
- 使用者回報 Windows dry run 先遇 OpenAlex anonymous-search 503，加入 OpenAlex key 後又遇 query_timeout 504。dry run 不載入 Gemini，但需 OpenAlex key 時應載入 .env；目前 checkout 未取得完整終端 log，504 對應的實際子查詢仍需從 Search plan 確認。
- HANDOFF 記載 2026-09-29 rate limiting/key rotation 里程碑有 504 tests 通過。這是舊執行紀錄，非本次重新測試；舊版 462 數字已過時。

## 目前工作基線

- 正式入口為 literature_review.main：規劃 → 多 query 搜尋與排名 → screening → OA PDF 下載 → 全文抽取與分段 → per-paper functional scoring / notes → synthesis → report 與 papers JSON。
- 正式證據路徑每篇論文內取樣 top-2 chunks；corpus-wide retrieval、RCS 等為 CLI／比較／legacy 路徑。詳見 HANDOFF.md。
- 預設研究政策為最近三年與 top-venue whitelist；後者是硬篩選，已知可能使某些 query 零候選。一次性探索可明確使用 --venues none。
- Gemini key 分工、503/429 的 stage-specific 行為、額度限制及真實 run 命令見 HANDOFF.md。

## 下一步

1. 若要評估剛才那輪，先從 WSL/OpenCode 找回原始終端 log、Langfuse trace（若有）、data/outputs/report_*.json 與 papers_*.json；不要只憑 503 重跑。
2. 由 log 的 [llm] label/status 找出 503 發生的階段。若流程中止，檢查候選數、venue 篩選、下載／抽取狀態及每把 key 的實際額度紀錄。
3. 若決定重新跑真實流程，先確認使用到的所有 Gemini key 剩餘額度、venue policy，並備份或指定下載目錄；預設 data/run/ 會被清空重建。
4. 完整 run 後再用逐篇 evidence、claim-to-chunk traceability 與 section-distribution 統計評估輸出；schema 通過本身不等於學術品質已驗證。

## 近期完成項目索引

- Planner prompt tightening：.omo/plans/planner-prompt-tightening.md
- Section stats / references heading filter：.omo/plans/section-stats-and-ref-filter.md
- Gemini rate limiting / key rotation：.omo/plans/llm-rate-limit-and-key-rotation.md
- 更早里程碑：見 .omo/plans/；詳細測試 log 若未在此 checkout，需回原執行環境查找。

本檔僅記錄有來源的狀態；新的測試、run 或 commit 完成後再更新日期與證據位置。不要從未追蹤檔案缺席推論其在另一個 working copy 不存在。
