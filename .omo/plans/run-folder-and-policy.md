# run-folder-and-policy — 下載落點管理 + 頂會/三年窗檢索政策

## TL;DR (For humans)

教授指導：篩選論文「只挑三年內」且「只專注頂會」。使用者另定下載資料夾規則：改用**固定新資料夾（預設 `data/run/`）**，每次跑系統流程前**清空**；想看檔就不跑；可顯式 `--dest-dir` / `DEST_DIR` 覆寫（覆寫時**不清空**、自己管理）。頂會白名單＋三年窗為**搜尋後硬濾**（不改成 API 端過濾，理由見下）。`Unpaywall` 列為後續候選、不急。

本里程碑＝純 code/config/test + 文件，**不燒 Gemini 額度**。F4 real run 仍擱置（等 quota）。

**不會做**：API 端 venue 過濾（OpenAlex source-id 誤殺 arXiv 主源、SS 相關性搜尋無 venue 參數）、Unpaywall backfill、任何 provider 行為變更。

---

## Scope

**In**
- `literature_review/main.py`：常數 `MIN_YEAR→YEAR_WINDOW`、`DEST_DIR` 預設 `data/papers`→`data/run`、argparse 加 `--dest-dir` / `--year-from` / `--year-to` / `--venues`、執行前「預設資料夾才清空」邏輯、`FilterPolicy` 帶入 min_year（年窗）+ venues（頂會）。
- `literature_review/models.py`：`FilterPolicy` 加 `venues: tuple[str, ...] = ()`。
- `literature_review/ranking.py`：頂會別名表 `TOP_VENUE_ALIASES` + `normalize_venue()` + `filter_papers` 加 venues 硬濾。
- `literature_review/search.py` CLI：`--venues`（與 `--year-from` 並存）。
- `literature_review/ss_search.py`：無變更（year 轉譯已存在）。
- `.gitignore`：加 `data/run/`。
- 測試：`tests/test_ranking.py`（venue 濾）、`tests/test_main.py`（年窗計算、dest 落點/清空、flag→policy 傳遞、`year_from` 預期值更新）、必要時 `tests/test_models.py`。
- 文件：README（新資料夾語意 + 新 flags）、AGENTS.md（架構圖 FilterPolicy 註記 + Commands flags）、HANDOFF、`.omo/STATE.md`，執行完成後同步。

**Out**
- API 端（provider 請求層）venue/日期過濾改寫
- Unpaywall / 任何新 provider
- LLM/評分/抽取/下載層行為變更
- Gemini 額度相關驗證（F4）

---

## Verification strategy

每 Todo＝改檔 + 對照檢查 + 對應測試。最終：完整 suite 全綠、dry-run 驗證下載落點在 `data/run/` 且事前清空、`git status` 只含預期檔、無 key 洩漏。測試數將增加（新增 venue/年窗/dest 測試）——以實測為準，寫回文件。

---

## Execution strategy

```
Todo 0（預檢）→ Todo 1（FilterPolicy.venues + 別名表 + filter_papers）→ Todo 2（main.py 落點/年窗/flags）
→ Todo 3（search.py CLI --venues + .gitignore）→ Todo 4（測試）→ Todo 5（文件同步 + 驗收 + commit 指令）
```

---

## Todos（evidence log 存 `.omo/evidence/run-policy-*.log`，gitignored）

- [x] 0. **預檢** — 確立執行前底線

  **References**
  - `.omo/evidence/m5c-wrapup-final.log`（414 tests 基準）
  - `AGENTS.md` Commands / Current architecture（SS 主源段落）
  - `git status --short` 底線（預期乾淨；上次 skill/補勾 commit 未 push：`[ahead 15]`）

  **Implementation**
  1. `git status --short` 記錄底線，確認工作區無未預期變動。
  2. 記錄基準測試數：`uv run python -m unittest discover -s tests` 至 `.omo/evidence/run-policy-todo0.log`。

  **Acceptance**
  - 底線無未預期變動；測試數記錄於 log（預期 414，以實測為準）。

