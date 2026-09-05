# m3c-main-entry — Work Plan (M3C)

## TL;DR (For humans)

**What you'll get:** 一個 `main.py` 端到端入口：互動式問一句 query → **LLM 產生 SearchPlan（正式/預設）** → 每組 query 各自去 OpenAlex 搜尋/篩選/排名 → 各自下載 PDF(跨 query 用共享集合去重，重複算達成、失敗才遞補)→ 抽全文 → LLM 綜合報告。**一條指令產出文獻回顧報告**；無 key 時 `--dry-run` 可跑到「下載完成」為止（乾跑強制 rule-based，零消耗）。

**Why this approach:** M1-M3B 已把每個部件做完並驗收，缺的只是串接。使用者定案的關鍵：**不做多 query 合併**——每 query 各自選取/下載，用共享「已下載 paper_id 集合」解決跨 query 重複；重複論文算達成（不重複寫檔、不遞補），只有下載失敗（無 OA / 網路錯）才遞補該 query 自己的續集。key 分配：搜尋計畫（LLM 版）用 `GEMINI_API_KEY`、合成報告用 `GEMINI_API_KEY_2`（`GeminiJsonClient` 支援 `api_key` 參數）。

**What it will NOT do:** 不做多 query 合併 / 全局排名選集；不動 `ranking.py` 的同標題去重（M3B 已驗收）；不碰 DOI 去重；不做 Unpaywall / arXiv 補查（M3C 之後）；不實作 OpenAlex 翻頁（limit 50 < 200 上限，無需）；不改既有 `search.py` / `planning.py` / `pipeline.py` 公開契約（僅 `download_and_backfill` 加可選參數）；不 commit git（使用者做）。

**Effort:** Medium（1 個新模組 + 1 個既有函式擴充 + 測試 + 文件）
**Risk:** Medium-Low — 動到 `download_and_backfill` 簽名/統計（需回歸 M3B 的 8 個既有測試）；其餘純新增。key 使用僅在真實 smoke（先確認）。

**Decisions（全部已與使用者定案，2026-09-05）:**
0. 入口：`literature_review/main.py`，互動式問 query，參數寫死（`LIMIT=50`、`MIN_YEAR=2021`、`TOTAL_TARGET=15`、`TOP_K_CHUNKS=8`），`target_n = ceil(15 / len(plan.queries))`。
1. plan：**LLM 正式/預設使用**（`create_llm_plan`，用 key1）；rule-based 退居**純備案**（`--rule-based` flag 可強制；LLM 失敗自動 fallback；`--dry-run` 強制 rule-based 保持零 key）。⚠️ 修正紀錄：9/2 roadmap 定義 M3 =「LLM 取代 rule-based planner」，原決策 1 誤寫「預設 rule-based」與之相反且交付時未標記，2026-09-05 使用者指出後修正（見 Amendment 1）。
2. key 分配：planning（僅 LLM 版）用 `GEMINI_API_KEY`；synthesis 用 `GEMINI_API_KEY_2`（`GeminiJsonClient(api_key=os.getenv("GEMINI_API_KEY_2"))`）。
3. 多 query：**無合併**。每 query 各自 `filter_and_rank` → 各自取 top `target_n` → 各自 `download_and_backfill`；共享 `already_downloaded: set[str]` 跨 query 去重。
4. 重複語意：paper_id 已在共享集合 → **算達成**（quota 消耗 1、不寫檔、不遞補）；下載失敗（`NoOpenAccessError` / `PdfDownloadError`）→ 遞補該 query 自己排名續集。
5. 去重鍵：跨 query 用 **`paper_id`**（OpenAlex ID，`W...`）；同 query 內維持既有標題去重（不動）。
6. `download_and_backfill` 小擴充：加 `already_downloaded` 參數 + `DownloadResult.downloaded_paper_ids`（與 path 同序配對）+ `DownloadStats.duplicate_reused`。
7. `--dry-run`：無 key 模式；下載完成即停——不抽全文、不載入 embedding encoder、不呼叫 LLM。**乾跑強制 rule-based**（即使沒下 `--rule-based`，因為乾跑的意義是零消耗）。
8. Unpaywall：**M3C 之後**（使用者定）。
9. 紀律：任何消耗 key 的測試/smoke 前，執行代理**先跟使用者確認**。
10. 執行方式：**route B**（執行代理獨立 session 實作，使用者貼指令包；執行代理不 commit）。

