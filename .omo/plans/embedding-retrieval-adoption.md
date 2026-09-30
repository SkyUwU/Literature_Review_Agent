# embedding-retrieval-adoption - Work Plan (M2)

## TL;DR (For humans)

**What you'll get:** 把正式 pipeline 的檢索從「詞法（lexical）關鍵字匹配」換成「語意（embedding）向量匹配」。這是 M1（pairwise eval）量測證實「embedding 整體優於 lexical」後的正式採用：之後你跑 pipeline，預設就是用 embedding 檢索，不需要打任何額外參數。同時在 coverage 選取 40 個 chunk 時，把「參考文獻區」標成最低優先權，確保送給 LLM 的內容不會塞滿參考文獻。

**Why this approach:** M1 用 8 份真實 PDF + 6 個 query 的 A/B 比較，結論是 embedding 檢索找到的段落多半比 lexical 更相關（overall verdict: embedding better）。所以正式 pipeline 改用 embedding 是證據驅動的決定。參考文獻區塊對文獻回顧幾乎沒價值，標成最低權重可省 token、減少干擾。

**What it will NOT do:** 不會加「可切換 lexical/embedding」的參數（未來使用就用 embedding，不麻煩使用者）。不會做「切 chunk 前刪除參考文獻」（那是較激進、風險較高的版本，留給之後）。不會調 top-k、不會換更大的 embedding model、不會動 Grobid/PyMuPDF 升級。不會處理「搜尋/下載論文前端」（那是 M3/M4）。不做 embedding 向量持久化快取（每次 pipeline 對當次 PDF 重算即可）。

**Effort:** Small-Medium
**Risk:** Low - 主軸是把一處檢索呼叫換掉 + 在已有 appendix 手法的 coverage.py 加一條同位規則；都有測試鎖住。

**Decisions to sanity-check:** (1) pipeline 直接換 embedding，無 `--retriever` 選項；(2) 每次 run 對當次 PDF 重算 embedding（不持久化）；(3) 參考文獻用「coverage.py 標最低權重（比附錄還低）」的低風險做法，不做前端刪除；(4) 測試用 fake encoder（mock `default_encoder`）或讓 pipeline 接受可注入 encoder，避免下載模型。

Your next move: 經使用者確認後，在 separate worker session 執行（`/start-work embedding-retrieval-adoption`）。完整執行細節如下。

---

> TL;DR (machine): Small effort, Low risk; switch pipeline retrieval call from `retrieve_evidence` (lexical) to `retrieve_evidence_embedding` (embedding) with no `--retriever` switch; add a conservative references-lowest-priority rule to coverage.py; keep all tests green; update docs.

## Scope

### Must have
- `literature_review/pipeline.py` — 把 `run_evidence_pipeline`、`retrieve_from_pdf`、`run_synthesis_pipeline`、`main`（含 dry-run 與正式路徑）裡的 `retrieve_evidence(...)` 呼叫換成 `retrieve_evidence_embedding(...)`；移除不再需要的 `retrieve_evidence` import（若全無使用）；換成 embedding 的呼叫不走 lexical。
- 測試：pipeline 的 embedding 檢索路徑用 fake encoder（mock `default_encoder`，不需下載模型）；現有 lexical 導向的 pipeline 測試若因換檢索而失效需調整（或補 embedding 路徑測試）。
- `literature_review/coverage.py` — 新增：偵測 References/Bibliography 標題（`_REFERENCES_REGEX`，行首、可選編號、忽略大小寫、單詞邊界）；在 `_select_pack` 中把「References 起點之後的 chunk」的優先權設為**比附錄(13)更低(14)**；但**保留現有附錄優先權邏輯**（references 的降權不回退附錄原本處理）；保守為先、寧可少濾不誤傷關鍵內容。
- `tests/test_coverage.py`（或既有對應測試）— 新增 references 規則測試：含 References 標題的論文，其後 chunk 優先權最低、不進入 cap；無 references 標題的正常論文不受影響；附錄 + references 同時存在時的行為（不誤傷附錄以外的正文）。
- 文件：AGENTS.md Commands / README / HANDOFF.md 更新——「pipeline 預設用 embedding 檢索」。

