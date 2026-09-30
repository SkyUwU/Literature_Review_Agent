# evidence-cited-synthesis - Work Plan

## TL;DR (For humans)

**What you'll get:** 把 `data/papers/` 資料夾裡的多篇論文 PDF，一路跑成一份「每個事實句都附 chunk ID 引用標記、每條未來方向都附論文編號與段落出處」的文獻綜合報告；每篇 include/consider 論文另有一份章節式讀書筆記（含明示 limitations），未來方向由「多篇收斂的高分證據摘要」與「論文自己承認的限制」兩種可信來源生成。

**Why this approach:** 相關性評估（query-biased top-k）與內容摘要（逐篇覆蓋）是兩種不同的證據需求，分開處理才不會漏掉實驗/結果章節；論文分數採「證據越多越可信」的收縮平均；綜合報告由 LLM 直接成文（行內引用標記＋事後機器驗證），不做多餘的中間層；LLM 每一步都有不需 API 的機械化 fallback，測試全程用假 client。

**What it will NOT do:** 不組裝最終 `LiteratureReviewReport`（等搜尋端合流，下一里程碑）；不做 embedding/GROBID/LLM 規劃搜尋/整篇長上下文模式；不造假論文標題作者（證據路徑沒有此 metadata）；測試不依賴任何 API key；worker 不執行任何改動 git 狀態的指令（add/commit/push 一律回報由使用者執行）。

**Effort:** Medium
**Risk:** Medium - LLM 輸出 grounding 邊界與真實 PDF 章節變異，均以驗證＋deterministic fallback 抑制

**Decisions to sanity-check:** 收縮平均參數 p=3、m=4（寫在政策可調）；讀書筆記對象=include＋consider；CLI 支援資料夾展開；綜合報告直接成文＋行內 `[chunk_id]` 標記驗證（無 claims 中間層）；里程碑工作分支=`feature/plan-doc`，完成定義=推上 GitHub 分支（不合回 main），計畫書本身納入 git 追蹤（方案A）。

Your next move: 審核本計畫後，以 `/start-work evidence-cited-synthesis` 交由 worker 執行。Full execution detail follows below.

---

> TL;DR (machine): Medium/Medium - 6 implementation todos + 4 final verifiers; multi-PDF pipeline, shrinkage aggregation, per-paper notes, direct-report synthesis with inline citation validation, docs+gitignore; single commit on feature/plan-doc; user executes git.

## Scope

### Must have

