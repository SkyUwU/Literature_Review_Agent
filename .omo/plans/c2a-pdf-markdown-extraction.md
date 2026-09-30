# C2a: PDF 抽取換 pymupdf4llm + 章節切分(markdown 抽取層)

- slug:`c2a-pdf-markdown-extraction`
- date:2026-09-10
- status:🟡 計畫待核准(使用者 2026-09-10 拍板拆三階段 C2a/C2b/C2c;本計畫為 C2a)
- 前置:M5e 已 commit(執行代理回報已執行,push 晚點);C2b(功能性分數評分改版)將接在本計畫的章節基礎上

## 動機(背景事實)

1. **表格破碎**(K3c 實證):現況抽取 `extraction.py` 用 **pypdf** 裸文字(`page.extract_text()`),表格被切成碎片 → RCS LLM 保守低估(使用者 vs LLM rel 10vs6,機制=PDF 抽取表格破碎)。
2. **chunk 零散、無章節語意**:`evidence.py: chunk_document` 以 250 字/30 重疊滑動視窗切跨頁 chunks——chunk 不知道自己在哪個章節;`coverage.py` 只能用 **regex 猜章節**(`classify_section`,line-based、<80 字、anchored)——脆弱的啟發式。
3. **C2b 的入口依賴**:功能性分數評分的「章節先驗過濾(只留 Method/Experiments/Limitations)」「per-paper top-2 取樣」「逐篇筆記按章節餵入」全部需要**可靠的章節資訊**——本計畫是全部下游的基礎。
4. 使用者 2026-09-10 帶回建議:`pymupdf4llm`(PyMuPDF 官方 markdown 轉換,表格→markdown 表格)+ `langchain-text-splitters.MarkdownHeaderTextSplitter`(依 `#/##/###` 切分、chunk 自帶章節 metadata)——比 Marker-pdf(深度學習版面分析)更輕、免費、純本地。

## 關鍵 API 查證(context7,2026-09-10)

- `pymupdf4llm.to_markdown(doc, *, page_chunks=True)` → `list[dict]`,每頁 dict 含 `metadata`(含 `page`/`page_count`/`title`)、`text`(該頁 markdown、含表格)、`tables`、`toc_items`。
- `pymupdf4llm.to_markdown(doc, *, page_separators=True)` → 單一 string,頁之間插入頁分隔標記(確切格式 **PoC 確認**)。
- `pymupdf4llm.TocHeaders(doc)` → 用 PDF 書籤(TOC)決定標題階層(`hdr_info=`),`toc_items` 帶頁碼;無書籤 PDF 需 fallback(自動標題偵測)。
- `MarkdownHeaderTextSplitter(headers_to_split_on=[("#","Header 1"),("##","Section"),("###","Subsection")]).split_text(md)` → 每個 `Document` 有 `.page_content` 與 `.metadata`(各級標題值,子章節自動繼承父章節 metadata)。

## 設計決策(本計畫定案;2026-09-10 討論後簡化——黑名單 + 去頁碼工程)

- **抽取契約不變**:`FullTextDocument`/`PageText` 保留(`extract_pdf_text(path, paper_id)` 簽名不變);text 內容從「pypdf 裸文字」換成「pymupdf4llm 逐頁 markdown」(表格→markdown 表格,留在 text 內)→ 滑動視窗切出的 chunk **不再破碎**。
- **不做頁碼回推工程(2026-09-10 使用者簡化裁示)**:chunk 重點 = 哪篇論文 + 哪個章節;chunk_id 簡化為 `{paper_id}-c{n}`;`page_start`/`page_end` **改 Optional**(預設 None)——下游 `EvidenceSummary`/`EvidenceCitation`/`ChunkReference` 的 page 欄位同步 Optional 化(排序等相容用 `or 0`);pymupdf4llm 逐頁 `metadata.page` 現成時順手填、無則 None,**不做回推工程**。全下游改寫留給 C2b/C2c。
- **章節切分**:新函式 `chapter_chunk_document(document, policy)`——整份 markdown → `MarkdownHeaderTextSplitter`(`#/##/###`,維持三層)→ 每 chunk 帶 `section`(章節路徑,如 `"Header 1 > Section > Subsection"`)→ 超大章節(＞`max_words`)段落優先細切(sub-chunk 共用 section)。
- **`max_words` 250 → 350(2026-09-10 使用者裁示「250 好少」)**:350 words ≈ 455 tokens,**bge-small-en-v1.5 max_seq_length=512 為天花板**(超過會被 sentence-transformers 截斷、chunk 尾部進不了向量 → 傷檢索);350 保有 ~12% 邊際,**此為上限不再調大**;若實測仍不夠,「embedding 前超長 chunk 處理」列 C2b 之後討論(屆時 chunk 改走功能性評分,embedding 只剩取樣層)。`ChunkPolicy.max_words` 上限 `le=1_000` 不動。
- **契約擴充(向後相容)**:`EvidenceChunk` 加 `section: str | None = None`;舊 `chunk_document` 保留(產 section=None 的 chunk,舊行為不變)。
- **章節黑名單(2026-09-10 使用者簡化裁示,Q1)**:不做 include 白名單(每篇論文章節標題不同,白名單會誤殺);只丟「一定沒用的」——references/acknowledgments(可選 appendix)。章節分類表 = 單一來源:`_SECTION_HEADERS` 擴充(加入 acknowledgments),黑名單類 = 最低權重區(現況 references=14 之意)。
- **fallback**:pymupdf4llm 失敗(掃描版無 OCR 引擎/例外)→ `extraction_method="pypdf"` 既有路徑(`extraction_method` 欄位區分,「pypdf」vs「pymupdf4llm」)。
- **章節邊界無 overlap 是接受取捨**(2026-09-10 自審):章節為語意單位、章節間不重疊;跨章節語意**不追求**——C2b 送 LLM 時會把「論文名稱 + 章節標題」包裝進 chunk(參考訊號),單一語意單位即完整(2026-09-10 使用者確認)。

