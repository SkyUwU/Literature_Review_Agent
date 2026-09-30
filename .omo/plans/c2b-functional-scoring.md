# C2b: 功能性分數評分改版(取代 rel/qual 雙維 + 配額∩閾值入選)

- slug:`c2b-functional-scoring`
- date:2026-09-10
- status:✅ 已核准(2026-09-10 拍板功能性分數系列 + 2026-09-11 審核 Q1-Q7 修入 + 使用者核准,閾值 6.0 暫定)
- 前置:C2a(章節切分 `section` + `drop_noise_sections`)為本計畫輸入基礎;C2b 完成後接 C2c(筆記章節餵入 + 報告生成 claim 標註)

## 動機(背景事實)

1. **rel/qual 雙維缺「使用價值」**(J,2026-09-06 起討論):現況只評「主題契合 + 內容可信」,無法區分「高度相關但無推進(漂亮綜述)」與「提供可複用方法/可對比數據」——後者才是研究 idea 需要的。
2. **rel 的 query 字面偏誤**:relevance 是對 sub-query 的相關度,跨 query 不可比;C 桶(方法論文)rel 中等卻高價值 → 現行「rel≥8 且 qual≥6」AND 閾值抹殺(M5e 分桶實證)。
3. **使用者 2026-09-10 定案**:功能性單一 1-10 分數**取代** rel/qual 雙維——評「該 chunk 對研究 idea 的貢獻」,與 query 字面無關 → 跨 query 同尺;J「第三維度」演進為「合併維度」,契約簡化。
4. **入選機制**:配額∩閾值**直接取代** include/exclude(方案 A):per-query 內部比較、各挑前 N 篇(第一輪 query 各 2、follow-up 各 1),且平均分 ≥ 閾值——多樣性(配額)與品質(閾值)雙保底。收縮平均退役(每篇等量 top-2 → 平均直接可比)。
5. **C2a 提供輸入**:chunk 帶 `section`、黑名單工具 `drop_noise_sections`;chunk_id = `{paper_id}-c{n}`;page 欄位 Optional。
6. RCS(summarize_and_rerank)的 rel/qual+summary 在 C2b 被取代 → 只留 compare/legacy(比照 evidence_ranking 先例)。

## 現況 code 事實(2026-09-10 查證)

- `models.py`:`LlmEvidenceAssessment`(chunk_id/summary/rationale_relevance/rationale_quality/relevance_score/evidence_quality_score)、`LlmEvidenceAssessmentBatch`、`EvidenceSummary`(繼承+paper_id/page)、`EvidenceRerankResponse`、`EvidenceCitation`(chunk_id/page/summary/rel/qual)、`PaperAssessment`(rel/qual/recommendation include|consider|exclude/evidence)、`EvidenceAggregationPolicy`(include 8/6、consider 6、prior 5.5、shrinkage 1)。
- `llm_evidence.py`:`summarize_and_rerank` B=4 批次、index 制(`## Chunk N`)+ expected-chunk-ids repair、一次 repair 預算;`build_evidence_prompt` 已帶 citation/venue 參考訊號。
- `assessment.py`:`_shrunk_mean` + include/consider 判定。
- `pipeline.py`:`run_synthesis_pipeline` = chunk_document → `retrieve_evidence_embedding`(corpus-wide top_k=32)→ RCS → 聚合 → usable_ids → notes → `synthesize_report`。
- `main.py`(M5e):`screen_candidates` 決定 keep/maybe → `download_and_backfill(priority_groups=...)`;**downloads 是 flat list,沒有 query 歸屬**;`FullTextDocument` 沒有 title/query 欄位;`paper_meta` 只有 (citation_count, venue)。
- `synthesis.py`:notes 輸入 = 該論文全部 chunks(`_bounded_chunks_for_llm` cap=40 截斷);`synthesize_report` 吃 `EvidenceAssessmentResponse`(A 區塊 summaries + B 筆記);allowed markers = RCS summaries ∪ notes coverage;`_convergence_direction`/`_fallback_direction` 用 rel/qual。

## 設計決策(2026-09-10 使用者定案 + 本計畫裁示點)

