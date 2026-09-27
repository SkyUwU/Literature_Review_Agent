# screening-prompt-upgrade — 候選論文 screening prompt 升級（引用數 + 三層分桶 + 全域評判）

## TL;DR (For humans)

使用者與外部 AI 討論後，整理出 M5e screening 的 prompt 與資料格式化改進（歸類印象：三桶選樣→SurveyG 階層、Relevance/Gap Analysis→ScholarGym 回饋迴圈，均未逐一查證原始碼）：

1. **補上引用數**（目前 prompt 只給 title/abstract/year，screening.py:255）。
2. **Query 底下用 Markdown 階層呈現三層分桶**（Category A 奠基 / B 前沿 / C 應用）——目前 `sample_candidates` 把分桶資訊攤平丟棄，prompt 看不到；需讓 A/B/C 標籤保留並傳進 `build_screening_prompt`。
3. **全域預先條件化**：指示 LLM 先瀏覽全部子 query 與類別再統一決策，避免多樣性崩潰／前幾篇吃光 keep。
4. 候選清單放中間、**輸出要求壓最尾**（Last Instruction：`No Markdown, no explanation`），降低 LLM 在 JSON 外夾前言導致 parse 失敗的機率。
5. 空 query 標記 `### Empty retrieval`，讓 LLM 在 `missing_pieces`＋`follow_up_queries` 補盲；未分桶標記 `### Un-bucketed`。

純 code+test+文件里程碑，**不燒 Gemini 額度**（測試全用 fake client）。輸出 contract（`_LlmScreeningOutput`）**完全不動**——`[DOC_n]` 中括號可選＋`_normalize_doc_id`（screening.py:78,96-101）已確保 `[DOC_1]`/`DOC_1` 都解析成功，無 KeyError 風險。

**不會做**：輸出欄位改名（`status`/`covered_aspects` 等外部 AI 命名**不採納**，保留 `priority`/`covered_areas`/`missing_pieces`/`doc_id`）、程式端 follow-up 防呆、任何評分/抽取/下載層變更、真實 run。

---

## Scope

**In**
- `literature_review/screening.py`：
  - 新增 `SampledCandidates(papers: list[RankedPaper], buckets: dict[str, str])` dataclass；`sample_candidates` 改回傳之（`buckets`＝paper_id → `"a"|"b"|"c"`；未分桶 case 為空 dict）。
  - `build_screening_prompt` 重寫為新英文 prompt（全文見 Todo 2）；多 `main_query: str | None = None`（None 時省略 `## Research topic` 段）。
  - `screen_candidates` 加 `main_query: str | None = None`；`_doc_id_map` / `_resolve_output` / `_build_screening_repair_prompt` 型別同步吃 `dict[str, SampledCandidates]`。
  - repair prompt 的候選行補引用數（一致性）。
  - `__all__` 加 `SampledCandidates`。
- `literature_review/main.py`：`screen_candidates` 兩處呼叫（:344、:370）傳 `main_query=query`；`query_candidates` / `follow_up_candidates` 型別註記（:330、:349）更新。
- 測試：
  - `tests/test_screening.py`：既有 3 個 bucket 測試改讀 `sampled.papers`＋新增 buckets 斷言；既有 `screen_candidates`/`build_screening_prompt` 呼叫（:142,192,210,217,235,241,257,278）改包 `SampledCandidates`；新增 prompt 新格式測試（引用/N/A、三層標題與分組、未分桶、檢索為空、DOC 全域連續、輸出要求壓尾、全域審視文字）。
  - `tests/test_main.py`：`FakeScreenClient` regex（:122）改新版 bullet 格式。
- 文件：AGENTS.md（M5e screening 敘述一行微調）、HANDOFF.md（里程碑）、`.omo/STATE.md`（里程碑表＋測試數，執行完成後同步）。

**Out**
- 輸出 contract / Pydantic schema 改名或加欄位
- 程式端 follow-up 防呆（`fu.query in planned` skip）——記 HANDOFF 當候選，不在本里程碑
- 引用數上限、跨 query 比較邏輯、任何 provider 行為
- Gemini / Langfuse 相關驗證（F4 仍擱置）

---

## Verification strategy