Your next move: 執行代理在獨立 session 實作（使用者貼指令包）；不做 `/start-work`。

---

## Amendment 1（2026-09-05 晚，使用者指出後修正）

**原因**：2026-09-02 roadmap（舊 session 紀錄）定義 **M3 =「LLM 規劃 SearchPlan（取代 rule-based planner，輸出同 Pydantic 契約）」**——使用者的意圖是 **LLM 成為正式/預設使用**。但本計畫原決策 1 誤寫「預設 rule-based、--llm-plan 選配」，與 roadmap 方向相反，且在交付 brief 時未標記此偏離，導致使用者批准時認知停留在「說好的取代」。2026-09-05 使用者指出紀錄自相矛盾。

**修正內容**：
1. **LLM 為正式/預設 planner**：完整 run 預設走 `create_llm_plan`（key1）；rule-based 僅為備案（LLM 失敗 fallback）。
2. **CLI flag 反轉**：`--llm-plan`（選用 LLM）→ `--rule-based`（強制 rule-based 的逃生門；正常不需要）。
3. **dry-run 強制 rule-based**：乾跑零 key 原則不變（決策 7）。
4. **key 分配不變**：planning=key1、synthesis=key2（決策 2）。
5. **受影響 Todo**：Todo 2（main.py 簽名/CLI）、Todo 6（測試情境：預設 LLM、`--rule-based`、乾跑 fallback）、Todo 7（文件：AGENTS.md / HANDOFF.md / README.md 描述統一「LLM 預設、rule-based 備案」）、Todo 8（完整 run 的預設路徑即測 key1+key2，不再需要額外 `--llm-plan` 驗證）。
6. **防再犯**：見 `.omo/notes/plan-review-checklist.md` 第九類（跨文件行為一致性檢查）；此類「與先前方向相反」的決策往後必須在 Decisions 區塊標 `⚠️ 與先前方向相反` 並在交付 brief 第一段明示。

---

## 下一步行動卡 (ACTION CARD — 執行 agent 每次開 session 第一件先看這塊)

> **單一事實來源。目前要做哪個 Todo、讀哪個指令包。避免轉述長命令。**
>
> **當前狀態：Todo 1（download_and_backfill 擴充）→ Todo 2（main.py 骨架）→ Todo 3（每 query 搜尋→排名→下載）→ Todo 4（pipeline 串接）→ Todo 5（key 分配）→ Todo 6（測試）→ Todo 7（文件）→ Todo 8（驗收 smoke）→ 最終驗收 F1-F4**
>
> **⚠️ Commit 紀律：執行代理不執行任何 git commit/push——commit 一律由使用者（本人）親自做。** 你只改 code、跑測試、存 log、回報。
>
> **⚠️ key 紀律：任何消耗 GEMINI_API_KEY / GEMINI_API_KEY_2 的 smoke，動手前先跟使用者確認。**
>
> 建立後續 Todo 細節如下。目前先跑 `git status` 確認工作區乾淨（只允許 `.omo/` 檔案未 commit）。

---

## Scope

**IN:**
- `literature_review/pdf_downloader.py`：`download_and_backfill` 加 `already_downloaded` + `DownloadResult.downloaded_paper_ids` + `DownloadStats.duplicate_reused`（含 to_dict 同步 + 既有測試相容）。
- `literature_review/main.py`（新）：`run_end_to_end(...)`（可注入） + `main()`（互動式）；寫死參數。
- `tests/test_main.py`（新）+ `tests/test_pdf_downloader.py`（擴充）。
- 文件：AGENTS.md / HANDOFF.md / README.md。
- 驗收 smoke：fake e2e（無 key）+ 真實 smoke（先確認 key）。

**OUT:**
- 不做多 query 合併、全局排名選集（決策 3）。
- 不改 `ranking.py` / `selection.py` / `planning.py` / `search.py` / `pipeline.py` 的既有契約（Todo 1 的 `download_and_backfill` 擴充除外）。
- 不做 Unpaywall / arXiv（決策 8）。
- 不做 OpenAlex 翻頁、不改 `SearchRequest.limit` 上限。
- 不動 `.env`、不印/抄 API key、不碰 `data/papers/` 既有內容。
- 不 commit / 不 push（Commit strategy）。
- 不改「覆蓋包 / coverage pack」在 pipeline 的角色（已知現況，見 STATE.md）。