- **功能性分數契約**:新 `LlmFunctionalAssessment` = `{chunk_id, rationale, utility_score}`(1-10;**先 rationale 後 score**——沿用 M4 I/A10「先理解再給分」;summary 欄位移除,筆記輸入是 chunks 原文不需要 summary;三層結構簡化為「理由→分數」)。
- **評分流程(取代 corpus-wide RCS)**:
  1. 每篇論文:chunks(C2a 章節切分)→ `drop_noise_sections`(黑名單)→ **per-paper embedding top-2 取樣**(在該論文自家 chunks 內檢索;**embedding query 與評分 prompt 的 query 皆 = 主 research query(使用者輸入的那句,非 sub-query)**——功能性評分與 sub-query 字面無關);**黑名單後無 chunks 的論文 → 直接排除並記錄 rationale(Q2 審核補入)**。
  2. **小批次 5/batch** 功能性評分(429 風險低 + 無陪襯效應);每 chunk 包裝 `paper title + section`(C2a 提供 section;title 由 main.py 傳入,參考訊號先例=M5b citation/venue 延伸)。
  3. **每篇平均**(等量 top-2 → 直接平均可比,**收縮平均退役**;若某篇黑名單後 <2 chunks → 以實際數平均,樣本數記入 rationale)。
  - **top-2 取樣章節偏誤為接受取捨(Q5 審核補入)**:embedding 取樣自然偏好 method/results 類,若論文貢獻集中在 limitations(gap 陳述)可能漏——記錄為觀察點;若真實 run 證據顯示低估,後議「章節類別分區取樣(method 1 + limitation 1)」,本計畫不做。
- **論文層入選 = 配額∩閾值**:
  - per-query 分組(論文→query 歸屬由 main.py 從 decisions/downloads 記錄傳入);
  - 各 query 內依平均分排序,取前 N 篇(N=2 第一輪、N=1 follow-up),**且平均 ≥ 閾值**;
  - 未達閾值的配額空缺不遞補(誠實 shortfall,比照 M5e 下載 shortfall 精神);
  - `PaperAssessment` 改:`utility_score`(float,平均)取代 rel/qual;`recommendation` 改 `Literal["include","exclude"]`(**去 consider**——配額∩閾值是兩分法,灰階語意退役);rationale 記錄「平均分、配額來源 query、閾值判定」;evidence 保留(功能性 citations)。
  - **閾值預設值(裁示點)**:語意 = 10 直接推進 idea / 5 背景價值 / 1 無貢獻。先設 **6.0**(env `FUNCTIONAL_THRESHOLD` 覆寫),Todo 5 校準後調整——因 M5b.1 的 8/6 基準已斷裂,功能性是新尺,舊值不可沿用。
- **下游相容(C2b 範圍內最小改動)**:`summarize_and_rerank`/`aggregate_evidence_assessments` 保留為 compare/legacy(不刪);`synthesize_report` 改吃「功能性 assessment + 筆記」,**報告 prompt 不再送 A 區塊(chunk summaries)——E「報告輸入瘦身」定義的拿掉 A 因此提前在本計畫落地**;allowed markers = 入選論文的功能性評分 chunks ∪ notes coverage;deterministic directions 改用 utility_score。
- **client**:功能性評分走 `client_rcs`(Ollama 有則用 / fallback client_synth key2)——與現況 RCS 相同線路;第三 key 待議(不變)。
- **不接 C2c 範圍**:筆記仍吃 chunks 全量截斷(章節餵入=C2c);報告 claim 標註=C2c;`data/papers` 策略不動。

## Todo 0 — 契約改版(models.py)

