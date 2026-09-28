# .omo/STATE.md — 專案現況（最後更新：2026-09-29）

> `opencode.json` 只自動載入 `AGENTS.md`；需要時用 `@.omo/STATE.md` 重新載入。
> 規劃側維護，執行側只讀不改。架構摘要見 `AGENTS.md`；詳細交接見 `HANDOFF.md`；決策紀錄見 `.omo/plans/`。

## 現況快照

- 測試 **462 全綠**（2026-09-28 planner-prompt-tightening 收尾複跑，455 → 462）；section-stats 里程碑後測試數以 `.omo/evidence/section-stats-final.log` 實測為準。
- 已完成並 commit：M1、M2、M3A-C、M4、M5a-e、M5b/b1、M5c、M6、C2a-e、S1-S5、LLM Input Hygiene、Output Traceability、JSON 輸出、run-folder-and-policy、venues-by-name、papers-output（`d6d78d9`+`1c00de7`）、screening-prompt 升級（`7531aed`+`86f7f68`+`d1b1119`）、planner-prompt-tightening（`736afcf`+`d64ce08`）。
- **section-stats-and-ref-filter code/test/docs 已完成、待使用者 commit**：計畫 `.omo/plans/section-stats-and-ref-filter.md`（SD 統計、references? heading regex、層 1 補洞、層 2 別名摺合；含 key-leak canonical 正則併入）。
- **下一步＝F4 真實 run（需 key1-3 + SS key + Langfuse server up）**：量 SD baseline；B（section-aware sampling）以 F4 SD 數據為 gate。

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
| M5c SS 為主檢索 | ✅ | SS 主源 + DOI 去重 + 摘要 backfill（F4 real run 掛帳） |
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
| main.py `.env` 自動載入 | ✅ | `_build_clients` 呼叫 `load_local_env()`，完整 run 免 `--env-file`（376 tests） |
| Run folder & 研究政策 | ✅ | `data/run/` 每跑重建、`YEAR_WINDOW=3` 三年窗、頂會白名單硬濾（`FilterPolicy.venues`）、`--dest-dir/--year-from/--year-to/--venues` |
| Venues by name | ✅ 已 commit | `--venues` 按會議名對應別名表（`resolve_venues`：key/別名→展開全別名、未知→警告＋raw、`none`→不濾、全未知→警告＋不濾）|
| Papers output JSON | ✅ `d6d78d9`+`1c00de7` | 真實 run 另寫 `data/outputs/papers_*.json`（完整 Paper metadata＋query＋priority＋路徑＋run 概覽），與 report 同 ts 同資料夾、累積；dry-run 不產檔 |
| Screening prompt 升級 | ✅ `7531aed`+`86f7f68`+`d1b1119` | 引用數＋`Category A/B/C`／`Un-bucketed`／`Empty retrieval` 標記＋全域評判準則＋JSON 輸出要求壓尾；`SampledCandidates` 傳 bucket、`main_query` 接主 query |
| Planner prompt tightening | ✅ `736afcf`+`d64ce08` | `SearchPlan.queries` 3-4 條（schema 自握上下限）、Keyword Length (Strict) 2-4 關鍵字（含 good/bad 範例）、planning 全段 `_BudgetedClient` ≤2 LLM calls（schema+overlap repair 合計 ≤1）、overlap repair 接受＝≤0.5 或 strict 改善、`create_rule_based_plan max_queries<3 → ValueError`；462 tests（log：`.omo/evidence/planner-prompt-tightening-*.log`） |
| Section stats + references heading fix | ✅ code/test/docs 完成、待 commit | `section_stats.py` SD 統計（層 2 別名摺合＋保名成桶）印於 synthesis stdout；`_REFERENCES_HEADING_REGEX`（references? 覆蓋單複數＋5 新別名）；層 1 補 `conclusion(?:s)?`/`evaluation(?:s)?`/`method(?:ology|ologies|ogies|s)?`；key-leak canonical 正則併入 AGENTS（log：`.omo/evidence/section-stats-*.log`） |

## 下一步：候選里程碑（動工前先討論）

- **run-folder-and-policy 已完成 code/test/docs、待使用者 commit**：計畫 `.omo/plans/run-folder-and-policy.md`。F4 續跑不受影響。
- **M5c 已完成**（code commit + F1-F4b 驗收；F4 real run 因 Gemini 配額掛帳）。
  計畫：`.omo/plans/m5c-semantic-scholar.md`（Todos 已勾）；文件收尾：`.omo/plans/m5c-doc-wrapup.md`。
  F4 續跑指令：`printf 'literature review agent\n' | uv run -m literature_review.main`（需 key1-3 + SS key；下載落點已改 `data/run/`，run 後該資料夾即結果、不需還原）。
