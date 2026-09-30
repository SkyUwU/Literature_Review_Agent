# m4-rcs-scoring - Work Plan

## TL;DR (For humans)

**What you'll get:** M4（RCS 評分層改版）的程式碼、測試、文件都已完成（Todo 1-7 ✅，196 tests OK）。現在只剩最後一步（Todo 8）：用改後的程式跑一次真實文獻回顧，跟前一次（改前）的結果對比，看分數鑑別度有沒有變好，並給出門檻是否還需要調整的建議。完成後 M4 整體驗收。

**Why this approach:** 「先調後驗」是既定原則——門檻值（include 8/6、consider 6、prior 5.5）是初始值，必須用真實資料驗證分布再決定是否校正，不能只靠單元測試。

**What it will NOT do:** 不再改評分邏輯（除非 run 中發現 bug，修復屬 Todo 8 範圍）；門檻調整只能「建議」，不能自行改 code；不做 C2（報告生成設計）與第三維度 utility_score（候選 J，動工前先與使用者討論）。

**Effort:** Small（單一 todo，一次真實 run）
**Risk:** Low - code 已完成且測試全綠;剩餘風險只在真實 run 的環境面(Langfuse/key/網路)

**Decisions to sanity-check:** 門檻 include 8/6、consider 6、prior 5.5(float)、m=4 不動——這些已在 code,本計畫只做「驗證 + 校準建議」。

Your next move: 執行 Todo 8（route B：使用者把下方的 Todo 8 執行指示貼給執行代理），完成後規劃 agent 驗收。

---

> TL;DR (machine): Small effort, Low risk; M4 code 已完成(Todo 1-7 ✅、196 tests),剩 Todo 8 = 改後真實 run + 改前/改後對比 + 門檻校準建議

## Scope
### Must have（已於 2026-09-06 完成 — Todo 1-7 ✅）
- 分數制 1-10：chunk 層 int（`LlmEvidenceAssessment`/`EvidenceCitation` le=10）、論文層 float 1 位（`PaperAssessment` int→float）
- 聚合：`_shrunk_mean` round 1 位不 floor、prior_score=5.5(float)、shrinkage_strength=4 不動
- 門檻：include = relevance≥8 且 quality≥6；consider = relevance≥6（`EvidenceAggregationPolicy`、`AssessmentPolicy` 同步 8/6/6）
- prompt（`build_evidence_prompt`）：1-10、兩分數 10/5/1 錨點、「先 rationale 後 score」、移除 recommendation 要求、理論論文無數字不扣分
- chunk 層 `recommendation` 從 models 契約與 synthesis 區塊 A 移除（論文層 `PaperAssessment.recommendation` 保留）
- metadata 初評 mapping 對齊 1-10（新公式 `ceil(rank*10/3)`，不複製舊恆 1 bug）+ deprecated 標記
- 全套件 196 tests 綠 + 文件（README/AGENTS/HANDOFF/STATE）同步

### Must have（本計畫剩餘 — Todo 8）
- 改後真實 run：同 query「literature review agent」，完整端到端（LLM planner key1 + synthesis key2），全產物存 `.omo/evidence/`
- `m4-after.log` + `m4-after-dist.json`（每篇 paper_id / relevance / quality / recommendation / chunk_count）
- 對比表 `.omo/evidence/m4-before-after-compare.md`：分數分布（改前 vs 改後）、include/consider/exclude 分布變化、每篇分數對照、rationale↔分數一致性抽查
- 門檻校準建議段落（分布異常 → 具體調整建議；**只建議、不改 code 門檻**）

### Must NOT have (guardrails, anti-slop, scope boundaries)
- 不再改評分/聚合/prompt 邏輯（Todo 1-7 已完成；除非 run 暴露 bug 需修復，修復內容要在回報中明確列出）
- 執行代理**不自行調整門檻/prior**（校準只能寫建議，交使用者決策）
- 不做 C2 報告生成設計、不做 utility_score 第三維度（候選 J，動工前先討論）
- 不碰 `.env`、不印/抄 API key、不動 `data/papers/` 內容（run 用 temp dir）
- 執行代理不執行任何 git commit

## Verification strategy
> Zero human intervention - all verification is agent-executed.
- 本計畫剩餘驗證 = Todo 8 的產物（log/JSON/對比 md）齊全且數據自洽；由規劃 agent 驗收（讀檔核對），不需使用者動手
- 驗收對照對象：`.omo/evidence/m4-baseline-before.*`（Todo 1 已產的改前 baseline）

## Execution strategy
### Parallel execution waves
- **Wave 1**: Todo 8（單一 todo，內部三個動作：run → 對比 → 校準建議）
- **Wave 2**: Final verification（F1-F4，由規劃 agent 驗收後回報使用者）

### Dependency matrix
| Todo | Depends on | Blocks | Can parallelize with |
| --- | --- | --- | --- |
| 8 | Todo 1-7(code 完成)、Todo 1 產物(baseline) | - | - |

## Todos
> Implementation + Test = ONE todo. Never separate.