**Must NOT-Have:**
- `main.py` 不得把 `paper.open_access_pdf_url` / API key / 任何祕密印到 stdout 或寫入 log。
- 重複下載不得寫入第二份檔案（同名檔不得覆寫）。
- 不得為「跨 query 重複」觸發遞補（語意：重複 = 已達成）。
- 不得在 `--dry-run` 或 fake smoke 中呼叫真實 Gemini。

---

## Verification strategy

- 每 Todo 有 Acceptance + QA（happy + failure），證據存 `.omo/evidence/`（log 檔由執行代理跑並存檔，規劃 agent 只讀驗收）。
- Todo 8 的 fake e2e 必須**無 key** 可通過；真實 smoke 需使用者先確認才跑。
- 全套件回歸：`uv run python -m unittest discover -s tests -v`（既有 164 + 新增）。
- 最終驗收 wave：F1-F4 全過才宣告完成（見 Final verification wave）。

---

## Execution strategy

- **route B**（專案慣例）：執行代理在獨立 session 依 ACTION CARD + Todo 指令包實作；規劃 agent 驗收（讀 log 核對）；**不 spawn 執行 subagent、不執行 git**。
- 測試策略：**tests-after + 每 Todo 附測試**（新功能加測試後再跑全套件回歸；不 TDD 也無妨，但測試必須存在且綠）。
- 每 Todo 完成後跑一次 `uv run python -m unittest discover -s tests -v` 並存 log 到 `.omo/evidence/`。

---

## Todos

- [ ] 1. `pdf_downloader.py`:擴充 `download_and_backfill`（already_downloaded + 配對回傳）— 重複算達成不遞補、失敗照舊遞補

**What to do:**
- `literature_review/pdf_downloader.py`：
  - `download_and_backfill(ranked_papers, dest_dir, target_n, *, fetcher=default_fetcher, stats_path=None, already_downloaded=None)`：新增 `already_downloaded: set[str] | None = None` 可選參數。
  - 迴圈（現有 line 156-173 基礎上）：
    - **靠前檢查重複**：`if already_downloaded is not None and ranked.paper.paper_id in already_downloaded:` → `stats.duplicate_reused += 1; continue`（不寫檔、不計 failure、不遞補）。
    - **break 條件改為**：`if stats.downloaded + stats.duplicate_reused >= target_n: break`（重複 = 已達成，消耗 quota）。
    - 成功下載後：`if already_downloaded is not None: already_downloaded.add(ranked.paper.paper_id)`。
  - `DownloadStats` 加欄位 `duplicate_reused: int = 0`；`to_dict()`（line 108-120）加 `"duplicate_reused"`。
  - `DownloadResult` 加欄位 `downloaded_paper_ids: list[str] = field(default_factory=list)`（與 `downloaded_paths` **同 index 對應**；成功下載時兩邊同時 append）。
  - `download_pdf` 不改、`_safe_filename` 不改、`Fetcher` 型別不改。
- `tests/test_pdf_downloader.py` 擴充（見 QA）。

**Must NOT:**
- 不改既有欄位語意：`downloaded` 仍 = 真下載數（`len(downloaded_paths)`）；重複不計入 `downloaded`。
- 不刪既有欄位；`already_downloaded=None`（舊呼叫）時行為與 M3B 完全一致。
- 不為重複觸發遞補。

**References:**
- `literature_review/pdf_downloader.py:80-129`（DownloadStats / DownloadResult）、`132-180`（download_and_backfill 迴圈與統計）
- `literature_review/models.py:8-16`（Paper.paper_id 欄位）
- `tests/test_pdf_downloader.py`（既有 8 tests 為相容基準）

**Evidence of completion:**
- 新測試綠 + 既有 8 個 pdf_downloader tests 全綠（log 存 Todo 6）。
- 手動驗證：`grep -n "already_downloaded\|duplicate_reused\|downloaded_paper_ids" literature_review/pdf_downloader.py` 全部命中。

