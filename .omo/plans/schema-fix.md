# schema-fix - Work Plan

## TL;DR (For humans)

**What you'll get:** (1) 把 `PaperSummary` 的 `stated_limitations` 合併進 `claims`，消除 LLM 漏填 `aspect` 的格式錯誤根因；(2) 讓 `generate_json()` 接受 schema 參數，每個 LLM 呼叫傳入對應的 Pydantic schema；(3) 重試機制擴展 + fallback 防線。真實 API 端到端測試通過。

**Why this approach:** 合併後 LLM 只需填一個 `claims` 陣列，用 `aspect` 區分類型，認知負擔更低、格式錯誤風險更小。加上 schema-guided generation，Gemini 直接看到正確的 JSON 結構。雙重保險。

**What it will NOT do:** 不新增依賴；不動 Git（由使用者執行）；不改動 `literature_review/pipeline.py`。

**Effort:** Small
**Risk:** Low

Your next move: 以 `/start-work schema-fix` 交由 worker 執行。

---

## Scope

### Must have

1. **`models.py`** — 刪除 `LlmPaperSummaryNote.stated_limitations` 和 `PaperSummary.stated_limitations`
2. **`synthesis.py`** — Deterministic 路徑合併 limitation claims 進 `claims`；LLM prompt 簡化；下游篩選改用 `filter(claims, aspect="limitations")`
3. **`llm_evidence.py`** — `generate_json()` 加 `schema` 參數
4. **`synthesis.py`** — `_generate_validated()` 傳入正確 schema + 重試擴展
5. **`synthesis.py`** — `_claim_from_note()` fallback
6. **`test_synthesis.py`** — FakeClient 簽名 + 所有斷言調整

### Must NOT have

- 不改動 `literature_review/pipeline.py`
- 不新增 Python 依賴
- 不提交 Git（由使用者執行）
- 不修改 `data/papers/` 或 `.env`

---

## Todo 1: `models.py` — 刪除 `stated_limitations` 欄位

**What to do:**

1. 在 `LlmPaperSummaryNote` 中刪除 `stated_limitations` 欄位：
   ```python
   class LlmPaperSummaryNote(BaseModel):
       claims: list[LlmNoteClaim] = Field(min_length=1)
       # stated_limitations 已刪除 — 全部 claim 統一用 aspect 區分
       coverage_chunk_ids: list[str] = Field(min_length=1)
   ```

2. 在 `PaperSummary` 中刪除 `stated_limitations` 欄位：
   ```python
   class PaperSummary(BaseModel):
       paper_id: str
       claims: list[PaperSummaryClaim]
       # stated_limitations 已刪除 — limitations 就是 aspect="limitations" 的 claims
       coverage_chunk_ids: list[str]
   ```

**Evidence of completion:**
- `uv run python -m unittest tests.test_models -v` 全綠（需配合後續 Todo 調整測試）

---

## Todo 2: `synthesis.py` — Deterministic 路徑合併

**What to do:**

修改 `build_deterministic_paper_notes()`：不再分開處理 `stated_limitations`，改為全部合併進 `claims`：

```python
def build_deterministic_paper_notes(
    paper_chunks: list[EvidenceChunk],
    assessment: PaperAssessment,
    policy: CoveragePackPolicy,
) -> PaperSummary:
    pack = build_coverage_packs(paper_chunks, policy).get(assessment.paper_id, [])
    if not pack:
        raise SynthesisError(f"No evidence chunks available for paper {assessment.paper_id}.")
    claims = _claims_from_chunks(pack)
    # limitations 合併進 claims（aspect 由 classify_section 決定為 "limitations"）
    limitation_chunks = detect_limitation_chunks(paper_chunks)
    limitation_claims = _claims_from_chunks(limitation_chunks, aspect="limitations")
    all_claims = claims + limitation_claims
    referenced = {ref.chunk_id for claim in all_claims for ref in claim.evidence}
    return PaperSummary(
        paper_id=assessment.paper_id,
        claims=all_claims,
        coverage_chunk_ids=sorted(referenced),
    )
```