- [x] 1. M4 code 改版 + 測試 + 文件（已完成 2026-09-06，196 tests OK；內容摘要見上方 Scope Must have 第一段）
- [ ] 8. 改後真實 run + 改前/改後對比 + 門檻校準建議
  What to do / Must NOT do: **(a) 改後真實 run**：確認 Langfuse up（`curl http://localhost:3000/api/public/health`）；跑完整端到端 `uv run python -m literature_review.main`（interactive 餵 query「literature review agent」；LLM planner 用 `GEMINI_API_KEY`、synthesis 用 `GEMINI_API_KEY_2`；不帶 `--dry-run`/`--rule-based`）——若 `/tmp/m4_after.py`（先前執行代理的腳本）不存在或已過時，直接照 M3C smoke 方式用 main.py 跑；輸出 log 存 `.omo/evidence/m4-after.log`；從 log 提取每篇論文的 aggregate relevance/quality（float 1 位）、recommendation（include/consider/exclude）、chunk 數，存 `.omo/evidence/m4-after-dist.json`（結構與 `m4-baseline-before-dist.json` 一致：array of {paper_id, relevance, quality, recommendation, chunk_count}）。(b) **對比表**：寫 `.omo/evidence/m4-before-after-compare.md`，含四段——① 分數分布對比（改前 1-5 整數 vs 改後 1 位小數：範圍/集中度/鑑別度，每篇列出）；② 等級分布對比（include/consider/exclude 兩次 run 的數量與名單）；③ 每篇分數變化注記（若同一批論文重疊）；④ rationale↔分數一致性抽查（挑 1-2 篇改後 include/consider，檢查 rationale 理由與分數吻合、無「高分卻理由空洞」）。(c) **校準建議**：依分布數據給出結論——分布正常 → 寫「無需調整」+ 理由（include/consider 數量可接受、分數有鑑別度）；分布異常（如 include/consider 過多/過少、分數過度集中 8-10 或全卡 6-7）→ 提出具體調整建議（例：quality 門檻調高/調低、relevance 門檻、prior 增減），**只寫建議不得改 code 閾值**。Don't: 不碰 data/papers/（用 temp dir）；不 commit；不印 key；不改門檻 code。
  Parallelization: Wave 1 | Blocked by: 1-7 | Blocks: -
  References (executor has NO interview context - be exhaustive): `.omo/STATE.md`（「M3C 驗收現況」：完整 run 方式/key 分配/Langfuse health 指令；「M4 驗收現況」與流程決定：route B 執行慣例、`.omo/evidence/` 存 log）；`literature_review/main.py`（端到端入口）；`.omo/evidence/` 下既有檔案（`m4-baseline-before.log`、`m4-baseline-before-dist.json`、M3C 的 `smoke-m3c-real-full.log` 可作 log 格式參考）
  Acceptance criteria (agent-executable): `.omo/evidence/m4-after.log`、`m4-after-dist.json`、`m4-before-after-compare.md` 三檔存在且非空；`m4-after-dist.json` 可 `json.load`、每筆含 paper_id/relevance/quality/recommendation/chunk_count，分數為 1 位小數 float 且 ∈[1,10]；compare.md 含五段（四段內容 + 校準結論段）；compare.md 的分布數字與兩個 dist JSON 統計一致；run log 無 `Pipeline failed` 且無 `AIza` key 字樣
  QA scenarios (name the exact tool + invocation): happy – 上述驗收指令（json.load、grep 無 Pipeline failed、數字交叉核對）；failure – 改後 run 失敗（如 key 缺/Langfuse down）→ 依錯誤訊息修復後重跑，不允許用舊 log 冒充；`m4-after-dist.json` 分數若出現 5 位整數範圍外或全無 include → 記錄異常於 compare.md 校準段，不得自行改 code；Evidence `.omo/evidence/task-8-m4-rcs-scoring.log`（執行過程 log）
  Commit: N（執行代理不 commit）

## Final verification wave
> Runs in parallel after ALL todos. ALL must APPROVE. Surface results and wait for the user's explicit okay before declaring complete.
- [ ] F1. Plan compliance audit
- [ ] F2. Code quality review
- [ ] F3. Real manual QA
- [ ] F4. Scope fidelity

## Commit strategy
- 執行代理**不執行任何 git commit**（專案慣例，git 由使用者親做）。
- 使用者驗收通過後 commit：M4 code + tests + 文件（含本計畫檔與 STATE）一次收（`feat(scoring): RCS 1-10 scoring, float aggregation, remove chunk recommendation` 或拆分 code/docs 兩次，依使用者偏好）。`.omo/drafts/`/`.omo/notes/`/`.omo/evidence/` 在 .gitignore（內部工作產物，不進 repo，不加 `-f`）。
- 執行前 `git status` 確認：既有未 commit 變更（M3B/M3C 待 commit + M4 code）——M3B/M3C 與 M4 分兩次 commit 較清晰（先收 M3B+M3C，再收 M4）。

## Success criteria
- 改後真實 run 成功（無 Pipeline failed、Langfuse 有 trace、key 無洩漏）
- 對比資料齊全且自洽：改前 vs 改後的分數/等級分布清楚呈現，鑑別度提升（或持平）有數據依據
- 校準建議明確：要嘛「無需調整＋理由」，要嘛具體數值建議（交使用者決策）
- 備註：M4 整體成功標準（196 tests 綠、1-10 契約、prompt 錨點等）已由 Todo 1-7 達成；本計畫只驗證最後一哩。