**QA scenarios:**
- happy：2 個「query 清單」共享同一 `already_downloaded` set；第一篇被第 2 個清單命中 → `duplicate_reused=1`、不寫第二份檔、`downloaded` 不增、接著下載續集論文。
- failure：清單前 `target_n` 篇全是重複 → 迴圈提早 break（`duplicate_reused >= target_n`）、無新檔案寫入、`downloaded=0`；若下載失敗 → 仍遞補該清單續集（既有行為不變）。
- 證據路徑：`.omo/evidence/test-suite-m3c.log`（含 `test_pdf_downloader` 案例名）。

**Commit:** 使用者（本人）commit，指令見 Commit strategy。

---

- [ ] 2. `main.py`:建立入口骨架（run_end_to_end 可注入 + main() 互動式）— 參數寫死、互動式問 query

**What to do:**
- 新增 `literature_review/main.py`：
  - 模組常數：`LIMIT = 50`、`MIN_YEAR = 2021`、`TOTAL_TARGET = 15`、`TOP_K_CHUNKS = 8`、`DEST_DIR = Path("data/papers")`。
  - `run_end_to_end(query: str, *, dest_dir: Path, client_plan: JsonGenerationClient | None = None, client_synth: JsonGenerationClient | None = None, use_llm_plan: bool = False, dry_run: bool = False, json_fetcher: JsonFetcher = search.fetch_json, pdf_fetcher: Fetcher | None = None) -> dict[str, object]`：
    - 流程：`create_rule_based_plan(query)`（`use_llm_plan=True` 時 `create_llm_plan(query, client_plan)`，失敗 fallback rule-based）。
    - `target_n = math.ceil(TOTAL_TARGET / len(plan.queries))`。
    - 建立共享 `already_downloaded: set[str] = set()`。
    - 對每 query 依序：搜尋 → 排名 → 下載（Todo 3 細節）；收集 `(paper_id, path)` 與 stats。
    - dry_run → 回傳 dict：`{"plan": plan, "downloads": [...], "stats_per_query": [...], "dry_run": True}`；非 dry_run → 串 pipeline（Todo 4）回傳含 `report`。
    - 所有外部 IO（OpenAlex fetch、PDF fetch、LLM client）都可注入，**測試不需真實網路/key**。
  - `main()`：`argparse` 只保留 `--llm-plan`、`--dry-run`；query 用 `input("請輸入 research query：") `互動式取得；準備 `client_plan` / `client_synth`（Todo 5）；呼叫 `run_end_to_end`；印結果摘要（統計 + 報告 JSON）。
- 模組入口：`if __name__ == "__main__": main()`。

**Must NOT:**
- 不把 `GEMINI_API_KEY*` 印出/寫入 log；key 只在 `GeminiJsonClient(api_key=...)` 內使用。
- 不在 `main()` 實作長 argparse 選項（參數寫死為原則）。
- 不出現「合併/全局排名」邏輯。

**References:**
- `literature_review/planning.py:15`（create_rule_based_plan）、`:98`（create_llm_plan，`max_queries` 預設 5）
- `literature_review/search.py:117`（search_papers，`json_fetcher` 可注入）
- `literature_review/pipeline.py:109`（run_synthesis_pipeline 簽名）
- `literature_review/models.py:27-41`（SearchPlan：queries 1~10）、`:60-77`（SearchRequest / SearchResponse）、`:127-140`（FullTextDocument）

**Evidence of completion:**
- `"literature review agent" | uv run python -m literature_review.main --dry-run`（PowerShell pipe 餵 stdin；無 key）互動流程可跑到下載階段（temp dir）且不爆炸（log 存 Todo 8）。
- `grep -n "def run_end_to_end\|def main\|TOTAL_TARGET\|LIMIT" literature_review/main.py` 命中。

**QA scenarios:**
- happy：注入 fake（Todo 6 詳細）跑完整流程，回傳 dict 含預期 key。
- failure：`--llm-plan` 且 `client_plan=None` / 無 key → 拋錯前 fallback rule-based（不崩潰）；`input` 讀到 EOF（pipe 結束情境）→ `EOFError` 處理（印錯誤、exit 1）。
- 證據路徑：`.omo/evidence/smoke-m3c-fake-e2e.log`。

**Commit:** 使用者（本人）commit。

---

- [ ] 3. `main.py`:每 query 搜尋 → filter_and_rank → 各自取下載清單（共享集合去重）— 無合併、重複算達成

