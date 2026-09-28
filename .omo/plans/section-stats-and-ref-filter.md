# section-stats-and-ref-filter — 抽樣 SD 統計 + references 標題補漏 + 章節兩層正規化

## TL;DR (For humans)

使用者駆動的兩件事綁成里程碑 2（2026-09-28 多輪討論定案）：

1. **SD（Section Distribution）統計**：真實 synthesis run 抽樣後印「逐篇一行＋Aggregate 總表」，用來量測「抽樣 chunk 到底落在哪些頂層章節、是否被 Introduction/Abstract 壟斷」——這是**里程碑 3（section-aware sampling B）開不開工的 empirical gate**。純 Python、零 LLM 額度、不進 `PapersOutput.run`。
2. **references 標題補漏**：`_classify_heading` 目前只能命中 `References`/`Bibliography`，單數 `Reference`、`Reference List`、`References Cited`、`Literature Cited`、`Works Cited` 全部 miss。**（執行前查證第 2 輪修正）** 新 `_REFERENCES_HEADING_REGEX` 的別名集必須用 `references?` 涵蓋**單＋複數**，把既有 `References`/`Bibliography` 也保留——若只寫 `reference`（單數），規則是認不出「References」的（`\b` 在 e/s 之間不成立），現有 test_synthesis.py:949 與 test_functional.py:461/541 會直接變紅。
3. **層 1 / 層 2 章節正規化**：先在共用 `_SECTION_HEADERS` 補三個**安全**複數洞（`Conclusions`/`Evaluations`/`Methodologies`→現況 miss）；再新增「層 2 別名表」把 `approach`/`proposed framework`/`experimental setup` 等摺合到 family（method/experiments/results/evaluation）。**表外真章節「保名成桶」**（如 `Datasets`，使用者已批准），只有 `top_level_section=None` 進 `other`。
4. **key-leak canonical（2026-09-28 併入，使用者定案）**：Gemini key 新增 `AQ.Ab8` 開頭前綴（比 `AIza` 長，使用者確認皆大寫、有點號）。本里程碑把 key 洩漏檢查升級為 AGENTS.md 的單一 canonical 正則；歷史計畫檔保留當時驗收原文不動（方案 A）。

**兩側語義分割（使用者定案）**：機器側（SD 統計、未來 B 分群）用正規名；LLM 側**不動**——`chunk.section` 原始路徑照送（functional.py:64-67、`build_functional_prompt`、逐篇筆記）。**逐篇筆記本里程碑不動**（`_group_chunks_by_section` 維持 raw top-level 鍵），「筆記 aspect 正規化」列為未來獨立里程碑。

**不會做（本里程碑）**：B 實作（gated，只在設計記錄段）、`PapersOutput.run` 加欄、改 `functional.py`／prompt／筆記、`top_level_section` 行為、任何 LLM 面、F4 真實 run（使用者另行執行）。

里程碑順序定案：**里程碑 1（planner-prompt-tightening）＋本里程碑都在 F4 之前落地**；F4 真實 run 量 SD baseline 後再決定 B。

---

## Scope

**In**
- `literature_review/coverage.py`：
  - 層 1 補洞：`:15` `method(?:ology|ogies|s)?\b`、`:14` `evaluation(?:s)?\b`、`:18` `conclusion(?:s)?\b`（安全 super-set，無新誤判面）。
  - 新增 `_REFERENCES_HEADING_REGEX`（只走 heading path；別名集用 `references?` 涵蓋單＋複數：既有 `References` ／`Bibliography` ＋新增 `Reference`／`Reference List`／`References Cited`／`Literature Cited`／`Works Cited`）；`_classify_heading`（:71）改用新 regex；`_REFERENCES_REGEX`（:31）**保留**為 legacy 文字掃描（`_has_references_header` :94-100、`_NOISE_CLASSES` 判定不變）。