- [x] 1. **FilterPolicy.venues + 別名表 + filter_papers 硬濾**

  **References**
  - `models.py:83-88` `FilterPolicy`（min_year/max_year/min_citation_count）
  - `ranking.py:27-42` `filter_papers`（min_year 判定在 :33-38）
  - `ranking.py:139-159` `filter_and_rank`
  - `search.py:131` OpenAlex `venue` 來源、`ss_search.py:136` SS `venue` 來源

  **Implementation**
  1. `models.py` `FilterPolicy` 加 `venues: tuple[str, ...] = ()`（`()`＝不限制）。
  2. `ranking.py` 新增 `TOP_VENUE_ALIASES: dict[str, tuple[str, ...]]`（預設常數，使用者已確認含 AAAI＋IJCAI；key＝規範名，values＝別名）：`NeurIPS`「neurips, nips, annual conference on neural information processing systems」、`ICML`「icml, international conference on machine learning」、`ICLR`「iclr, international conference on learning representations」、`ACL`「acl, association for computational linguistics, annual meeting of the association for computational linguistics」、`EMNLP`「emnlp, conference on empirical methods in natural language processing」、`NAACL`「naacl, north american chapter of the association for computational linguistics」、`SIGIR`/`CIKM`/`WSDM`/`WWW`/`KDD`/`RecSys`（縮寫 + 各自全名）、`CVPR`/`ICCV`/`ECCV`（縮寫 + 全名）、`AAAI`「aaai, aaai conference on artificial intelligence」、`IJCAI`「ijcai, international joint conference on artificial intelligence」。
  3. `ranking.py` 新增 `normalize_venue(text) -> str`（lower + 去非 alnum）。
  4. **venue 匹配契約（定案）**：`FilterPolicy.venues: tuple[str, ...]` 存放**正規化後的別名 token**（lower、無標點）。`ranking.py` 提供 `default_venues() -> tuple[str, ...]`＝`TOP_VENUE_ALIASES` 的 **keys ∪ 所有 values** 正規化後集合。過濾＝`paper.venue` 正規化後**包含任何一個 token**（`token in normalized_venue`）才保留。理由：provider 常給冗長全名（`proceedingsoftheinternationalconferenceonlearningrepresentationsiclr`），相等比對會 miss。`paper.venue` None → 剔除；`venues=()` → 不過濾（零回歸）。
  5. `filter_papers` 實作該契約；`filter_and_rank` 不用改（policy 已傳入）。

  **Acceptance**
  - 新測試：pytest/unittest 覆蓋「縮寫命中 / 全名命中 / **冗長前後綴命中**（`Proceedings of the International Conference on Learning Representations (ICLR)` → 保留）／混大小寫命中 / 非頂會剔除 / venue=None 剔除（白名單啟用時）/ 空白名單=no-op」。
  - 既有 ranking 測試不改可過（`FilterPolicy()` 預設 venues=() 行為不變）。

  **QA**
  - happy：白名單命中者保留、其餘剔除；failure：`default_venues()` 對 normalize 後為空值的 token（如 `"nips  "` 外圍髒字）要跳過、不得放空 token；已知限制：包含式匹配下，名稱**內含**短縮寫的第三地 workshop（如 loft 某場名含 "acl"）可能誤命中——列入 plan 已知限制、以較長 token 優先。

- [x] 2. **main.py：落點 + 年窗 + flags**

  **References**
  - `main.py:57-61` 常數（`LIMIT=100`、`MIN_YEAR=2021`、`DEST_DIR=Path("data/papers")`）
  - `main.py:158` `_search_candidates` 建 `SearchRequest(..., year_from=MIN_YEAR)`
  - `main.py:203` `filter_and_rank(response, FilterPolicy(min_year=MIN_YEAR), encoder=...)`
  - `main.py:549-563` argparse（現僅 `--rule-based` / `--dry-run`）
  - `main.py:580-582` `run_end_to_end(..., dest_dir=DEST_DIR, ...)`

  **Implementation**
  1. 常數：`YEAR_WINDOW = 3`；`DEST_DIR = Path("data/run")`；`MIN_YEAR` 移除（改函式 `default_min_year(today: date | None = None) -> int`＝`(today or date.today()).year - YEAR_WINDOW + 1`）。
  2. argparse 加：`--dest-dir`（Path，覆寫）、`--year-from` / `--year-to`（int，年月逃生門）、`--venues`（str，逗號分隔，覆寫頂會白名單；`""`＝不限制）。
  3. `main()`：`dest_dir = args.dest_dir or Path(os.getenv("DEST_DIR") or DEST_DIR)`；**僅當 `args.dest_dir` 與 env 皆未提供**（即等於預設 `data/run`）→ 執行前清空該資料夾（`shutil.rmtree` 後重建，或清內容保目錄）。
  4. 年窗生效：`year_from = args.year_from or default_min_year()`、`year_to = args.year_to`；`_search_candidates`/`_search_and_rank` 把 `year_from/year_to` 帶進 `SearchRequest`。
  5. `_search_and_rank` 的 `FilterPolicy(min_year=year_from, venues=...)`：`venues`＝`args.venues` 解析（空字串→()`；None→`default_venues()`；None、空字串以外的 tokens 直接 normalize 後當白名單）。
  6. 清空只在 main() 層做；`run_end_to_end` 不動（測試可注入 temp dest_dir）。

  **Acceptance**
  - 新測試：`default_min_year()`（含今年→2024）；`main()` argparse → `run_end_to_end` 的 `dest_dir` 與 policy 傳遞（fake clients）；預設 dest 執行前清空、覆寫時不清（mock download 捕捉 dest 路徑）；`test_main.py:960` 之 `year_from=2021` 預期改為計算值。
  - dry-run 常數測試（`year_from` 斷言）同步更新。

  **QA**
  - happy：預設 run 下載全落 `data/run/`、事前清空、只留年內論文；failure：`--year-from 1900+` 由 `SearchRequest` 欄位驗證擋下（ValidationError⊂ValueError，main 已 catch）；`--venues ""` 不加限制；env `DEST_DIR` 存在時優先於常數、且不清空；`DEST_DIR=""` 視為未設定。

- [x] 3. **search.py CLI `--venues` + `.gitignore`**

  **References**
  - `search.py:157-165` argparse（`--year-from` 等已存在）
  - `search.py:189` `filter_and_rank` 呼叫（`min_year=arguments.year_from`）
  - `.gitignore:5-6`（`data/papers/`、`data/outputs/`）

  **Implementation**
  1. `.gitignore` 加 `data/run/`（並擇一：保留 `data/papers/` 於 gitignore 供手動用途）。
  2. search.py CLI 加 `--venues`（逗號分隔；None→`default_venues()`；`""`→()`）。
  3. 該呼叫處帶入 `venues`。

  **Acceptance**
  - `-m literature_review.search "..." --venues neurips,icml --year-from 2024 --rank` 跑通（可驗其 FilterPolicy 含 venues，透過輸出或測試）。
  - `.gitignore` 含 `data/run/`。