**What to do:**
- 在 `run_end_to_end` 內實作「每 query 一輪」：
  1. `request = SearchRequest(query=plan.queries[i].query, limit=LIMIT, year_from=MIN_YEAR)`。
  2. `response = search_papers(request, json_fetcher=json_fetcher)`。
  3. `ranked = filter_and_rank(response, FilterPolicy(min_year=MIN_YEAR))`（沿用 search.py CLI 的用法：`ranking.py:94` 回傳 `RankedSearchResponse`，其 **`.ranked_papers`** 為 `list[RankedPaper]`——注意欄位名不是 `papers`，見 `models.py:102`）。
  4. `result = download_and_backfill(ranked.ranked_papers, dest_dir, target_n, already_downloaded=already_downloaded, fetcher=pdf_fetcher if pdf_fetcher is not None else pdf_downloader.default_fetcher)`——**注意 pdf_fetcher=None 時必須 fallback 到 `pdf_downloader.default_fetcher`，絕不可把 None 傳給 fetcher 參數（download_pdf 會呼叫它）。**
  5. 收集 `list(zip(result.downloaded_paper_ids, result.downloaded_paths))` 進跨 query 累積清單（供 Todo 4 抽全文）。
  6. 每 query 統計（DownloadStats 摘要）記入 `stats_per_query`。
- 順序固定為 `plan.queries` 的順序（結果確定可重現）。
- 不做任何跨 query 的合併/全局排名/dedup 以外的動作。

**Must NOT:**
- 不把多 query 候選合併成單一 ranked 清單（維持每 query 各自 downnload）。
- 不依賴 OpenAlex 翻頁（`LIMIT=50` ≤ 200 上限）。

**References:**
- `literature_review/ranking.py:94-101`（filter_and_rank）、`:26-41`（filter_papers：標題去重、min_year）
- `literature_review/pdf_downloader.py:132-180`（Todo 1 後的新簽名）
- `literature_review/models.py:60-77`（SearchRequest / SearchResponse 欄位）

**Evidence of completion:**
- fake e2e log 顯示：每 query 的 OA 比例、downloaded、duplicate_reused、shortfall；跨 query 重複論文只下載一次。
- `grep -n "SearchRequest\|filter_and_rank\|download_and_backfill" literature_review/main.py` 命中。

**QA scenarios:**
- happy：2 個 fake query 皆命中同一篇熱門論文 → 總檔案數 = 獨特論文數（非 2×target_n）。
- failure：某 query 前幾篇皆無 OA → `failed_no_oa` 計數、遞補續集；候選用盡 → `shortfall` 統計、流程不崩潰。
- 證據路徑：`.omo/evidence/smoke-m3c-fake-e2e.log`。

**Commit:** 使用者（本人）commit。

---

- [ ] 4. `main.py`:pipeline 串接（PDF → extract_pdf_text → run_synthesis_pipeline）— 報告輸出 + dry-run 分流

**What to do:**
- 非 dry_run 時：
  1. 對 Todo 3 收集的 `(paper_id, path)` 依序 `extract_pdf_text(path, paper_id)` → `list[FullTextDocument]`（`extraction.py:14` 簽名：`extract_pdf_text(path, paper_id) -> FullTextDocument`）。
  2. **收集迴圈內用 try/except 包住 `extract_pdf_text`**：單一 PDF 抽取失敗 → 記錄該 `paper_id` 進 `failed_extractions: list[str]` 並 `continue`（不中斷整個 run）；其餘文件照常。若全部文件都失敗 → 回傳錯誤（比照 `pipeline.py:105` 的 `raise ValueError` 語意）。
  3. `report = run_synthesis_pipeline(documents, query, client_synth, retrieval_policy=EvidenceRetrievalPolicy(top_k=TOP_K_CHUNKS))`——其餘 policy（chunk/coverage/aggregation/encoder）用預設值（`pipeline.py:109-119` 完整簽名）。
  4. 回傳 dict 含 `report`（`SynthesisResponse.model_dump_json()` 字串）。
- dry_run 時：**不**呼叫 `extract_pdf_text`、**不**呼叫 LLM；回傳「下載清單（paper_id + path）+ 每 query 統計」即可。
- Langfuse：完整 run 結束後在 `main()` 呼叫 `pipeline._flush_langfuse()`（`pipeline.py:153-160` 既有 best-effort helper，非 dry_run 才呼叫）。
- 報告輸出：`main()` 印 `report` 的 `model_dump_json(indent=2)`（比照 `pipeline.py:163-194` 既有輸出方式）。

