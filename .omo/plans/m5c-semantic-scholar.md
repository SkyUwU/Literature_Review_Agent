# m5c-semantic-scholar - Work Plan

## TL;DR (For humans)

把 Semantic Scholar 接為主要檢索來源。新增 `doi` 欄位、去重從 `paper_id` 改成 DOI→title+year、建 SS adapter（含 1 RPS 速率控制與 429 退避）、pipeline 以 SS 為主（key 缺失時 fallback 回 OpenAlex）、abstract 不足的論文用 DOI 從 OpenAlex 補摘要。保留 OpenAlex 搜尋不動（fallback / CLI 用）。

**新增 5 個檔案**：`ss_search.py` + `test_ss_search.py`（adapter）；改 5 個檔案：`models.py`、`search.py`、`pdf_downloader.py`、`main.py`，加上對應測試。 probe 為一次性腳本，放 `.omo/drafts/`（gitignored）。

**不會做**：arXiv adapter（之後再說）、移除 paper_id（代價大且不必要）、改 ranking 公式、動 OpenAlex `search.py` 的搜尋邏輯。

---

## Scope

**In**
- `Paper.doi: str | None` 欄位
- OpenAlex adapter 補抓 DOI（`REQUESTED_FIELDS` + `paper_from_openalex`）
- 去重鍵改為正規化 DOI → 備援正規化 title+year
- `literature_review/ss_search.py`：SS adapter，回傳 `SearchResponse(provider="semantic_scholar")`
- SS 速率控制：2.0s 間隔 + 429 指數退避（尊重 `Retry-After`，最多 3 次）
- SS adapter 填 `citation_count`，現有三成分 ranking 不改
- `main.py` 以 SS 為主搜尋（key 存在時），fallback 回 OpenAlex（key 缺失時）
- abstract 不足的論文用 DOI 對回 OpenAlex 補摘要（`reconstruct_abstract` from `search.py:78`）
- 一次性 diagnostic probe（SS vs OpenAlex DOI 重疊率 / abstract 覆蓋率 / OA PDF 覆蓋率）

**Out**
- arXiv adapter（之後）
- paper_id 移除（代價大，chunk_id/檔名/報告全動，不必要）
- ranking 公式改動（SS citation_count 已直接補進現有三成分）
- OpenAlex `search.py` 搜尋邏輯變動（保留為 fallback / CLI 用）

---

## Verification strategy

每個 Todo = 實作 + 測試（tests-after）。Agent-executed QA：每 Todo 結束後跑 `uv run python -m unittest discover -s tests -v`（或指定測試檔），加上對應 smoke。最終 Full verification wave 跑完整 suite + dry-run + real smoke。

---

## Execution strategy

```
Todo 0 (probe)          ← 可與 Todo 1 並行；結果決定 Todo 4 backfill 門檻
Todo 1 (doi field)      ← 前置：Todo 2/3/4 都需要
Todo 2 (dedup)          ← 依賴 1；與 3 可並行
Todo 3 (SS adapter)     ← 依賴 1；與 2 可並行
Todo 4 (pipeline)       ← 依賴 2+3+0（probe 結果）
Todo 5 (smoke/tests)    ← 依賴全部
```

---

## Todos

