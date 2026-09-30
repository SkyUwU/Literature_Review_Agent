# pdf-fetcher — Work Plan (M3B)

## TL;DR (For humans)

**What you'll get:** 新增「自動下載 PDF」部件:搜尋選出的論文,自動從 OpenAlex 的 OA PDF 連結下載到 `data/papers/`,銜接現有 pipeline。同時調整 ranking 分數(三者標準化 0~1、等權重),並在選集層加「下載失敗遞補」。

**Why this approach:** 目前 pipeline 只吃「使用者手動放的本地 PDF」;搜尋層(OpenAlex)拿到論文後,「拿到全文」的橋不存在。M3B 補上這座橋:擴充 `Paper` 拿 OA PDF 連結 → 下載器下載 → 遞補失敗者 → 統計 OA 可下載比例(作為日後是否加 arXiv/Unpaywall 的數據依據)。ranking 調整是使用者確認的方向:現況 lexical 無上限會碾壓,改為三者標準化 0~1 等權重,讓分數可解釋、不偏頗。

**What it will NOT do:** 不接進 `main.py`(串接是 M3C);不加 arXiv/Unpaywall 多來源(留待數據決定,若 OA 比例低才加);不改 synthesis/pipeline 下游;不做 Grobid 解析(維持 pypdf)。

**Effort:** Medium(擴充 Paper + OpenAlex request + 下載器 + ranking 調整 + 遞補 + 測試)
**Risk:** Medium — 動到 `Paper`/`RankedPaper` 契約、ranking 行為、既有測試;下載器是新模組(需處理網路失敗)。

**Decisions to sanity-check:**
1. **OA 來源**:只用 OpenAlex `best_oa_location.pdf_url`(不接 arXiv/Unpaywall)。理由:現有 search 已是 OpenAlex;先用數據看 OA 比例,不夠再加。
2. **Paper 擴充**:加 `open_access_pdf_url: HttpUrl | None = None`(可選,因為非 OA 論文沒有)。`paper_from_openalex` 解析 `best_oa_location.pdf_url` 存入。
3. **OpenAlex request**:`REQUESTED_FIELDS` 加 `best_oa_location`。
4. **ranking 調整**:`lexical_score` 也標準化到 0~1——用 **`命中數 / max(query 字詞數, 1)`**(與 query 字詞數相關,不寫死 3);citation/recency 維持 0~1,三者**等權重相加**(1/1/1),總分範圍 0~3。**提醒**:總分範圍改變(原本 lexical 無上限),`SelectionPolicy.min_score` 的語意需重新理解(若有人設高分門檻,新分數下可能選不到)。
5. **遞補策略**:selection 照舊取 top N → 下載器實際下載 → 失敗的從排名往後遞補,維持 N 篇。順便統計 OA 下載比例。
6. **失敗統計**:下載器回報「成功/失敗/失敗原因」→ 記錄 OA 可下載比例,存 log/報告,作 M3B 後續決策依據。
7. **測試**:下載器用 fake(fake fetcher 不回真網路),測成功/失敗/遞補/統計。
8. **銜接**:下載器 `download_pdf(paper, dest_dir)` 的 `dest_dir` 由**呼叫者**決定(真實使用傳 `data/papers/`——現有 pipeline 輸入契約;smoke 傳暫存目錄),檔名用 paper_id/DOI 免得撞名。

Your next move: 執行代理在獨立 session 實作(使用者貼指令包);不做 `/start-work`。

---

## 下一步行動卡 (ACTION CARD — 執行 agent 每次開 session 第一件先看這塊)

> **單一事實來源。目前要做哪個 Todo、讀哪個指令包。避免轉述長命令。**
>
> **當前狀態：Todo 1（Paper+檢索擴充）→ Todo 2（ranking 調整）→ Todo 3（下載器）→ Todo 4（遞補+統計）→ Todo 5（測試）→ Todo 6（文件）→ Todo 7（驗證/smoke）**
>
> **⚠️ Commit 紀律：執行代理不執行任何 git commit/push——commit 一律由使用者（本人）親自做。** 你只改 code、跑測試、存 log、回報。
>
> 建立後續 Todo 細節如下。

---

## Todo 1: 擴充 Paper + OpenAlex 檢索取 OA PDF

