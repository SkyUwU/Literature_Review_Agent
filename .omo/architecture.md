# 現行架構說明（給外部 AI 對照用）

> 建立：2026-09-27（以 `literature_review/` 現行 code 實核）。尚未逐一對照任何論文之 GitHub 原始碼；下表的「歸類」皆為**印象中歸類，未逐一查證原始碼**，僅供討論起點。
> 正式端到端入口 = `literature_review.main.run_end_to_end()`（`main.py`）。

## 0. 系統總覽

```
ResearchIdea(query) -> SearchPlan -> 每 query 各別檢索（SS 主 / OpenAlex fallback）
-> metadata filter/rank -> LLM screening（keep/maybe/reject + gap follow-up ≤1 輪）
-> OA PDF 下載去重 -> 抽取(markdown)-> 章節感知切分 -> 每篇「內部」embedding top-2 取樣
-> LLM 功能性評分(1-10, batch 8) -> quota∩threshold 入選 -> 逐篇筆記(claims)
-> 綜合報告(outline→report→directions, [claim-N]) + papers/report JSON 落盤
```

## 1. 規劃（Planning）

- **輸入**：使用者 query 字串
- **處理**：`planning.create_llm_plan`（預設，key1）一次 LLM call 分解成 3-5 條互補子查詢（面向錯開＋同義詞改寫）；`create_rule_based_plan` 為免 key 備案（`--dry-run` 固定）。
- **輸出**：`SearchPlan`（query-only 契約，`SearchPlan.idea` 可選）
- **特性**：單次分解、無「先搜尋再產生子題」的檢索回饋
- **歸類**：ScholarGym *Query Planning*（印象、收斂）

## 2. 搜尋檢索（Search）

- **處理**：有 `SEMANTIC_SCHOLAR_API_KEY` 先查 Semantic Scholar（`ss_search.search_ss`）；缺失/空結果/失敗 → fallback OpenAlex（`search.search_papers`）；SS 缺摘要的論文按 DOI 從 OpenAlex backfill，仍無摘要則丟棄。
- **限制**：`LIMIT=100`；年份窗 `YEAR_WINDOW=3`（unit：`default_min_year()`＝當年前-2，每請求套用＋`FilterPolicy(min_year)` 後衛）；頂會白名單硬濾（`FilterPolicy.venues`，17 會、別名包夾式匹配）。
- **輸出**：`SearchResponse`（provider、request、`Paper[]`）
- **特性**：provider API 選源＋參數，**自行設計、未參考特定論文**（使用者認定）。

## 3. 排名與 LLM 篩選（Rank + Screening）

- **排名**：`ranking.rank_papers_embedding` — bge-small 對 query vs title+abstract 的 cosine ＋citation＋recency 三成分等權（總分 0..3）；`--dry-run` 用 lexical（`rank_papers`）。
- **篩選**：`screening.sample_candidates` 三桶選樣（A 權威 25%／B 前沿 50%／C 跨領域 25%，每 query 送 LLM 約 24 篇）→ **單一 merged call** `screen_candidates` 回 keep/maybe/reject＋gap 分析（covered/missing/`follow_up_queries` ≤3）→ 每條 follow-up 補搜一輪（最多 1 輪）。
- **輸出**：`ScreeningResult`；下載採用 keep 優先、maybe 補滿額。
- **歸類**：screening 判定＝ScholarGym *Relevance Assessment*（使用者認定）；gap follow-up＝ScholarGym *iterative query decomposition* 的落地（印象）；三桶選樣＝與 SurveyG 多層多面向取樣概念類似（**⚠️ 不確定**，計畫檔未記來源）。

## 4. PDF 取得（Acquisition）

- **處理**：`pdf_downloader.download_and_backfill` — 依 `priority_groups`（keep→maybe）top-N 下載；失敗（無 OA／網路）以後位候選遞補；跨 query 共享 `already_downloaded` 去重（DOI→title+year）。
- **落點**：`data/run/`（每跑重建；`--dest-dir`/`DEST_DIR` 覆寫且不清空）。
- **回報**：OA 覆蓋率（`oa_ratio_candidates/attempted`、`shortfall`、`duplicate_reused`）。

## 5. 抽取與切分（Extraction + Chunking）