- `literature_review/section_stats.py`（**新模組**，SD 與 B 共用單一來源）：
  - `_SECTION_FAMILY_ALIASES`：`approach`／`our approach`／`proposed method`／`proposed approach`／`proposed framework`→`method`；`experimental setup`→`experiments`；`experimental results`→`results`；`empirical evaluation`→`evaluation`。
  - `canonical_section_name(top_heading: str | None) -> str`：None/空白→`other`；層 1（`coverage._classify_heading`）命中→層 2 別名摺合或保表名；層 1 無命中→先查層 2 原始別名，再無→**保名成桶**（清洗後原標題）；層 1 為 `references`/`appendix`→`other`（抽樣前已被 `drop_noise_sections` 移走，僅作防漏）。
  - `section_distribution(sampled: dict[str, list[EvidenceChunk]]) -> dict[str, dict[str, int]]`：逐篇 {canonical name: count}；top-level 用 `synthesis.top_level_section(chunk.section, paper_title)`。
  - `render_section_distribution(distribution, paper_titles=None) -> str`：逐篇一行＋Aggregate 總表（bucket／chunk 數／涵蓋篇數／總和），緊湊 ASCII。
  - `print_section_distribution(...)`：stdout。
- `literature_review/pipeline.py`：`run_synthesis_pipeline` 加 `print_section_distribution: bool = True`；`:224`（`sampled_flat` 計算後）印 SD（空 dict 則跳過）。此參數是**印表開關**，不是資料契約。
- `tests/test_synthesis.py`（classify 測試現址 :939-1021）：補複數與 heading-path references 案例。
- `tests/test_section_stats.py`（**新檔**）：canonical 對照表、keep-name、None/空白、別名摺合、distribution＋render 形狀、`redirect_stdout` 捕 stdout。
- 文件：AGENTS.md（SD 統計一句＋層 1/層 2 段）、`HANDOFF.md`（里程碑列＋Follow-up 節：B gate、synthesis.py:615 deferred、筆記正規化）、`.omo/STATE.md`。
- 文件（key-leak canonical，併入本里程碑，2026-09-28 使用者定案方案 A）：
  - `AGENTS.md`：新增「Key-leak check」一節，定義單一 canonical 正則 →
    `git grep -nE "AIza[A-Za-z0-9_-]{20,}|AQ\.[A-Za-z0-9_-]{20,}"`
  - `HANDOFF.md:161`：key 洩漏描述改引用該 canonical 正則。
  - `planner-prompt-tightening.md`：加一行註記「驗收當時用 `AIza` check；現行 canonical 見 AGENTS.md」。

**Out**
- 里程碑 3（B section-aware sampling）**實作**（見下方「Deferred / Gated」）。
- `PapersOutput.run`（models.py:74-78 / main.py:558-604）**不動**——SD 數據只印 stdout，事後由 agent 撈 log 存 `.omo/evidence/`。
- `functional.py` `sample_top_chunks_per_paper` ／ `build_functional_prompt`（:47-101 尤其 :64-67）**不改**；樹 LLM 面（screening/functional/notes/report prompt）**不改**。
- `top_level_section`／`merge_numbered_section` 行為、`_REFERENCES_REGEX` 本身皆**不動**。
- F4 真實 run（使用者執行；本里程碑只備好量測面）。
- 層 2 別名表的擴充提案（除既定 8 條外，一律下次討論）＆ 顯示名（display name）設計。
- 歷史已完成計畫檔（m3c-main-entry／m5c-doc-wrapup／k-retrieval-improvements／k2-topk-32／k3-per-paper-cap／m4-rcs-scoring／screening-prompt-upgrade 等）的 `AIza` 描述**一律不動**——保留當時驗收紀錄（方案 A）。

---

## Verification strategy

每 Todo＝改檔＋對應測試；最終完整 suite 全綠（以實測為準）、`git status --short` 只含預期檔、零 LLM 開銷與零新相依；`coverage.py` 變更對 `drop_noise_sections`／`_region_priority`／`build_coverage_packs` 的既有行為以既有測試（test_synthesis.py CoveragePackTests :179-438、NOISE/classify :939-1021）回歸把關。**過渡紅**：Todo 1 動共用表後、Todo 3 接完才整套回綠——勿中途判敗。

---

## Execution strategy

```
Todo 0（預檢）→ Todo 1（coverage.py 層1＋references heading regex＋測試）
→ Todo 2（section_stats.py 模組＋測試）→ Todo 3（pipeline 印表＋測試）
→ Todo 4（文件＋最終驗收＋commit）
```

