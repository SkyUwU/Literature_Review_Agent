# m5c-doc-wrapup — M5c（SS 主檢索）文件收尾

## TL;DR (For humans)

M5c（Semantic Scholar 主檢索）的 code 已 commit（Todos 1-4 + F1-F3/F4b 驗收過、F4 real run 因 Gemini 配額掛帳），但文件/tracker 沒跟上 code。本計畫＝**純文件收尾，零 code 變更**：讓 HANDOFF / AGENTS / STATE / README / `.env.example` 與 code 現況一致，並在 AGENTS.md 補「現行 vs legacy」對照表。commit/push 由使用者親做。

**不會做**：改任何 code、跑 F4 real run（另行掛帳）、變更 provider 行為、寫入任何 API key 數值。

---

## Scope

**In**
- 勾選 `.omo/plans/m5c-semantic-scholar.md` Todo 0-5（F4 標掛帳）
- `HANDOFF.md`：新增「Latest milestone: M5c」節 + 里程碑表加列 + 測試數 376→414 + 「21 檔基準」→26 檔 + API notes 更新（SS 已是主源） + F4 掛帳註記
- `AGENTS.md`：架構圖加 SS 主源 + Commands/key 補 `SEMANTIC_SCHOLAR_API_KEY` + 新增現行 vs legacy 對照表
- `.omo/STATE.md`：里程碑表 M5c ⏳→✅、測試數同步、「下一步」段落重寫為候選清單、保留已改未 commit 的 2026-09-22 討論紀錄
- `README.md`：key config 補 SS key 欄位名與 provider 說明（不寫數值）
- `.env.example`：補空欄位 `GEMINI_API_KEY_2` / `GEMINI_API_KEY_3` / `SEMANTIC_SCHOLAR_API_KEY` / `OLLAMA_BASE_URL` / `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` / `LANGFUSE_HOST`
- 本計畫檔 `.omo/plans/m5c-doc-wrapup.md`

**Out**
- 任何 code / test 檔變更
- 任何 API key 數值寫入 git 追蹤檔案
- F4 real run、provider 行為變更

---

## Verification strategy

每 Todo＝改檔 + 對照檢查。最終：跑完整 suite 確認零回歸（測試數不變）、`git status --short` 只含預期文件、用 grep 確認 git 追蹤檔無 `AIza` / `sk-` / 真實 key pattern。

---

## Execution strategy

```
Todo 0（預檢）→ Todo 1（AGENTS）→ Todo 2（HANDOFF）→ Todo 3（STATE）
→ Todo 4（README + .env.example）→ Todo 5（勾計畫 + 驗收 + commit 指令）
```

---

## Todos（所有 log 存 `.omo/evidence/m5c-wrapup-*.log`，gitignored）

- [x] 0. **預檢** — 確立執行前底線

  **References**
  - `uv run python -m unittest discover -s tests -v`（AGENTS.md:38）
  - `.omo/evidence/m5c-smoke.log` — F1 已記 414 tests

  **Implementation**
  1. `uv run python -m unittest discover -s tests -v` 跑完整 suite，輸出至 `.omo/evidence/m5c-wrapup-todo0.log`，記錄測試數（smoke log 預期 414；**以實際跑出數字為準**）。
  2. `git status --short` 記錄底線：只有 `.omo/STATE.md` 是 M、`.omo/plans/m5c-doc-wrapup.md` 是 ??。

  **Acceptance**
  - suite exit 0、output 有 "OK"、測試數紀錄於 log。
  - `git status` 無未預期變動。

- [x] 1. **AGENTS.md 更新** — 架構圖 + Commands/key + 現行 vs legacy 對照表

  **References**
  - `AGENTS.md:34-48` Commands + key wiring 描述
  - `AGENTS.md:58-75` Current architecture（架構圖 + M3B + M3C + K 段）
  - 對照表以 main.py 正式流程與現行/legacy 檔案為準（範圍見 TL;DR）

  **Implementation**
  1. 「Commands」段落 `main.py` 說明加入 SS：SS 為主要搜尋來源（key 存在時）、無 key fallback OpenAlex、placeholder abstract 由 OpenAlex DOI lookup backfill。
  2. Key wiring 描述補 `SEMANTIC_SCHOLAR_API_KEY`（規劃/篩選 key1、評分/筆記 key2、報告 key3 不變）。
  3. 「Current architecture」圖：`OpenAlex retrieval` → `Semantic Scholar retrieval (OpenAlex fallback + DOI abstract backfill)`；M3B PDF 取得段補註 SS `openAccessPdf` 來源路徑。
  4. 在 Current architecture 段後新增「現行 vs legacy 對照」小節（緊湊表格：階段 / 現行 / legacy・逃生門・對照）。

  **Acceptance**
  - `grep -n "Semantic Scholar\|SEMANTIC_SCHOLAR_API_KEY" AGENTS.md` 有結果。
  - 對照表每列有現行來源檔與 legacy 來源檔。