- [ ] 0. **SS vs OpenAlex diagnostic probe** — 建立一次性診斷腳本，量三個數字決定設計

  **References**
  - `literature_review/search.py:25-30` — OpenAlex URL/fields（參考格式）
  - SS API：`GET https://api.semanticscholar.org/graph/v1/paper/search`，header `x-api-key`（大小寫敏感），`fields` 單一逗號分隔字串
  - 安全存取：`(paper.get("externalIds") or {}).get("DOI")`（大寫 DOI key）、`((paper.get("openAccessPdf") or {}).get("url") or "")`（可能 null 或空字串）
  - `.env` 金鑰名：`SEMANTIC_SCHOLAR_API_KEY`（已由使用者寫入）
  - `.omo/drafts/m5c-semantic-scholar.md` — draft state

  **Implementation**
  1. 在 `.omo/drafts/ss-vs-openalex-probe.py`（gitignored）建立一次性腳本：stdlib urllib only（不加 requests 依賴）、`SEMANTIC_SCHOLAR_API_KEY` from env、SS 每請求 ≥1.1s 間隔、429 指數退避（2^attempt s + 0-0.5 jitter、尊重 `Retry-After`、最多 3 次）、共 ≤5 個 API 請求（2 query × 2 source + 1 margin）。
  2. 兩個搜尋：SS `/paper/search`（limit=100, year=`MIN_YEAR-`, fields=title,abstract,year,authors,externalIds,citationCount,openAccessPdf,venue）+ OpenAlex（limit=100, filter=from_publication_date）同 query。
  3. 計算：DOI overlap（Jaccard + openalex_found_in_ss + ss_found_in_openalex）、SS abstract 覆蓋率（abstract ≥20字）、SS OA PDF 覆蓋率（openAccessPdf.url 非空）。
  4. 輸出 `.omo/evidence/ss-probe-{timestamp}.json`，內容包含3 metrics per query。

  **Acceptance**
  - JSON 存在，`reports` 陣列有 ≥1 筆，每筆含 `doi_overlap`（`jaccard`, `openalex_found_in_ss`, `ss_found_in_openalex`）、`ss_abstract_coverage`、`ss_open_access_pdf_coverage`、`openalex_abstract_coverage`、`openalex_open_access_pdf_coverage`
  - stderr 有 0 次未處理 429（有 retry 記錄也 OK，但不 crash）
  - Exit code 0

  **QA**
  - Happy：`uv run --env-file .env python .omo/drafts/ss-vs-openalex-probe.py` → exit 0、JSON 可 parse、metrics 均為 0–1 之間的數字
  - Failure：不設 key → exit 2、stderr 有明確錯誤訊息

  **Commit**：none（一次性腳本，留在 `.omo/drafts/` 或刪除）

- [ ] 1. **`Paper.doi` 欄位 + OpenAlex 補抓 DOI** — 為跨來源去重奠基

  **References**
  - `literature_review/models.py:44-57` — Paper class，於 `paper_id` 後新增 `doi` 欄位
  - `literature_review/search.py:27-30` — `REQUESTED_FIELDS`，加入 `"doi"`
  - `literature_review/search.py:86-118` — `paper_from_openalex`，於 `paper_id=record.get("id")` 後（~:88）加入 DOI 正規化，DOI = `record.get("doi")`（格式 `https://doi.org/10.xxxx/...`），去除前綴 `https://doi.org/` / `http://doi.org/` / `doi:`，lowercase，空值 → None
  - `norm_doi` 正規化邏輯：`v.strip().lower()` → 去三個前綴 → `return v or None`

  **Implementation**
  1. `models.py`：`doi: str | None = Field(default=None, description="Normalised bare DOI, e.g. 10.1145/xxx")`
  2. `search.py:27-30`：`REQUESTED_FIELDS` 字串末尾加 `",doi"`
  3. `search.py:~88-108`：在 `paper_from_openalex` 中，`paper_id` 後加：
     ```python
     raw_doi = record.get("doi") or ""
     doi = raw_doi.strip().lower()
     for pfx in ("https://doi.org/", "http://doi.org/", "doi:"):
         if doi.startswith(pfx):
             doi = doi[len(pfx):]
     doi = doi or None
     ```
     並在 `Paper(...)` 建構中加入 `doi=doi`
  4. 新增測試（`tests/test_search.py`）：mock record 含 doi / 不含 doi / 空字串 → 驗證 `Paper.doi` 正確 / None
  5. 新增測試（`tests/test_models.py`）：`Paper(..., doi=None)` 不報錯、`Paper(..., doi="10.1145/abc")` 接受

  **Acceptance**
  - `Paper` 有 `doi: str | None`，預設 None
  - OpenAlex 回傳的 papers 有 DOI 時 `paper.doi` 為正規化字串（小寫、無 URL 前綴）；無 DOI 時為 None
  - 全部既有測試仍通過（`uv run python -m unittest discover -s tests -v` exit 0）
  - 新增 ≥2 個測試通過

  **QA**
  - Happy：mock `{"doi": "https://doi.org/10.1145/1234"}` → `paper.doi == "10.1145/1234"`
  - Happy：mock `{"doi": null}` → `paper.doi is None`
  - Happy：mock `{"doi": "DOI:10.999/x"}` → `paper.doi == "10.999/x"`
  - Failure：`Paper(...)` 缺 doi → 預設 None，不 crash

  **Commit**：`feat(models,search): add doi field to Paper and populate from OpenAlex`
  檔案：`models.py`、`search.py`、`tests/test_models.py`（若新增）、`tests/test_search.py`

