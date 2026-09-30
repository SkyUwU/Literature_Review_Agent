# papers-output — 下載論文清單 JSON 輸出

## TL;DR (For humans)

真實 run 結束時，把**實際下載的論文清單**連同完整 metadata（title/authors/year/venue/citation_count/doi/abstract/OA url/paper_id）、**是哪個 planned query 拉下來的**、**screening 判定（keep/maybe，若無則 null）**、**本地下載路徑**，加上 run 概覽（query、planned queries、stats_per_query、failed_extractions），寫成 `data/outputs/papers_%Y%m%d_%H%M%S_%f.json`——與 `report_*.json` **同資料夾、共用同一時間戳、累積不清空**。每篇一篇一條；被多 query 重複召喚（`duplicate_reused`）只反映在統計、不重複列。**僅非 dry-run 寫檔**（與報告規則一致）。`paper_id` 保留。純輸出機能，**不燒 Gemini 額度**。

`data/run/` 有幾個 PDF，`papers.papers` 就有幾筆（去重語意下穩定成立）。

**不會做**：清空 `data/outputs/`、dry-run 寫檔、reuse 論文細列（改 `download_and_backfill` 回傳）、stdout 輸出變化、F4 real run。

---

## Scope

**In**
- `literature_review/models.py`：`DownloadedPaperEntry`（`paper: Paper`＋`query`＋`local_path`＋`priority: Literal["keep","maybe"] | None`）＋`PapersOutput`（`run: dict[str, object]`＋`papers: list[DownloadedPaperEntry]`），放 `Paper`（:44）附近。
- `literature_review/main.py`：
  - `run_end_to_end`（:253-507）收集「已下載 paper_id → 完整 `Paper`」＋priority（screening 路徑自 `keep_ranked/maybe_ranked`：:384-399；legacy 路徑自 `ranked_papers`：:424-434），由 `downloads`（:416-421/:447-452）依序組 `PapersOutput`，回傳加 `"papers"`（dry 與真實 run 皆回傳；寫檔與否由 main() 決定）。
  - 新增 `save_papers_output(...)`（鏡像 `save_report_output`：:575-597；`timestamp` 參數同款）。
  - `main()` 非 dry-run（:693-713）：`ts = datetime.now()` 一次，`save_report_output(result["report"], timestamp=ts)` 與 `save_papers_output(result["papers"], timestamp=ts)` 共用；各印存檔路徑。
- 測試：`tests/test_main.py`（新增 `run_end_to_end` papers 收集、`save_papers_output`、main 整合、dry-run 不寫檔）；必要時 `tests/test_models.py`。
- 文件：AGENTS.md（run 輸出段落加 papers 檔）、README、HANDOFF（新里程碑節）、`.omo/STATE.md`、本計畫檔。
- `.gitignore`：無需動（`data/outputs/` 已忽略，:7）。

**Out**
- dry-run 產檔／`data/outputs/` 清空／reuse 具體論文列示／下載器行為變更／stdout 變更
- 任何 provider/評分/報告層行為變更、Gemini 驗證

---

## 定案決策（使用者已確認，2026-09-22）

- 檔案：`data/outputs/papers_%Y%m%d_%H%M%S_%f.json`，與 `report_*` 共用**同一 ts**，累積不清空；寫失敗→stderr 警告＋回傳 None（不 crash），同 `save_report_output` 規則。
- 內容：`PapersOutput.run`＝`{query, planned_queries, follow_ups, stats_per_query, failed_extractions}`；`planned_queries` 只列**初始** plan queries（使用者定案：補搜另以 `follow_ups` 欄位同存，papers JSON 自包含）；`papers[i]`＝完整 Paper 欄位（含 `paper_id`）＋`query`＋`priority`（screening 才有；legacy 為 None）＋`local_path`。
- 每篇論文只列一筆（`downloads` 本身即「實際寫檔清單」，去重語意下天然唯唯一）；`duplicate_reused` 只在 `run.stats_per_query` 呈現。
- dry-run：`run_end_to_end` 照常回傳 `"papers"`（資料現成），`main()` 不寫檔。

---

## Verification strategy

每 Todo＝改檔 + 對照檢查 + 對應測試。最終：完整 suite 全綠（442 基準＋新增）、非 dry-run main() 整合測試證實 `data/outputs/` 產生同 ts 的 `report_*`/`papers_*`、git status 只含預期檔、無 key 洩漏。測試數以實測為準。evidence log 存 `.omo/evidence/papers-output-*.log`。

---

## Execution strategy

```
Todo 0（預檢）→ Todo 1（models.py：DownloadedPaperEntry + PapersOutput）→ Todo 2（run_end_to_end 收集/組 papers）
→ Todo 3（save_papers_output + main() 共用 ts 接線）→ Todo 4（測試）→ Todo 5（文件 + 驗收 + commit 指令）
```