**Must NOT:**
- dry_run 不得碰 LLM / embedding encoder（不載入 bge 模型權重）。
- 不接受「無 client_synth 卻非 dry_run」（拋明確錯誤：`run_synthesis_pipeline` 需要 client）。

**References:**
- `literature_review/extraction.py:14`
- `literature_review/pipeline.py:109-140`（run_synthesis_pipeline 主流程）、`:155-158`（Langfuse flush）、`:163-194`（pipeline main 的 policy 組法與輸出）
- `literature_review/models.py:127-140`（FullTextDocument）

**Evidence of completion:**
- fake（無 key）完整 run：`run_end_to_end(..., client_synth=FakeClient())` 產出含 `report` 的 dict；`--dry-run` 產出 downnload-only dict。
- log：`.omo/evidence/smoke-m3c-fake-e2e.log` 含兩種模式結果。

**QA scenarios:**
- happy：fake client 完整 pipeline 產出 `SynthesisResponse` 形狀的 JSON；cite marker 存在。
- failure：某 PDF 抽不出文字（`extract_pdf_text` 拋錯）→ 該文件跳過並記錄（不讓整個 run 崩潰），其餘文件照常。
- 證據路徑：`.omo/evidence/test-suite-m3c.log` + `smoke-m3c-fake-e2e.log`。

**Commit:** 使用者（本人）commit。

---

- [ ] 5. `main.py`:key 分配（planning=key1、synthesis=key2）— 兩階段不同 key、無洩漏

**What to do:**
- 在 `main()`（非 test 路徑）：
  - `--llm-plan` 時：`client_plan = GeminiJsonClient(api_key=os.getenv("GEMINI_API_KEY"))`（`llm_evidence.py:39` 簽名 `GeminiJsonClient(model=..., api_key=...)`）。
  - 非 dry_run 時：`client_synth = GeminiJsonClient(api_key=os.getenv("GEMINI_API_KEY_2"))`；若 env 缺 key2 → 明確錯誤訊息（「請在 .env 設定 GEMINI_API_KEY_2」）後 exit 1。
  - dry_run：兩個 client 皆 None。
- `run_end_to_end` 預設不自己讀 env（由 `main()` 注入）；測試直接傳入 fake client。

**Must NOT:**
- 不得在任何 stdout/log/exception message 中印出 key 值。
- `--llm-plan` 使用 key1、synthesis 使用 key2，**不得混用**。

**References:**
- `literature_review/llm_evidence.py:36-48`（GeminiJsonClient 構造與 key 來源）
- `AGENTS.md`「API Keys (.env)」段（GEMINI_API_KEY / GEMINI_API_KEY_2）
- `.env.example`（若存在，比照格式）

**Evidence of completion:**
- 真實 smoke log（Todo 8）與 grep 檢查：`grep -rn "GEMINI_API_KEY_2" literature_review/main.py` 命中且 log 內無 `AIza...` 字樣。
- 缺 key2 時非 dry-run → 錯誤訊息、exit 1、無 stack trace 洩 key。

**QA scenarios:**
- happy：fake client 注入 → 不讀 env 也能跑（確認 run_end_to_end 與 env 解耦）。
- failure：`GEMINI_API_KEY_2` 缺席 + 非 dry-run → exit 1 + 清楚錯誤（無 key 值外洩）。
- 證據路徑：`.omo/evidence/smoke-m3c-real-*.log`（真實）與 faked log。

**Commit:** 使用者（本人）commit。

---

- [ ] 6. `tests/test_main.py`:新增單元測試 + fake e2e — 全情境覆蓋、既有套件回歸