**Evidence of completion:**
- `uv run python -m unittest tests.test_synthesis -v` 全綠

---

## Todo 3: `synthesis.py` — LLM prompt 簡化

**What to do:**

1. 修改 `build_paper_notes_prompt()`：刪除 `stated_limitations` 相關指示，改為要求 LLM 在 `claims` 陣列中用 `aspect` 區分：

```python
def build_paper_notes_prompt(paper_id: str, chunks: list[EvidenceChunk]) -> str:
    payload = [
        {
            "chunk_id": chunk.chunk_id,
            "paper_id": chunk.paper_id,
            "page_start": chunk.page_start,
            "page_end": chunk.page_end,
            "text": chunk.text,
        }
        for chunk in chunks
    ]
    sections_by_aspect: dict[str, list[str]] = {}
    for chunk in chunks:
        _, aspect = classify_section(chunk.text)
        sections_by_aspect.setdefault(aspect, []).append(chunk.chunk_id)
    limitation_ids = sorted({chunk.chunk_id for chunk in detect_limitation_chunks(chunks)})
    return (
        "Summarize this single paper using only the supplied evidence chunks. "
        "Do not use outside knowledge and do not invent claims. "
        "Classify each chunk by section and select representative chunk_ids per aspect. "
        "If a chunk states limitations or future work, give it aspect='limitations'. "
        f"Detected limitation cues: {json.dumps(limitation_ids)}. "
        f"Section classification of the supplied chunks: {json.dumps(sections_by_aspect)}. "
        "Every aspect must be one full English word of at least three letters chosen from: "
        f"{', '.join(_NOTE_ASPECTS)}. "
        "Return exactly one JSON object, without Markdown code fences or surrounding explanation, "
        "containing 'claims' and 'coverage_chunk_ids'. Each claim must contain 'text' of at least "
        "20 characters, 'chunk_ids' as a non-empty subset of the supplied chunk identifiers, and 'aspect'. "
        f"Paper ID: {paper_id}\n"
        f"Evidence chunks: {json.dumps(payload, ensure_ascii=False)}"
    )
```

2. 修改 `summarize_paper_notes()`：刪除所有 `stated_limitations` 處理邏輯：

```python
def summarize_paper_notes(
    paper_id: str,
    chunks: list[EvidenceChunk],
    client: JsonGenerationClient,
    policy: CoveragePackPolicy,
) -> PaperSummary:
    supplied = _bounded_chunks_for_llm(chunks, policy.llm_input_cap)
    if not supplied:
        raise SynthesisError(f"No evidence chunks available for paper {paper_id}.")
    note = _generate_validated(
        client, LlmPaperSummaryNote,
        build_paper_notes_prompt(paper_id, supplied),
        LlmPaperSummaryNote.model_json_schema(),
    )
    chunk_by_id = {chunk.chunk_id: chunk for chunk in supplied}
    cited_ids = {
        chunk_id for claim in note.claims for chunk_id in claim.chunk_ids
    } | set(note.coverage_chunk_ids)
    unknown_ids = sorted(cited_ids - chunk_by_id.keys())
    if unknown_ids:
        raise SynthesisError(
            f"LLM note for paper {paper_id} cites unknown chunk ids: {', '.join(unknown_ids)}."
        )
    claims = [_claim_from_note(claim, chunk_by_id) for claim in note.claims]
    coverage_chunk_ids = sorted(
        {reference.chunk_id for claim in claims for reference in claim.evidence}
    )
    if not coverage_chunk_ids:
        raise SynthesisError(f"LLM note for paper {paper_id} cites no evidence chunks.")
    return PaperSummary(
        paper_id=paper_id,
        claims=claims,
        coverage_chunk_ids=coverage_chunk_ids,
    )
```

**Evidence of completion:**
- `uv run python -m unittest tests.test_synthesis -v` 全綠

---

## Todo 4: `synthesis.py` — 下游篩選改用 filter

**What to do:**

1. 修改 `_limitation_directions()`：改用 `filter(claims, aspect="limitations")` 而非 `detect_limitation_chunks()`：

