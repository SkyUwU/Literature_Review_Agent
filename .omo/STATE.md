# .omo/STATE.md — 專案現況小抄（最後更新：2026-09-03）

> 每個 session 開始自動載入（`opencode.json` instructions）。gate 過後由規劃 agent 更新；執行 agent 只讀不改。
> 可隨時用 `@.omo/STATE.md` 重新載入，避免依賴對話記憶。

## 現況（一句話）
- 里程碑：**pairwise-retrieval-eval**（route B：不 spawn subagent、執行/規劃由使用者切換）。
- **Todo 1–4 全部完成 ✅**：smoke 通過（`PYEXIT=0`，`overall_verdict: embedding better`，6 queries 全跑完，142 tests OK）。
- **目前階段：Todo 5（文件更新）+ Todo 6（opencode.json + 2 skills）→ commit → M1 收尾。**

## 下一步
1. **Todo 5**：更新 AGENTS.md（加 pairwise CLI 命令）+ HANDOFF.md（加 milestone 段落）。指令在計畫檔行動卡。
2. **Todo 6**：建立 `opencode.json` + `.opencode/skills/project-context/SKILL.md` + `.opencode/skills/review-progress/SKILL.md`。內容在計畫檔行動卡（decision-complete，不得偏差）。
3. **Commit**：三個 commit（feat + docs + chore）。
4. **提醒使用者重啟 opencode** 以載入新設定。

## Smoke 結果摘要
- Query 1–6 全跑完，judge 退避重試正常運作。
- **embedding better**（4/6 queries）、**lexical better**（1/6：writing a survey）、**comparable**（1/6：code agent）。
- 這證明 embedding 檢索整體優於 lexical，但有例外（survey 類 query）。

## 卡點
- 無。所有阻塞已解除。

## 待決策
- 之後里程碑（M2 調參 / M3 LLM search plan / M4 多來源+引用次數 / M5 引用擴充 / M6 評估）——M1 完成後再排。
- **prompt 靈感來源（M2/M3 階段做，限「系統大架構」內的核心 prompt）**：RCS 與 LLM SearchPlan 的 prompt 可參考對應 agent 框架的摘要/重排/查詢規劃 prompt 改寫。pairwise eval prompt 不需特別找參考。

## API Keys（.env）
- `GEMINI_API_KEY`：第一組 key，主要用於小測試。
- `GEMINI_API_KEY_2`：第二組 key，用於完整 smoke / 高消耗場景。`--api-key-suffix 2` flag 支援選擇。

## 共通常識（勿重問）
- route B：不 spawn subagent、不執行 git、不印/抄 API key、不碰 `.env` 與 `data/papers/` 內容。
- 角色切換不必開新 session；同 session 可用 `@.omo/STATE.md` 重新載入狀態。
- 狀態以本檔 + 計畫檔「下一步行動卡」+ 計畫 amendment 為準。
- 重要步驟一律寫進行動卡/STATE.md，不靠對話記憶（防上下文壓縮）。
