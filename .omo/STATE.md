# .omo/STATE.md — 專案現況（最後更新：2026-09-21）

> `opencode.json` 只自動載入 `AGENTS.md`；需要時用 `@.omo/STATE.md` 重新載入。
> 規劃側維護，執行側只讀不改。架構摘要見 `AGENTS.md`；詳細交接見 `HANDOFF.md`；決策紀錄見 `.omo/plans/`。

## 現況快照

- 測試 **376 全綠**（2026-09-21 main.py `.env` 自動載入修正）。
- 已完成並 commit：M1、M2、M3A-C、M4、M5a-e、M5b/b1、M6、C2a-e、S1-S5、LLM Input Hygiene、Output Traceability、JSON 輸出。
- **唯一待執行里程碑：M5c（Semantic Scholar 為主檢索）** — 計畫 `.omo/plans/m5c-semantic-scholar.md`（唯一權威）已寫，待貼執行指令包。

## 里程碑一覽

| 里程碑 | 狀態 | 成果一句話 |
| --- | --- | --- |
| M1 pairwise retrieval eval | ✅ | embedding 檢索勝 lexical（6 queries overall verdict） |
| M2 embedding adoption | ✅ | pipeline 正式改用 bge-small embedding（lexical 留 legacy） |
| M3A LLM planner | ✅ `2a09f7b`+`c5414a1` | query-only `SearchPlan` + `create_llm_plan`（rule-based 備案） |
| M3B PDF downloader | ✅ | OA PDF 抓取 + `download_and_backfill`（OA 覆蓋 77.3%） |
| M3C main.py 端到端 | ✅ | query → plan → search/rank → download → synthesis 入口 |
| M4 RCS 1-10 評分 | ✅ | 1-10 制 + 操作型錨點（後由 C2b 取代） |
| M5a planner 多面向 | ✅ `6457dbe9` | 子查詢重合度 0.444 → ≤0.133 |
| M5b RCS 校準 | ✅ | A9/A10 雙 rationale、B=4；10 分比例 47% → 3-9% |
| M5b.1 prompt 小修 | ✅ | 誠實證偽「加指示」路線（8B 無效），停止投入 |
| M5c SS 為主檢索 | ⏳ 計畫已寫待執行 | SS 主源 + DOI 去重 + 摘要 backfill |
| M5e 候選池升級 | ✅ | 分桶選樣 + LLM 篩選（keep/maybe/exclude）+ gap follow-up |
| M6 RCS on Ollama | ✅ | `OllamaJsonClient` + Gemini fallback（後裁定評分線路回 Gemini） |
| C2a pymupdf4llm 抽取 | ✅ | markdown 表格 + 章節感知 chunk（`{paper_id}-c{n}`） |
| C2b 功能性評分 | ✅ | 單一 1-10 utility 取代 rel/qual、配額∩閾值、consider 退役 |
| C2c claim 標註 | ✅ `60fdf0bc`+`22308360` | `[claim-N]` + `claim_chunks` + key3 |
| C2d aspect metadata 過濾 | ✅ | 期刊前綴/作者行誤判修復 |
| C2e 三 call 報告生成 | ✅ | outline → report → directions（方向 call 不含大綱） |
| S1-S5 評分/輸出線 | ✅ `3ab07097`+`c01a3b32` | 0.7×max+0.3×mean、sub-query 取樣、claim 統一 |
| LLM Input Hygiene | ✅ | LLM 輸入零頁碼（對外由程式組裝） |
| Output Traceability | ✅ | `claim_id` / `claim_ids` 進對外輸出 |
| JSON 輸出 | ✅ | `save_report_output` → `data/outputs/report_*.json` |

## 下一步：M5c（Semantic Scholar 為主檢索）

- 計畫：`.omo/plans/m5c-semantic-scholar.md`（唯一權威，含 Todo 0-5 + F1-F4 + Commit strategy）。
- 執行順序：**Todo 0 probe → 1（`Paper.doi` 欄位）→ 2∥3（去重 / SS adapter）→ 4（pipeline 接線）→ 5（smoke）**。
- 設計要點：SS 為主（key 存在時）、OpenAlex 雙角色（搜尋 fallback + DOI 補摘要 backfill）；**adapter 不過濾無摘要論文**（過濾移到 backfill 之後、`filter_and_rank` 之前）；`Paper.abstract` 必填 → 用 `ABSTRACT_PLACEHOLDER` 佔位（backfill 取代、救不回才在 pipeline 過濾）。
- 環境：`SEMANTIC_SCHOLAR_API_KEY` 已在 WSL `.env`（勿再提醒）。

## 掛帳（仍開放）

