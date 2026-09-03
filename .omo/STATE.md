# .omo/STATE.md — 專案現況小抄（最後更新：2026-09-03）

> 每個 session 開始自動載入（`opencode.json` instructions）。gate 過後由規劃 agent 更新；執行 agent 只讀不改。
> 可隨時用 `@.omo/STATE.md` 重新載入，避免依賴對話記憶。

## 現況（一句話）
- **M1（pairwise-retrieval-eval）已完整收尾 ✅**：Todo 1-6 全完成、smoke 通過（embedding better）、commit+push、gitignore 整理、skill 建立。
- **M2 計畫檔已寫好**（`.omo/plans/embedding-retrieval-adoption.md`）：把 pipeline 檢索從 lexical 正式換成 embedding + coverage 加參考文獻低權重。
- **目前階段：M2 計畫待使用者確認 → 執行（/start-work）→ 驗收。**

## 下一步
1. **使用者確認 M2 計畫**（`.omo/plans/embedding-retrieval-adoption.md`）。
2. **執行 `/start-work embedding-retrieval-adoption`** 由 worker 執行 Todo 1-4。
3. 驗收 smoke → Final wave F1-F4 → commit。
4. M1 收尾遺留：opencode 尚未重啟（skill 需重啟才生效）；Windows 端尚未 pull 同步（等轉過去一起）。

## M2 範圍（已定案）
- pipeline 預設直接用 embedding（`retrieve_evidence_embedding`），不加 `--retriever` 選項。
- coverage.py 加 References/Bibliography **精確區間規則**：只在 References 起點與 Appendix 起點之間降為最低權重(14)，附錄維持(13)，正文(1-12)不受影響。決策表 C1-C4 + 測試表 T1-T6 已寫在計畫 Todo 2。
- 不做前端刪除參考文獻、不調 top-k、不換 embedding model、不做持久化快取、不動 ranking/selection。
- 測試用 fake encoder（mock default_encoder 或 pipeline 接受可注入 encoder）。

## 執行方式（M2）
- **不用 `/start-work`**（route B：規劃 agent 不自行觸發執行）。由使用者**手動把執行指令包貼給執行代理**去改 code，改完回報 → 規劃 agent 驗收。

## M1 成果摘要
- pairwise eval：`overall_verdict: embedding better`（4/6 embedding 贏、1/6 lexical 贏 survey、1/6 平手）。證明 embedding 檢索整體優於 lexical，但有例外。
- 檔案：`pairwise_eval.py` + `test_pairwise_eval.py`（142 tests OK）+ embedding_retriever（cached 版）+ `--api-key-suffix`。

## 卡點
- 無阻塞。M2 規劃進行中。

## 待決策（M2 之後）
- **資料來源策略（M3/M4）**：詳見 `.omo/notes/architecture-futures.md`。包含：是否只用 OA 全文、arXiv/Semantic Scholar/Unpaywall 來源、自動拿 OA PDF、下載 PDF 成本、ranking/selection 角色。
- M2 是否納入「濾參考文獻區塊再切 chunk」（PaperQA2/Grobid 靈感）。
- 確切 API 來源組合（OpenAlex？arXiv？SS？）。

## API Keys（.env）
- `GEMINI_API_KEY`：第一組 key，主要用於小測試。
- `GEMINI_API_KEY_2`：第二組 key，用於完整 smoke / 高消耗場景。`--api-key-suffix 2` flag 支援選擇。

## 共通常識（勿重問）
- route B：不 spawn subagent、不執行 git、不印/抄 API key、不碰 `.env` 與 `data/papers/` 內容。
- 角色切換不必開新 session；同 session 可用 `@.omo/STATE.md` 重新載入狀態。
- 狀態以本檔 + 計畫檔「下一步行動卡」+ 計畫 amendment 為準。
- 重要步驟一律寫進行動卡/STATE.md，不靠對話記憶（防上下文壓縮）。
- 規劃 agent 只改 `.omo/*.md`，不碰 product code / tests / 其他目錄。