```python
def _limitation_directions(
    notes: list[PaperSummary],
) -> list[LlmSynthesisDirection]:
    directions: list[LlmSynthesisDirection] = []
    for note in notes:
        limitation_claims = [c for c in note.claims if c.aspect == "limitations"]
        if not limitation_claims:
            continue
        chunk_ids = sorted({ref.chunk_id for claim in limitation_claims for ref in claim.evidence})
        direction = _make_direction(
            f"Target the stated limitations of {note.paper_id}",
            f"The authors of {note.paper_id} state limitations in chunks "
            f"{', '.join(chunk_ids)}; future work should address them directly.",
            [note.paper_id],
            chunk_ids,
        )
        if direction is not None:
            directions.append(direction)
    return directions
```

2. 修改 `_deterministic_directions()`：不再傳 `coverage_packs`，改傳 `notes`：
   - 簽名改為 `(response, notes: list[PaperSummary])`
   - 呼叫 `_limitation_directions(notes)` 而非 `_limitation_directions(assessments, coverage_packs)`

3. 修改 `build_deterministic_synthesis()`：把 `notes` 傳給 `_deterministic_directions()`

4. 修改 `build_synthesis_prompt()`：只傳 `claims`，不傳 `stated_limitations`：
   ```python
   notes = [
       {
           "paper_id": note.paper_id,
           "claims": [
               {
                   "text": claim.text,
                   "aspect": claim.aspect,
                   "chunk_ids": [reference.chunk_id for reference in claim.evidence],
               }
               for claim in note.claims
           ],
           "coverage_chunk_ids": note.coverage_chunk_ids,
       }
       for note in paper_summaries
       if note.paper_id in usable_ids
   ]
   ```

**Evidence of completion:**
- `uv run python -m unittest tests.test_synthesis -v` 全綠

---

## Todo 5: `llm_evidence.py` — schema 參數

**What to do:**

1. 修改 `JsonGenerationClient` protocol：
   ```python
   def generate_json(self, prompt: str, schema: dict | None = None) -> str:
   ```

2. 修改 `GeminiJsonClient.generate_json()`：
   ```python
   def generate_json(self, prompt: str, schema: dict | None = None) -> str:
       interaction = self._client.interactions.create(
           model=self._model,
           input=prompt,
           response_format={
               "type": "text",
               "mime_type": "application/json",
               "schema": schema if schema is not None else LlmEvidenceAssessmentBatch.model_json_schema(),
           },
       )
       if not interaction.output_text:
           raise LlmEvidenceError("Gemini returned no text output.")
       return interaction.output_text
   ```

3. 修改 `summarize_and_rerank()`：明確傳入 schema
   ```python
   raw_output = client.generate_json(build_evidence_prompt(response), LlmEvidenceAssessmentBatch.model_json_schema())
   # retry:
   generated = validate_evidence_assessments(client.generate_json(build_json_repair_prompt(raw_output), LlmEvidenceAssessmentBatch.model_json_schema()))
   ```

**Evidence of completion:**
- `uv run python -m unittest tests.test_llm_evidence tests.test_synthesis -v` 全綠

---

## Todo 6: `synthesis.py` — `_generate_validated()` 重試擴展

**What to do:**

1. 修改 `_generate_validated()` 簽名和邏輯：
   ```python
   def _generate_validated(
       client: JsonGenerationClient, model: type[_MODEL_T], prompt: str, schema: dict
   ) -> _MODEL_T:
       raw_output = client.generate_json(prompt, schema)
       try:
           return _parse_llm_model(model, raw_output)
       except SynthesisError as exc:
           repair = _build_schema_repair_prompt(raw_output, str(exc))
           return _parse_llm_model(model, client.generate_json(repair, schema))
   ```

2. 新增 `_build_schema_repair_prompt()`：
   ```python
   def _build_schema_repair_prompt(raw_output: str, error_details: str) -> str:
       return (
           "The previous response had schema validation errors. Return a repaired version "
           "as exactly one JSON object, without Markdown or explanation. "
           "Fix the specific issue described below while preserving all other correct content. "
           f"Validation error: {error_details}\n"
           f"Previous response:\n{raw_output}"
       )
   ```