**Rollback**：變更限 `tests/test_synthesis.py`、`tests/test_section_stats.py`、`literature_review/coverage.py`、`literature_review/section_stats.py`、`literature_review/pipeline.py`，`git checkout -- <列表>` 即完整復原。

---

## Todos

- [ ] 0. **預檢**

  **References**
  - `.omo/STATE.md:8`（測試數以實測為準，里程碑 1 落地後為 462；Todo 0 記錄實際基準）
  - 里程碑 1（planner-prompt-tightening.md）**先落地**：本計畫內容假設里程碑 1 已 commit（sequencing 定案）。

  **Implementation**
  1. 跑完整 suite → `.omo/evidence/section-stats-todo0.log`（記錄基準測試數）。
  2. Grep 確認無測試斷言「複數標題 → other」或「Reference List → other」的舊行為（`rg -n "Conclusions|References Cited|Methodologies|Evaluations|Literature Cited|Works Cited|Reference List" tests/`）。

  **Acceptance**
  - 基準記錄；無衝突測試斷言。

- [ ] 1. **coverage.py：層 1 補洞 + references heading 補漏（+ 測試）**

  > **執行偏離（2026-09-29）**：`method` regex 實際採用 `(?:ology|ologies|ogies|s)?`——計畫原寫 `(?:ology|ogies|s)?` 漏了 `ologies` 會 miss 掉「Methodologies」（method+ologies 是 ologies 並非 ogies 分支）。此為計畫改 code 同步。

  **References**
  - `coverage.py:8-23` `_SECTION_HEADERS`（:14 evaluation、:15 method、:18 conclusion）
  - `coverage.py:31` `_REFERENCES_REGEX`（保留）、`:32` `_REFERENCES_PRIORITY`
  - `coverage.py:66-75` `_classify_heading`（:71-72 references 分支）
  - `coverage.py:94-100` `_has_references_header`（legacy 文字掃描，不動）
  - `tests/test_synthesis.py:939-1021` classify/noise 測試

  **Implementation**
  1. `_SECTION_HEADERS` 三行改 regex：
     ```python
     ("evaluation", 6, re.compile(r"(?i)^[0-9]*\.?\s*evaluation(?:s)?\b")),
     ("method", 7, re.compile(r"(?i)^[0-9]*\.?\s*method(?:ology|ologies|ogies|s)?\b")),
     ("conclusion", 10, re.compile(r"(?i)^[0-9]*\.?\s*conclusion(?:s)?\b")),
     ```
  2. `:31` 旁新增（**修正版**，`references?` 涵蓋單＋複數，保留既有 `References`/`Bibliography`）：
     ```python
     _REFERENCES_HEADING_REGEX = re.compile(
         r"(?i)^[0-9]*\.?\s*(?:references?|references?\s+list|references?\s+cited|"
         r"literature\s+cited|works\s+cited|bibliography)\b"
     )
     ```
  3. `_classify_heading`：`:71` 改用 `_REFERENCES_HEADING_REGEX`（heading path）；`_has_references_header` 維持 `_REFERENCES_REGEX`（legacy 文字掃描，避免誤殺正文「Reference」字串）。docstring 補一句「heading 用標題別名集、文字掃描保留舊 regex」。
  4. 註解：`_SECTION_HEADERS` 加一行「層 1 共用表；層 2 別名摺合在 section_stats.py」。
  5. 測試（test_synthesis.py，classify 測試區加案例）：
     - `classify_chunk("... Conclusions")` → `(10, "conclusion")`；`"Evaluations"` → `(6, "evaluation")`；`"Methodologies"` → `(7, "method")`。
     - `_classify_heading`/`classify_chunk`：`"Reference"`、`"Reference List"`、`"References Cited"`、`"Literature Cited"`、`"Works Cited"` → `(14, "references")`。
     - 既有回歸（不可變紅）：`"References"`（test_synthesis.py:949）與 `"Bibliography"`（:950）仍 → `(14, "references")`——修正版 regex 以 `references?` 保留複數命中。
     - 文字掃描回歸：`_has_references_header("... references ...")` 與 `_has_references_header("... Reference List ...")` 行為不變（既有案例綠）。

  **Acceptance**
  - 新案例逐項綠；既有 classify/NOISE/CoveragePack 測試全綠；`drop_noise_sections` 對這些新增 references heading 也能正確掉（`References Cited` 章的 chunk 被 drop）。

  **QA**
  - happy：複數與標題別名全命中。failure：`evaluation(?:s)?` 不可誤抓 `evidence`（`(?:s)?\b` 後 `\b` 邊界 + 錨點已設）；確認 `rg` 無舊行為斷言。已知限制：這是**行為變更**（「Conclusions」原為 other，現為 conclusion）——單數路徑全不變。