---

## Todos

- [x] 0. **預檢** — 確立執行前底線

  **References**
  - `.omo/evidence/venues-by-name-todo4.log`（442 tests 基準，2026-09-22）
  - `git status --short` 底線（預期乾淨；venues-by-name 兩筆 commit 已由使用者提交）

  **Implementation**
  1. `git status --short` 記錄底線；確認工作區無未預期變動。
  2. 基準測試數落 `.omo/evidence/papers-output-todo0.log`。

  **Acceptance**
  - 底線乾淨；測試數記錄（預期 442，以實測為準）。

- [x] 1. **models.py：`DownloadedPaperEntry` + `PapersOutput`**

  **References**
  - `models.py:44-63` `Paper` 欄位（paper_id/doi/title/authors/year/abstract/url/venue/citation_count/open_access_pdf_url）
  - `models.py:89` `FilterPolicy.venues`（同檔 enum/literal 慣例；priority 用 `Literal["keep","maybe"] | None`）

  **Implementation**
  1. `DownloadedPaperEntry(BaseModel)`：`paper: Paper`、`query: str`（`min_length=1`）、`local_path: str`、`priority: Literal["keep", "maybe"] | None = None`。
  2. `PapersOutput(BaseModel)`：`run: dict[str, object]`、`papers: list[DownloadedPaperEntry] = Field(default_factory=list)`（**不加 min_length**：允許全下載失敗時為空清單，避免模型建構直接炸）。
  3. import：`Literal`（models.py:3）與 `BaseModel/Field`（models.py:5）**均已存在**，無需新增。

  **Acceptance**
  - 兩模型可 `model_validate_json` 往返；`priority` 只接受 keep/maybe/null；`papers` 允許空清單。

  **QA**
  - failure：`priority="exclude"` 應被 pydantic 拒絕（screening 只把 keep/maybe 用於下載，exclude 不應進來）；全下載失敗時 `papers=[]` 仍可建模（不 min_length）。

- [x] 2. **run_end_to_end 收集完整 Paper 並回傳 `papers`**

  **References**
  - `main.py:384-399` screening keep/maybe 分組、`main.py:369-378` `ranked_by_id`/`paper_titles`
  - `main.py:401-421` screening 路徑下載＋`paper_queries` 過濾＋`downloads` 組建
  - `main.py:422-452` legacy 路徑：`ranked_papers` 排名、`paper_queries`/`paper_titles` 記錄、`downloads` 組建
  - `main.py:454-507` dry-/non-dry 回傳點

  **Implementation**
  1. 引進收集容器：`downloaded_papers: dict[str, Paper]`、`paper_priority: dict[str, str]`。
  2. screening 路徑（:401-421 後）：對 `result.downloaded_paper_ids`，自 `ranked_by_id` 取 `item.paper` 入 `downloaded_papers`；priority 依該 id 在 keep/maybe 何組（自 :384-399 的構建過程一併記錄）。
  3. legacy 路徑（:422-452）：`ranked_papers` 中 `paper_id ∈ result.downloaded_paper_ids` 者入 `downloaded_papers`；`paper_priority` 不寫（None）。
  4. 兩路徑結束後組 `papers = [DownloadedPaperEntry(paper=downloaded_papers[id], query=paper_queries[id], local_path=path, priority=paper_priority.get(id)) for id, path in downloads]`；缺 `paper` 或缺 `query` 者**跳過並記入 run 的 warn 級欄位**（防呆；正常不發生）。
  5. `PapersOutput` 的構建點**放各 return 之前**：`run={"query": query, "planned_queries": [q.query for q in plan.queries], "follow_ups": follow_ups, "stats_per_query": stats_per_query, "failed_extractions": failed_extractions}`。dry-run（:454-464）時 extraction 不會跑，`failed_extractions=[]`；non-dry-run 需在 extraction 迴圈（:469-479）**之後**建 papers，使 `run.failed_extractions` 反映真實值，再進 non-dry return（:494）。
  6. 兩路徑皆回傳 `"papers": <PapersOutput>`。

  **Acceptance**
  - 新測試（mirror 既有 test_main：:283-284 的雙 query 去重案例）：`papers` 與 `downloads` 一一對應；每筆 paper metadata 與注入 fixture 一致（title/year/venue/citation_count/abstract/authors）；legacy 路徑 priority 全 None；screening 路徑 priority 正確 keep/maybe；跨 query 重複篇只有一筆；dry-run 回傳亦含 papers（`failed_extractions=[]`）。
  - **既有 `fake_result` 測試（:810 `test_main_full_run_saves_report_json`）需補 `"papers"` key**（`PapersOutput`）；:800/:1233 為 dry-run，main() 不讀 `"papers"`，不需動。
  - 既有 `test_main` 其他斷言皆為子集式（如 :438），回傳加 key 不破。

  **QA**
  - happy：`papers` 長度 == 實際寫檔數（總 `downloaded_paper_ids`，不含 reused）；failure：`ranked_by_id` 找不到該 paper_id 時防呆跳過不 crash；`paper_queries` 缺 key 時不 KeyError。

