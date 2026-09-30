# Output Traceability（逐篇筆記標號 + 論文清單 claim 索引 + report 純 prose）

- 日期：2026-09-15（核准：使用者明確拍板）
- 目的：讓 claim-N 標籤直接寫進對外表，消費者不用靠「順序推導」；report 改純 prose
- 測試基線：**369 tests 全綠**（llm-input-hygiene 後）；預估 +5~8
- 依循：route B；本計畫檔為唯一權威

## 設計背景

現況三張表的問題：
- `paper_summaries[].claims[]` 有 claim 內容（text/aspect/evidence 含頁碼），但**沒有 claim-N 標籤**——消費者要自己數順序推
- `claim_chunks` 有 claim-N → chunks，但**沒有 claim 內容**
- 兩者靠「文件序」隱含對應，容易斷

改完後：
```
paper_summaries: claim_id + text + aspect + evidence（含 chunks/頁碼）← 最完整
paper_sources: paper_id + source_path + claim_ids[] ← claim→哪篇一行查
claim_chunks: claim-N → chunk_ids ← 報告 marker/方向共用
```

## 範圍（三項）

| # | 內容 | 檔案 |
|---|---|---|
| 1 | `PaperSummaryClaim` 加 `claim_id: str`（required） | `models.py:407-412` |
| 2 | `PaperSource` 加 `claim_ids: list[str] = Field(default_factory=list)` | `models.py:497-501` |
| 3 | pipeline 層：resolve paper_sources 時填 `claim_ids`；移除 materials prose append | `pipeline.py:285-308` |

## Must NOT

- 不動 LLM 契約（LlmNoteClaim / LlmPaperSummaryNote / LlmSynthesisDirection）
- 不動 `claim_chunks` 欄位（保留，標籤系統共用）
- 不動 `render_materials_section` 函式簽名（保留公開，消費者自組 prose 時用）
- 不動 deterministic 逃生路徑（synthesis.py:168-256）——它自帶材料清單，不走 pipeline append
- 不碰 `.env` / `data/papers/`；零真實 LLM/OpenAlex 呼叫
- 不執行任何 git commit；不修改本計畫檔

---

## Todos

- [ ] 1. `models.py`：`PaperSummaryClaim`（:407-412）加 `claim_id: str = Field(min_length=1)`；`PaperSource`（:497-501）加 `claim_ids: list[str] = Field(default_factory=list)`，docstring 更新「the global claim tags (claim-N) owned by this paper」。測試：`tests/test_models.py` 所有 `PaperSummaryClaim(...)` 建構加 `claim_id="claim-1"`；`PaperSource(...)` 可不加（有 default）。QA：`uv run python -m unittest tests.test_models -v 2>&1 | tee .omo/evidence/output-traceability-t1.log`。Acceptance：既有的 LLM 路徑 `PaperSummary` 組裝仍可編譯（claim_id 在 synthesize_report 填入，見 Todo 2）。
- [ ] 2. `synthesis.py`：新增 `_tag_claim_ids(paper_summaries: list[PaperSummary], usable_ids: set[str]) -> None`——與 `_claim_paper_map` 相同計數順序，直接 mutate 每個 `PaperSummaryClaim` 加 `claim_id`（`note.claims[i] = note.claims[i].model_copy(update={"claim_id": f"claim-{counter}"})`）。呼叫位置：`synthesize_report`（:1028 旁）`claim_chunks = build_claim_chunks(usable_notes)` 之前（或之後，均可——counter 邏輯一致）。測試：`tests/test_synthesis.py` 新增單元測試 `_tag_claim_ids`（fake PaperSummary 兩篇三條 claim → 每條帶正確 claim_id）；既有 LLM 路徑與 deterministic 路徑的 `PaperSummary` 組裝測試（:103/:697 相關）加 `self.assertEqual(claims[0].claim_id, "claim-1")`。deterministic 路徑（:168-256）的 `build_deterministic_paper_notes` 不動——claim_id 由 `synthesize_report` 統一 tag（不在個別 note 函式填）。QA：`uv run python -m unittest tests.test_synthesis -v 2>&1 | tee .omo/evidence/output-traceability-t2.log`。
- [ ] 3. `pipeline.py:285-308`：① resolve paper_sources 時，從 `result.paper_summaries` 聚合每篇的 `claim_ids`：`paper_id_to_claims = {}; for s in result.paper_summaries: paper_id_to_claims[s.paper_id] = [c.claim_id for c in s.claims]`，resolved_sources = `source.model_copy(update={"source_path": ..., "claim_ids": paper_id_to_claims.get(source.paper_id, [])})`。② 移除 materials prose append（:294-302 刪 `if result.generated_by == "llm":` 區塊），`report_text = result.report` 直接用。測試：`tests/test_pipeline.py` 新增 resolve 後 paper_sources 有 claim_ids 的斷言（fake e2e）；`report` 不含「## 材料來源清單」的 assertNotIn。QA：`uv run python -m unittest tests.test_pipeline -v 2>&1 | tee .omo/evidence/output-traceability-t3.log`。Acceptance：`render_materials_section` 函式仍存在（__all__ 有）；deterministic 路徑的報告自帶材料清單（不走 pipeline append，不受影響）。
- [ ] 4. `tests/test_main.py`：fake e2e 的 `SynthesisResponse`（若有）補 `paper_sources` 的 `claim_ids`（default 空列表，多數測試不需改）；驗證 report 尾端無材料清單 prose 的斷言（若有）。QA：`uv run python -m unittest tests.test_main -v 2>&1 | tee .omo/evidence/output-traceability-t4.log`。
- [ ] 5. 全量測試：`uv run python -m unittest discover -s tests -v 2>&1 | tee .omo/evidence/output-traceability-tests.log`。期望全綠；回報確切測試總數。

## Final verification wave

- [ ] F1. grep `claim_id.*Field\|claim_ids.*Field` 於 `models.py` 確認兩處新增
- [ ] F2. grep `材料來源清單` 於 `pipeline.py`：LLM 路徑零命中（append 已移）；deterministic 路徑保留（:287 注釋或 code）
- [ ] F3. Todo 5 log 全綠

## 真實驗收建議（使用者親做，非執行代理 Todo）

所有 Todo 通過後，跑一次真實完整 run 驗證：
```powershell
printf 'literature review agent\n' | uv run --env-file .env python -m literature_review.main
```
確認：
- report 純 prose，尾端無「## 材料來源清單」
- `result.paper_summaries[].claims[].claim_id` 非空，與 `result.claim_chunks` 的 key 一致
- `result.paper_sources[].claim_ids` 非空，與 `result.claim_chunks` 對照正確
- `result.future_directions[].supporting_claim_ids` 全部落在 `result.claim_chunks` key 內

## Commit strategy（使用者親做）

1. **code 1 筆**：`models.py`、`synthesis.py`、`pipeline.py`、`test_models.py`、`test_synthesis.py`、`test_pipeline.py`、`test_main.py`
   - message：「feat: output traceability — add claim_id to PaperSummaryClaim, claim_ids to PaperSource, make report prose pure」
2. **docs 1 筆**：`.omo/STATE.md`、`.omo/plans/output-traceability.md`、`HANDOFF.md`
   - message：「docs: record output traceability milestone」
3. push

## 執行回報格式（執行代理）

- 每個 Todo 的 log 檔名清單；全部受影響檔案清單（含計畫外變更原因）；測試總數；F1/F2 結果。不做 git commit、不跑真實 API。
