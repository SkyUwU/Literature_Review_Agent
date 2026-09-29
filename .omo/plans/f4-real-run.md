# F4 real run + SD baseline（2026-09-29）

本計畫＝**里程碑 1（planner-prompt-tightening）＋里程碑 2（section-stats-and-ref-filter）都 commit 之後的第一次真實端到端 run**。三個目的：(a) 量出 SD（章節分布）baseline → B（section-aware sampling）的 gate；(b) 補跑 M5c 掛帳的 F4 real smoke；(c) 順便量 `synthesis.py:615`「每章節至少一 claim」軟偏差。

## TL;DR

- 零 code 變更、零 test 變更：只跑 `literature_review.main` 真實 run + 收集/log + 文件回填 + 結論筆記。
- 真實 run 耗 Gemini 額度（key1 planner、key2 functional+notes、key3 report）＋ SS key；由執行代理（Sisyphus）以 A 案執行，使用者負責 commit。
- 結果 log 存 `.omo/evidence/f4-real-run.log`；`data/outputs/` 的 `report_*.json`／`papers_*.json` 為主要資料來源。
- 執行偏差照 AGENTS.md：發生即停下問使用者或回報；計畫與實際不符時回填本檔。

## Scope In

- F4 真實 run（`main.py` 完整流程：LLM plan → SS/OpenAlex search → screening → rank → download → extract → functional → notes → report）。
- 收集 SD 表（stdout，`print_section_distribution=True` 既有功能）、報告 JSON、papers JSON、`[claim-N]`/`claim_chunks`、`failed_extractions`、warnings。
- B gate 判讀（四家族 vs Intro/Abstract 比例）並記錄結論。
- `synthesis.py:615` 軟偏差測量（每篇 claim 數分布）。
- HANDOFF / STATE / 計畫檔回填；結論筆記存 `.omo/evidence/`。
- key-leak canonical check。

## Scope Out

- 任何 code / config / test 變更。
- B 實作、functional prompt、synthesis 規則調整——全為「F4 後依數據再討論」候選。
- 修改 provider 行為、重試失敗 run（429 停手回報）。
- 多 query 或不同 query 比較 run。

## Verification

- Todo 0 預檢全過（git clean、Langfuse health、key 存在、`data/run` 現況記錄）。
- Todo 1 run exit 0、無 traceback、產出 `report_*.json` 與 `papers_*.json`。
- Todo 2 收集齊全：SD 表、≥1 `[claim-N]`、≥1 include、failed_extractions 清單、key-leak canonical 無洩漏。
- Todo 3 gate 判定有數據支撐（含數字）。
- Todo 4 文件回填完成、結論筆記存檔。
- Todo 5 驗收表交付、commit 指令交使用者。

## Execution strategy

單一 run 順序執行；任何 429 / LLM error / SS 失敗依既有 fallback 機制或停下回報。不自動重試真實 run。

## Todos

### Todo 0 — 預檢 checklist（唯讀）

- [ ] `git status --short`：乾淨（里程碑 1+2 已 commit；昨天確認過 `b7cd72b`/`f7a3d25`）
- [ ] `curl http://localhost:3000/api/public/health`：Langfuse up
- [ ] `.env` key 存在檢查（只 check set/unset，不印值）：`GEMINI_API_KEY`、`GEMINI_API_KEY_2`、`GEMINI_API_KEY_3`、`SEMANTIC_SCHOLAR_API_KEY`
- [ ] `data/run/` 現況記錄（leftover？無 → 繼續）

Acceptance：四項全過；缺任何 key → 停下問使用者。

### Todo 1 — 真實 run（耗 Gemini 額度）

- [ ] `printf 'literature review agent\n' | uv run python -m literature_review.main 2>&1 | tee .omo/evidence/f4-real-run.log`
- [ ] exit 0；無 traceback；SD 表印出；`data/outputs/` 有新 `report_*.json`＋`papers_*.json`
- 失敗處理：429 / quota → **停手回報**，不自動重試；其他 LLM error 依既有 fallback。
- > **執行紀錄（2026-09-29）**：兩次嘗試皆停在 screening（`screening.py:504`），key1（gemini-3.6-flash）限流：第一次 503 暫時高峰、第二次 429「20 requests per day Free Tier 用盡」。無 PDF 下載、無 report/papers JSON；log 已存 `.omo/evidence/f4-real-run.log`（兩次，tee -a 合併）。**掛帳暫停**：key1 日額度用盡，等翌日重置後續跑；重跑時只消 key1 額度即可完成（plan/rank/download 已驗證過爬到 screening）。

Acceptance：exit 0 ＋ SD 表 ＋ report/papers JSON 存在。

### Todo 2 — 收集

- [ ] 從 log 解析 SD 表（逐篇＋Aggregate）逐字複製到結論筆記
- [ ] `data/outputs/` 最新 `report_*.json`：include 篇數、`[claim-N]` markers（找 `[claim-` 出現數）、`claim_chunks`/`claim_ids`、每篇 claim 數（軟偏差測量）
- [ ] 最新 `papers_*.json`：下載清單、failed_extractions、warnings、stats per query
- [ ] key-leak canonical：`git grep -nE "AIza[A-Za-z0-9_-]{20,}|AQ\.[A-Za-z0-9_-]{20,}"` → 只允許測試檔假 key `AIza000`（太短不匹配 → 應為 no leak）

Acceptance：全數收集、數字有意義。

### Todo 3 — B gate 判讀

- [ ] 算 method+results+experiments+evaluation 四家族合計 chunk 數 ÷ 總數；vs Introduction/Abstract（或其他第一家族）佔比
- [ ] 判讀：四家族 < 約 40% → 「壟斷成立，B 待與使用者討論開工」；否則記錄反證
- [ ] 記錄數字＋結論到結論筆記

Acceptance：有明確百分比與一句結論。

### Todo 4 — 文件回填

- [ ] `.omo/evidence/f4-conclusion.md`（新）：run 摘要、SD 表、gate 結論、claim 分布、failed_extractions、warnings
- [ ] `HANDOFF.md`：里程碑表加列（F4 real run 完成）、Follow-up 節更新（B gate 結論、:615 軟偏差測量結果）
- [ ] `.omo/STATE.md`：快照更新（測試數不變）、「下一步」改為 B 討論或新事項
- [ ] 本計畫檔勾 Todo 0-4、記錄執行偏差

Acceptance：三檔＋結論筆記一致。

### Todo 5 — 回報＋commit 指令

- [ ] 驗收表（include／claim／SD／gate／leak）呈現給使用者
- [ ] commit 指令交使用者親做（docs 一筆：`HANDOFF.md`、`.omo/STATE.md`、`.omo/plans/f4-real-run.md`、`.omo/evidence/f4-conclusion.md`；注意 `.omo/evidence/` 是否為 gitignored——若被忽略則不入 commit，改僅本地留存）

Acceptance：使用者收到完整 commit 指令＋驗收表。

## Commit strategy

| Todo | Commit message | 受影響檔案 |
|---|---|---|
| 0-4 | `docs: F4 real run + SD baseline & B gate conclusion` | `HANDOFF.md`、`.omo/STATE.md`、`.omo/plans/f4-real-run.md`、`.omo/evidence/f4-conclusion.md`（若該目錄非 gitignored；待 Todo 5 確認） |

commit/push 由使用者親做。

## Success criteria

- 完成一次成功的真實端到端 run，SD baseline 有數字、B gate 有結論。
- M5c 掛帳 F4 判定有最新結果（PASS / 仍掛帳理由）。
- 零 code/test/config 變更；無 key 洩漏；`data/run/` 不需還原。