- [x] 2. **HANDOFF.md 更新**

  **References**
  - `HANDOFF.md:18-50` 里程碑表
  - `HANDOFF.md:337-341` Suggested sequence（item 1 已達成的 SS DOI merge）
  - `HANDOFF.md:346` API notes 的 SS「optional 未來來源」句
  - `.omo/evidence/m5c-smoke.log`、`m5c-todo0-probe.log`、`ss-probe-20260921T115955Z.json`、`m5c-real-check.json`（M5c 實證）

  **Implementation**
  1. 里程碑表加 `M5c Semantic Scholar primary search | ss_search.py, main.py, search.py, pdf_downloader.py | Done; F4 real run deferred (Gemini quota)`。
  2. 加「## Latest milestone: M5c — Semantic Scholar primary search (2026-09-21)」節：Todo 0 probe 結果（DOI overlap Jaccard 0.05-0.13、SS abstract 覆蓋 0.91-0.92、SS OA PDF 0.55-0.76）、Todos 1-4 摘要、F1-F3/F4b PASS、F4 掛帳原因（Gemini 429）、測試數（以實際為準）。
  3. 在 M5c 新節註記目前 `data/papers/` 基準 **26 檔**（HANDOFF 舊文多處「21 檔基準」是當時真實紀錄，**不回溯修改**）。
  4. API notes（:346）：Semantic Scholar 從「optional 未來來源」改「已是主要搜尋來源」。
  5. Suggested sequence（:337）：移除已完成的 SS API + DOI merge 項目。

  **Acceptance**
  - HANDOFF 有 M5c 節 + 里程碑表列 + 測試數 + 26 檔基準註記。
  - `grep -n "optional 未來來源\|optional later metadata" HANDOFF.md` 無結果。

- [x] 3. **`.omo/STATE.md` 更新**

  **References**
  - `.omo/STATE.md:8-10` 現況快照（測試 376、「唯一待執行里程碑：M5c」）
  - `.omo/STATE.md:14-37` 里程碑表
  - `.omo/STATE.md:39-44` 「下一步：M5c」段
  - `.omo/STATE.md:46-63` 2026-09-22 討論紀錄（已改未 commit，保留）

  **Implementation**
  1. 現況快照：測試數 376→實際值；「唯一待執行里程碑：M5c」改「所有里程碑（至 M5c）已完成；下一里程碑待選」。
  2. 里程碑表：M5c 行改 `✅ 已 commit + F1-F4b 驗收過（F4 real run 掛帳）`。
  3. 「下一步：M5c」整段改名重寫為「下一步：候選里程碑」，收錄未動工候選（M5f/M5d/paper_id→DOI/marker-pdf/A2/functional Section/main.py DEST_DIR/data-papers 策略）+ 2026-09-22 討論第 4 點使用者偏好（每輪 run 獨立資料夾）。
  4. 保留 2026-09-22 討論紀錄原樣（本次不收進 code）。

  **Acceptance**
  - STATE 不再含「M5c 待執行」；測試數＝實際值。

- [x] 4. **README.md + `.env.example`**

  **References**
  - `README.md:54` full-run key 說明段
  - `.env.example` 全文（目前僅 `GEMINI_API_KEY`）

  **Implementation**
  1. README 的 full-run key 說明補 `SEMANTIC_SCHOLAR_API_KEY` 欄位名與一句「SS 為主要檢索來源（缺 key 時自動回退 OpenAlex）」——**不寫任何數值**。
  2. `.env.example` 依現行 key/config 補空欄位：`GEMINI_API_KEY`/`_2`/`_3`、`SEMANTIC_SCHOLAR_API_KEY`、`OLLAMA_BASE_URL`、`LANGFUSE_HOST`、`LANGFUSE_PUBLIC_KEY`、`LANGFUSE_SECRET_KEY`，各附一行註解說明用途。

  **Acceptance**
  - `git grep -n -E "AIza|sk-[A-Za-z0-9]{8,}|SEMANTIC_SCHOLAR_API_KEY=[^$]"` 於追蹤檔無命中（欄位名以空值即可，不得帶實值）。
  - `.env.example` 每行 = 註解或 `NAME=` 空值。

- [x] 5. **勾計畫 + 最終驗收 + commit 指令**

  **References**
  - `.omo/plans/m5c-semantic-scholar.md` Todo 0-5 checkboxes
  - commit strategy（見下）

  **Implementation**
  1. `.omo/plans/m5c-semantic-scholar.md`：Todo 0-5 勾 `[x]`（Todo 5 的 real smoke 標「掛帳 pending quota」不 block）、Final verification F1-F4 更新（F4 = deferred）。
  2. 跑完整 suite 最後一次（輸出 `.omo/evidence/m5c-wrapup-final.log`，確認零回歸）、`git status --short` 列出預期檔案。
  3. 提供使用者單一 docs commit + push 指令（列齊全檔案）。

  **Acceptance**
  - suite 全綠；`git status --short` 只含計畫內檔案；commit 指令一次列齊。

---

## Commit strategy（使用者親做）

```powershell
git add AGENTS.md HANDOFF.md README.md .env.example .omo/STATE.md .omo/plans/m5c-semantic-scholar.md .omo/plans/m5c-doc-wrapup.md
git commit -m "docs: M5c wrap-up — sync HANDOFF/AGENTS/STATE/README/.env with SS primary search"
git push
```

---

## Success criteria

- 文件與 code 現況一致：SS 為主源 + DOI dedup + abstract backfill 已反映於 AGENTS/HANDOFF/STATE/README。
- 測試全綠（414，零 code 變更）。
- 對照表完整標出現行 vs legacy。
- git 追蹤檔案內無任何 API key 數值。