1. **key call 統計未納 log**（S1-S5 final run）→ 由 Langfuse dashboard 補查，或接受缺項。
2. **JSON 輸出真實 run 展示**：使用者有空跑 `uv run python -m literature_review` 看 `data/outputs/report_*.json`（main.py 現已自動讀 `.env`，`--env-file .env` 仍可用；dry-run 不產生檔案，已由測試覆蓋）。
3. **已由 S1-S5 final run 關閉的舊掛帳**：M5b Todo 6、M5e Todo 5（usable 6 篇、天花板 8.8 > 8.5 基準）、C2b Todo 5、C2c 收集項、C2e 真實 run — log `.omo/evidence/s1s5-real-final.log`。

## 未動工候選（動工前討論）

- **M5f embedding 檢索 query 表徵**：論文相似度改「對全部 sub-query cosine 取 max」（multi-query max，零額外 LLM 成本）；動工時機＝M5e 驗收後離線評測單 query vs multi-query 進池差異。
- **M5d planner plan-review loop**：建議版＝planner 產 plan → 純 OpenAlex 乾搜看命中統計 → 統計餵回調整（只燒 planner 呼叫）；完整 run 回饋不做。
- **paper_id→DOI 長期遷移**：DOI 取代 paper_id 為 canonical ID（動 `chunk_id`/PDF 檔名/traceability，需獨立里程碑）；觸發條件＝M5c probe 顯示 SS 無 DOI 比例高。
- **Marker-pdf（PDF 深度版面分析）**：治療 A2 表格破碎病根；動工前先 PoC（2-3 樣本 fitz vs marker 對比）。（`pymupdf4llm` 已由 C2a 採用。）
- **A2 表格/結構化證據低估**：K3c 校準發現（使用者 vs LLM 10vs6）；複驗＝再抽 2-3 表格型 chunks 對比。
- **functional prompt 教 LLM 解讀 Section 欄位**：一行 prompt（附錄/References chunk 鮮少貢獻 utility）；與 T1 章節包裝同批（章節資訊主題）。
- **Claude 4 建議未落地項**：① 陪襯效應（逐 chunk 化，校準後仍飄才考慮）、③ max-chunk 採納條件（併閾值決策）、④ 本地小模型（M6 已試 Ollama，已裁定評分線路回 Gemini，列遠期）。
- **`main.py` DEST_DIR 覆寫（候選 M）**：支援 env/flag 覆寫 `data/papers/`，免手動搬檔還原。
- **`data/papers/` 累積策略（待討論）**：每次 run 清空 / 互動詢問 / 維持現況。

## 關鍵裁示（使用者定案）

- **Key 配置**：key1=`GEMINI_API_KEY`（planner/screening）、key2=`GEMINI_API_KEY_2`（評分 + 逐篇筆記）、key3=`GEMINI_API_KEY_3`（報告三 call；缺 → exit 1）。備案：key2 429 密集 → notes 移 key3（notes 先於報告、依序執行無碰撞）。
- **報告生成**：三 call 兩階段（outline → report → directions），方向 call 輸入**不含大綱**、不限 limitation；三 call **不送 assessments 摘要**、共用 key3。
- **評分**：正式路徑＝功能性評分（單一 1-10 utility，`functional.py`）；入選 = 配額 ∩ 閾值（`FUNCTIONAL_THRESHOLD` 預設 6.0，真實 run min 恰貼線 → 維持）；RCS 留 legacy。
- **參數**：`llm_input_cap=80`（欄位上限 200）、`FUNCTIONAL_BATCH_SIZE` env、`NOTES_PACING_SECONDS=4s`。
- **LLM 輸入最少必要**：整理/評分層不給頁碼等資訊；對外輸出（`ChunkReference`/paper_sources/materials）由程式機械組裝。
- **流程慣例**：route B（執行代理改 code、規劃側只驗收，不 spawn 子代理）；git commit/push **一律使用者親自做**；執行指令包精簡（計畫檔為唯一權威）；commit 指令包一次列齊全檔清單；每個 Todo 的 log 存 `.omo/evidence/<slug>-<todo>.log` 並在回報附檔名清單。
- **邊界**：`bounded evidence` — 每個 claim 只能依賴供應的 chunk 集，不得用外部知識。

## 工作產物（2026-09-21 整理）

- 未追蹤工作產物（`drafts/`、`evidence/`、`notepads/`、`notes/`、`start-work/`、`run-continuation/`）不進 git。
- 舊工作產物已打包至 `.omo_archive_20260921.tar.gz`（含舊 drafts、`.omo` backup、103 個早期 log；已 gitignore）。`evidence/` 保留知識類 `.md` 對照表 + 近期里程碑 log，供 `project-context` skill 與查證使用。