## Todo 0 — 真實樣本先行(併入 Todo 1/2 實作迴圈,非獨立 gate)

- **2026-09-10 裁示:取消獨立 PoC gate**(去頁碼後無技術選型風險)——改「實作即驗證」:Todo 1 完成抽取 → **立即用真實樣本跑對比**;Todo 2 完成章節切分 → **立即用真實樣本跑端到端**。工具不行就回報規劃調整,不硬做。
- 樣本:從 `data/papers/` **複製** 2-3 篇到 `/tmp/c2a-poc/`(含表格者優先,如 W3195625625(表格型);**至少 1 篇無書籤** 避免標題偵測評估被樣本偏差誤導;**不直接改寫 data/papers**)。
- 驗證點(實作完成時同步產出證據):
  1. 表格完整性:同一表格區域「pypdf 裸文字 vs pymupdf4llm markdown」對比(舊破碎、新完整)→ `.omo/evidence/c2a-table-compare.md`。**此為真實證據;Todo 5 的合成 fixture 只是 sanity check(同源偏誤,證明力有限)**。
  2. 標題偵測:無書籤時 `#/##/###` 標題是否出現、層級是否合理(splitter 可切)。
  3. `MarkdownHeaderTextSplitter` 章節數/每章節 metadata/超大章節(＞350 字)出現情況/文件開頭無標題段行為。

## Todo 1 — 相依 + extraction 換血(extraction.py)

- `pyproject.toml` 加:`pymupdf>=1.24`、`pymupdf4llm`(最新穩定)、`langchain-text-splitters`(輕量包,只含 splitters);`uv sync`。
- `extract_pdf_text(path, paper_id)`:
  - 主路徑:pymupdf4llm `to_markdown(path, page_chunks=True)` → 每頁 `metadata["page"]`(依 PoC 結論 0/1-based)→ `PageText(page_number, text)`;`extraction_method="pymupdf4llm"`。
  - fallback:任何例外或 0 頁 → pypdf 既有路徑,`extraction_method="pypdf"`(stderr 警告,不 raise,除非兩者皆失敗)。
  - `len(text) >= 20` 過濾保留;加密 PDF 處理沿用 pypdf 分支。
- 測試:mock 兩路徑;`exact` 頁碼/index;fallback 觸發;`extraction_method` 值。

## Todo 2 — EvidenceChunk.section + 章節切分(evidence.py)

- `models.py`:
  - `EvidenceChunk` 加 `section: str | None = Field(default=None, description="Markdown heading path, e.g. 'Section > Subsection'.")`。
  - `EvidenceChunk.page_start`/`page_end` 改 `int | None`(Optional,預設 None);`EvidenceSummary`/`EvidenceCitation`/`ChunkReference` 的 page 欄位同步 Optional(2026-09-10 使用者裁示:不做頁碼回推工程,現成才填)。既有排序/聚合處以 `or 0` 相容(本計畫最小相容改動,全下游改寫留 C2b/C2c)。