- **處理**：`extraction.extract_pdf_text`（pymupdf4llm markdown、表格保留）；`evidence.chapter_chunk_document` 章節感知切分（chunk id `{paper_id}-c{n}`、含跨頁 `page_start/page_end`、`section` 路徑）。
- **過濾**：`drop_noise_sections` 丟參考文獻/appendix；`min_words≥4`。
- **輸出**：`FullTextDocument[]` → `EvidenceChunk[]`

## 6. 每篇內部取樣 + 功能性評分（Sampling + Functional Scoring）

- **取樣**：`functional.sample_top_chunks_per_paper` — **每篇論文的 chunks 只在自家論文內競爭**（無 corpus-wide top-k），query 用「該篇被抓入的 sub-query」（S2）、section wrapper 前綴，取 **top-2**。黑名單後無 chunk 的論文 → 不進評分。
- **評分**：`functional.score_chunks_functionally` — LLM 每 **8** chunk 一批打**單一 1-10 utility**（`FUNCTIONAL_BATCH_SIZE=8`，index→trusted id 反解＋repair）；`aggregate_functional` = `0.7×max + 0.3×mean`。
- **入選**：`select_quota_threshold` — per-query 組內配額（首輪 `n_first_round=2`、補搜 `n_follow_up=1`）∩ 平均 ≥ `threshold=6.0` → include/exclude。
- **歸類**：每篇內部 embedding top-2 → LLM 評分＝**PaperQA2**（使用者認定）。

## 7. 逐篇筆記（Per-paper Notes）

- **處理**：`synthesis.paper_notes`（key2）→ 每篇 `PaperSummary`（contribution/method/experiments/results/limitations claims），每 claim 帶 `claim_id`＋`ChunkReference`（chunk/頁碼，機械組裝，LLM 不產出頁碼）。

## 8. 綜合報告（Synthesis）

- **處理**：**三 call**（key3）outline → report → future directions；報告直接散文＋內嵌 `[claim-N]`；材料來源清單由程式 `render_materials_section` 組裝；`bounded evidence`（每 claim 只能依賴供應的 chunk 集）。
- **歸類**：outline→report 大綱驅動寫作＝**STORM**（印象、收斂）。

## 9. 輸出與可觀測性（Output / Observability）

- `main.save_report_output` / `save_papers_output` → `data/outputs/report_*.json`＋`papers_*.json`（同 timestamp、累積；dry-run 不產檔）。`PapersOutput.run` 含 query／planned_queries／follow_ups／stats_per_query／failed_extractions／warnings。
- Key 拆三：（key1）planner+screening、（key2）functional 評分+逐篇筆記、（key3）報告三 call。
- Langfuse 每 LLM call 樹狀 trace。

## 對照快速表（印象歸類）

| 本系統階段 | 對照論文/系統 | 確度 |
|---|---|---|
| planning 多子查詢分解 | ScholarGym *Query Planning* | ✅ 印象 |
| search 檢索呼叫 | 無特定論文（自行設計） | ✅ 使用者認定 |
| screening keep/maybe/reject | ScholarGym *Relevance Assessment* | ✅ 使用者認定 |
| gap follow-up（≤1 輪） | ScholarGym *iterative query decomposition* 落地 | ✅ 印象 |
| 三桶選樣 A/B/C | 類似 SurveyG 多層多面向取樣 | ⚠️ 不確定 |
| 每篇內部 top-2 → LLM 評分 | PaperQA2 | ✅ 使用者認定 |
| 綜合報告 outline→report→directions | STORM 大綱驅動寫作 | ✅ 印象 |

## 已知限制（說「能更好」時的重點）

- **metadata 層篩選 ≠ 全文學術評估**：screening 只看 title/abstract；functional 評分也只見每篇 2 個抽樣 chunk，非整篇論文回顧。
- **無生成時檢索**：follow-up 只在 screening 後一輪，OpenScholar 式的「生成中再檢索」未做。
- **單一 1-10 utility** 取代摳雙維度（rel/qual/RCS）——取捨見 C2b 紀錄。
- **真實端到端 run 仍受 Gemini 配額卡關（F4）**：完整「下載→抽取→評分→報告」的真實產出尚未在最新設定下全跑一遍；tests（449）鎖的是合成/注入路徑。