**Evidence of completion:**
- `uv run python -m unittest tests.test_synthesis -v` 全綠

---

## Todo 7: `synthesis.py` — `_claim_from_note()` fallback

**What to do:**

修改 `_claim_from_note()`：aspect 為空時自動填 `"limitations"`：

```python
def _claim_from_note(
    claim: LlmNoteClaim,
    chunk_by_id: dict[str, EvidenceChunk],
) -> PaperSummaryClaim:
    references: list[ChunkReference] = []
    for chunk_id in dict.fromkeys(claim.chunk_ids):
        chunk = chunk_by_id[chunk_id]
        references.append(
            ChunkReference(
                chunk_id=chunk.chunk_id,
                paper_id=chunk.paper_id,
                page_start=chunk.page_start,
                page_end=chunk.page_end,
                quote=chunk.text[:_QUOTE_MAX_CHARS],
            )
        )
    aspect = claim.aspect if claim.aspect else "limitations"
    return PaperSummaryClaim(text=claim.text, aspect=aspect, evidence=references)
```

**Evidence of completion:**
- `uv run python -m unittest tests.test_synthesis -v` 全綠

---

## Todo 8: `test_synthesis.py` — FakeClient 簽名 + 斷言調整

**What to do:**

1. `FakeNoteClient.generate_json(self, prompt: str, schema: dict | None = None) -> str`
2. `RetryNoteClient.generate_json(self, prompt: str, schema: dict | None = None) -> str`
3. `FakeSynthesisClient.generate_json(self, prompt: str, schema: dict | None = None) -> str`
4. `valid_note_payload()` 刪除 `stated_limitations` 欄位，只保留 `claims`
5. 所有斷言從 `note.stated_limitations` 改為 filter `note.claims`：
   - `note.stated_limitations[0].evidence[0].chunk_id` → `[c for c in note.claims if c.aspect == "limitations"][0].evidence[0].chunk_id`
   - 等等

**Evidence of completion:**
- `uv run python -m unittest discover -s tests -v` 全綠（71+ tests）

---

## Todo 9: 修復 LLM 幻覺 chunk ID（真實 API 測試發現）

**問題**：`OpenScholar.pdf` 有 67 chunks 但只送 40 個給 LLM。LLM 看到 chunk ID 格式後發明 `OpenScholar.pdf-p5-5-c16` 等不存在的 ID，`unknown_ids` 檢查攔截後整條 pipeline 失敗。

**What to do:**

1. `literature_review/synthesis.py` — `build_paper_notes_prompt()` 強化：
   - 在 prompt 中明確列出完整允許的 chunk ID 清單
   - 加一段「Never invent, guess, or modify chunk IDs. Only use the exact IDs from the list below.」

2. `literature_review/synthesis.py` — `_build_chunk_repair_prompt()`：新增 repair prompt，內容包含錯誤的 chunk IDs + 允許清單

3. `literature_review/synthesis.py` — `summarize_paper_notes()`：當 `unknown_ids` 被攔截時，做**一次** repair 重試（不是直接失敗）

4. 用泛型 helper 或直接在 `summarize_paper_notes` 內實作，重試邏輯與 `_generate_validated` 一致：「先試一次 → 失敗 → repair prompt 再試一次 → 再失敗才 raise」

**Evidence of completion:**
- 新增測試：fake client 第一次回傳幻覺 ID → 驗證會觸發 repair 重試 → 第二次回傳正確 → pipeline 成功
- `uv run python -m unittest discover -s tests -v` 全綠

---

## Final verification

完成所有 Todo 後，執行：

```powershell
uv run python -m unittest discover -s tests -v
uv run python -m literature_review.pipeline data/papers "literature review agent" --top-k 5 --dry-run
```

然後由使用者執行真實 API 測試：
```powershell
uv run python -m literature_review.pipeline data/papers "literature review agent" --top-k 5
```

目標：真實 API 測試成功輸出 SynthesisResponse，不再出現 `stated_limitations.0.aspect: Field required` 驗證錯誤。