- `literature_review/models.py` 新增契約：`ChunkReference`、`PaperSummaryClaim`、`PaperSummary`、`LlmNoteClaim`、`LlmPaperSummaryNote`、`LlmSynthesisDirection`、`LlmSynthesisBatch`、`PaperSource`、`CoveragePackPolicy`、`SynthesisResponse`；`EvidenceAggregationPolicy` 增 `prior_score`（default 3）與 `shrinkage_strength`（default 4）；`FutureDirection` 增選填 `supporting_chunk_ids`（default 空 list）。所有新模型皆可 `model_validate_json()` round-trip。
- `literature_review/assessment.py`：`aggregate_evidence_assessments()` 改用收縮平均 `_shrunk_mean(scores, policy) = floor((n*mean + m*p)/(n+m) + 0.5)`（n=該論文相異 citation 數），relevance 與 quality 對稱套用；rationale 註明 n 與公式；limitations 增「chunk 數受檢索分配影響」聲明。`assess_selected_papers()` 與 `EvidenceCitation` 建構不動。
- 新檔 `literature_review/synthesis.py`：
  - `SynthesisError(RuntimeError)`。
  - `build_coverage_packs(all_chunks, policy) -> dict[str, list[EvidenceChunk]]`：deterministic；章節標題 regex（case-insensitive、行首、短行 <80 字元，可帶編號前綴：abstract/introduction/related work/background/method(s)/approach/experiment(s)/evaluation/results/discussion/conclusion/limitations/future work/**appendix/supplementary material**）；每節取第一個 chunk；優先序 abstract > limitations/future work > results/experiments/evaluation > method/approach > introduction/conclusion > 其餘 > **附錄區（第一個 appendix/supplementary 標題之後的 chunks，僅在名額有剩時選取）**；上限 `CoveragePackPolicy.max_chunks_per_paper`（default 6）；無標題時 fallback：頁 1 第一個 chunk＋等距 stride 補滿。
  - `detect_limitation_chunks(paper_chunks)`：cue regex `limitation|future work|fails? to|does not|remains (un)?(explor|solv|address)|lack of`（case-insensitive）。
  - Deterministic 逐篇筆記與方向規則、`build_deterministic_synthesis(...)`（無 API、`generated_by="deterministic"`、報告本文由模板組成並附 `[chunk_id]` 標記）。
  - LLM 路徑：`summarize_paper_notes(...)`（每篇一次呼叫，輸入=該篇 capped chunks（`llm_input_cap` default 40，超限先裁減——**若偵測到附錄邊界，先用附錄前的正文 chunks 等距填滿上限，名額有剩才以 stride 補入附錄 chunks；無附錄則全文等距**），輸出 `LlmPaperSummaryNote`）；`synthesize_report(...)`（一次呼叫，輸入=**僅 include/consider 論文**的證據摘要＋逐篇筆記，輸出 `LlmSynthesisBatch`＝報告本文（流暫文章，每個事實句尾附 `[chunk_id]` 行內標記）＋結構化 future_directions）；共用模式：fence strip、`model_validate_json()`、語法錯誤一次修補重試、grounding 驗證（未知 chunk_id/paper_id、或報告內引用標記抽取後為空/含未知 ID → `SynthesisError`）；prompt 明令「只用供應證據、不得發明」。兩個 LLM 函式都需要 client（RCS 與筆記皆為 LLM 步驟）；純 deterministic 路徑由 `build_deterministic_paper_notes`/`build_deterministic_synthesis` 公開函式提供，供測試與無網路情境直接組合呼叫。
  - 引用標記規則：報告內行內標記 `[chunk_id]` 的合法集合＝供應的證據摘要 chunk_ids ∪ 筆記 coverage_chunk_ids；directions 的 `supporting_chunk_ids` 同集合，`supporting_paper_ids` ⊆ 已評估的 include/consider 論文。
- `literature_review/pipeline.py`：
  - `expand_pdf_inputs(inputs) -> list[str]`：目錄→`sorted(glob "*.pdf")`、檔案保留、保序去重、空結果 `ValueError`。
  - `run_synthesis_pipeline(documents, query, client, *, chunk_policy, retrieval_policy, coverage_policy, aggregation_policy) -> SynthesisResponse`：逐檔 chunk（0 chunk 檔案 stderr 警告並跳過，全數失效才 raise）、paper_id 重複 → `ValueError`、合併語料單次 `retrieve_evidence`、`summarize_and_rerank`、`aggregate_evidence_assessments`、僅對 include/consider 論文做覆蓋包＋筆記、`PaperSource{paper_id, source_path}` 來源清單、最終 synthesis。
  - CLI：位置參數 `inputs` `nargs="+"`（檔案或目錄）；`--paper-id` 僅單一輸入可用（否則報錯）；非 dry-run 走完整流程印 `SynthesisResponse` JSON；`--dry-run` 行為與現況完全一致（本地步驟、不讀 key、印合併檢索 JSON）；錯誤處理沿用 exit 1 並納入 `SynthesisError`。
- 測試（全部 unittest、FakeClient/RetryClient、零 live API）：`tests/test_models.py`、`tests/test_assessment.py`（更新期望值＋收縮邊界案例）、新檔 `tests/test_synthesis.py`、`tests/test_pipeline.py` 擴充。
- 文件：`HANDOFF.md`（Next milestone 改寫為本規格、design decisions 增三條、里程碑表加 synthesis 行、測試數更新、Suggested sequence 更新、兩個 starter prompt 更新）、`AGENTS.md`（架構圖加雙線＋multi-PDF、「非全文評估」句延伸至 chunk-based synthesis、pipeline 指令範例改多檔/資料夾形式、檔案末尾新增 `## Terminology` 中英詞彙表）、`README.md`（synthesis 模組說明段落＋多檔範例）、`.gitignore`（新增 `.omo/drafts/`、`.omo/run-continuation/`、`.omo/evidence/` 排除條目——方案A）。

### Must NOT have (guardrails, anti-slop, scope boundaries)

- 不做 `LiteratureReviewReport` 組裝、不改 `demo.py`。
- 不做 embedding 檢索、GROBID/Docling、LLM search planner、整篇長上下文模式、語料層面 aspect-coverage 缺口分析。
- 不新增依賴；不改 `search.py`/`ranking.py`/`selection.py`/`planning.py`/`extraction.py`/`evidence.py`/`evidence_ranking.py`/`llm_evidence.py` 的既有行為。
- 測試不得要求 `GEMINI_API_KEY` 或任何網路；`--dry-run` 不得讀 key。
- 不造假論文 title/author/year；exclude 論文不進筆記、方向材料或綜合 prompt。
- Worker 不執行 add/commit/push/merge/checkout 等改動 git 狀態的指令（唯讀 `git status`/`git diff` 可用於驗證）；一律回報精確指令由使用者執行。
- Git 不 add：`.omo/drafts/`、`.omo/run-continuation/`、`.omo/evidence/`、`.env`、`Summer_Project.pdf`、`data/papers/`、`.venv/`。

## Verification strategy

> Zero human intervention - all verification is agent-executed.

- Test decision: tests-after，`unittest`（`python -m unittest discover`）；每個 todo 附帶自己的測試（Implementation+Test=ONE todo）。
- 環境：WSL-native `uv`（先 `uv --version` 確認），`uv sync` 後跑全套。
- Evidence: `.omo/evidence/task-<N>-evidence-cited-synthesis.log`（每個 todo 的指令輸出存檔）；最終全套輸出存 `.omo/evidence/final-suite-evidence-cited-synthesis.log`。

## Execution strategy

### Parallel execution waves

> Target 5-8 todos per wave. Fewer than 3 (except the final) means you under-split.

- Wave 1（契約與聚合基礎）：Todo 1、Todo 2（可平行，兩者僅共用 models.py 的 policy 欄位——Todo 2 需要Todo 1 的 policy 欄位，故 Todo 2 blocked by Todo 1；若序列執行則 1→2）。
- Wave 2（synthesis 模組）：Todo 3、Todo 4（4 blocked by 3：共用 fallback 與驗證 helper）。
- Wave 3（整併與文件）：Todo 5（blocked by 1-4）、Todo 6（blocked by 5，文件需寫入最終測試數）。

### Dependency matrix

| Todo | Depends on | Blocks | Can parallelize with |
| --- | --- | --- | --- |
| 1 | - | 2,3,4,5 | - |
| 2 | 1 | 5 | - |
| 3 | 1 | 4,5 | 2 |
| 4 | 1,3 | 5 | - |
| 5 | 1,2,3,4 | 6 | - |
| 6 | 5 | F1-F4 | - |

## Todos

> Implementation + Test = ONE todo. Never separate.
<!-- APPEND TASK BATCHES BELOW THIS LINE WITH edit/apply_patch - never rewrite the headers above. -->

- [x] 1. models.py 新增 synthesis 契約與政策欄位＋模型測試
  What to do / Must NOT do: 在 `literature_review/models.py` 末尾依序新增 `ChunkReference{chunk_id, paper_id, page_start>=1, page_end>=1, quote: str min_length=20}`、`PaperSummaryClaim{text: str min_length=20, aspect: str min_length=3, evidence: list[ChunkReference] min_length=1}`、`PaperSummary{paper_id: str, claims: list[PaperSummaryClaim] min_length=1, stated_limitations: list[PaperSummaryClaim] default_factory=list, coverage_chunk_ids: list[str] min_length=1}`、`LlmNoteClaim{text: str min_length=20, chunk_ids: list[str] min_length=1, aspect: str min_length=3}`、`LlmPaperSummaryNote{claims: list[LlmNoteClaim] min_length=1, stated_limitations: list[LlmNoteClaim] default_factory=list, coverage_chunk_ids: list[str] default_factory=list}`、`LlmSynthesisDirection{title: str min_length=3, rationale: str min_length=20, supporting_paper_ids: list[str] min_length=1, supporting_chunk_ids: list[str] min_length=1}`、`LlmSynthesisBatch{report: str min_length=100（含行內 [chunk_id] 引用標記）, future_directions: list[LlmSynthesisDirection] min_length=1}`、`PaperSource{paper_id: str, source_path: str min_length=1}`、`CoveragePackPolicy{max_chunks_per_paper: int default=6 ge=1 le=50, llm_input_cap: int default=40 ge=1 le=200}`、`SynthesisResponse{paper_sources: list[PaperSource] min_length=1, evidence_assessment_response: EvidenceAssessmentResponse, paper_summaries: list[PaperSummary] min_length=1, report: str min_length=100（含行內 [chunk_id] 引用標記）, future_directions: list[FutureDirection] min_length=1, limitations: list[str], generated_by: Literal["deterministic","llm"]}`；`EvidenceAggregationPolicy` 增 `prior_score: int = Field(default=3, ge=1, le=5)` 與 `shrinkage_strength: int = Field(default=4, ge=0)`；`FutureDirection` 增 `supporting_chunk_ids: list[str] = Field(default_factory=list)`。MUST NOT：改任何既有必填欄位、動 `LiteratureReviewReport`、動 `demo.py`、**不新增 SynthesizedClaim 或任何 claims 中間層契約**。
  Parallelization: Wave 1 | Blocked by: - | Blocks: 2,3,4,5
  References (executor has NO interview context - be exhaustive): literature_review/models.py:200-273（EvidenceCitation/PaperAssessment/policies/Responses/FutureDirection 既有形狀與欄位語法）；literature_review/models.py:1-5（import 與 Field 用法）；tests/test_models.py（既有模型測試風格）
  Acceptance criteria (agent-executable): `uv run python -m unittest tests.test_models -v` 全綠；新增測試含：每個新模型 `Model.model_validate_json(Model(...).model_dump_json())` round-trip 成功；`FutureDirection(...).supporting_chunk_ids == []`；`EvidenceAggregationPolicy().prior_score == 3 and .shrinkage_strength == 4`；`LlmSynthesisBatch` 與 `SynthesisResponse` 以含 `[chunk-id]` 標記的最小合法 report 建構成功。
  QA scenarios (name the exact tool + invocation): happy=`uv run python -m unittest tests.test_models -v` 輸出存 `.omo/evidence/task-1-evidence-cited-synthesis.log`；failure=故意省略 `LlmNoteClaim.text` → Pydantic ValidationError（測試以 `assertRaises(ValidationError)` 斷言）。
  Commit: N | 併入最終單一 commit（見 Commit strategy；指令由使用者執行）

- [x] 2. assessment.py 收縮平均聚合＋更新 test_assessment.py
  What to do / Must NOT do: 在 `literature_review/assessment.py` 新增 `_shrunk_mean(scores: list[int], policy: EvidenceAggregationPolicy) -> int`，公式 `math.floor((len(scores)*mean + policy.shrinkage_strength*policy.prior_score)/(len(scores)+policy.shrinkage_strength) + 0.5)`；`aggregate_evidence_assessments()` 內 relevance 與 evidence_quality 改用它；rationale 字串加入 `from N chunk(s) (shrunk mean, prior p=..., strength m=...)`；`limitations` 附加 `"Chunk counts partly reflect retrieval allocation across papers, not absolute paper quality."`。MUST NOT：動 `assess_selected_papers()`、`_rounded_mean()` 保留（或移除其唯一使用處時同步刪除）、動 `EvidenceCitation` 建構欄位。
  Parallelization: Wave 1 | Blocked by: 1 | Blocks: 5
  References (executor has NO interview context - be exhaustive): literature_review/assessment.py:78-80（_rounded_mean）；literature_review/assessment.py:83-147（aggregate_evidence_assessments 現況與 rationale/limitations 格式）；tests/test_assessment.py:37-99（現有期望值：relevance 5/quality 4 → 收縮後 n=2、scores [5,4] → 21/6=3.5→4，quality [4,3] → 19/6≈3.17→3，recommendation 仍 "include"）；literature_review/models.py:231-236（EvidenceAggregationPolicy）
  Acceptance criteria (agent-executable): `uv run python -m unittest tests.test_assessment -v` 全綠；更新後 `test_aggregates_chunk_evidence_with_page_traceability` 斷言 relevance_score==4、evidence_quality_score==3、recommendation=="include"；新增測試：(a) n=1、scores=[4] → relevance 3；(b) n=8、scores=[5,4,4,5,4,5,4,5]（mean 4.5）→ relevance 4；(c) 自訂 `EvidenceAggregationPolicy(prior_score=3, shrinkage_strength=0)` 時退化為普通半捨入平均；(d) limitations 含 "retrieval allocation"。
  QA scenarios (name the exact tool + invocation): happy=`uv run python -m unittest tests.test_assessment -v` 存 `.omo/evidence/task-2-evidence-cited-synthesis.log`；failure=建構 `EvidenceAggregationPolicy(prior_score=0)` → ValidationError（Pydantic ge=1）。
  Commit: N | 併入最終單一 commit

- [x] 3. synthesis.py deterministic 路徑（覆蓋包/筆記/模板報告）＋測試
  What to do / Must NOT do: 新檔 `literature_review/synthesis.py`：`SynthesisError(RuntimeError)`；`build_coverage_packs(all_chunks, policy)` 依 Scope-Must-have 的 regex/優先序（含附錄區最低優先）/cap/stride-fallback 規則實作；`detect_limitation_chunks(paper_chunks)` cue regex；`build_deterministic_paper_notes(paper_chunks, assessment, policy) -> PaperSummary`（claims 引用覆蓋包 chunks，quote=chunk.text 前 240 字元（至少保留 20 字元），aspect=偵測章節名或 "other"；coverage_chunk_ids=覆蓋包 chunk_ids 排序聯集（與 claims 引用一致）；stated_limitations 來自 `detect_limitation_chunks`）；deterministic 方向規則：≥2 個 include/consider 論文各有 relevance_score>=4 的 citation → 收斂方向（引用這些 chunk ids 與 paper ids）；各論文有 limitation chunks → 限制方向（rationale 指名該限制並引用 chunk ids）；皆無 → fallback 方向引用最高分 assessment 的 citations；`build_deterministic_synthesis(evidence_assessment_response, coverage_packs, paper_sources, policy) -> SynthesisResponse`（`generated_by="deterministic"`；**report 由模板組成**：每篇 include/consider 論文一段，逐條列出其筆記 claims 句並在句尾附 `[chunk_id]` 標記；續以每篇 assessment 的 rationale 句附 `[chunk_id]` 標記作為綜合段；文末附「材料來源清單」段落（使用的證據摘要與筆記出處）；limitations 含 "Section-sampled coverage, not a full-text reading." 與 "Every factual sentence carries an inline [chunk_id] citation; this is not a whole-paper review."）。MUST NOT：import llm_evidence 以外不改其行為；不做任何網路/金鑰存取。
  Parallelization: Wave 2 | Blocked by: 1 | Blocks: 4,5
  References (executor has NO interview context - be exhaustive): literature_review/evidence.py:22-33（chunk_id 格式 `{paper_id}-p{start}-{end}-c{n}` 與 page 欄位）；literature_review/models.py:200-220（EvidenceCitation 欄位）；literature_review/assessment.py:110-124（citation 建構與 rationale 內 chunk 標記格式先例）；本計畫 Scope-Must-have 的 regex/優先序規格
  Acceptance criteria (agent-executable): `uv run python -m unittest tests.test_synthesis -v` 全綠；測試涵蓋：(a) 含 "Abstract/Method/Experiments/Results/Limitations" 標題的合成 chunks → 覆蓋包每節一個、優先序正確、cap=6 生效；(b) 無標題文件 → stride fallback 且首個 chunk 來自頁 1；(c) limitation cue 命中/不命中；(d) 兩篇論文各有高分 citation → 產生收斂方向且 `supporting_chunk_ids`/`supporting_paper_ids` 正確；(e) 單篇無 limitation → fallback 方向存在；(f) 完整 `build_deterministic_synthesis` 輸出可 `model_dump_json()`、`generated_by=="deterministic"`、report 內含至少一個 `[chunk_id]` 標記且全部屬於供應集合。
  QA scenarios (name the exact tool + invocation): happy=`uv run python -m unittest tests.test_synthesis -v` 存 `.omo/evidence/task-3-evidence-cited-synthesis.log`；failure=空 chunks 輸入 → `build_coverage_packs` 回傳空 dict 而非 raise（合成文件測試斷言）。
  Commit: N | 併入最終單一 commit

- [x] 4. synthesis.py LLM 路徑（逐篇筆記＋直接成文綜合報告）＋假 client 測試
  What to do / Must NOT do: 在 `literature_review/synthesis.py` 新增 `build_paper_notes_prompt(paper_id, chunks)`（內容：僅依供應 chunks 分類章節、每 aspect 挑代表 chunk_ids、抽取 stated limitations、禁止使用外部知識、aspect 一律用完整英文單詞（contribution/method/experiments/results/limitations/other 等，長度至少 3）、回傳單一 JSON 物件無 code fence，附 chunks 的 id/paper_id/page_start/page_end/text 清單）；`summarize_paper_notes(paper_id, chunks, client, policy) -> PaperSummary`（超過 `llm_input_cap` 先裁減——**若偵測到附錄邊界，先用附錄前的正文 chunks 等距填滿上限，名額有剩才以 stride 補入附錄 chunks；無附錄則全文等距**；fence strip＋`LlmPaperSummaryNote.model_validate_json()`＋語法錯誤一次修補重試（模式照 llm_evidence.py:108-137 本地複製，錯誤型別 `SynthesisError`）；grounding：所有 chunk_ids ⊆ 供應集合否則 `SynthesisError`；enrich：`LlmNoteClaim` → `PaperSummaryClaim`，`ChunkReference.quote` 取自受信任的 chunk.text 前 240 字元；`PaperSummary.coverage_chunk_ids` = claims 與 stated_limitations 引用的 chunk_ids 排序聯集（空 → `SynthesisError`））；`build_synthesis_prompt(evidence_assessment_response, paper_summaries)`（**僅供應 include/consider 論文**的證據摘要（RCS 摘要＋分數＋頁碼）與逐篇筆記＋規則：直接寫出流暢的綜合報告文章、每個事實句尾附 `[chunk_id]` 行內標記（僅可用供應的 ID）、文末附「材料來源清單」、future_directions 以 JSON 陣列回傳且 ids 皆須來自供應集合、限制/未來方向參考以 limitation/future-work 材料為主、禁止使用外部知識）；`synthesize_report(evidence_assessment_response, coverage_packs, paper_summaries, client) -> SynthesisResponse`（驗證 `LlmSynthesisBatch`；**行內標記驗證**：以 regex 抽取 report 內所有 `[chunk_id]` → 必須 ⊆（證據摘要 chunk_ids ∪ 筆記 coverage_chunk_ids）且至少一個標記，否則 `SynthesisError`；enrich `LlmSynthesisDirection` → `FutureDirection`；`generated_by="llm"`；limitations 同 deterministic 句並附「N 條被排除論文的證據摘要未納入綜合」當有 exclude 論文時）。MUST NOT：修改 `llm_evidence.py`；不列印模型原始輸出於錯誤訊息。
  Parallelization: Wave 2 | Blocked by: 3 | Blocks: 5
  References (executor has NO interview context - be exhaustive): literature_review/llm_evidence.py:24-29（JsonGenerationClient Protocol）；literature_review/llm_evidence.py:76-105（prompt 風格與 repair prompt 先例）；literature_review/llm_evidence.py:108-125（fence strip 與 schema 錯誤訊息模式）；literature_review/llm_evidence.py:133-142（一次修補重試與嚴格 ID 檢查先例）；tests/test_llm_evidence.py:13-31（FakeClient/RetryClient 模式）
  Acceptance criteria (agent-executable): `uv run python -m unittest tests.test_synthesis -v` 全綠；新增測試：(a) FakeClient 回傳合法 note JSON → PaperSummary enriched、prompt 含全部 chunk_id 與 "only"；(b) note 引用未知 chunk_id → `SynthesisError`；(c) 第一次回 `{"report": ` 壞 JSON、第二次合法 → 恰兩次 prompt、第二個含 "malformed"；(d) `synthesize_report` happy：report 含多個合法 `[chunk_id]` 標記且全部通過驗證、文末含材料來源清單、directions 的 `supporting_chunk_ids` 涵蓋 limitation chunk、`generated_by=="llm"`；(e) report 含未知 `[unknown-id]` 標記 → `SynthesisError`；(f) report 無任何標記 → `SynthesisError`。
  QA scenarios (name the exact tool + invocation): happy=`uv run python -m unittest tests.test_synthesis -v` 存 `.omo/evidence/task-4-evidence-cited-synthesis.log`；failure=client 拋 `LlmEvidenceError` 樣態以 FakeClient 子類模擬 → `SynthesisError` 轉型斷言。
  Commit: N | 併入最終單一 commit

- [x] 5. pipeline.py 多檔/資料夾 + 端到端 synthesis + 測試
  What to do / Must NOT do: `literature_review/pipeline.py` 新增 `expand_pdf_inputs(inputs: list[str]) -> list[str]`（目錄→`sorted(glob "*.pdf")`、檔案保留、保序去重、空 → ValueError）；新增 `run_synthesis_pipeline(documents: list[FullTextDocument], query, client: JsonGenerationClient, *, chunk_policy, retrieval_policy, coverage_policy, aggregation_policy) -> SynthesisResponse`（client 必填——RCS 與筆記皆為 LLM 步驟；流程如 Scope-Must-have；deterministic 筆記/綜合由 todo 3 的公開函式另行直接呼叫，不經本函式）；`main()` 改 `inputs` `nargs="+"`，`--paper-id` 僅單一輸入允許（否則 parser.error），非 dry-run 建 `GeminiJsonClient` 跑完整流程並印 `SynthesisResponse.model_dump(mode="json")`；`--dry-run` 維持現行行為（合併檢索 JSON、不讀 key）；except 元組加 `SynthesisError`。既有 `run_evidence_pipeline()` 與 `retrieve_from_pdf()` 簽名與行為不變。MUST NOT：動 extraction/chunking 內部；dry-run 下建構任何 client。
  Parallelization: Wave 3 | Blocked by: 1,2,3,4 | Blocks: 6
  References (executor has NO interview context - be exhaustive): literature_review/pipeline.py:14-43（既有流程函式）；literature_review/pipeline.py:46-74（CLI 與錯誤處理）；literature_review/evidence_ranking.py（retrieve_evidence 簽名）；literature_review/models.py:117-123（FullTextDocument.source_path → PaperSource）；tests/test_pipeline.py:26-49（fake-client 流程測試風格）
  Acceptance criteria (agent-executable): `uv run python -m unittest tests.test_pipeline tests.test_synthesis -v` 全綠；新增測試：(a) `expand_pdf_inputs`：暫存目錄（tempfile）含 a.pdf/b.pdf 與忽略的 notes.txt → 展開排序正確；混合檔案+目錄去重；空目錄 → ValueError；(b) 兩份合成 FullTextDocument + FakeClient（回傳合法 note 與 synthesis JSON）→ `run_synthesis_pipeline` 產出 2 個 paper_summaries、paper_sources 含兩個 source_path、future_directions >=1、report 含合法標記；(c) 兩文件同 paper_id → ValueError；(d) 一文件 0 chunks（空頁面文字）→ 被跳過且另一份完成；(e) 既有 dry-run 相關測試不需修改即通過。
  QA scenarios (name the exact tool + invocation): happy=`uv run python -m unittest tests.test_pipeline -v` 存 `.omo/evidence/task-5-evidence-cited-synthesis.log`；failure=`uv run python -m literature_review.pipeline data/papers "query" --paper-id x --dry-run`（多輸入+--paper-id）→ stderr 含錯誤訊息且 exit 1（以 subprocess 或直接呼叫 parser 邏輯之單元測試斷言）。
  Commit: N | 併入最終單一 commit

- [x] 6. 文件與 gitignore 更新：HANDOFF.md / AGENTS.md / README.md / .gitignore
  What to do / Must NOT do: `HANDOFF.md`：(1) 「Next milestone」整節改寫為本計畫規格（多 PDF/資料夾、收縮聚合、include/consider 逐篇筆記、limitation 錨地方向、直接成文 synthesis＋行內引用標記驗證、三層溯源），保留指令碼區塊格式；(2) 「Important design decisions」新增三條（相關性 vs 覆蓋兩種證據視角；低相關/exclude chunk 非缺口證據、缺口僅來自明示 limitations 與後續 aspect-coverage 分析；收縮聚合與檢索分配混淆聲明）；(3) 里程碑表加「Evidence-cited synthesis and future directions | `synthesis.py`, `pipeline.py`, `models.py`, `assessment.py` | Done; …」一行、測試數改為實際數字；(4) 「Suggested sequence」改為：report 組裝（搜尋端合流）→ aspect-coverage 缺口分析 → embedding/GROBID/LLM planner；(5) 兩個 starter prompt 的「current next milestone」措辭更新為 report 組裝。`AGENTS.md`：(1) Current architecture 圖改為 `ResearchIdea -> SearchPlan -> OpenAlex retrieval -> metadata filter/rank/select -> local PDF extraction (multi-PDF) -> EvidenceChunk retrieval -> LLM contextual summary/re-rank -> evidence-backed paper assessment -> per-paper coverage notes (include/consider) -> synthesis report with inline citations / future directions`；(2) 第 18 行句尾延伸「; a chunk-based synthesis is likewise not a whole-paper review」；(3) Commands 區 pipeline 範例改 `uv run python -m literature_review.pipeline data/papers "literature review agent" --top-k 8 --dry-run`；(4) 檔案末尾新增 `## Terminology` 中英詞彙表（每詞一句定義，至少含：Chunk 證據段落（`{paper_id}-p{start}-{end}-c{n}`）、證據摘要 evidence summary（RCS 對某 chunk 的摘要＋分數＋頁碼）、覆蓋包 coverage pack、RCS（summarize_and_rerank）、筆記主張 PaperSummaryClaim（單篇、引 ChunkReference）、逐篇筆記 PaperSummary/per-paper notes、綜合報告 synthesis report（直接成文、行內 [chunk_id] 標記）、未來方向 FutureDirection、收縮平均 shrunk mean、include/consider/exclude 推薦等級（僅 include/consider 有筆記與綜合）、bounded evidence 有限證據集）。`README.md`：Current milestone 後新增一段說明 `literature_review.synthesis`（三層溯源、include/consider 筆記、direction 來源）與多檔指令範例。`.gitignore`：新增段落 `# Planner artifacts (committed plans live under .omo/plans/; drafts/evidence/run-state stay untracked)` 並加入 `.omo/drafts/`、`.omo/run-continuation/`、`.omo/evidence/` 三行。MUST NOT：提交 `.omo/drafts/`、`.omo/run-continuation/`、`.omo/evidence/`、`.env`、`Summer_Project.pdf`、`data/papers/`；不刪除既有正確章節。
  Parallelization: Wave 3 | Blocked by: 5 | Blocks: F1-F4
  References (executor has NO interview context - be exhaustive): HANDOFF.md:22-37（里程碑表與測試數）、HANDOFF.md:39-62（design decisions 與 Next milestone）、HANDOFF.md:80-84（Suggested sequence）、HANDOFF.md:101-113（兩個 prompt）、AGENTS.md:17-19（workflow 原則句）、AGENTS.md:36-42（架構圖）、AGENTS.md:21-28（Commands）、README.md（Current milestone 段落位置）、.gitignore（現有 12 行結構）
  Acceptance criteria (agent-executable): `grep -c "synthesis" HANDOFF.md` >= 5；`grep -n "per-paper coverage notes" AGENTS.md` 命中架構圖；`grep -n "## Terminology" AGENTS.md` 命中；`grep -n "omo/drafts" .gitignore` 命中且 `grep -n "omo/evidence" .gitignore` 命中；`grep -n "data/papers \"" README.md` 或等效多檔範例命中；HANDOFF 測試數等於 todo 5 後 `uv run python -m unittest discover -s tests -v` 的實際通過數（數字寫入前先跑一次並存 `.omo/evidence/final-suite-evidence-cited-synthesis.log`）。
  QA scenarios (name the exact tool + invocation): happy=上述 grep 檢查 + 全套 unittest 存檔；failure=文件中殘留 "29 tests" 與實際數字不符 → 修正後重跑 grep。
  Commit: N | 併入最終單一 commit

## Final verification wave

> Runs in parallel after ALL todos. ALL must APPROVE. Surface results and wait for the user's explicit okay before declaring complete.

- [x] F1. Plan compliance audit
  What to do / Must NOT do: 逐條核對本計畫 Scope-Must-have 與每個 todo 的 Acceptance criteria 是否皆有對應實作與測試；核對 Must-NOT-Have 清單零違反（唯讀 `git status`/`git diff --stat` 確認僅含預期檔案；無 `.omo/drafts/`、`.env`、PDF、`data/papers/` 被 staged）。
  Parallelization: Final wave | Blocked by: 1-6 | Blocks: 交付
  References: 本計畫全文；`git status`；`git diff --stat`
  Acceptance criteria (agent-executable): 產出核對清單存 `.omo/evidence/f1-compliance-evidence-cited-synthesis.log`，每列 PASS/FAIL，全 PASS。
  QA scenarios: happy=清單全 PASS；failure=任何 FAIL → 回到對應 todo 修復後重審。
  Commit: N

- [x] F2. Code quality review
  What to do / Must NOT do: 審查新程式碼：型別標註完整、無裸 `except`、錯誤訊息不外洩模型原文、函式單一職責、與既有模組風格一致（docstring 風格、命名）。
  Parallelization: Final wave | Blocked by: 1-6 | Blocks: 交付
  References: literature_review/synthesis.py、pipeline.py、models.py、assessment.py 的 diff
  Acceptance criteria (agent-executable): 審查意見存 `.omo/evidence/f2-quality-evidence-cited-synthesis.log`；無 blocking 問題（或已修復並重跑相關測試）。
  QA scenarios: happy=零 blocking；failure=有 blocking → 修復＋重跑 `uv run python -m unittest discover -s tests -v`。
  Commit: N

- [x] F3. Real manual QA（keyless 煙霧測試）
  What to do / Must NOT do: 以合成 FullTextDocument（兩份、含章節標題與 limitation 句）與一個內聯最小 FakeClient（keyless、無網路，定義在煙霧腳本內）呼叫 `run_synthesis_pipeline(...)`，驗證端到端 JSON：三層溯源齊備（report 內行內 `[chunk_id]` 標記全部可驗證、directions 帶 paper+chunk ids、paper_sources 帶路徑）、limitations 含非全文聲明；另以 `--dry-run` 跑真實資料夾路徑（若 `data/papers/` 有 PDF）確認不讀 key。
  Parallelization: Final wave | Blocked by: 1-6 | Blocks: 交付
  References: literature_review/pipeline.py 的 run_synthesis_pipeline；tests/test_pipeline.py 的合成文件建法
  Acceptance criteria (agent-executable): 煙霧腳本輸出存 `.omo/evidence/f3-manual-qa-evidence-cited-synthesis.log`；JSON 結構斷言全數成立。
  QA scenarios: happy=斷言全過；failure=任何斷言失敗 → 回對應 todo。
  Commit: N

- [x] F4. Scope fidelity
  What to do / Must NOT do: 確認無 scope creep：無新依賴（`git diff pyproject.toml uv.lock` 為空或僅鎖定雜訊）、未動 Scope-OUT 模組（`git diff --name-only` 核對）、測試零 live API（`grep -rn "GEMINI" tests/` 僅出現於既有 llm_evidence 測試的環境變數相關或全無）。
  Parallelization: Final wave | Blocked by: 1-6 | Blocks: 交付
  References: `git diff --name-only`、pyproject.toml、tests/
  Acceptance criteria (agent-executable): 核對輸出存 `.omo/evidence/f4-scope-evidence-cited-synthesis.log`，全 PASS。
  QA scenarios: happy=全 PASS；failure=越界 → 回滾該變更。
  Commit: N

## Commit strategy

分支策略（方案A）：計畫書已由使用者**在執行開始前** commit 到 `feature/plan-doc` 並 `git push -u origin feature/plan-doc`（已完成，commit a98a820）。里程碑實作由 worker 在同一分支完成**單一 commit**；worker 只回報指令、由使用者執行。全部 todo 與 final wave 通過後，回報使用者執行：

```bash
cd ~/projects/Literature_Review_Agent
git status
git add literature_review/models.py literature_review/assessment.py literature_review/coverage.py literature_review/synthesis.py literature_review/pipeline.py tests/test_models.py tests/test_assessment.py tests/test_synthesis.py tests/test_pipeline.py .gitignore HANDOFF.md AGENTS.md README.md
git commit -m "feat(synthesis): evidence-cited multi-paper synthesis with future directions"
git status
git push   # upstream 已由使用者首次推送時以 -u 建立
```

使用者檢視 diff 並確認 push 內容後，本里程碑即完成。**不合回 main**——合併時機由使用者日後自行決定（2026-08-26 使用者指定）。

絕不 `git add` `.omo/drafts/`、`.omo/run-continuation/`、`.omo/evidence/`、`.env`、`Summer_Project.pdf`、`data/papers/`、`.venv/`。

## Success criteria

- `uv sync` 後 `uv run python -m unittest discover -s tests -v` 全綠（預估 45-55 測試），零 live API。
- `--dry-run` 行為與現況一致且不讀 key；多檔/資料夾輸入可用。
- `SynthesisResponse` 端到端可由 FakeClient 流程產出、deterministic 筆記/報告可由公開函式無 API 組合產出；報告本文每個事實句附行內 `[chunk_id]` 標記且全部通過驗證；三層溯源（報告引用/方向 ids/檔案路徑）齊備。
- 收縮聚合生效且 rationale/limitations 如實揭露；include/consider 纔有筆記與綜合；directions 僅引用供應集合內的 chunks/papers。
- HANDOFF/AGENTS/README/.gitignore 更新完成；`.omo/plans/` 進入 git 追蹤（方案A）；單一 commit 於 `feature/plan-doc` 並 push，git 歷史乾淨、未追蹤敏感檔案。