每 Todo＝改檔＋對應測試；最終完整 suite 全綠（449 為底線、以實測為準）、`git status --short` 只含預期檔、無 key 洩漏。額外做一次「sample prompt 傾印」：無網路、用小腳本把內建 prompt 印到 `.omo/evidence/screening-prompt-sample-<todo>.log`，供人工檢查結構與順序。

---

## Execution strategy

```
Todo 0（預檢）→ Todo 1（SampledCandidates + sample_candidates）→ Todo 2（prompt 重寫 + screen_candidates）
→ Todo 3（main.py 接線）→ Todo 4（測試更新+新增）→ Todo 5（文件同步 + 最終驗收 + commit 指令）
```

**中間狀態**：Todo 1-3 改 API 形狀後，`test_screening.py` / `test_main.py` 的既有 callers 尚未更新，**suite 會紅到 Todo 4 才回綠**——這是預期的過渡，勿在中途判定失敗。**Rollback**：變更僅限 `screening.py`、`main.py`，`git checkout -- <那兩檔> tests/` 即可完整復原；無遷移資料或 schema 變更。

---

## Todos（evidence log 存 `.omo/evidence/screening-prompt-<todo>.log`，gitignored）

- [ ] 0. **預檢** — 確立執行前底線

  **References**
  - `.omo/STATE.md:8`（449 tests 全綠）
  - `git status --short`（預期乾淨；`feature/plan-doc` 已同步 origin）

  **Implementation**
  1. `git status --short` 記錄底線，確認只有先前文件變更（AGENTS/HANDOFF/README/.omo/architecture.md，未 commit）或乾淨。
  2. 跑完整 suite 至 `.omo/evidence/screening-prompt-todo0.log`，記錄基準測試數。

  **Acceptance**
  - 底線記錄完整；測試數以實測為準（預期 449）。

- [ ] 1. **`SampledCandidates` dataclass + `sample_candidates` 回傳改造**

  **References**
  - `screening.py:24-33` `__all__`；`:36-39` 常數（`DEFAULT_PER_QUERY_TARGET=24`、bucket ratios）
  - `screening.py:166-210` `sample_candidates`（unbucketed 早退 :185-186；`seen` 去重 :203-209）
  - `tests/test_screening.py:112-137` BucketSamplingTests（:114/:120/:135 直接迭代回傳值）

  **Implementation**
  1. `from dataclasses import dataclass, field`；新增
     ```python
     @dataclass
     class SampledCandidates:
         """Bucket-sampled candidates plus the paper_id -> a/b/c label map."""
         papers: list[RankedPaper]
         buckets: dict[str, str] = field(default_factory=dict)
     ```
  2. `sample_candidates(...) -> SampledCandidates`：
     - unbucketed（`len <= per_query_target`，:185-186）：`SampledCandidates(papers=list(ranked_papers), buckets={})`。
     - bucketed：沿用現有 A→B→C 順序＋`seen` 去重邏輯，回傳時 `buckets[item.paper.paper_id] = name`（每篇只記「實際收走它的桶」）。
  3. `__all__` 加 `"SampledCandidates"`。
  4. 不改 `_doc_id_map` 本體（它吃 list），Todo 2 才一起改型別。

  **Acceptance**
  - BucketSamplingTests 三處改 `sampled.papers` 後全綠；新增斷言：回傳物件有 `.papers`/`.buckets`；large pool 每個 paper 在 pools 都有對應 bucket label（a/b/c）；small pool `buckets == {}`。

  **QA**
  - happy：小池→空 buckets；大池→每篇唯一 label。failure：`buckets` 與 `papers` 數量/paper_id 完全對齊。已知限制：分桶僅在 `len > 24` 時發生（`DEFAULT_PER_QUERY_TARGET=24`），這是既有語意、非本次變更。