- 新:`LlmFunctionalAssessment`(chunk_id、rationale `min_length=20`、utility_score `ge=1 le=10`)、`LlmFunctionalAssessmentBatch`(assessments `min_length=1`)。
- `PaperAssessment`:移除 rel/qual 兩欄 → `utility_score: float`(`ge=1 le=10`);`recommendation: Literal["include","exclude"]`;`evidence: list[EvidenceCitation]` 保留。
- `EvidenceCitation` 改功能性:移除 summary/relevance_score/evidence_quality_score → 加 `utility_score: int`(`ge=1 le=10`)、`rationale`(`min_length=20`)(chunk_id/page 保留,page 為 Optional——C2a)。
- 新 `FunctionalScoringPolicy`:`batch_size=5`(env `FUNCTIONAL_BATCH_SIZE`)、`top_chunks_per_paper=2`、`n_first_round=2`、`n_follow_up=1`、`threshold=6.0`(env `FUNCTIONAL_THRESHOLD`)、`min_words=4`(沿用)。
- 新 response:`FunctionalAggregation`(paper_id/utility_score/evidence/path…)或直接 list[PaperAssessment](裁示:C2b 用 list + wrapper dict,避免過度建模)。
- 既有 `LlmEvidenceAssessment`/`EvidenceSummary`/`EvidenceRerankResponse`/`EvidenceAggregationPolicy`/`AssessmentPolicy`(metadata deprecated 區)保留(legacy compare)。
- 測試:新契約驗證(分數範圍、rationale 長度、extra forbid)、PaperAssessment 遷移、legacy 不變。

## Todo 1 — 功能性評分函式(llm_evidence.py 或新 functional.py)

- `build_functional_prompt(query, chunks, paper_titles) -> str`:
  - **`query` 參數 = 主 research query(呼叫端保證傳主 query,非 sub-query——Q7 審核明確化,防 per-query 迴圈誤用 sub-query)**;
  - 每 chunk:`## Chunk N` + `Paper: {title} | Section: {section}` + text(參考訊號:title/section 包裝;**index 制沿用 M5b**——不送真實 chunk_id,程式 resolve);
  - 指示:評「對 research idea 的貢獻(utility)」非 query 字面;10=直接提供可複用的方法/數據/填補缺口、5=有用背景、1=無貢獻;先寫 rationale(用自己的話重述 chunk 核心 + 對 idea 的關係)再給分。
- `score_chunks_functionally(query, chunks, client, *, batch_size, paper_titles) -> list[LlmFunctionalAssessment]`:
  - 批次迴圈 + index resolve + expected-ids repair(一次預算)——結構沿用 `summarize_and_rerank` 的批次防竄改設計(M5b 教訓);langfuse observe。
- 測試:批次切分、index resolve、竄改/缺漏 repair、prompt 包裝(title/section 出現)、fake client JSON 驗證。

## Todo 2 — per-paper 取樣與平均(functional_scoring.py 或 pipeline 內)

- `sample_top_chunks_per_paper(paper_chunks: list[EvidenceChunk], query, *, top_n, encoder) -> dict[str, list[EvidenceChunk]]`:
  - 每篇:黑名單過濾 → 自家 chunks 內 embedding 檢索 top_n(embedding query = 主 query;encoder 注入比照現況);不足 top_n 以實際數;**黑名單後無 chunks → 該論文不進評分(記錄)**
- `aggregate_functional(assessments, per_paper_chunks) -> dict[str, FunctionalPaperScore]`(**審核 Q1 修:此階段產「分數容器」非最終 PaperAssessment**):
  - `FunctionalPaperScore`:paper_id、utility_score(float,round 1 位)、n_samples、evidence(功能性 citations)、rationale 素材(平均分 + 樣本數 + 來源 query 待 Todo 3 填);
  - recommendation **不在此階段設定**(避免「先建後改」接口矛盾)。
- 測試:黑名單前處理、per-paper top-2 不跨論文、不足數、無 chunks 排除、平均正確、fake encoder。

## Todo 3 — 配額∩閾值論文層入選

- `select_quota_threshold(scores: dict[str, FunctionalPaperScore], paper_queries: dict[str, str], follow_up_queries: set[str], *, n_first_round, n_follow_up, threshold) -> list[PaperAssessment]`:
  - per-query 分組 → 各組內按 utility_score 降序 → 取前 N(第一輪 2 / follow-up 1)→ 篩平均 ≥ threshold → **統一在此建最終 `list[PaperAssessment]`**(recommendation: 入選=include、其餘=exclude;rationale 記「平均分、n_samples、配額來源 query、閾值判定」;**黑名單後無 chunks 的論文在此以 exclude + 原因記錄**)——**審核 Q1 修:最終 assessment 只有 Todo 3 產出**;
  - 回傳 sorted assessments(pipeline 用入選 set 驅動 notes)。