**What to do:**
- `models.py`:`Paper` 加 `open_access_pdf_url: HttpUrl | None = None`。
- `search.py`:`REQUESTED_FIELDS` 加 `best_oa_location`。
- `paper_from_openalex`:從 `record["best_oa_location"]` 取 `pdf_url`,有則填 `open_access_pdf_url`(沒有或 None 就 `None`)。
- 確保非 OA / 無 pdf_url 的論文仍正常轉換(open_access_pdf_url=None)。

**Must NOT:**
- 不改 `url`(維持 landing page)。
- 不加 arXiv/Unpaywall(本計畫不做)。
- 不刪 ResearchIdea 等既有模型。

**Evidence of completion:**
- `grep -n "open_access_pdf_url" literature_review/models.py` 命中。
- `grep -n "best_oa_location" literature_review/search.py` 命中。
- `grep -n "open_access_pdf_url" literature_review/search.py` 命中(填值)。

---

## Todo 2: ranking 分數標準化 + 等權重

**What to do:**
- `ranking.py`:`rank_papers` 中 `lexical_score` 改為標準化 0~1——**`命中數 / max(len(query_terms), 1)`**(隨 query 字詞數伸縮,而非寫死 3);citation/recency 維持 0~1。
- `total_score = lexical_score + citation_score + recency_score`(三者等權,各最大 1.0,總分 0~3)。
- 更新 `rationale` 文字描述(「matched query terms, citation & recency signals」)。

**Must NOT:**
- 不改 filter_papers / duplicate_preference_key(過濾邏輯不動)。
- 不刪 matched_terms(追溯仍要)。

**Evidence of completion:**
- `grep -n "lexical_score\|total_score" literature_review/ranking.py` 顯示標準化 + 等權。
- 既有 test_ranking 仍相容(見 Todo 5)。

---

## Todo 3: 新增 PDF 下載器模組

**What to do:**
- 新增 `literature_review/pdf_downloader.py`:
  - `download_pdf(paper: Paper, dest_dir: Path) -> Path`(從 `open_access_pdf_url` 下載,**存到呼叫者指定的 `dest_dir`**;真實使用時傳 `data/papers/`,smoke 時傳暫存目錄)。
  - 處理網路失敗(HTTP error / timeout)→ 拋或回傳明確失敗(不下載就崩潰)。
  - 非 OA(無 `open_access_pdf_url`)→ 明確回報「無 OA 連結」。
  - 可注入 fetcher(fake 測試用,不回真網路)。
- 檔名避免撞名(用 paper_id 的 slug 或 DOI 轉檔名)。

> **重要**:`dest_dir` 由「呼叫者」決定,下載器不寫死 `data/papers/`。這樣 smoke 可下到暫存、真實用可下到 `data/papers/`,同一套 code。

**Must NOT:**
- 不接進 pipeline/main(那是 M3C)。
- 不做 Grobid 解析(維持 pypdf extraction)。
- 不把 PDF 內容塞進 Paper 模型。

**Evidence of completion:**
- `grep -n "def download_pdf" literature_review/pdf_downloader.py` 命中。
- 測試(見 Todo 5)用 fake 通過。

---

## Todo 4: 遞補 + OA 比例統計

**What to do:**
- 在 selection 之後加「下載 + 遞補」邏輯(可放 downloader 或獨立函式):
  - 對 selected top N 逐篇嘗試下載。
  - 失敗的(無 OA / 網路失敗)→ 從排名往後遞補(ranked_papers 中未選的),直到湊滿 N 或**候選用盡**。
  - **候選用盡的定義**:若 filter+rank 後候選總數本身 < N,湊不滿是正常;回傳「實際取得的篇數」+ 記錄「不足 N 篇」,不假設一定湊滿。
  - **記錄統計**(兩個比例都記,各有意義):
    - `oa_ratio_candidates` = **有 OA 連結的篇數 / 候選總數** → 反映 OpenAlex 對候選論文的 OA 覆蓋率(**日後決定是否加多來源的關鍵數據**)。
    - `oa_ratio_attempted` = **成功下載篇數 / 嘗試下載篇數** → 反映實際下載成功率(連結品質)。
    - 外加:requested / candidates_available / attempted / downloaded / failed(分類)/ shortfall。
  - **統計結果存檔**:以 JSON 寫到 `.omo/evidence/oa-coverage-m3b.json`(被 gitignore 不上傳,但留著日後參考;規劃 agent 驗收時讀它,把 OA 覆蓋率結論記進 STATE/架構筆記,作為 M3B 後續「是否加 arXiv/Unpaywall」的依據)。