- **候選**：見「未動工候選（動工前討論）」清單；使用者曾提單獨資料夾偏好已成案＝`data/run/` 每跑重建（見其里程碑）。
- **section-stats-and-ref-filter 已完成 code/test/docs、待使用者 commit**：計畫 `.omo/plans/section-stats-and-ref-filter.md`——SD 統計（零 LLM、逐篇＋Aggregate）、references 標題補漏、層 1 缺章節補洞＋層 2 別名表、key-leak canonical；B（section-aware sampling）以 F4 SD 數據為 gate。
- 環境：`SEMANTIC_SCHOLAR_API_KEY` 已在 WSL `.env`（勿再提醒）。

## 2026-09-22 討論紀錄（使用者休息前交付；明日提醒）

使用者今晚提出 4 點，明日逐項提醒、確認後才動：

1. **寫計畫的 skill 目前不存在**。本專案只有 `plan-review`（審核、唯讀）、`project-context`、`review-progress`——皆 route-B 相容（主代理執行、不 spawn 子代理）。openCode 全域的 `plan-write` 類 skill 會 spawn 子代理 → 不符合本專案 route B，不可用。**本專案目標 = 建立 route-B 相容的 `plan-write` skill**（主代理直接寫 `.omo/plans/*.md`、不 spawn 子代理、寫前先與使用者確認計畫內容）。已授權建立、尚未建立。
2. **多 query 偏離已閉合**：SS pacing 1.1→2.0（code + 計畫同步，已 commit）。此為修我自己的幻覺衍生物，非使用者要求的需求。
3. **系統運作模式（使用者問我答、已確認）**：
   - 基礎篩選先做（OpenAlex URL filter `from_publication_date` / SS `year`）→ `FilterPolicy(min_year)` 於 ranking 前統一後衛（provider 間語意不同，故用統一 FilterPolicy 後衛）。
   - **當前 main.py 活路徑＝`filter_and_rank(response, FilterPolicy(min_year), encoder)` → RankingResponse（ranking.py）**。
     `SelectionPolicy`/`SelectedPaperSet`/`select_papers` 只存在 `selection.py` + `search.py`（search CLI 路徑），
     **不在 main.py 的 run_end_to_end 裡**。我先前把 SelectionPolicy 寫進「當前系統」是幻覺，已更正。
   - 每 query 各自搜尋；第二輪補搜在 LLM screening 後（gap follow_up_queries，≤3）。
   - LLM 一次接收所有 query 分桶取樣結果（單一 call）；第二輪 follow-up 補搜尋也在同 screening 流程。
   - dedup / 孤兒進 archive / W4416209823 已移 8 檔 archive。
   - EvidenceCitation = functional score 的證據出處（chunk + 頁碼範圍 + rationale）。
4. **使用者偏好（未成計畫、待討論）**：每輪 run 輸出一份獨立資料夾（下載/產出落點），避免累積混雜。非已批准 code 變更，明天再議。

**幻覺更正（2026-09-22）**：我先前捏造「把多個 query 的條件在 OpenAlex 一次查詢中同時給定」是使用者需求——**使用者從未提出**，純屬我的幻覺。多 query 是系統既有設計（`SearchPlan.queries`），各 query 獨立搜尋/排名/下載配額。已自行更正，不得再當成使用者需求。

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
- **Unpaywall no-OA backfill（後續優先、考慮中）**：無 OpenAlex/SS OA link 的論文從 Unpaywall 補 OA；使用者有考慮、尚未定案，動工前先議。
- **下載論文清單 JSON 輸出（✅ 已落地 2026-09-22）**：查詢+下載後把「實際下載論文清單」含 metadata 匯出 JSON 到 `data/outputs/`（與報告同資料夾、累積不清空）＝`papers_*.json`，`papers-output` 里程碑（Todo 全部完成）。
- **`main.py` DEST_DIR 覆寫（✅ 已落地 2026-09-22）**：`--dest-dir` / `DEST_DIR` 覆寫 `data/run/`（覆寫時不清空），免手動搬檔還原。
- **`data/papers/` 累積策略（✅ 已定案 2026-09-22）**：系統流程改落 `data/run/` 每跑清空重建；`data/papers/` 退役為手動用途。

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