- [x] 4. **測試更新 + 新增**

  **References**
  - `tests/test_ranking.py:56,65,84,150,163`（既有 filter/filter_and_rank 測試）
  - `tests/test_main.py:960`（`year_from=2021` 斷言）
  - `tests/test_models.py`、`tests/test_search.py:11`（已用顯式 year_from，不需改）

  **Implementation**
  1. Todo 1/2/3 所列新測試全數落 `tests/test_ranking.py`（venue）＋ `tests/test_main.py`（年窗/dest/flag）＋ `tests/test_models.py`（FilterPolicy 預設）。
  2. `test_main.py:960` 改斷言 `default_min_year()`（或注入固定 today 間接驗證）。
  3. **既有 main() 測試已 patch `run_end_to_end`（如 :784-807），只斷言 kwargs，不觸檔案系統**——切換 `DEST_DIR` 預設零回歸。清空行為新測試用 tmp cwd（沿用 :760-782 既有 chdir 模式）＋ mock `run_end_to_end` 捕捉 `dest_dir` kwarg＋在 tmp 內放一個絆腳檔證實被清。
  4. 跑完整 suite，記錄新計數至 `.omo/evidence/run-policy-todo4.log`。

  **Acceptance**
  - suite 全綠；測試數＞414（實測為準）且新測試逐項覆蓋 QA 情境。

- [x] 5. **文件同步 + 最終驗收 + commit 指令**

  **References**
  - `AGENTS.md` Commands / 架構圖（FilterPolicy 註記）
  - `README.md:48-60`（full run 段落：參數與落點說明）
  - `HANDOFF.md` 里程碑表 + M5c 節
  - `STATE.md` 現況快照 + 里程碑表 + 未動工候選（把本里程碑標「動工/完成」，Unpaywall 標「考慮中、後續優先」）

  **Implementation**
  1. README：`data/run/` 語意（每跑清空）、`--dest-dir/--year-from/--venues` 說明、三年窗/頂會預設。
  2. AGENTS.md：架構圖 `filter_and_rank` 註記 venue 白名單；Commands 補 flags。
  3. HANDOFF/STATE：里程碑表加列、測試數更新、Unpaywall 候選狀態。
  4. 最終：完整 suite（存 `.omo/evidence/run-policy-final.log`）、`git status --short`、`git grep` 無 key 洩漏。
  5. 提供使用者 commit 指令（code 一筆 + docs 一筆 + push；一併提醒未 push 的 3 commits）。

  **Acceptance**
  - suite 全綠；git status 只含預期檔；commit 指令一次列齊；無 key 洩漏。

---

## Commit strategy（使用者親做）

```bash
# code 一筆
git add literature_review/models.py literature_review/ranking.py literature_review/main.py literature_review/search.py tests/ .gitignore
git commit -m "feat(policy): top-venue whitelist + 3-year window + data/run download folder"
# docs 一筆
git add README.md AGENTS.md HANDOFF.md .omo/STATE.md .omo/plans/run-folder-and-policy.md
git commit -m "docs: sync README/AGENTS/HANDOFF/STATE with run-folder-and-policy milestone"
git push   # 連同先前 3 個未 push commits（ahead 15 → 17）
```

---

## Success criteria

- 每年跑：下載全落 `data/run/`（固定名稱）、每跑 rmtree＋重建同名資料夾；`--dest-dir`/`DEST_DIR` 覆寫時不清空。
- 每 query 檢索預設限三年內（2026→2024 起）+ 頂會白名單硬濾（**包含式**別名匹配：`ML/NLP/IR/CV/AI 共 17 會`，含 AAAI/IJCAI）；`--year-from/--year-to/--venues` 可逃生門式覆寫。
- 別名表常數可調、含來源說明註記（縮寫＋官方全名＋常見寫法）；`venues=()`＝不限制（既有行為零回歸）。
- 414+ tests 全綠；README/AGENTS/HANDOFF/STATE 同步；無 key 洩漏。
- 已知限制：OpenAlex fallback 路徑下頂會論文可能 venue="arXiv"→被硬濾剔除（SS 主源無此問題）；包含式匹配可能誤中名稱內含縮寫的非頂會 workshop。