- [ ] 2. **`build_screening_prompt` 重寫 + `screen_candidates` 接 `main_query`**

  **References**
  - `screening.py:213-256` 現行 prompt（candidate 列示 :255）；`:242-244` 目前「Return JSON」在候選前（本次移到最尾）
  - `screening.py:104-118` `_doc_id_map`（全域 DOC 編號須保持）
  - `screening.py:259-279` repair prompt（:267 候選行）
  - `screening.py:341-375` `screen_candidates`（prompt :356、schema :357、repair :365-366）
  - `main.py:344` / `:370` 兩處 `screen_candidates` 呼叫（main_query 來源＝`run_end_to_end` 的 `query`）

  **Implementation**
  1. 型別：`build_screening_prompt(query_candidates: dict[str, SampledCandidates], main_query: str | None = None) -> str`；`screen_candidates(..., main_query: str | None = None, parse=None)`；`_doc_id_map`/`_resolve_output`/`_build_screening_repair_prompt` 改用 `dict[str, SampledCandidates]`（取值處改用 `.papers`，判空用 `.papers`）。
  2. prompt 全文＝下列**定稿**（英文、零中文；候選在中、輸出要求壓尾）。`build_screening_prompt` 依此組裝，靜態文字逐字複製、不得改詞——prompt 本文即權威，傾印 log 僅供對照。

     ```text
     You are a senior scholarly review assistant. Your task is to evaluate the
     candidate papers retrieved for the user's research topic, select the core papers
     of substantial reference value, and analyze the information gaps in the current
     literature pool.
     ```
     - `## Research topic`
     - `{research_idea}`（=`main_query`；`None` 時整段省略）
     - `## Rating guidance and principles`：

     ```text
     1. **Global review first**: Before making any decisions, browse ALL sub-queries
        and ALL candidate categories to establish a complete picture of the literature
        landscape, then evaluate as a whole. Balance representative papers across
        sub-topics so every sub-direction is represented, and avoid biasing decisions
        toward a single sub-direction (do not rush to exhaust your keep decisions on
        the first papers you see).
     2. **Layered judgment**:
        - If a sub-query displays the `Category A/B/C` headers:
          * Category A (Foundational): value its role as a core starting point or
            classic baseline of the field; do not reject it simply because it was
            published earlier.
          * Category B (Frontier): value whether it proposes an innovative
            architecture, mechanism, or latest breakthrough.
          * Category C (Applied): value whether it provides cross-domain integration
            or a novel application context.
        - If a sub-query is marked `Un-bucketed` or has few candidates:
          * evaluate directly whether the paper provides substantive methodological or
            experimental support for the overall research topic.
        - If a sub-query is marked `Empty retrieval`:
          * record this blind spot under `missing_pieces` and propose a precise
            follow-up search in `follow_up_queries`.
     3. **Decision labels**:
        - "keep": highly relevant to the research topic, or of key representativeness;
          it should be downloaded now.
        - "maybe": uncertain value — keep it as a buffer in case the keep set is too
          small to fill the review.
        - "reject": off-topic, redundant, or low-quality; reject it explicitly.
     4. Base each decision on the paper's title, abstract, year, and citation count
        only. Every candidate must receive exactly one decision; make decisions only
        about the papers listed below — never output a document id not shown above.
     ```
     - `## Candidate papers` 段：`The candidate papers below are organized by (1) the retrieval sub-query that found them and (2) their scholarly role (Category A foundational / Category B frontier / Category C applied).`
     - 逐 query：`## Query {n}: {subquery}`；桶標題（只印有人的桶）：
       `### Category A: Foundational (High-Impact / Baseline)` / `### Category B: Frontier (Recent Frontier)` / `### Category C: Applied (Domain / Integration)`
       - 每篇 bullet：`- [DOC_n] "{title}" ({year}, citations: {citations})`＋下行`  Abstract: {abstract}`；`year=None`→`n/a`、`citation_count=None`→`N/A`；title/abstract 先 `" ".join(...split())`。
       - 有論文但 `buckets=={}`：`### Un-bucketed` 下單一 bullet 清單。
       - `.papers` 為空：`### Empty retrieval`＋`No candidates were found for this sub-query; note this blind spot in \`missing_pieces\` and propose a follow-up search in \`follow_up_queries\`.`
       - DOC 編號跨 query、跨桶、跨空 query **全域連續**（沿用 counter 語意，`DOC_n` 以 `f"DOC_{counter}"` 產生）。
     - `## Output`（**定稿，唯一壓尾段**）：

     ```text
     Return exactly one JSON object matching the provided schema. In every decision
     set "doc_id" to the candidate's exact document id as listed above (e.g.
     "[DOC_1]", copy it word for word).

     The output has two parts:

     1. Per-paper decisions (`decisions`): exactly one entry per candidate, each with
        - `doc_id`: the document id shown above, copied verbatim;
        - `priority`: "keep", "maybe", or "reject";
        - `reason`: a brief scholarly justification, 1-2 sentences.

     2. Global information-gap reflection (the feedback loop): honestly state what the
        current pool already covers and what is still missing, as a roadmap for the
        next search round:
        - `covered_areas`: what the selected papers already cover well;
        - `missing_pieces`: the key aspects or evidence still absent to fully support
          the research topic;
        - `follow_up_queries`: 1-3 precise search queries that would fill the gaps
          (must target the missing aspects and avoid repeating the initial sub-queries
          above; empty list [] if coverage is sufficient).

     Form example:
     {
       "decisions": [
         {"doc_id": "[DOC_1]", "priority": "keep", "reason": "..."},
         {"doc_id": "[DOC_2]", "priority": "maybe", "reason": "..."}
       ],
       "covered_areas": ["..."],
       "missing_pieces": ["..."],
       "follow_up_queries": ["..."]
     }

     No Markdown, no explanation, no preamble. Your reply must be the JSON object
     itself and nothing else.
     ```
  3. `_build_screening_repair_prompt` 候選行改含引用數：`- [doc_id] ({year}, citations: {citations}) {title}`（None 同上處理）。
  4. 傾印范本：跑一次 `build_screening_prompt`（單 query、雙桶＋空 query）存 `.omo/evidence/screening-prompt-sample-todo2.log`。

  **Acceptance**
  - prompt 輸出順序：`## Research topic`→guidance→`## Candidate papers`（含 query/桶）→`## Output`（末段含 No Markdown）；`No Markdown` 字串位置在最後一個 query 區塊之後。
  - 引用數與 None 兜底、三種分支、DOC 全域連續皆正確；全程英文（可 `grep -P '[\x{4e00}-\x{9fff}]'` 檢查零中文）。

  **QA**
  - happy：main_query=None 不印 `## Research topic`；main_query 有值則印。failure：空 query 不會進「決策必須含該 query 的 doc_id」檢查（`_doc_id_map` 本就只收有 items 的 query，contract 不變）；repair prompt 仍適用原 parse。已知限制：分桶標題與 SurveyG 對應是使用者印象級歸類，非原始碼查證。