**Must NOT:**
- 不改變 ranking/selection 本身(只加「下載+遞補」層)。
- 不硬性「只選 OA」(保留非 OA 高相關的候選,靠遞補處理)。

**Evidence of completion:**
- `grep -n "def.*download_and_backfill\|def.*backfill\|oa_coverage\|download_stats" literature_review/pdf_downloader.py`(或對應)命中。
- 測試覆蓋「遞補」與「統計」(見 Todo 5)。

---

## Todo 5: 測試（無 key、用 fake，不依賴真實網路）

**What to do:**
- 擴充/新增測試(全用 **fake fetcher**,**不**真的連 OpenAlex / 不真的下載;這樣測試可重複、快速、斷網也能跑):
  - `test_pdf_downloader.py`:
    - 有 OA URL → fake fetcher 下載成功 → 檔案存在。
    - 無 OA URL → 回報「無 OA」。
    - 網路失敗 → 回報失敗 / 不崩潰。
    - 遞補:top N 有 2 篇失敗 → 遞補第 N+1、N+2 → 仍湊滿 N。
    - 統計:成功/失敗/OA 比例正確。
  - `test_ranking.py`:更新 lexical 標準化後的行為(既有 `test_rank_prefers_query_term_in_title` 應仍過,確認)。
- 用 fake,不需 key、不需真網路。

> **測試 vs 真實 smoke 的區別(避免誤會)**:測試(Todo 5)用 fake 是為了可重複、不依賴網路;**真實 smoke(Todo 7c)會真的查 OpenAlex、真的下載 PDF**。OpenAlex 免費、不用 key,所以「無 key」不是「不能查 OpenAlex」,而是「查 OpenAlex 本來就不用 key」。

**Evidence of completion:**
- 全套件測試綠;log 存 `.omo/evidence/test-suite-m3b-pdf-fetcher.log`(執行代理跑)。

---

## Todo 6: 文件

**What to do:**
- `AGENTS.md`:架構加「M3B: PDF 自動下載(downloader + OA 統計)」;ranking 描述改為「標準化 0~1 等權」。
- `HANDOFF.md`:加「Latest milestone: PDF fetcher (M3B)」。
- `README.md`:更新 ranking 分數說明 + 補 pdf_downloader 用途。

**Evidence of completion:**
- `grep -n "pdf_downloader\|M3B\|open_access_pdf_url" AGENTS.md / HANDOFF.md / README.md` 命中。

---

## Todo 7: 驗證 / smoke（真實 smoke 用真 OpenAlex，免費免 key）

**What to do:**
- (a) 全套件 `uv run python -m unittest discover -s tests -v` 全綠。
- (b) 無 key / 無真網路:fake 跑「search(fake)→ select → download(fake)→ backfill → 統計」整段,確認遞補與統計正確。
- (c) **真實 smoke(需網路;OpenAlex 免費免 key)**:對真實 query(如 `"literature review agent"`)跑 `search_papers` → 看 `open_access_pdf_url` 有無值;若有,對 1-2 篇真實下載到暫存(如系統暫存目錄,**不要**存進 `data/papers/` 或 commit),確認能拿到 PDF(成功或明確失敗原因)。
  - **注意**:不 commit 下載的 PDF;不下載大量(1-2 篇即可);失敗也算有效結果(記比例)。
- 記錄:OA 可下載比例、成功/失敗、無 key 洩漏。

> **澄清**:**OpenAlex 免費、不用 API key**。測試(Todo 5/7b)用 fake 是為可重複、不依賴網路;真實 smoke(7c)真的查 OpenAlex、真的下載 PDF。兩者不衝突。

**Must NOT:** smoke 期間不改 code;不 commit 產物;不印 key;下載的 PDF 只放暫存不進 `data/papers/`。

**Evidence of completion:**
- `.omo/evidence/smoke-m3b-pdf-fetcher.log`(執行代理跑存)。

---

## Commit strategy（由使用者執行，執行代理不 commit）
- 執行代理**不執行任何 git commit**;只改 code、跑測試、存 log、回報。
- 使用者（本人）驗收後親自 commit/push：
  - code commit：`git add literature_review/models.py literature_review/search.py literature_review/ranking.py literature_review/pdf_downloader.py tests/` → `feat(pdf): add OA PDF downloader with backfill and normalized ranking`
  - docs commit：`git add AGENTS.md HANDOFF.md README.md` → `docs: document PDF fetcher milestone`
- 注意：`.omo/evidence/` 被 gitignore，不 commit。