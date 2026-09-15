# LLM Input Hygiene（LLM 輸入整潔）— 五項合成里程碑

- 日期：2026-09-15（核准：使用者明確五項範圍 + 「直接寫計畫並自我審核」）
- 目的：貫徹 2026-09-15 架構裁示「內部 LLM 輸入最少必要；對外輸出由程式機械組裝」
- 測試基線：**363 tests 全綠**（C2e 後）；本里程碑預期 +8 左右
- 依循：route B（執行代理改 code、使用者 commit）；本計畫檔為唯一權威

## 範圍（五項，全做）

| # | 內容 | 檔案:位置 |
|---|---|---|
| 1 | notes prompt 輸入移除頁碼 | `synthesis.py:598-603` |
| 2 | pairwise_eval judge 輸入移除頁碼 | `pairwise_eval.py:220-228` |
| 3 | functional prompt 教 LLM 解讀 Section 欄位 | `functional.py:69-97` |
| 4 | T1 embedding 取樣包裝頂層章節 | `embedding_retriever.py:58-61`、`functional.py:209-248`、`pipeline.py:217-223` |
| 5 | directions 移除 `supporting_paper_ids`（LLM 契約），對外程式展開 | `models.py:449`、`synthesis.py:807-820`、`:1016-1057` |

## 關鍵設計決策（不得更改）

- **⑤ 輸入保留 `paper_id`、輸出移除**：`paper_id` 字串對 LLM 無意義，但其**論文分組結構**是 directions 判斷 cross-paper convergence 的必要訊號（prompt 明示「across all supplied papers」）。移除輸出欄位、由程式從 claim→paper 展開（1:1 確定函數）即可；**不得同時移除 notes payload 的 `paper_id`**。
- **④ `encode_chunks` 加選用參數** `text_for: Callable[[EvidenceChunk], str] | None = None`；預設 `None`＝原行為（`chunk.text`），`retrieval_eval`/`pairwise_eval` 基準零變化。**不得**改預設行為。
- **④ 章節前綴**：`f"{top_level_section(chunk.section, paper_titles.get(chunk.paper_id) if paper_titles else None) or 'other'} | {chunk.text}"`，只用頂層章節。
- **⑤ claim→paper 計數**：新 helper `_claim_paper_map` 須與 `_build_notes_payload` 相同順序（usable notes 文件序、claim 流水號 `claim-1..N`）。
- 對外契約 **一律不動**：`FutureDirection` / `ChunkReference` / `SynthesisResponse` / `EvidenceCitation` 欄位、deterministic 逃生路徑（synthesis.py:168-256）。

## Must NOT

- 不改共享 `encode_chunks` 預設行為（基準測試 `retrieval_eval`/`pairwise_eval` 向量不變）
- 不移除 notes payload 的 `paper_id`（⑤ 的分組語意）
- 不改外部契約、不改 deterministic 路徑、不改 `build_claim_chunks` / `_build_notes_payload` 既有輸出
- 不碰 `.env`、`data/papers/`；**零真實 LLM/OpenAlex 呼叫**（全部 fake/單元測試）
- 不執行任何 git commit；不在執行期間修改本計畫檔
- 不做五項以外的 refactor（如 top_level_section 抽離共用等）

---

## Todos