- [ ] 2. **section_stats.py：層 2 別名 + canonical + distribution + render（+ 測試）**

  **References**
  - `coverage.py:66-75` `_classify_heading`（層 1 來源；同套件私有存取可接受）
  - `synthesis.py:429-453` `top_level_section`（層級無關、回傳 raw 首段或 None）
  - `models.py:170-182` `EvidenceChunk`（`:182` section 欄位）

  **Implementation**
  1. 新檔 `literature_review/section_stats.py`：
     ```python
     """Machine-side section canonicalization and sampled-chunk distribution stats (SD).

     Bilingual single source for the SD printable report and the gated B sampling
     design. This is a read-only machine surface: the LLM side keeps the raw
     ``chunk.section`` path unchanged (see functional._section_wrapped_text)."""

     _SECTION_FAMILY_ALIASES: dict[str, str] = {
         "approach": "method",
         "our approach": "method",
         "proposed method": "method",
         "proposed approach": "method",
         "proposed framework": "method",
         "experimental setup": "experiments",
         "experimental results": "results",
         "empirical evaluation": "evaluation",
     }

     def canonical_section_name(top_heading: str | None) -> str:
         # None/empty -> "other" (preamble/headerless/legacy chunks)
         # layer-1: coverage._classify_heading(heading)[1]
         #   - known table name -> layer-2 fold by table name, else keep name
         #   - "other"/"references"/"appendix" -> layer-2 alias on normalized raw
         #     heading; no alias -> keep cleaned raw heading (保名成桶);
         #     "references"/"appendix" -> "other" (post-drop, should not occur)
     ```
     （與「B gate」段共用 `canonical_section_name`。）
  2. `section_distribution(sampled, paper_titles=None)`：對每個 `chunk`取 `top_level_section(chunk.section, paper_titles.get(chunk.paper_id))` → `canonical_section_name` → 計數；回傳逐篇 dict；空/none 安全。
  3. `render_section_distribution(...) -> str`：
     - 逐篇一行：`{paper_id}: {section}:{n} {section}:{n} ...`（計數 0 的 section 不出現；none 只剩其他）
     - Aggregate 總表：
       ```text
       Section distribution across {total} sampled chunks (top-level, canonicalized)
         method        6  (papers: 3)
         results       4  (papers: 2)
         introduction  2  (papers: 2)
         datasets      1  (papers: 1)
         other         1  (papers: 1)
       ```
     - `print_section_distribution(sampled, paper_titles=None)`：`print(render(…))`。
  4. 新測試 `tests/test_section_stats.py`（`unittest`，貼合命名規範）：
     - canonical：`"Introduction"→introduction`；`"Conclusion"→conclusion`、`"Conclusions"→conclusion`；`"Method"/"Methods"/"Methodology"/"Methodologies"→method`；`"Evaluation"/"Evaluations"→evaluation`。
     - 層 2：`"Approach"`/`"Our Approach"`/`"Proposed Method"`/`"Proposed Approach"`/`"Proposed Framework"`→`method`；`"Experimental Setup"→experiments`；`"Experimental Results"→results`；`"Empirical Evaluation"→evaluation`。
     - 保名成桶：`"Datasets"→datasets`、`"Case Studies"→case studies`（清洗 lower＋strip 後原字面）。**前導編號保留**：`"12 Case Studies"→"12 case studies"`（`top_level_section` 回 raw 含編號、clean 只做 lower＋strip，**不刪數字**——以實測輸出定錨）。
     - None/`""`→`other`；`references`/`appendix` 頂層→`other`。
     - distribution：兩篇各計數正確；render 含逐篇行＋Aggregate 總表＋總和；`redirect_stdout` 捕到 `print_section_distribution` 輸出。

  **Acceptance**
  - new 測試全綠；`render` 形狀固定；`canonical_section_name` 為其中單一來源（供 Todo 3 與未來 B 引用）。

  **QA**
  - happy：表內/層 2/保名/None 四類全覆蓋；保名成桶含`"12 Case Studies"→"12 case studies"`（前導編號保留、實測定錨）。failure：`data "coverage._classify_heading"` 一口咬；`top_level_section` 對 headerless 回 None→`other`。已知限制：層 2 是封閉表（僅 8 條既定），未列別名一律保名成桶——使用者已批准此行為；擴充別名下次討論、且需驗證不影響 `drop_noise_sections` 路徑。