### Must NOT have (guardrails, anti-slop, scope boundaries)
- 不加 `--retriever {lexical,embedding}` CLI 選項（預設直接用 embedding）。
- 不做「切 chunk 前刪除參考文獻」——只做 coverage.py 的保守降權重。
- 不調 `--top-k` 預設（維持 3）——留調參里程碑。
- 不換更大/不同 embedding model——維持 `BAAI/bge-small-en-v1.5`。
- 不做 embedding 向量持久化 / 快取檔——每次 run 重算。
- 不動 `evidence_ranking.py`（lexical 留作 legacy，不刪）、`ranking.py`、`selection.py`、`planning.py`。
- 不新增 Python 依賴（`sentence-transformers` 已存在）。
- worker 不執行 git、不印 key、不改 `.env` / `data/papers/`。
- 不做 M3/M4 範圍（搜尋/下載前端、引用擴充、資料來源策略）。

---

## Todo 1: `pipeline.py` — 換成 embedding 檢索

**What to do:**
1. 在 `pipeline.py` 替換 import：從 `literature_review.evidence_ranking import retrieve_evidence` 改為 `from literature_review.embedding_retriever import retrieve_evidence_embedding`（若 `retrieve_evidence` 不再被使用則移除；保留 `evidence_ranking` 檔案本身）。
2. 找出所有 `retrieve_evidence(` 呼叫點（`run_evidence_pipeline`、`retrieve_from_pdf`、`run_synthesis_pipeline`、`main` 的 dry-run 與正式路徑），全部換成 `retrieve_evidence_embedding(`，參數保持 `(chunks, query, retrieval_policy)`。
3. 確認 `retrieve_evidence_embedding` 的簽名相容：`(chunks, query, policy, encoder=None)`——pipeline 呼叫不傳 encoder（用預設 `default_encoder()`），真實 run 會建 bge-small 模型。

**測試策略（先寫測試再改或用 TDD）：**
- 因 `retrieve_evidence_embedding` 預設會呼叫 `default_encoder()`（下載模型），pipeline 測試必須攔截。兩種方式擇一（計畫先訂，避免實作時卡住）：
  - **A**：測試用 `unittest.mock.patch("literature_review.embedding_retriever.default_encoder", return_value=FakeEncoder())`，攔截模型建立。
  - **B**：讓 `run_synthesis_pipeline` / `run_evidence_pipeline` / `retrieve_from_pdf` 增加可選 `encoder` 參數（預設 None→`default_encoder()`），測試注入 fake encoder。
  - **決定**：優先選 **B**（讓 pipeline 函式接受可注入 `encoder`，乾淨、不需 global mock）；若改簽名會破壞過多現有測試契約，退而用 **A**。實作 agent 採「能讓套件全綠且測試乾淨」的方案，並在回報中說明採了哪個。
- 新增/調整測試：`tests/test_pipeline.py` 有 embedding 路徑（fake encoder）的檢索測試 + dry-run 測試;確保 `uv run python -m unittest discover -s tests -v` 全綠。

**Evidence of completion:**
- `grep -n "retrieve_evidence_embedding" literature_review/pipeline.py` 命中全部檢索呼叫點。
- `grep -n "retrieve_evidence(" literature_review/pipeline.py` 無命中（lexical 已不再用於 pipeline）。
- 測試全綠 log 存 `.omo/evidence/test-suite-m2-todo1.log`。

**QA scenarios:**
- happy：`uv run python -m literature_review.pipeline data/papers "literature review agent" --top-k 3 --dry-run` 輸出 embedding-ranked chunks（rationale 含 "Semantic retrieval"）。
- failure：dry-run 嘗試下載模型（未 mock）→ 會卡/慢，確認測試有攔截；failure = 測試紅 → 修。

## Todo 2: `coverage.py` — 參考文獻最低權重規則（精確區間版本）

**目標設計**：只在「References 起點」與「Appendix 起點」**之間**的 chunk 降為最低權重；附錄維持原本處理。避免「References 之後全降」誤傷「附錄排在 References 之後」的論文。