- [ ] 3. **main.py 接線**

  **References**
  - `main.py:57` screening import 行
  - `main.py:330-344`（`query_candidates` 型別 + 首次 `screen_candidates`）
  - `main.py:349-370`（`follow_up_candidates` 型別 + 二次 `screen_candidates`）

  **Implementation**
  1. import 加 `SampledCandidates`（僅型別註記用）。
  2. `query_candidates: dict[str, SampledCandidates]`、`follow_up_candidates: dict[str, SampledCandidates]`。
  3. `screen_candidates(query_candidates, client_screen, main_query=query)`、`screen_candidates(follow_up_candidates, client_screen, main_query=query)`。

  **Acceptance**
  - main() 測試（fake clients）仍跑通；無其他 `screen_candidates` 呼叫點遺漏。

- [ ] 4. **測試更新 + 新增**

  **References**
  - `tests/test_screening.py:18-23` imports；`:112-137` BucketSamplingTests；`:140-290` ScreeningContractTests + prompt test
  - `tests/test_main.py:113-143` `FakeScreenClient`（regex :122）

  **Implementation**
  1. `test_screening.py`：
     - import `SampledCandidates`；加 helper `_sampled(*items: RankedPaper) -> SampledCandidates`（`buckets={}`）。
     - 既有 `candidates = {q: [...list...]}` 各處（:142,192,210,217,235,257,278）改 `_sampled(...)`；BucketSamplingTests 三處改 `.papers`＋新增 buckets 斷言（Todo 1）。
     - 重寫 `test_build_prompt_lists_doc_ids_and_hides_rank_and_score`（:241-254）對齊新格式（`- [DOC_1] "..."`、citation、`## Research topic`、`doc_id`、no rank/score、禁發明 id 句改新版措辭）。
     - 新增：
       - 引用/年份渲染（含 `None→N/A`/`n/a`）
       - 三層標題＋正確分組（一桶只有該桶論文）
       - unbucketed `### Un-bucketed` 標記
       - empty retrieval `### Empty retrieval`＋補盲句
       - DOC 編號跨 query/桶/空 query 連續
       - `No Markdown` 在最後一個 `## Query` 之後（輸出要求壓尾）＋ `## Research topic` 段存在
       - `main_query=None` 省略 Research topic 段
  2. `test_main.py`：`FakeScreenClient.generate_json` 的 regex（:122）改 `re.findall(r"- \[(DOC_\d+)\]", prompt)`（fake 只取 doc_id 用；title 不再需要）。`MaybeFirstScreenClient` 不改。
  3. 完整 suite 至 `.omo/evidence/screening-prompt-todo4.log`，記錄新測試數。

  **Acceptance**
  - 新增測試逐項覆蓋上述 QA 情境；改動處全綠；suite 全綠且測試數高於 449（以實測為準）。