- [ ] 2. **去重鍵改為 DOI→title+year** — 跨來源去重不再依賴 paper_id

  **References**
  - `literature_review/pdf_downloader.py:45-53` — `_safe_filename`（**不改**，仍用 `paper.paper_id`）
  - `literature_review/pdf_downloader.py:168` — `already_downloaded.add(ranked.paper.paper_id)` → 改為 `dedup_key(ranked.paper)`
  - `literature_review/pdf_downloader.py:182` — 同上（失敗時也要加 dedup_key 避免重複嘗試）
  - `literature_review/main.py:160` — `already_downloaded: set[str] = set()`（型別不變，內容變 dedup_key）
  - `re` 模組：用於 title 正規化

  **Implementation**
  1. `pdf_downloader.py` 新增：
     ```python
     def dedup_key(paper: Paper) -> str:
         if paper.doi:
             return f"doi:{paper.doi}"
         norm_title = re.sub(r"[^a-z0-9]", "", paper.title.lower())
         return f"title:{norm_title}:{paper.year}"
     ```
  2. `pdf_downloader.py:168`：`already_downloaded.add(dedup_key(ranked.paper))`
  3. `pdf_downloader.py:182`：`already_downloaded.add(dedup_key(ranked.paper))`
  4. 新增測試（`tests/test_pdf_downloader.py`）：
     - paper 有 DOI → `dedup_key == "doi:10.1145/abc"`
     - paper 無 DOI → `dedup_key == "title:literaturereviewagent:2024"`
     - 兩篇同 DOI 不同 paper_id → 同 dedup_key ✓
     - title 標點/大小寫 → 正規化一致
     - `_safe_filename` 行為不變（仍有獨立測試覆蓋）

  **Acceptance**
  - `dedup_key(paper)` 回傳一致的字串（DOI 優先，title+year 備援）
  - `_safe_filename` 行為不變（既有的 `paper_id` slug 邏輯原封不動）
  - `download_and_backfill` 以 DOI 去重：同 DOI 不同 paper_id 只下載一次
  - 全部既有測試 + 新測試通過

  **QA**
  - Happy：`paper.doi="10.1145/abc", paper_id="W123"` → `dedup_key == "doi:10.1145/abc"`
  - Happy：`paper.doi=None, title="Lit Review!", year=2024` → `"title:litreview:2024"`
  - Happy：兩篇不同 paper_id、同 DOI → `download_and_backfill` 只下一次（`duplicate_reused >= 1`）
  - Happy：兩篇同 title 不同年 → 不同 dedup_key（不誤併）
  - Failure：OpenAlex W-ID 和 SS paperId 不同但同 DOI → 去重成功（不重複下載）

  **Commit**：`feat(dedup): replace paper_id dedup with DOI (fallback title+year)`
  檔案：`pdf_downloader.py`、`tests/test_pdf_downloader.py`