**要新增的（僅此而已，與現有 appendix 同構）：**
```python
_REFERENCES_REGEX = re.compile(r"(?i)^[0-9]*\.?\s*(?:references|bibliography)\b")
_REFERENCES_PRIORITY = 14  # 最低，低於 appendix(13)

def _has_references_header(text: str) -> bool:
    """比照 _has_appendix_header：逐行檢查 <80 字且匹配 _REFERENCES_REGEX 的標題行。"""
    for line in text.splitlines():
        stripped = line.strip()
        if len(stripped) < 80 and _REFERENCES_REGEX.match(stripped):
            return True
    return False
```

**改動只集中在 `_select_pack` 一個函式**。請依下列**決策表**實作（照表，勿自由發揮）——先找出 `refs_index`（第一個 `_has_references_header` 的位置，可能無）與 `appendix_index`（原有邏輯，可能無），再依表決定每個 chunk 的優先權：

| case | 條件 | 每個 chunk 的優先權判定 |
|---|---|---|
| C1 | 無 refs 標題 | 保持**原本邏輯不改**（含 appendix 13、正文 1-12、無標題 fallback）|
| C2 | 有 refs、無 appendix | `chunk位置 < refs_index` → 原本邏輯；`chunk位置 >= refs_index` → `_REFERENCES_PRIORITY(14)` |
| C3 | 有 refs、有 appendix、且 `refs_index < appendix_index`（refs 在前）| `chunk位置 < refs_index` → 原本邏輯；`refs_index <= chunk位置 < appendix_index` → `_REFERENCES_PRIORITY(14)`；`chunk位置 >= appendix_index` → `_APPENDIX_PRIORITY(13)` |
| C4 | 有 refs、有 appendix、且 `appendix_index <= refs_index`（appendix 在前）| `chunk位置 < appendix_index` → 原本邏輯；`appendix_index <= chunk位置 < refs_index` → `_APPENDIX_PRIORITY(13)`；`chunk位置 >= refs_index` → `_REFERENCES_PRIORITY(14)` |

**硬性守則（確保不亂）：**
- **正文 chunk（優先權 1-12）的相對排序與挑選完全不受影響**——references/appendix 只在「低優先權區」（13/14）內互相調整。
- **不刪除、不修改** `_has_appendix_header`、`_APPENDIX_PRIORITY`、`_APPENDIX_REGEX`、`classify_section` 現有行為。
- 若 C1 原本走「無標題 fallback stride」分支，維持原樣。
- **若實作卡住、不確定某 case 何去何從，停下來回報規劃 agent，不要自行發明規則或擴大範圍。**

**測試策略（TDD，測試表照此建，跑綠才算過）：**
新增到 `tests/test_coverage.py`（或既有對應測試檔）：

| 測試 | 輸入摘要 | 預期 |
|---|---|---|
| T1 | 一篇論文含 "References" 標題，其後無 appendix | refs 及其後 chunk 優先權 14、不進 cap |
| T2 | 一篇論文無任何 references/appendix 標題（回歸）| 行為與現在完全相同 |
| T3 | refs 在前、appendix 在後（`refs_index < appendix_index`）| refs~appendix 之間 = 14；appendix 及其後 = 13；兩者都不進 cap 優先 |
| T4 | appendix 在前、refs 在後（`appendix_index < refs_index`）| appendix~refs 之間 = 13；refs 及其後 = 14 |
| T5 | 只有 "Bibliography"（同義詞）標題 | 比照 references 處理（T1 邏輯）|
| T6 | 正文在前、無章節標題、有 refs（fallback 分支情境）| refs 之後不進 cap；正文不受誤降 |

**Evidence of completion:**
- 測試全綠 log 存 `.omo/evidence/test-suite-m2-todo2.log`。
- `grep -n "_REFERENCES\|_has_references_header" literature_review/coverage.py` 命中。

**QA scenarios:**
- happy：含 references 的論文 coverage pack 不含 references chunk；T1-T6 全綠；appendix 邏輯未回退。
- failure：任一 case 結果不符決策表 → 修並補測試；failure = 不確定 → 停止回報規劃 agent。