**What to do:**
- 新增 `tests/test_main.py`：
  - Fake 注入：`FakeJsonFetcher`（回傳預製 OpenAlex payload 的 callable）、`FakePdfFetcher`（回傳 `b"%PDF-..."` bytes）、`FakeGeminiClient`（實作 `JsonGenerationClient` protocol，回傳預製 JSON——比照 `tests/` 既有 fake 慣例，可參考 `test_planning.py` / `test_pipeline.py` 的 fake client）。
  - 覆蓋情境：
    1. 2 個 query、共享集合、跨 query 重複 → 總下載 = 獨特論文數、`duplicate_reused` 統計正確。
    2. 下載失敗（raise `PdfDownloadError`）→ 遞補該 query 續集。
    3. `--dry-run` → 不呼叫 fake LLM、不載入 encoder。
    4. 完整 run（fake synth client）→ 回傳 dict 含 `report`。
    5. `target_n` 公式正確（如 5 queries → `ceil(15/5)=3`、3 queries → `ceil(15/3)=5`）。
    6. `--llm-plan` failure → fallback rule-based（不崩潰）。
  - 全部使用 `tempfile.TemporaryDirectory`，**不碰 `data/papers/`**。
- 跑全套件回歸並存 log：`uv run python -m unittest discover -s tests -v` → `.omo/evidence/test-suite-m3c.log`。

**Must NOT:**
- 測試不得發真實網路請求、不得讀 `.env`、不得消耗 key。
- 不 mock 未注入的介面（流程必須走 `run_end_to_end` 的注入點）。

**References:**
- `tests/test_planning.py`、`tests/test_pipeline.py`、`tests/test_pdf_downloader.py`（fake client 慣例）
- `literature_review/main.py`（Todo 2-5 產物）

**Evidence of completion:**
- `.omo/evidence/test-suite-m3c.log` 全綠；`grep -c "test_main" .omo/evidence/test-suite-m3c.log` ≥ 1（該檔案例數 > 0）。

**QA scenarios:**
- happy/failure 如上情境列表；證據 = log 中對應 test 案例名。
- 額外：既有 164 tests 回歸無破壞（尤其 `test_pdf_downloader.py` 8 個）。

**Commit:** 使用者（本人）commit。

---

- [ ] 7. 文件：AGENTS.md / HANDOFF.md / README 更新 — main.py 用法與架構

**What to do:**
- `AGENTS.md`：新增 main.py 段落（指令、參數寫死原則、key 分配、`--dry-run`）；「Current architecture」段補「main.py 端到端入口」一行（若 HANDOFF 已更新，此處可只指路）。
- `HANDOFF.md`：M3C 完成段（驗收結果、測試數、已知限制：無翻頁、無 Unpaywall、版本重複的 ID 法盲點等）。
- `README.md`：新增「Run the full pipeline (M3C)」指令區塊（`uv run python -m literature_review.main` 系列）。
- 更新 STATE.md：M3C 完成（此項亦可由規劃 agent 在驗收後更新——文件 Todo 只涵蓋 AGENTS/HANDOFF/README）。

**Must NOT:**
- 不在文件寫入任何真實 API key / 授權資訊。

**References:**
- `AGENTS.md`（Commands 段、Current architecture 段）
- `HANDOFF.md`（M3B 段格式為範例）
- `README.md`（Search papers / pipeline 指令區塊）

**Evidence of completion:**
- `grep -n "literature_review.main" README.md AGENTS.md HANDOFF.md` 全命中。
- 測試數更新為實際值（如 164+）。

**Commit:** 使用者（本人）commit。

---

- [ ] 8. 驗收：fake e2e smoke（無 key）+ 真實 smoke（先確認）— 存 log、報告輸出驗證

**What to do:**
- fake e2e（免 key、免網路）：
  - `uv run python - <<'PY' ... PY` 或等價指令，用 `run_end_to_end(query="literature review agent", dry_run=True, json_fetcher=FakeJsonFetcher, ...)` 與完整 run（fake synth client）各跑一次 → log 存 `.omo/evidence/smoke-m3c-fake-e2e.log`。
  - 驗證：流程順序、統計欄位、報告 JSON 形狀。
- 真實 smoke（**先跟使用者確認才執行**）：
  - 真 OpenAlex（`search.fetch_json`）+ 真 Gemini（key1 若測 `--llm-plan`、key2 給 synthesis）；dest 用 `tempfile.mkdtemp()`，**不碰 `data/papers/`**。
  - 指令範例：`"retrieval augmented generation" | uv run python -m literature_review.main --dry-run`（乾跑，真 OpenAlex 真下載）與完整 run（含 synthesis，需 key2）。完整 run 前再次確認。
  - log 存 `.omo/evidence/smoke-m3c-real-*.log`。
  - 驗證：報告 JSON 含 inline citation、paper_sources、無 key 洩漏（`grep -i "AIza"` 應無結果）、Langfuse 有 trace（server up 時）。

