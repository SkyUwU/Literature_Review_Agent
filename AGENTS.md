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
- 每篇論文內部取樣及 functional scoring 是正式證據路徑；corpus-wide retrieval、lexical retrieval、RCS 等是 CLI／比較／legacy 路徑。
- 沒有 `GROQ_API_KEY` 時，真實流程的 Gemini key 選擇為 planner + screening 共用 `--plan-key`；functional scoring 用 `--scoring-key`；per-paper notes 用 `--notes-key`；report 用 `--report-key`。未指定 `--scoring-key` 時，為相容舊命令而沿用 `--notes-key`。
- Gemini 503 最多 exponential backoff 重試 3 次；重試後仍失敗就停止該輪，不降級略過 screening 或改用其他模型。
- 預設近三年與 top-venue whitelist 可能使候選為零；必要時可用 `--year-from`、`--year-to`、`--venues none` 做一次性覆寫，勿默默改變政策。
- 預設下載資料夾 `data/run/` 每次真實執行會重建；報告及 papers JSON 累積在 `data/outputs/`。指定 `--dest-dir` 可選擇其他下載位置。
- Gemini key 分工、錯誤降級、額度／速率控制及 run 命令詳見 `HANDOFF.md`；503 是服務錯誤，不能由此推斷生成內容品質。
- 設定 `GROQ_API_KEY` 後，Groq `openai/gpt-oss-120b` 負責 plan、分批 screening 與全域 gap 彙整、scoring/report；Gemini 負責 per-paper notes。所有 Groq 階段共用 process 內 TPM tracker，優先讀取 provider rate-limit/reset headers，並以成功回應的實際 prompt/completion tokens 更新；有 retry-after 的 TPM 429 會有限等待重試。screening prompts 仍以約 3,000 估計輸入 tokens 為批次目標。沒有 Groq key 時維持單次 Gemini screening；額度與 key 選擇見 `HANDOFF.md`。

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