- [ ] 3. **Semantic Scholar adapter** — 建立 SS 搜尋模組（1 RPS + 429 退避）

  **References**
  - `literature_review/search.py:25-54` — OpenAlex adapter pattern（URL 建構 / fetch / normalize）
  - `literature_review/search.py:86-118` — `paper_from_openalex`（SS normalizer 的參考模板）
  - `literature_review/models.py:44-76` — Paper / SearchRequest / SearchResponse
  - SS API：`GET https://api.semanticscholar.org/graph/v1/paper/search?query=&fields=&limit=&year=YYYY-`
  - SS 認證：`x-api-key` header（大小寫敏感）；fields = 單一逗號分隔字串
  - SS rate limit：有 key = 1 RPS；429 backoff = 2^attempt s + jitter，尊重 Retry-After
  - SS response：`data` 陣列；`externalIds.DOI`（大寫 key）；`openAccessPdf` 可能 null 或 `{url: ""}`；`citationCount`；`authors` 陣列含 `{authorId, name}`

  **Implementation**
  1. 新建 `literature_review/ss_search.py`：
     - 常數：`SS_SEARCH_URL`、`SS_FIELDS`（title,abstract,year,authors,externalIds,citationCount,openAccessPdf,venue,url）、`ABSTRACT_PLACEHOLDER`（`"Abstract not available for this paper."`）、`SS_PACE_SECONDS = 2.0`（實作後由使用者裁定由 1.1 提高：1.1s 曾連續撞 429）、`SS_MAX_RETRIES = 3`、`SS_USER_AGENT`
     - `ss_get_json(url, api_key) -> dict`：每次 ≥2.0s pacing（含第一次）；429 指數退避（2^attempt + random 0–0.5 jitter），尊重 `Retry-After` header（`error.headers.get("Retry-After")`），最多 3 次，超過 raise `RuntimeError`
     - `paper_from_ss(record) -> Paper | None`：normalise 一筆 SS 記錄。`paper_id = record["paperId"]`（40字元 hex，slug 用）；`doi` = `(record.get("externalIds") or {}).get("DOI")` 正規化；`citation_count = record.get("citationCount")`；`open_access_pdf_url` guard null/空字串；**abstract 缺失/過短 → 填 `ABSTRACT_PLACEHOLDER`（不能留 None/空——`Paper.abstract` 是 required `min_length=20`（models.py:51），None 或空字串建構直接 crash）**；`url = record.get("url")`（SS 有；缺則 fallback `https://www.semanticscholar.org/paper/{paper_id}`）；`authors` 從 list of `{name}` 取。Return None **僅**在 `paperId` 或 `title` 或 `year` 缺失、或 `authors` 為空（與 OpenAlex adapter 同 pattern，`search.py:98`——這幾欄 Paper 都是必填，無救）。
     - `search_ss(request: SearchRequest, *, api_key: str) -> SearchResponse`：組 URL → `ss_get_json` → normalize papers → `SearchResponse(provider="semantic_scholar", ...)`
  2. 新建 `tests/test_ss_search.py`：
     - mock `ss_get_json` → 驗證 URL 格式（query/fields/limit/year 正確）
     - mock 完整 response → 驗證 `SearchResponse.papers` 全部符合 `Paper` 契約
     - mock `externalIds` 缺失 → `paper.doi is None`
     - mock `openAccessPdf` null → `open_access_pdf_url is None`
      - mock `abstract` 為 None 或 len<20 → paper 仍被回傳，abstract = `ABSTRACT_PLACEHOLDER`（≥20 字、能過 `Paper` 建構、不 crash）
      - mock record 缺 title / year / authors 空 → return None（跳過）
      - mock record 缺 `url` → fallback `https://www.semanticscholar.org/paper/{paper_id}`
     - mock 429 → 驗證 retry 2 次後成功（mock `time.sleep` 確認呼叫）
     - mock 連 network error → 驗證 retry 退避
     - mock 429 × 3 次 → raise RuntimeError

  **Acceptance**
  - `ss_search.py` 存在，`search_ss` 回傳 `SearchResponse(provider="semantic_scholar")`
  - 所有回傳 papers `citation_count` 已填（不為 None）；abstract 為真實摘要或 `ABSTRACT_PLACEHOLDER`（皆 ≥20 字、能過 `Paper` 建構）
  - rate limit：mock `time.sleep` 被呼叫 ≥1 次（間隔 ≥1.0s）；429 被重試
  - 全部既有測試 + 新測試通過

  **QA**
  - Happy：mock SS response 10 筆 → `search_ss` 回傳 10 個 Paper，每筆 `provider` 正確
  - Happy：mock 10 筆中 1 筆 null abstract → 回傳 10 個 Paper（1 筆 abstract=placeholder），不跳過、不 crash
  - Happy：mock record 缺 `url` → fallback paper URL 生效
  - Happy：mock `externalIds: {}` → `paper.doi is None`
  - Happy：mock 429 → 200 → paper 成功返回；`time.sleep` 被呼叫 ≥2 次（2.0s + backoff）
  - Failure：mock 429 × 3 → raise `RuntimeError`

  **Commit**：`feat(ss): add Semantic Scholar adapter with rate-limit safety`
  檔案：`literature_review/ss_search.py`（新）、`tests/test_ss_search.py`（新）