- [ ] 3. **pipeline 接線：SD 印表 + 開關 + 測試**

  **References**
  - `pipeline.py:205-224`（`effective_functional_policy`→`sampled_flat` 區段）
  - `pipeline.py:216-223` `sample_top_chunks_per_paper` 呼叫

  **Implementation**
  1. `run_synthesis_pipeline` 簽名加 `print_section_distribution: bool = True`（docstring 一句：真實 synthesis run 抽樣後印 SD，純 stdout、非資料契約）。
  2. `:224` 之後、`score_chunks_functionally`（:225）之前：
     ```python
     if print_section_distribution and sampled:
         print_section_distribution(sampled, paper_titles=paper_titles)
     ```
  3. 測試（test_pipeline.py）：`redirect_stdout` 捕 `run_synthesis_pipeline`（既有 fake 裝配）→ 含 `Section distribution across`；傳 `print_section_distribution=False` → stdout 空、行為與既有測試一致。

  **Acceptance**
  - `sampled` 非空時印表、空時不印；False 完全關閉；既有 pipeline 測試全綠（若個別測試要求 stdout 安靜，該測試傳 False，agent-code 依實情註記）。

  **QA**
  - happy：真實 run（`pipeline.py main()` 與 `main.py`）都會印；CLI `main` 呼叫未傳 flag 亦得益。failure：score 前印表不影響 `functional_assessments`（唯見批次順序）。已知限制：SD 輸出為人類可讀表，非 machine contract（`PapersOutput.run` 本里程碑不加）。

- [ ] 4. **文件同步 + 最終驗收 + commit 指令**

  **References**
  - `AGENTS.md`「Current architecture」／「Terminology」段
  - `HANDOFF.md` 里程碑表＋Follow-up 節（:161 Key split 段為 key-leak 描述現址）
  - `.omo/STATE.md` 現況快照＋里程碑表
  - `planner-prompt-tightening.md`（key-leak canonical 一行註記落點）

  **Implementation**
  1. AGENTS.md：「Current architecture」補一句「SD 統計（top-level canonical 兩層）印在 synthesis run stdout、`.omo/evidence/` 留存」；「Terminology」補 `canonical section family` 一句（method 系：method/approach/proposed framework…）。
  2. HANDOFF.md：里程碑表加列 `Section stats + references heading fix`；Follow-up 節記三項——**(a)** B gate 條件（下方 Deferred 段），**(b)** `synthesis.py:615` claim-count 軟偏差（驗證＝F4 後量 [claim-N] 分佈），**(c)** 筆記 aspect 正規化＋顯示名（未來獨立里程碑）。
  3. STATE.md：快照測試數＋里程碑表加列。
  4. **Key-leak canonical（使用者定案方案 A）**：
     - `AGENTS.md`：新增「Key-leak check」一節，定義單一 canonical 正則 → `git grep -nE "AIza[A-Za-z0-9_-]{20,}|AQ\.[A-Za-z0-9_-]{20,}"`（`AQ\.` 點號＋≥20 字元門檻滅 `Aquaculture`/`AQI` 誤報；測試內 `AIza000` 假 key 佔位保留）。
     - `HANDOFF.md:161`：`grep ...` 描述改引用該 canonical 正則（不查值、只引指令）。
     - `planner-prompt-tightening.md`：加一行註記「驗收當時用 `AIza` check；現行 canonical 見 AGENTS.md」。
  5. 最終驗收：完整 suite → `.omo/evidence/section-stats-final.log`；`git status --short` 只含預期檔；`git grep -nE "AIza[A-Za-z0-9_-]{20,}|AQ\.[A-Za-z0-9_-]{20,}"` 僅測試假 key、無洩漏；手動跑一次 `python -c` canonical 抽樣輸出對照。

  **Acceptance**
  - suite 全綠；git status 合乎預期；canonical 正則下無 key 洩漏；三項 follow-up 已記入 HANDOFF、key-leak check 已入 AGENTS 且 HANDOFF/planner 計畫已引用。