- 測試:分組、配額截斷、閾值篩選、follow-up 組 N=1、shortfall 誠實、邊界(相等分數平手規則:chunk_id lexicographic)。

## Todo 4 — 接線(pipeline.py + main.py + synthesis.py 相容)

- `pipeline.run_synthesis_pipeline` 改:
  - 新參數 `paper_titles: dict[str, str]`、`paper_queries: dict[str, str]`、`follow_up_queries: set[str]`(都 optional,缺省退化成「全部分在一組、N=len」相容舊測試);
  - **`retrieval_policy`(top_k/max_chunks_per_paper)退役(Q4 審核修)**:corpus-wide RCS 不存在後該參數目標消失;per-paper 取樣改由 `FunctionalScoringPolicy.top_chunks_per_paper=2` 驅動——**main.py 的 `TOP_K_CHUNKS=32` 硬編碼一併移除或標 legacy 不使用**;
  - 流程:`chapter/prepare` → per-paper top-2 取樣 → `score_chunks_functionally` → `aggregate_functional` → `select_quota_threshold` → 入選 → notes(逐篇,pacing 保留)→ `synthesize_report`(新輸入)。
  - `client_rcs` = 功能性評分 client(沿用現況參數名,語意變「功能性評分 client」;fallback 到 client_synth 時,**批次間沿用 429 retry 守則 + NOTES_PACING 精神,必要時功能性批次也加 pacing**(Q6 審核補入))。
- `main.py`:
  - downloads 迴圈記錄 `paper_id → query`(keep/follow-up 來源)——`paper_queries`/`follow_up_queries` 傳入;
  - `paper_titles` 從 ranked_by_id(Paper.title)組裝;
  - `FUNCTIONAL_*` env 透傳(或 policy 參數)。
- `synthesis.py` 相容(拿掉 A 區塊):
  - `build_synthesis_prompt` 改只送 B(筆記)+ 入選論文的功能性 assessment 摘要(rationale/utility/證據 chunks 清單);移除 summaries 區塊;
  - `_allowed_marker_ids` 改:入選論文的功能性證據 chunks ∪ notes coverage;
  - `_convergence_direction`/`_fallback_direction` 改用 utility_score——**審核 Q3 修:收斂判據 `_CONVERGENCE_UTILITY_SCORE = 6`(與入選閾值一致,語意「有實質貢獻」;舊 rel 尺的 4 不可沿用)**;
  - `_render_report`/deterministic 路徑同步(legacy deterministic 保留相容)。
- 測試:fake e2e(shortfall=0 案例)、dry-run 不觸 key、每篇入選論文有 notes、synthesize marker 驗證、舊測試(契約欄位)遷移。

## Todo 5 — 測試/驗收 + 校準掛帳

- 既有套件全綠 + 新測試全綠;log `.omo/evidence/c2b-tests.log`。
- fake e2e:全鏈路(計畫 fake → 下載 fake → 抽取/章節 → top-2 → 功能性 fake client → 配額∩閾值 → notes → 報告),驗收:入選數符合配額與閾值、chat markers 全在 allowed set。
- **真實 run 掛帳**(修改清單清空後一次整條,比照 M5e Todo 5):指令 `printf 'literature review agent\n' | uv run --env-file .env python -m literature_review.main`;收集:
  - 功能性分數分布(每篇平均、chunk 分數直方圖);
  - 入選論文數 vs 配額上限 / shortfall;
  - **「keep 但 exclude」衝突集合人工抽查**(M5e Todo 5 驗收判準——分辨 M5e 選錯 vs 功能性誤殺);
  - 閾值校準建議(6.0 預設 vs 分布);
  - 對照 M5b.1 斷裂基準(舊 rel 8.5 不可比,只記分布不硬比)。
- 回報格式:測試數、檔案變更、證據路徑、入選/排除統計、衝突集合、閾值建議。

## Must NOT

