# Collaboration Guide

## 專案與回覆

- 專案：ADSL summer-project Task 1A，Literature Review Agent。使用者是資工碩士生，研究方向為 LLM。
- 主要使用繁體中文；回答直接、精簡，專業詞彙可保留英文。
- 開始專案工作先讀本檔、`HANDOFF.md`，再看 `.omo/STATE.md` 與目前相關計畫；先檢查 `git status`。

## 計畫與執行規則

- 討論或規劃：只討論或寫計畫，不改程式／設定；未明示前不寫 `.omo/plans/*.md`。
- 執行或實作：開始改程式前先把要做的範圍告知使用者並取得確認；以已確認的計畫為準。若現況與計畫不符，先停下來詢問。
- 本專案採 route B：主代理完成審核與驗收，不使用子代理。
- 計畫成文後先依 `plan-review` 流程唯讀自審；如本環境沒有該 skill，明確記錄限制，不假稱已審核。
- 只做一個有界里程碑；保留使用者變更。不要提交 `Summer_Project.pdf`、`data/papers/`、`data/outputs/`。
- 需要外部服務的真實執行前，提醒使用者在 <https://ai.dev/rate-limit> 確認本次會用到的每把 Gemini key 剩餘額度；不猜額度、不自動重跑。

## 工程約定

- `HANDOFF.md` 是架構、操作方式與已知限制的精簡交接入口；`.omo/STATE.md` 記錄目前狀態；`.omo/plans/` 保存計畫，`.omo/evidence/` 保存執行證據（可能是未追蹤檔案，跨 Windows/WSL 不一定可見）。
- Windows 與 WSL 是不同 Git working copy；以共享 remote 同步。避免同一功能在兩邊同時修改。`.env` 僅存本機，不提交、不要求使用者貼出 key。
- 階段介面使用 Pydantic；輸出保留來源資訊（provider、paper ID、檔案路徑、頁碼範圍）。LLM JSON 需驗證；支援時傳 JSON schema，回傳文字仍以 `model_validate_json()` 驗證。
- 明確區分 metadata／abstract 評估與全文評估；chunk-based synthesis 只代表供應的證據，不等於整篇論文審查。主張需能回溯來源。
- 保留舊路徑供比較，不要把 legacy/CLI 路徑誤稱為正式 end-to-end 路徑；目前路徑見下方及 `HANDOFF.md`。

## 目前正式路徑

`ResearchIdea → SearchPlan → Semantic Scholar（必要時 OpenAlex fallback／摘要補齊）→ 年份與 venue 篩選、embedding 排序 → LLM screening → PDF 下載與抽取 → 逐篇 chunk 取樣及 functional scoring → per-paper claims → 帶 [claim-N] 引用的 synthesis report`。

- 正式 end-to-end 入口：`literature_review.main`。Dry run 使用規則式規劃並停在下載階段，不呼叫 Gemini、不寫報告；只從 `.env` 載入可選的 `OPENALEX_API_KEY` 供搜尋驗證。
- 正式路徑先按章節分類並以 embedding 在每篇論文內挑證據：functional scoring 優先取 Method、Results 各 1 個 chunk（缺類別時補足至 2 個）；逐篇 notes 取 Abstract/context 1 個，再從 Method、Evaluation Setup、Results、Limitations/Future Work 各取至多 2 個，總數上限 9。References／acknowledgments 排除；Appendix 只保留能分類到上述類別的 chunk。corpus-wide retrieval、lexical retrieval、RCS 等是 CLI／比較／legacy 路徑。
- Gemini provider mode 的 key 選擇為 planner + screening 共用 `--plan-key`；functional scoring 用 `--scoring-key`；per-paper notes 用 `--notes-key`；report 用 `--report-key`。未指定 `--scoring-key` 時，為相容舊命令而沿用 `--notes-key`。
- Gemini 503 最多 exponential backoff 重試 3 次；重試後仍失敗就停止該輪，不降級略過 screening 或改用其他模型。
- 預設近三年與 top-venue whitelist 可能使候選為零；必要時可用 `--year-from`、`--year-to`、`--venues none` 做一次性覆寫，勿默默改變政策。
- 預設下載資料夾 `data/run/` 每次真實執行會重建；報告及 papers JSON 累積在 `data/outputs/`。指定 `--dest-dir` 可選擇其他下載位置。
- Semantic Scholar 搜尋會將年份範圍和 top-venue 名稱一併傳給 provider；本地 whitelist 仍為最後把關。`[search]` 統計分列 provider 回傳、abstract 可用、年份後、venue 後與最終篩選數。
- Gemini key 分工、錯誤降級、額度／速率控制及 run 命令詳見 `HANDOFF.md`；503 是服務錯誤，不能由此推斷生成內容品質。
- 設定 `GROQ_API_KEY` 後（且未指定其他 `LLM_PROVIDER`），Groq 負責 plan、screening、scoring、逐篇 notes 與 report。模型預設為 `openai/gpt-oss-120b`；`GROQ_MODEL` 統一覆寫所有階段，`GROQ_MODEL_PLAN`、`GROQ_MODEL_SCREENING`、`GROQ_MODEL_SCORING`、`GROQ_MODEL_NOTES`、`GROQ_MODEL_REPORT` 可個別覆寫。Groq notes 按 section 分批，預設每批估計輸入 2,500 tokens（可用 `GROQ_NOTES_BATCH_TOKENS` 調整），共用 process TPM tracker。Notes checkpoint 位於 `data/outputs/notes_checkpoints/<run-id>/`；失敗後照 stderr 的 run ID 使用 `--resume-notes <run-id>` 續跑，所有 notes 完成才產生 report。checkpoint 綁定 query、選用 paper、provider 與來源文件內容，不符時拒絕沿用。
- 可用 `.env` 的 `LLM_PROVIDER=openai` 明確改由 OpenAI 處理所有 LLM 階段；預設 `OPENAI_MODEL=gpt-5.6-luna`，也可用 `OPENAI_MODEL_PLAN`、`OPENAI_MODEL_SCREENING`、`OPENAI_MODEL_SCORING`、`OPENAI_MODEL_NOTES`、`OPENAI_MODEL_REPORT` 個別覆寫。需設定 `OPENAI_API_KEY`。未設定 `LLM_PROVIDER` 時維持舊路由（有 Groq key 走 Groq，否則 Gemini）；OpenAI 官方 SDK 使用預設 endpoint，不需要 Base URL。真實 OpenAI run 前應確認所屬 organization/project 的用量與模型權限。

## 常用命令（PowerShell）

```powershell
uv sync
uv run python -m unittest discover -s tests -v
uv run python -m literature_review.main --dry-run
uv run --env-file .env python -m literature_review.main
```

其他 CLI、篩選參數、key 選擇及輸出檔說明見 `HANDOFF.md`。

## Key leak check

凡文件或程式變更可能觸及 key，變更前後均執行：

```powershell
git grep -nE "AIza[A-Za-z0-9_-]{20,}|AQ\.[A-Za-z0-9_-]{20,}"
```

測試中的短假 key placeholder（例如 `AIza000`）是預期資料。