- [x] 3. **`save_papers_output` + main() 共用 ts 接線**

  **References**
  - `main.py:575-597` `save_report_output`（timestamp 參數 :579；檔名模式 :590）
  - `main.py:693-713` main() 非 dry-run 輸出段（report 呼叫 :711）

  **Implementation**
  1. `save_papers_output(output: PapersOutput, *, output_dir: str | Path = "data/outputs", timestamp: datetime | None = None) -> str | None`：`model_dump(mode="json")`、`mkdir(parents=True, exist_ok=True)`、檔名 `papers_{ts:%Y%m%d_%H%M%S_%f}.json`、`json.dump(ensure_ascii=False, indent=2)`；OSError→stderr 警告＋回傳 None。
  2. `main()` 非 dry-run：`ts = datetime.now()`；`save_report_output(result["report"], timestamp=ts)`；`save_papers_output(result["papers"], timestamp=ts)`；兩個 saved（None 抑制）各自印 `... saved to: ...`。
  3. import：`PapersOutput`、`DownloadedPaperEntry` 加進 main.py:44-51 的 models import。

  **Acceptance**
  - 新測試（mirror :896-925）：成功寫檔內容/檔名含 ts；失敗回 None 並印警告。
  - 新整合測試（mirror :823-839 的 mock 手法）：main() 呼叫 `save_report_output`/`save_papers_output` 傳**同一 ts**、輸出至 temp dir。

  **QA**
  - happy：報告與 papers 同 run 同名 ts 前綴；failure：output_dir 不可寫→兩者各自警告不 crash。

- [x] 4. **全 suite + 驗證**

  **References**
  - `tests/test_main.py` 既有 `save_report_output` 測試（:896-925）、main 整合（:823-839）、去重統計（:283-284）
  - `.omo/evidence/papers-output-todo4.log`

  **Implementation**
  1. 更新既有 `test_main_full_run_saves_report_json`（:810）：`fake_result` 補 `"papers"`（含 1 筆），並**同時 patch `save_papers_output`**（比照 save_report_output 的 save_to_tmp 手法寫入 `self.dest`），避免真實寫到 repo 的 `data/outputs/`。
  2. `uv run python -m unittest discover -s tests > .omo/evidence/papers-output-todo4.log`。
  3. `git status --short` 對照預期變動檔。

  **Acceptance**
  - 全 suite 全綠（442＋新增約 6-10）；log 留存；`data/outputs/` 無測試污染產生之檔案。

- [x] 5. **文件同步 + 最終驗收 + commit 指令**

  **References**
  - `AGENTS.md`（End-to-end entry 段落 `data/outputs/` 描述）
  - `README.md`（run 輸出段落）
  - `HANDOFF.md`（Latest milestone 節）
  - `.omo/STATE.md`（快照 442、里程碑表、候選移除）

  **Implementation**
  1. AGENTS.md：End-to-end entry 段補「非 dry-run 另寫 `papers_%Y%m%d_%H%M%S_%f.json`（下載論文清單＋metadata＋query＋priority，與報告同資料夾同 ts）」。
  2. README.md：同語意簡述（含檔名、僅真實 run、累積）。
  3. HANDOFF.md：加「Latest milestone: papers output (2026-09-22)」節。
  4. `.omo/STATE.md`：更新日期/測試數/里程碑表加列（⏳ 待 commit）；「未動工候選」移除本項。
  5. 本計畫檔 Todo 全勾；最終驗收（suite 全綠引用 todo4 log、git status、key 洩漏 grep）。

  **Acceptance**
  - 文件與行為一致；git status 僅預期檔；無 key 洩漏；commit 指令一次列齊。

---

## Commit strategy

使用者親做兩筆（承接 AGENTS.md）：code+tests 一筆（`literature_review/models.py main.py tests/`）、docs 一筆（`README.md AGENTS.md HANDOFF.md .omo/STATE.md .omo/plans/papers-output.md`），最後 `git push`（`feature/plan-doc` ahead 19 → 21）。

---

## Success criteria

- 真實 run 在 `data/outputs/` 產生與 `report_*` **同 ts** 的 `papers_*`，含 run 概覽＋每筆完整 Paper metadata＋query＋priority＋local_path。
- 每篇實際下載論文一筆；reused 只經 `run.stats_per_query[].duplicate_reused` 呈現。
- dry-run 不產檔；`data/outputs/` 不清空。
- 全 suite 全綠；文件一致；無 key 洩漏。