- [ ] 4. **Pipeline 串接：SS 為主 + abstract backfill** — 替換搜尋來源、補摘要

  **References**
  - `literature_review/main.py:95-113` — `_search_and_rank_one_query`，目前呼叫 `search.search_papers(request)`（:102）→ 替換為 SS（有 key 時）
  - `literature_review/main.py:116-129` — `run_end_to_end` 簽名，加入 `ss_api_key: str | None = None` 參數；從 env 讀 `SEMANTIC_SCHOLAR_API_KEY` 並往下傳
  - `literature_review/search.py:78-84` — `reconstruct_abstract`（from OpenAlex `abstract_inverted_index`），importable
  - OpenAlex DOI lookup：`GET https://api.openalex.org/works/doi:{doi}?select=abstract_inverted_index`（一個 DOI 一次請求，OpenAlex 限寬鬆不需 key）
  - `literature_review/main.py:127` — `json_fetcher: search.JsonFetcher`（不改，仍給 OpenAlex fallback 用）
  - Probe 結果 `.omo/evidence/ss-probe-*.json` → 若 abstract 覆蓋率 ≥60%，backfill 少量；若 <60%，backfill 量大但機制相同

  **Implementation**
  1. `main.py` `_search_and_rank_one_query`：加入 `ss_api_key: str | None = None` 參數，有 key 時呼叫 `ss_search.search_ss(request, api_key=ss_api_key)`，無 key 時 fallback 到 `search.search_papers(request, json_fetcher=json_fetcher)`
  2. `main.py` 新增 `backfill_abstracts(papers: list[Paper]) -> list[Paper]`：
     - 對每篇 abstract 為 `ABSTRACT_PLACEHOLDER`（或長度 <20 字）且 `doi` 不為 None 的 paper，用 OpenAlex DOI lookup 拿 `abstract_inverted_index`，用 `search.reconstruct_abstract()` 重建摘要，**取代 placeholder** 更新 `paper.abstract`
     - OpenAlex 請求限流：每請求間 ≥0.2s（OpenAlex polite pool 寬鬆，不需 2.0s）
     - DOI lookup 失敗或仍無 abstract → paper 維持 placeholder，log 警告
   3. `main.py` `run_end_to_end`：從 `os.environ.get("SEMANTIC_SCHOLAR_API_KEY")` 讀 key，傳入 `_search_and_rank_one_query`；搜尋後呼叫 `backfill_abstracts`，然後**過濾掉 abstract 仍為 placeholder 的 papers**（backfill 失敗 + 無 DOI 的那些）——過濾後才進 `filter_and_rank`
   4. `main.py` SS fallback：若 SS 回傳 0 筆 paper（key 有但搜尋失敗），自動 fallback 到 OpenAlex，log 警告
   5. 新增測試（`tests/test_main.py` 或 `tests/test_pipeline.py`）：
      - mock SS + OpenAlex → `_search_and_rank_one_query` 有 SS key → 呼叫 SS
      - mock SS + OpenAlex → 無 key → fallback OpenAlex（現有行為不變）
      - mock `backfill_abstracts`：有 DOI paper 無 abstract → DOI lookup 成功 → abstract 更新
      - mock `backfill_abstracts`：DOI lookup 失敗 → paper 保留、不 crash
      - mock SS return 0 papers → fallback OpenAlex → 正常繼續

  **Acceptance**
  - `main.py` 以 SS 為主要搜尋來源（key 存在時）
  - `backfill_abstracts` 正確補齊 abstract（DOI lookup 成功時）
  - 無 SS key → fallback 到 OpenAlex，現有行為不變（dry-run 正常）
  - 全部既有 + 新測試通過

  **QA**
  - Happy：SS 返回 50 papers（20 無 abstract），backfill 補齊 15 篇（5 無 DOI）→ 45 篇有 abstract 進入 ranking
  - Happy：無 SS key → fallback OpenAlex，`--dry-run` 正常 exit 0
  - Happy：SS 返回 0 papers → fallback OpenAlex + log 警告
  - Failure：OpenAlex DOI lookup 全部 500 → papers 維持 placeholder、pipeline 不 crash（log 錯誤）

  **Commit**：`feat(pipeline): Semantic Scholar primary search with OpenAlex abstract backfill`
  檔案：`literature_review/main.py`、`tests/test_main.py`（或 `test_pipeline.py`）