## Todo 3: 文件更新

**What to do:**
- `AGENTS.md` Commands / 架構描述：pipeline 預設用 embedding 檢索（`retrieve_evidence_embedding`），不再用 lexical。
- `HANDOFF.md` 加「Latest milestone」段落：M2 把 pipeline 正式切到 embedding（證據來自 M1 pairwise verdict）、coverage 加 references 低權重、檔案清單、suite count（以實際跑出為準）。
- 同步 README 若有描述 lexical-first / retrieval 處。

**Evidence of completion:**
- `grep -n "embedding" AGENTS.md` / `HANDOFF.md` 命中更新處。
- log 存 `.omo/evidence/task-3-m2-docs.log`。

## Todo 4: 全套件 + 真實 smoke（real API，不需新增 key）

**What to do / Must NOT do:**
- (a) `uv run python -m unittest discover -s tests -v` 全綠。
- (b) `curl http://localhost:3000/api/public/health` 紀錄 Langfuse 狀態（若 down 不阻擋，記錄即可）。
- (c) dry-run（不需 key）：`uv run python -m literature_review.pipeline data/papers "literature review agent" --top-k 3 --dry-run` → 確認輸出為 embedding rationale。
- (d) 真實 run（需 key，用 `--api-key-suffix 2` 走 `GEMINI_API_KEY_2`）：`uv run python -m literature_review.pipeline data/papers "literature review agent" --top-k 3 --model gemini-3.6-flash --api-key-suffix 2`（若非 pipeline 支援 suffix，則用既有 model flag；若 pipeline 不支援 suffix 就沿用 `GEMINI_API_KEY`）。**若 pipeline 不支援 `--api-key-suffix` 或 `--judge-delay`，這是已知、不動此里程碑加入——smoke 用 pipeline 既有的 key 載入路徑即可。**
- (e) 紀錄 smoke 結果：報告產出 + 無 `Pipeline failed` + 無 key 洩漏。
- (f) 驗證 embedding 確實生效：輸出 chunk 的 `rationale`/score 顯示語意檢索(非 matched_terms lexical)。

**Must NOT：** smoke 期間不改 code；不 commit report artifacts；不印 key。

**Evidence of completion:**
- `.omo/evidence/smoke-m2-embedding-adoption.log`（含 PYEXIT、判斷、sanity）。
- 報告 JSON 存在且 parse。

**QA scenarios:**
- happy：dry-run 與真實 run 都用 embedding、報告產出、套件全綠。
- failure：dry-run 卡在模型下載(需確認一次性 ~130MB 下載延遲正常)；failure = 套件紅或報告失敗 → 回 Todo 1/2 修。

---

## Final verification wave（平行，全數 APPROVE 才完成）
- [ ] F1. Plan compliance audit
- [ ] F2. Code quality review
- [ ] F3. Real manual QA
- [ ] F4. Scope fidelity

## Commit strategy
M2 完成、final wave approve 後，由使用者執行（worker 不 git）：

```powershell
git status
git add literature_review/pipeline.py literature_review/coverage.py tests/
git commit -m "feat(retrieval): switch pipeline to embedding retrieval and deprioritize references"
git add AGENTS.md HANDOFF.md README.md
git commit -m "docs: document embedding retrieval adoption milestone"
git status
```

不要 commit `.env`、`data/papers/`、`.omo/evidence/`。

## Success criteria

- `uv run python -m unittest discover -s tests -v` 全綠（既有 + 新增）。
- pipeline 所有檢索呼叫都改用 `retrieve_evidence_embedding`，pipeline 不再呼叫 lexical `retrieve_evidence`。
- dry-run 與 real run 輸出為 embedding 檢索（rationale 顯示語意）。
- coverage.py 有 references 低權重規則（優先權 14 墊底）且不誤傷正文；附錄邏輯保留。
- 文件更新、F1-F4 全 APPROVE。
- 明確記錄「lexical 轉為 legacy，未刪除檔案」與「參考文獻採保守降權重、前端刪除留待未來」。