**Must NOT:**
- 真實 smoke 前未經使用者確認不得執行（紀律）。
- 不將 download 目錄指向 `data/papers/`（一律 temp）。

**References:**
- `.omo/evidence/` 既有 log 命名慣例（`smoke-m3b-real-openalex-v4.log`）
- `AGENTS.md`（Langfuse 健康檢查：`curl http://localhost:3000/api/public/health`）

**Evidence of completion:**
- `.omo/evidence/smoke-m3c-fake-e2e.log`、`.omo/evidence/smoke-m3c-real-*.log` 存在且內容完整。
- `grep -i "AIza" .omo/evidence/smoke-m3c-*.log` 無命中（key 無洩漏）。

**QA scenarios:**
- happy：真實 run 產出完整報告、OA 統計與下載數合理、總目標 15 的 target_n 分配符合公式。
- failure：某 query 候選用盡 / 下載失敗 → shortfall 統計呈現、流程續跑不崩潰。
- 證據路徑：上述兩個 log。

**Commit:** 使用者（本人）commit。

---

## Final verification wave

執行代理完成所有 Todo 後，規劃 agent（或執行代理，依 route B 慣例）逐一驗收；**全部 APPROVE 才算完成**：

- [ ] F1. **Plan compliance audit**:逐一檢查 Todo 1-8 的「Evidence of completion」全部成立（grep 命中、log 存在、測試綠）。
- [ ] F2. **Code quality review**:新 code 符合專案慣例（Pydantic 契約、可注入、無 key 洩漏、`grep -i "AIza"` 無命中、無 `data/papers/` 汙染）；既有 164 tests 無破壞。
- [ ] F3. **Real manual QA**:真實 smoke log 驗證（報告 JSON 形狀、inline citation、統計合理）；執行代理實際上機跑過（非只貼 log）。
- [ ] F4. **Scope fidelity**:Must NOT-Have 清單全部未違反（無合併、無 Unpaywall、無翻頁、無 commit、重複不遞補）。

---

## Commit strategy

> **⚠️ 所有 commit/push 由使用者（本人）親自執行——執行代理與規劃 agent 都不 commit。**

建議兩次 commit（branch 沿用現有 `feature/plan-doc` 或新開 `feature/m3c-main-entry`）：

1. **code commit**（Todo 1-6 完成後）：
   ```powershell
   git add literature_review/main.py literature_review/pdf_downloader.py tests/test_main.py tests/test_pdf_downloader.py
   git commit -m "M3C: main.py end-to-end entry + downloader already_downloaded dedup"
   ```
2. **docs commit**（Todo 7 完成後）：
   ```powershell
   git add AGENTS.md HANDOFF.md README.md
   git commit -m "M3C: document main.py usage and architecture"
   ```
3. `.omo/` 檔案（計畫/STATE/log）由使用者決定是否與 docs 一起或分開 commit；**`.omo/evidence/` 的 log 被 gitignore、不需 commit**（見 `.omo/notes/plan-review-checklist.md` 第八項）。`data/papers/`、`Summer_Project.pdf`、`.env` 不得進 Git。

---

## Success criteria

1. `"<query>" | uv run python -m literature_review.main --dry-run`（無 key、PowerShell pipe）可互動式跑到「PDF 下載完成」，輸出每 query 統計（OA 比例、downloaded、duplicate_reused、shortfall）。
2. 完整 run（key2 就緒 + 使用者確認）產出綜合報告 JSON：inline `[chunk_id]` 引用、paper_sources、paper 級 include/consider 分級、future directions。
3. 多 query 無合併；跨 query 重複只下載一次（共享集合）；重複算達成；失敗遞補該 query 續集。
4. planning 用 key1、synthesis 用 key2；log/stdout 無任何 key 洩漏。
5. 既有 164 tests + 新增 tests 全綠（`.omo/evidence/test-suite-m3c.log`）。
6. 真實 smoke（先經使用者確認）通過（`.omo/evidence/smoke-m3c-real-*.log`）。
7. 文件更新完成（grep 命中）；未違反任何 Must NOT-Have。