- [ ] 5. **完整測試 + smoke 驗收** — 確認不回歸、真實 run 通過

  **References**
  - `uv run python -m unittest discover -s tests -v`
  - `uv run --env-file .env python -m literature_review.main --dry-run`（無 key smoke）
  - `printf 'literature review agent\n' | uv run --env-file .env python -m literature_review.main`（真實 run，需3 個 GEMINI key + SS key）
  - `.omo/evidence/m5c-smoke.log`（log 路徑）

  **Implementation**
  1. 跑完整 test suite，確認全綠（≥ 原有測試數 + 新增測試）
  2. dry-run smoke：exit 0，無例外，downloads 到 temp dir
  3. real smoke：exit 0，報告有 ≥1 個 `[claim-N]` marker，有 ≥1 篇 include 論文
  4. log 輸出到 `.omo/evidence/m5c-smoke.log`（含 test suite output + smoke output）

  **Acceptance**
  - 全部測試 exit 0（無 FAIL / ERROR）
  - dry-run exit 0，無 traceback
  - real smoke 產出報告，有 claim markers，無 pipeline failed
  - `.omo/evidence/m5c-smoke.log` 存在

  **QA**
  - Happy：test suite exit 0，output 有 "OK"
  - Happy：dry-run exit 0，stderr 無 traceback
  - Happy：real smoke exit 0，stdout 有 "Report saved to:" 或 report JSON

  **Commit**：`docs: M5c Semantic Scholar milestone — update HANDOFF/AGENTS/STATE`
  檔案：`HANDOFF.md`、`AGENTS.md`、`.omo/STATE.md`、`.omo/plans/m5c-semantic-scholar.md`

---

## Final verification wave

- [ ] F1. **全部測試通過** — `uv run python -m unittest discover -s tests -v` exit 0，output 有 "OK"
- [ ] F2. **Probe 指標合格** — `.omo/evidence/ss-probe-*.json` 的 `ss_abstract_coverage` ≥ 0.40（若 < 0.40 標警告但不 block，backfill 機制會補）
- [ ] F3. **Dry-run 通過** — `uv run --env-file .env python -m literature_review.main --dry-run` exit 0，無 traceback
- [ ] F4. **Real smoke 通過** — 報告有 ≥1 `[claim-N]` marker、≥1 篇 include 論文，`failed_extractions` 只含已知問題（如 JPEG 偽 PDF）

---

## Commit strategy

| Todo | Commit message | 受影響檔案 |
|---|---|---|
| 0 | （none） | `.omo/drafts/ss-vs-openalex-probe.py`（gitignored，不 commit） |
| 1 | `feat(models,search): add doi field to Paper and populate from OpenAlex` | `models.py`、`search.py`、`tests/test_models.py`、`tests/test_search.py` |
| 2 | `feat(dedup): replace paper_id dedup with DOI (fallback title+year)` | `pdf_downloader.py`、`tests/test_pdf_downloader.py` |
| 3 | `feat(ss): add Semantic Scholar adapter with rate-limit safety` | `literature_review/ss_search.py`（新）、`tests/test_ss_search.py`（新） |
| 4 | `feat(pipeline): Semantic Scholar primary search with OpenAlex abstract backfill` | `main.py`、`tests/test_main.py`（或 `test_pipeline.py`） |
| 5 | `docs: M5c Semantic Scholar milestone — update HANDOFF/AGENTS/STATE` | `HANDOFF.md`、`AGENTS.md`、`.omo/STATE.md`、`.omo/plans/m5c-semantic-scholar.md` |

---

## Success criteria

- `main.py` 以 Semantic Scholar 為主要搜尋來源（key 存在時）；key 缺失時 fallback 回 OpenAlex，行為不變
- 去重用 DOI（正規化）優先 → title+year 備援；不同 paper_id 但同 DOI 的論文只下載一次
- SS adapter 有 1 RPS 速率控制 + 429 退避，不觸發金鑰限流
- SS abstract 不足的論文由 OpenAlex DOI lookup 補摘要
- probe 指標紀錄在 `.omo/evidence/ss-probe-*.json`，可追溯
- 全部測試通過、dry-run 與 real smoke 正常