- [ ] 5. **文件同步 + 最終驗收 + commit 指令**

  **References**
  - `AGENTS.md`「Current architecture」M5e screening 敘述
  - `HANDOFF.md` 里程碑表 + 最近 milestone 節
  - `STATE.md` 現況快照 + 里程碑表（:8 測試數、里程碑表尾）
  - `README.md`（若 screening 已有描述則一併微調；無則不動）

  **Implementation**
  1. AGENTS.md：screening 敘述一句補「prompt 含引用數/年份＋三層分桶標記＋全域評判準則」。
  2. HANDOFF.md：里程碑表加列（screening-prompt-upgrade）+ 摘要。
  3. STATE.md：里程碑表加列、測試數更新（Todo 4 實測值）。
  4. 最終驗收：完整 suite（`.omo/evidence/screening-prompt-final.log`）、傾印 log 檢查零中文、`git status --short`、`git grep -n` 無 key 洩漏。

  **Acceptance**
  - suite 全綠；git status 只含預期檔；無 key 洩漏；commit 指令一次列齊。

---

## Commit strategy（使用者親做）

```bash
# code+tests 一筆
git add literature_review/screening.py literature_review/main.py tests/test_screening.py tests/test_main.py
git commit -m "feat(screening): citation + category-tiered prompt with global gap analysis (ScholarGym/SurveyG-inspired)"
# docs 一筆
git add AGENTS.md HANDOFF.md .omo/STATE.md .omo/plans/screening-prompt-upgrade.md
git commit -m "docs: sync AGENTS/HANDOFF/STATE with screening prompt upgrade milestone"
git push
```
（前次 AGENTS/HANDOFF/README/.omo/architecture.md 未 commit 的文件變更，視使用者意願併入 docs 筆或另行 commit。）

---

## Success criteria

- `sample_candidates` 回傳 `SampledCandidates`（papers＋buckets），未分桶（≤24）`buckets={}`；任何既有呼叫點語意零回歸。
- Prompt 全程英文：引用數＋年份（None→N/A/n/a）顯示於每篇；Query 下三層 `### Category A/B/C`（僅有人的桶）；未分桶標 `### Un-bucketed`；空 query 標 `### Empty retrieval`＋補盲指示；DOC 編號全域連續；輸出要求（含 No Markdown）為最後一段。
- 輸出 contract 不動：`_LlmScreeningOutput` schema、`priority/covered_areas/missing_pieces/follow_up_queries/doc_id`、`_normalize_doc_id` 反解、一次 repair 預算全保留。
- `screen_candidates`/`build_screening_prompt` 接受 `main_query`（None 可省略 Research topic）；main.py 兩處正式呼叫傳原始 query。
- 449 起全套 tests 全綠（新增數以實測為準）；AGENTS/HANDOFF/STATE 同步；無 key 洩漏；不燒 Gemini 額度。
- 已知限制：分桶→SurveyG / gap 迴圈→ScholarGym 的歸類屬使用者印象、未逐一查證原始碼。