- [ ] 1. `synthesis.py:598-603`：`build_paper_notes_prompt` 的 chunks dict 移除 `"page_start": chunk.page_start,` 與 `"page_end": chunk.page_end,` 兩行（僅留 `chunk_id`、`text`）。測試：`tests/test_synthesis.py::test_prompt_groups_chunks_by_section` 加 `self.assertNotIn("page_start", prompt)`。QA：`uv run python -m unittest tests.test_synthesis -v 2>&1 | tee .omo/evidence/llm-input-hygiene-t1.log`。Acceptance：prompt JSON chunks 只剩 chunk_id/text；`ChunkReference` 頁碼組裝（synthesis.py:653-654）未動。
- [ ] 2. `pairwise_eval.py:220-228`：`_chunk_context` 移除 `page_start`/`page_end` 兩行；docstring 改「Expose the identity fields the judge needs (chunk id, paper id, text).」。測試：`tests/test_pairwise_eval.py:303-304` 兩個 `assertIn('"page_start": 1', prompt)` / `assertIn('"page_end": 1', prompt)` 改 `assertNotIn`。QA：`uv run python -m unittest tests.test_pairwise_eval -v 2>&1 | tee .omo/evidence/llm-input-hygiene-t2.log`。Acceptance：judge prompt 無頁碼；`_chunk_context` 無其他使用點依賴頁碼（grep 確認）。
- [ ] 3. `functional.py:69-97`：`build_functional_prompt` 的 return 字串在「Scoring guide.」（:89）前加一句：「The 'Section' field shows the chunk's location in the paper (e.g. '2 Method', 'Appendix'); use it to judge the evidence type — appendix or References chunks rarely contribute utility.」測試：`tests/test_functional.py` 新增 `assertIn("The 'Section' field", prompt)`（可用既有 prompt 測試基座）。QA：`uv run python -m unittest tests.test_functional -v 2>&1 | tee .omo/evidence/llm-input-hygiene-t3.log`。Acceptance：:155/:186 既有 assertIn 不破。
- [ ] 4. T1 embedding 章節包裝：① `embedding_retriever.py:58-61`：`encode_chunks(chunks, encoder, *, text_for: Callable[[EvidenceChunk], str] | None = None)`，`texts = [text_for(chunk) if text_for is not None else chunk.text for chunk in chunks]`，docstring 註明預設 None＝原行為。② `functional.py`：`from literature_review.synthesis import top_level_section`（synthesis 不 import functional，無 circular）。③ `functional.py:209`：`sample_top_chunks_per_paper` 加 keyword-only `paper_titles: dict[str, str] | None = None`。④ `functional.py:242`：改 `chunk_vectors = encode_chunks(filtered, encoder, text_for=_section_wrapped_text)`，其中 `_section_wrapped_text = lambda chunk: f"{top_level_section(chunk.section, paper_titles.get(chunk.paper_id) if paper_titles else None) or 'other'} | {chunk.text}"`。⑤ `pipeline.py:217-223`：呼叫加 `paper_titles=paper_titles`。測試：`test_embedding_retriever.py` 新增 encode_chunks text_for 測試（fake encoder 記錄 texts：不傳→原樣；傳→包裝）；`test_functional.py` 新增 sample 包裝測試（fake encoder 收到 `"2 Method | ..."` 前綴、無 paper_titles 時 `"other | ..."`）。QA：`uv run python -m unittest tests.test_functional tests.test_embedding_retriever -v 2>&1 | tee .omo/evidence/llm-input-hygiene-t4.log`。Acceptance：`retrieval_eval.py`/`pairwise_eval.py` 未傳 text_for（零改動）；附錄黑名單機制（drop_noise_sections）不變。
- [ ] 5. directions 移除 `supporting_paper_ids`：① `models.py:449`：刪 `supporting_paper_ids: list[str] = Field(min_length=1)`；`LlmSynthesisDirection` docstring（:438-445）更新「LLM 只回 supporting_claim_ids；supporting_paper_ids 由程式從 claim→paper 對應展開」。② `synthesis.py:815-817`：輸出形狀指示改「'title', 'rationale', and 'supporting_claim_ids', where every claim id is copied exactly from the supplied material.」。③ `synthesis.py` 新增 helper（`_build_notes_payload` 旁）：`_claim_paper_map(paper_summaries, usable_ids) -> dict[str, str]`，走與 `_build_notes_payload` 相同的 usable notes 文件序 + claim 流水號。④ `synthesis.py:1010` 旁建立 `claim_paper_map`；`:1017-1022` 刪除 unknown_papers 檢查（欄位已不存在）；`:1023-1027` unknown_claims 檢查保留；`:1049-1057` 組裝 `FutureDirection(supporting_paper_ids=sorted({claim_paper_map[cid] for cid in direction.supporting_claim_ids}), ...)`（在 unknown_claims raise 之後，安全）。測試：移除 LLM 契約的 supporting_paper_ids——`test_synthesis.py:562`、`test_pipeline.py:146`、`test_main.py:163` 的 fake direction dict；`test_models.py`（:456 附近）LlmSynthesisDirection 構造；`test_synthesis.py:1409` 的 ghost 測試改 `supporting_claim_ids=["ghost-claim"]`（仍期望 raise）；`:371/:383` 外部 `FutureDirection.supporting_paper_ids` assert 保留並依 fake claims 所屬 paper 調整期望值；新增「展開正確性」測試（direction 只引某 claim → supporting_paper_ids == 該 claim 所屬 paper）。deterministic 路徑測試（:467 等）**不動**。QA：`uv run python -m unittest tests.test_synthesis tests.test_models tests.test_pipeline tests.test_main -v 2>&1 | tee .omo/evidence/llm-input-hygiene-t5.log`。Acceptance：LLM 輸入/輸出零 `supporting_paper_ids`（notes payload 的 `paper_id` 分組鍵保留）；外部 `FutureDirection.supporting_paper_ids` 非空且機械等於 claims 所屬 papers。
- [ ] 6. 全量測試：`uv run python -m unittest discover -s tests -v 2>&1 | tee .omo/evidence/llm-input-hygiene-tests.log`。期望全綠（363 + 新增 ≈ 371，以執行為準）；回報確切測試總數。

## Final verification wave

- [ ] F1. 執行代理 grep `page_start|page_end` 於 `literature_review/`，回報剩餘出現點逐一標注「程式組裝/契約」或「LLM 輸入」——LLM 輸入路徑（`build_paper_notes_prompt`、`_chunk_context`）必須零命中。
- [ ] F2. grep `supporting_paper_ids`：`LlmSynthesisDirection` 與 `build_directions_prompt` 零命中；`FutureDirection`（外部契約）、synthesis.py 組裝處、deterministic 路徑保留。
- [ ] F3. Todo 6 log 全綠（規劃 agent 驗收時讀 log 核對總數）。
- [ ] F4. 檢查無殘留舊斷言（`test_pairwise_eval.py`/`test_synthesis.py` 的 `assertIn("page_start", prompt)` 型）。

## Commit strategy（使用者親做）

1. **code 1 筆**：`literature_review/models.py`、`literature_review/synthesis.py`、`literature_review/functional.py`、`literature_review/embedding_retriever.py`、`literature_review/pairwise_eval.py`、`literature_review/pipeline.py`、`tests/test_synthesis.py`、`tests/test_functional.py`、`tests/test_embedding_retriever.py`、`tests/test_pairwise_eval.py`、`tests/test_models.py`、`tests/test_pipeline.py`、`tests/test_main.py`
   - message：「feat: LLM input hygiene — drop page/page-id provenance from internal prompts, wrap top-level sections for embedding sampling」
2. **docs 1 筆**：`.omo/STATE.md`、`.omo/plans/llm-input-hygiene.md`、`HANDOFF.md`
   - message：「docs: record LLM input hygiene milestone」
3. push（`git push`）。

## 執行回報格式（執行代理）

- 每個 Todo 的 log 檔名清單；全部受影響檔案清單（發現計畫外變更須逐一說明原因）；測試總數；F1/F2 grep 結果。**不做** git commit、**不跑**真實 API。