- `evidence.py` 新增 `chapter_chunk_document(document, policy) -> list[EvidenceChunk]`:
  1. 整份 markdown 來源:優先 `to_markdown(path, page_separators=True)` 單串;或 `page_chunks=True` 逐頁合併(PoC 定一種,不留雙軌)。
  2. `MarkdownHeaderTextSplitter(headers_to_split_on=[("#","Header 1"),("##","Section"),("###","Subsection")])` 切分(維持三層,2026-09-10 使用者確認——### 粒度對 C2b top-2 取樣與 C2c 筆記餵入皆加分)。
  3. 每段:section = 各級 metadata **依 headers_to_split_on 層級順序**組路徑(如 `"Header 1 > Section > Subsection"`,缺級跳過);文件開頭無標題段 → `section=None`。page_start/page_end:metadata.page 現成才填,否則 None(不做回推)。
  4. **章節內細切 = 段落優先(2026-09-10 使用者裁示)**:章節文字以空行(段落)為單位依序分組,累積至接近 `max_words`(=350,2026-09-10 裁示,原 250)就封組(組 ≤ max_words);單一段落本身 ＞ `max_words`(罕見)→ 純文字段落內部才滑動視窗切,**表格 block(連續 `|` 行的 markdown 表格)整體保留不切**(overlap 只存在於此兜底路徑);短段落(＜4 words)隨組合併(比照現況規則的精神)。
  5. **chunk_id = `{paper_id}-c{n}`**(2026-09-10 使用者簡化裁示:不再嵌 page 範圍;n 為**最終文件順序**全域連續編號,引用時直接可指認)。
- **本 Todo 不接 pipeline**(接線=C2b);測試直接呼叫新函式。
- 測試:章節切分、metadata 繼承(子章節帶父章節)、**段落優先細切(段落邊界不被切斷、累積封組、超長段落/表格 block 行為)**、無標題段(section=None)行為、chunk_id 格式與順序、無標題 PDF 行為(單一 chunk 或 fallback)。

## Todo 3 — 章節分類表單一來源 + coverage 章節優先(coverage.py)

- **章節名→priority 總表 = 單一來源**(2026-09-10 裁示:不做白名單,黑名單併入總表低位區):
  - `_SECTION_HEADERS` 擴充加入 acknowledgments(`acknowledgments` → priority 14 區,與 references 同級最低)。
  - 黑名單類(references/acknowledgments/bibliography)= priority 14;appendix 維持 13。
- 新 `classify_chunk(chunk) -> tuple[int, str]`:有 `chunk.section` → 以最後一級章節名(小寫、去編號)對照總表;無 section → 現況 regex `classify_section(text)`(既有 C1-C4 規則不變)。
- `_select_pack`/`_region_priority` 改用 `classify_chunk`(references/acknowledgments 偵測也以 section 優先)。
- **priority 表用途說明(2026-09-10 使用者質疑「黑名單後 priority 還需要嗎」)**:現況 priority 只服務 coverage 包選取(舊 pipeline 仍需,故 C2a 保留);C2b/C2c 改造後覆蓋包可能整體退役(STATE 已記「覆蓋包近乎多餘」)→ **priority 表屆時移除,本計畫不刪**。
- 測試:帶 section 的 chunk 分類正確(method/experiments/limitations/references/acknowledgments/other)、無 section 走舊路徑、C1-C4 全綠。

## Todo 4 — 章節黑名單過濾工具(供 C2b,不接 pipeline)

- 新 `drop_noise_sections(chunks: list[EvidenceChunk]) -> list[EvidenceChunk]`:**只丟 references/acknowledgments**(可選參數 `drop_appendix: bool = True`),**其餘全收**(不做 include 白名單——每篇論文章節標題不同,白名單會誤殺;2026-09-10 使用者裁示)。
- 分類判定與 Todo 3 共用 `classify_chunk`(單一來源)。
- 測試:references/acknowledgments/appendix 被丟、各類核心章節與 other 全保留、無 section chunk 保留(不誤殺)。

## Todo 5 — 測試與驗收

- 既有套件全綠 + 新增測試全綠(`uv run python -m unittest discover -s tests -v`),log 存 `.omo/evidence/c2a-tests.log`。
- **真實樣本端到端(2026-09-10 自審補入,Q4)**:用 PoC 樣本 PDF 跑 `chapter_chunk_document`(或 `extract_pdf_text` + 章節切分),輸出 chunk 總數、section 分布(哪些章節、各幾個 chunks)、黑名單章節是否被識別——證據存 `.omo/evidence/c2a-chapter-smoke.md`(C2a 不接 pipeline,此步補「函式在真實 PDF 上的行為」落差)。
- **表格保留 fixture 測試**:用 pymupdf 合成一份含 markdown 表格的 PDF → 斷言抽取後 text 含表格化的 `|` 行。**定位 = 可重現 sanity check(同源偏誤:製造與解析同源,證明力有限);真實證據以 PoC 對比為準(Q5)**。
- 驗收對比:PoC 樣本表格頁「舊 chunk text vs 新 chunk text」留存 `.omo/evidence/c2a-table-compare.md`(舊破碎、新完整 markdown 表格)。
- 回報格式:測試數、檔案變更清單、證據檔路徑、section/chunk_id 變更向後相容確認(既有 chunk_id 格式測試更新為新預期,禁止刪測試)。

## Must NOT

- **不接 pipeline/main.py**(接線=C2b);不改 RCS/notes/synthesis/ranking/評分契約/閾值。
- **不做頁碼回推工程**(2026-09-10 使用者裁示):不驗證頁分隔標記、不寫回推邏輯;page 欄位只做 Optional 化 + 現成才填。
- 不刪 `chunk_document` 舊函式(legacy 保留,compare 用)。
- 不 commit(使用者親做);不碰 `.env`/`data/papers/`(樣本複製到 `/tmp/c2a-poc/`);不 spawn;不印 key。
- 不動 `EvidenceChunk` 既有欄位語意(text 最小 20 字規則、section=None 舊行為保留)。

## Success criteria

1. **實作即驗證(取代獨立 PoC gate)**:真實樣本對比證據——表格在 pymupdf4llm 輸出為完整 markdown 表格(舊破碎 vs 新完整)+ 標題偵測可切出章節(或記錄替代方案並調整設計)。
2. 既有 tests 全綠(248 不減,chunk_id/page 格式測試以新預期合理替換);新增 tests 全綠(數量記錄)。
3. 真實樣本端到端:chunk 數 + section 分布 + 黑名單識別有證據(留 `.omo/evidence/c2a-chapter-smoke.md`)。
4. `section` 欄位與 page Optional 化向後相容(舊 `chunk_document` 行為不變、C1-C4 測試全綠)。
5. 章節黑名單過濾工具「只丟 references/acknowledgments」測試通過(供 C2b 直接使用)。

## Commit strategy(使用者親做)

```powershell
git add literature_review/extraction.py literature_review/evidence.py literature_review/models.py literature_review/coverage.py tests/ pyproject.toml uv.lock HANDOFF.md
git commit -m "feat: C2a markdown extraction with pymupdf4llm and section-aware chunking"
git add .omo/STATE.md .omo/plans/c2a-pdf-markdown-extraction.md
git commit -m "docs: record C2a plan and project state"
git push
```

(確切檔案以 `git status` 為準)

## 風險自審

| # | 風險 | 對策 | 殘餘 |
|---|---|---|---|
| P1 | pymupdf4llm 對部分 PDF 品質差/例外(掃描版、偽 PDF) | fallback pypdf + `extraction_method` 區分 + 警告;HANDOFF 記錄 | 低-中 |
| P2 | 標題偵測不可靠(無書籤 PDF、排版特殊)→ 章節切分品質差 | Todo 1/2 實作循環內先用真實樣本驗證(表格式對比 + 標題分配);失準時 fallback 現況 `chunk_document`(section=None) | 中 |
| P3 | 無標題 PDF → MarkdownHeaderTextSplitter 單一巨型 chunk;表格 block 超長 | 巨型章節段落優先細切(已設計);表格 block 整體保留不切(超長表格罕見,主文附錄才可能,附錄在黑名單);仍異常 → fallback 現況 `chunk_document` | 低 |
| P4 | section 名稱雜訊(排版錯、編號、縮寫)對照不到黑名單 | 正規化(小寫/去編號/包含比對)+ 黑名單只認 references/acknowledgments(寬鬆);對照表單一來源 | 低 |
| P5 | 新相依與既有環境衝突、uv.lock 變動 | `uv sync` 驗證;相依版本保守(最新穩定即可) | 低 |
| P6 | 既有 tests 依賴 chunk 計數/text/chunk_id/page 格式而被破壞 | 舊函式保留 + section=None + page Optional(`or 0` 相容);text/chunk_id 斷言更新為新預期(合理替換,禁止刪測試);預期受影響:test_extraction/test_evidence/test_pipeline(dry-run JSON)/test_coverage | 中 |
| P7 | 合成 fixture 同源偏誤(自己產自己解析)→ 虛假安心 | fixture 定位為 sanity check;驗收以真實樣本對比為準(2026-09-10 自審補入) | 低 |
| P8 | 樣本書籤覆蓋率單一 → 標題偵測評估被誤導 | 樣本至少 1 篇無書籤(2026-09-10 自審補入) | 低 |
| P9 | page 欄位 Optional 化 → 下游聚合/排序(None 參與)行為改變 | 本計畫只做 `or 0` 相容最小改動,語意檢核新增測試;全下游改寫留 C2b/C2c | 中 |

## 下一步行動卡

1. 使用者核准本計畫(或要求修改)。
2. 核准後:規劃 agent 給精簡執行指令包(執行代理讀計畫 Todo 0-5)。
3. 執行代理回報 → 規劃 agent 驗收 → 使用者 commit(兩筆 + push)。
4. 接著寫 C2b(功能性分數評分改版)計畫。