- **不接 C2c 範圍**:筆記仍吃 chunks 截斷(不改章節餵入)、報告不做 claim 標註、不做 A5(材料清單移出可由本計畫 synthesis 相容順帶,但不做 claim 標註)、不做第三 key。
- 不刪 legacy(`summarize_and_rerank`/`aggregate_evidence_assessments`/`chunk_document`/metadata assessment 全保留 compare)。
- 不 commit(使用者親做);不碰 `.env`/`data/papers/`;不 spawn;不印 key。
- `PaperAssessment` 語意無法共存時,以「functional 是唯一正式路徑」為準更新測試,禁止只改實作不改契約。

## Success criteria

1. 功能性分數取代 rel/qual 於正式路徑(RCS 只留 compare);契約/測試全綠(248 不減,遷移為新預期)。
2. 入選 = 配額∩閾值:每 query 前 N 篇且 ≥ 閾值;shortfall 誠實記錄。
3. fake e2e 全鏈路通過(無 key、fake client)。
4. 報告 prompt 不再送 chunk summaries(拿掉 A),marker 驗證仍全綠。
5. 真實 run 掛帳完成條件齊備(指令、收集項、衝突抽查判準)——待 OpenAlex 負載與修改清單清空後執行。

## Commit strategy(使用者親做)

```powershell
git add literature_review/models.py literature_review/functional.py literature_review/llm_evidence.py literature_review/pipeline.py literature_review/main.py literature_review/synthesis.py tests/ HANDOFF.md
git commit -m "feat: C2b functional scoring replaces rel/qual and quota-threshold selection"
git add .omo/STATE.md .omo/plans/c2b-functional-scoring.md
git commit -m "docs: record C2b plan and project state"
git push
```

(確切檔案以 `git status` 為準)

## 風險自審

| # | 風險 | 對策 | 殘餘 |
|---|---|---|---|
| P1 | Ollama 8B 對功能性評分遵從弱(相對 rel/qual 更主觀)→ 分數漂移/竄改 | index 制 + expected-ids repair 沿用;B=5 小批次;10/5/1 操作型錨點;Ollama 失準 → fallback Gemini 線路(client_rcs=None 時) | 中 |
| P2 | 閾值 6.0 未校準 → 入選過多/過少 | env 覆寫;Todo 5 真實 run 校準(分布 + 衝突抽查) | 中 |
| P3 | per-query 分組資料流錯(paper 歸錯 query)→ 配額誤配 | main.py 在 decisions/downloads 當下記錄;測試斷言分組 | 低 |
| P4 | per-paper top-2 取樣 embedding query = 主 query 與 sub-query 檢索層不一致之爭議 | 功能性語意既定「對 idea 貢獻」,主 query 為唯一合理基準;記錄於 HANDOFF | 低 |
| P5 | synthesis 相容改動(拿掉 A)影響面大(allowed markers/convergence/fallback) | 與 C2a 同策略:最小相容 + 全測試遷移;E 瘦身本屬定案,提前落地 | 中 |
| P6 | 某篇黑名單後 <2 chunks → 平均樣本少 | 以實際數平均(rationale 記樣本數);不補償不收縮(使用者定案) | 低 |
| P7 | legacy 契約與新契約並存造成混淆 | docs/HANDOFF 標 compare-only;STATE 更新 | 低 |
| P8 | per-paper top-2 取樣章節偏誤(偏好 method/results,漏 limitations/gap 貢獻)→ 低估 | 接受取捨 + 真實 run 觀察點;證據顯示低估 → 後議「章節類別分區取樣」(2026-09-10 審核補入) | 中 |
| P9 | C2a 新章節 chunking 下筆記截斷品質(段落 chunks 更多、cap=40 覆蓋下降) | 已知問題,C2c(筆記章節餵入)處理——C2b 只記錄不修(2026-09-10 審核補入) | 中 |
| P10 | fallback 到 key2 時功能性評分批次連打 → 429 | 429 retry 守則沿用 + 必要時批次間 pacing(與 NOTES_PACING 同精神)(2026-09-10 審核補入) | 低-中 |

## 下一步行動卡

1. 使用者核准本計畫(或調整裁示點:閾值預設、recommendation 兩分法)。
2. 核准後與 C2a 執行並列:執行代理先完成 C2a → 接著 C2b(計畫獨立、可分別驗收)。
3. 執行代理回報 → 規劃驗收 → 使用者 commit。
4. C2b 完成後寫 C2c(筆記章節餵入 + 報告 claim 標註)。