---

## Deferred / Gated（本里程碑不實作，設計記錄＋gate 條件）

**B：section-aware per-paper sampling（已定案設計，待 gate）**
- Gate 條件：F4 真實 run 的 SD 總表顯示抽樣被 Intro/Abstract 壟斷、method/results 家族顯著不足（如 method+results+experiments+evaluation 合計 < 總量約 40%），與使用者一起決定開工。
- 若開工（里程碑 3）：在 `sample_top_chunks_per_paper` 內改採每篇「method 家族 ↔ results 家族」各以**該篇 sub-query**（S2，functional.py:245 沿用）embedding 取最相關一段；兩家族取其 top-2 總預算**不變**；任一家族缺失 → 退回其他章節最高分一段。`canonical_section_name` 為分群單一來源（D1 approach→method、D3 proposed framework→method 自動生效）。
- 不實作 = 可逆、零殘留：本里程碑不碰 `functional.py`。

**已記錄 deferred issue：`synthesis.py:615` claim-count 軟偏差**
- `Cover every section with at least one claim` 迫使邊緣章節產 claim → 每篇 claim 數膨脹 → 報告 input surface 不均（soft bias）。
- 未來選項：①soft 化該規則 ②每篇 claim cap ③依 functional 過濾；F4 後以 report 的 `claim_chunks` 對每篇量 [claim-N] 分佈驗證。

**已記錄 deferred：逐篇筆記 aspect 正規化＋顯示名**
- 筆記維持 raw top-level 分組（`_group_chunks_by_section`，synthesis.py:527-543；`merge_numbered_section` :493-502 只收點號）；為何不共用別名表：Approach 與 Method 在筆記語境是**真實區別**。此為未來獨立里程碑。

---

## Commit strategy（使用者親做）

```bash
# code+tests 一筆
git add literature_review/coverage.py literature_review/section_stats.py literature_review/pipeline.py tests/test_synthesis.py tests/test_section_stats.py
git commit -m "feat(sections): print sampled-chunk section distribution and fix references heading coverage"
# docs 一筆（含 key-leak canonical 與 planner 註記）
git add AGENTS.md HANDOFF.md .omo/STATE.md .omo/plans/section-stats-and-ref-filter.md .omo/plans/planner-prompt-tightening.md
git commit -m "docs: sync AGENTS/HANDOFF/STATE with section stats milestone"
git push
```

---

## Success criteria

- 層 1 補洞後：`Conclusions`/`Evaluations`/`Methodologies` 正確進 conclusion/evaluation/method；既有單數行為零變化。
- heading-path references 全補：`Reference`/`Reference List`/`References Cited`/`Literature Cited`/`Works Cited`→references；legacy 文字掃描（`_has_references_header`）維持舊 `_REFERENCES_REGEX`，正文誤殺風險為零。
- `section_stats.py`：`canonical_section_name` 兩層正規化＋保名成桶＋`other` 語意（None only）逐案例通過；層 2 別名表 8 條固定；為 B 分群與 SD 共用。
- SD 在每個 synthesis run 的 stdout 印出逐篇＋總表；`print_section_distribution=False` 可關；`PapersOutput.run` 未動；功能層偵測語義不變。
- 零 LLM 零新相依；套件 suite 全綠（測試數以實測為準）；AGENTS/HANDOFF/STATE 同步（含 B gate 條件、synthesis.py:615、筆記正規化三項 follow-up）；key-leak canonical 正則（`AIza[A-Za-z0-9_-]{20,}|AQ\.[A-Za-z0-9_-]{20,}`）下無洩漏、檢查已入 AGENTS。
- F4 前置條件成立：里程碑 1＋2 都已 commit，F4 即可量測 SD baseline。