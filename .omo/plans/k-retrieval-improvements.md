# k-retrieval-improvements - Work Plan

## TL;DR (For humans)
<!-- 最後填 -->

**What you'll get:** 論文排名從「關鍵詞命中」升級為「語意相似度」(embedding),同時把三組規模參數調大(LIMIT 100、TOTAL_TARGET 20、TOP_K_CHUNKS 16),最後跑一次真實 end-to-end 對比,看分數分布是否上移、報告是否生得出來。

**Why this approach:** M4 改後真實 run「0 篇 include/consider」很可能不是閾值太嚴,而是候選集本身低相關(關鍵詞字面排名 + top-N 截斷把相關但用詞不同的論文擋在外面)。先修檢索層(治本),不夠才校準閾值(治標),避免先降後改回。

**What it will NOT do:** 不調閾值、不動 OpenAlex 搜尋語法、不改 planner prompt、不放寬無 abstract 論文、不加 utility 第三維度、不碰 search.py CLI 與 lexical 舊排名(保留比對)。

**Effort:** Short(4 todos,單一波)
**Risk:** Low - 純本地 embedding 替換 + 參數調高,無網路/LLM 契約變更;dry-run 契約由測試保護

**Decisions to sanity-check:** TOTAL_TARGET=20(你給 20-25 取下限);embedding score 用 max(cosine,0) clip;dry-run 維持 lexical ranking(保零模型承諾);真實 run encoder 建置失敗 = pipeline error(不靜默 fallback)。

Your next move: approve。Full execution detail follows below.

---

> TL;DR (machine): Short effort, Low risk - semantic paper ranking (bge-small-en-v1.5 title+abstract cosine) + LIMIT 100 / TOTAL_TARGET 20 / TOP_K_CHUNKS 16; real-run A/B vs M4-after distribution; thresholds untouched.

## Scope
### Must have
- `ranking.py`:新增 `rank_papers_embedding(papers, query, encoder)`(三成分等權:embedding cosine + citation + recency);`filter_and_rank(response, policy, *, encoder=None)`——`encoder=None` 走既有 lexical(相容),非 None 走 embedding。
- `main.py`:_LIMIT=100_、_TOTAL_TARGET=20_、_TOP_K_CHUNKS=16_;`run_end_to_end(..., encoder=None)` 新增注入參數;非 dry-run 且未注入時 `default_encoder()`;dry-run 維持 `encoder=None`(零模型承諾)。
- 測試:embedding 排名單元測試(fake encoder)+ main 注入/dry-run 測試;全部測試綠(196 + 新增)。
- 文件:README / AGENTS.md / HANDOFF.md / STATE.md 同步常數與 ranking 描述。
- 真實驗收:同 query「literature review agent」完整 run(key1+key2+Langfuse)→ 對比 `.omo/evidence/m4-after-dist.json`(before)→ 產出 log + dist + compare 報告;不足才給閾值校準建議(不實作)。

### Must NOT have (guardrails, anti-slop, scope boundaries)
- ❌ 不調 `EvidenceAggregationPolicy` 任何閾值(include/consider/prior 全不動)。
- ❌ 不放寬 `search.py:98` 無 abstract 論文丟棄(獨立議題)。
- ❌ 不改 OpenAlex 搜尋 URL 語法(`build_search_url` / `REQUESTED_FIELDS` 不動)。
- ❌ 不改 planner prompt / SearchPlan(候選 L 獨立)。
- ❌ 不加 utility 第三維度(候選 J)、Unpaywall(D)、PDF magic bytes(B)。
- ❌ 不刪/不改 lexical `rank_papers` 與 `RankedPaper.matched_terms` 契約；`search.py` CLI 行為不變。
- ❌ 不碰 `data/papers/`、`.env`；不 git commit(使用者親做)。

## Verification strategy
> Zero human intervention - all verification is agent-executed.
- Test decision: **tests-after**(新增功能以測試覆蓋;既有 196 測試全綠為回歸安全網)+ `unittest`(專案現用框架)。
- Evidence: `.omo/evidence/k-task-<N>-<slug>.log`(attemptDir = `.omo/evidence/`;執行代理依專案慣例存檔)。

## Execution strategy
### Parallel execution waves
- **Wave 1(Todo 1-3)**:ranking + main + 文件 —— 純本地,可平行(但按檔案依賴:Todo 1 → Todo 2 依賴其簽名;實務上同一執行代理順序做)。
- **Wave 2(Todo 4)**:真實驗收 run(需 key + Langfuse server 在線)。
- **Wave 3(F1-F4)**:規劃 agent 驗收(平行)。

### Dependency matrix
| Todo | Depends on | Blocks | Can parallelize with |
| --- | --- | --- | --- |
| 1 | - | 2(簽名) | - |
| 2 | 1 | 3,4 | - |
| 3 | 2 | 4(文件須反映 param) | - |
| 4 | 2,3 | F1-F4 | - |
| F1-F4 | 4 | - | 彼此 |

## Todos

- [ ] 1. ranking.py: 新增 `rank_papers_embedding(papers, query, encoder)` 與 `filter_and_rank(..., *, encoder=None)` embedding 分支 — 語意排名正式路徑
  What to do / Must NOT do:
  - 在 `literature_review/ranking.py` 新增 `from literature_review.embedding_retriever import Encoder, encode_query`(embedding_retriever 只 import models,無循環依賴)。
  - 新增 `rank_papers_embedding(papers: list[Paper], query: str, encoder: Encoder) -> list[RankedPaper]`:
    - `query_vector = encode_query(query, encoder)`(BGE 官方前綴由 `encode_query` 處理);
    - 論文側 `texts = [f"{paper.title}\n{paper.abstract}" for paper in papers]`,`vectors = encoder(texts)`(title+abstract 合併單一向量);
    - **長度防護**:`if len(vectors) != len(papers): raise ValueError("encoder returned ...; expected ...")`(防 encoder 靜默截斷造成漏評論文);
    - `similarity = max(sum(q*v for q, v in zip(query_vector, vector, strict=False)), 0.0)`(cosine ≥0 clip;encoder 已 normalize → dot = cosine);
    - `citation_score = min(math.log1p(paper.citation_count or 0) / math.log(1001), 1.0)`、`recency_score = min(max((paper.year - 2020) / 10, 0.0), 1.0)` —— 與現況 `rank_papers`(ranking.py:73-74)完全一致;
    - `total_score = similarity + citation_score + recency_score`;sort key 與 `rank_papers` 一致(`(-score, -citation, -year)`);
    - `RankedPaper(matched_terms=[], rationale="Semantic ranking using bge-small-en-v1.5 embeddings of the query vs. title+abstract; citation & recency contribute equal weight.")`,`score=round(score, 3)`。
  - `filter_and_rank(response, policy, *, encoder=None)`:encoder 非 None → `rank_papers_embedding(filter_papers(...), response.request.query, encoder)`;None → 現有 `rank_papers`(lexical,完全原行為)。
  - 不得修改 `rank_papers`/`filter_papers`/`query_terms` 現有行為;不得改 `RankedPaper` 模型。
  - Must NOT:不要在 ranking.py 內呼叫 `default_encoder()`(避免 ranking 依賴模型下載;encoder 一律由 caller 注入)。
  Parallelization: Wave 1 | Blocked by: - | Blocks: 2
  References:
  - `literature_review/ranking.py:59-91`(現 rank_papers 三成分)、`:94-101`(filter_and_rank)
  - `literature_review/embedding_retriever.py:17`(QUERY_PREFIX)、`:49-55`(encode_query)、`:26-41`(default_encoder,normalize → dot=cosine)
  - `literature_review/models.py:87-94`(RankedPaper:matched_terms 非 optional → `[]`;rationale 無 min_length)
  - `literature_review/selection.py:22`(select 只用 `.score`,matched_terms=[] 無下游影響)
  Acceptance criteria (agent-executable):
  - `uv run python -m unittest discover -s tests -v` 全綠(196 既有 + 新增)。
  - 新增測試(`tests/test_ranking.py` 內):(a) fake encoder 依 title 內容回傳可控向量 → 排序符合 cosine 期望;(b) 高 citation/recency 補償低 similarity(三成分並存);(c) 負 similarity 被 clip 為 0(不拉低總分);(d) `filter_and_rank(response, policy, encoder=fake)` 走 embedding(rationale 含 "Semantic ranking";matched_terms=[]);(e) `filter_and_rank(response, policy)`(無 encoder)行為與舊 lexical 完全一致(rationale 含 "Matched")。
  QA scenarios (exact tool + invocation):
  - happy:`uv run python -m unittest tests.test_ranking -v`;failure:fake encoder 回傳錯誤長度 → 明確失敗訊息(tests 斷言 zip strict 或長度檢查);Evidence `.omo/evidence/k-task-1-k-retrieval-improvements.log`
  Commit: Y | feat(ranking): semantic embedding ranking for papers (bge title+abstract cosine)

- [ ] 2. main.py: 常數調高 + `run_end_to_end` encoder 注入 + 真實路徑餵 `default_encoder()` — 端到端採用語意排名
  What to do / Must NOT do:
  - `main.py:35-38`:改成 `LIMIT = 100`、`TOTAL_TARGET = 20`、`TOP_K_CHUNKS = 16`(`MIN_YEAR=2021` 不變)。
  - import 加 `from literature_review.embedding_retriever import Encoder, default_encoder`。
  - `run_end_to_end(..., encoder: Encoder | None = None)` 新增 keyword-only 參數;在 for 迴圈**前**計算一次:`effective_encoder = None if dry_run else (encoder if encoder is not None else default_encoder())`;`filter_and_rank(response, FilterPolicy(min_year=MIN_YEAR), encoder=effective_encoder)`。
  - dry-run 路徑(encoder=None)必須完全不觸碰 `default_encoder()`(零模型下載/M3C 承諾)。
  - `main()` 不新增 CLI flag(維持 `--rule-based`/`--dry-run` 兩個即可)。
  - module docstring(`main.py:1-11`)同步:三常數新值 + ranking 語意化說明。
  - Must NOT:不要在 dry-run 分支 import 或呼叫 encoder;不要改 `_make_plan`/clients 邏輯;不要改 `pipeline.run_synthesis_pipeline` 簽名(只改傳入的 `EvidenceRetrievalPolicy(top_k=TOP_K_CHUNKS)`——已是 16)。
  Parallelization: Wave 1 | Blocked by: 1 | Blocks: 3, 4
  References:
  - `literature_review/main.py:35-38`(常數)、`:64-74`(run_end_to_end 簽名)、`:89-101`(plan/rank/download 迴圈)、`:140-145`(retrieval_policy top_k)、`:155-183`(_build_clients)
  - `literature_review/embedding_retriever.py:26-41`(default_encoder;首次下載 ~130MB)
  - `tests/test_main.py:15,309-334`(target_n 用 import 常數推導 → 值改不破)
  Acceptance criteria (agent-executable):
  - 全部測試綠;新增測試:(a) `run_end_to_end(dry_run=True, encoder=<raise-on-call sentinel>)` 不拋錯 → 證明 dry-run 不觸碰 encoder;(b) `run_end_to_end(encoder=fake, json_fetcher=fake, pdf_fetcher=fake)` 下載集合符合 fake encoder 的相似度排序期望(某論文因 embedding 相似度高被選上);(c) dry-run 回傳 dict 不含 report、`dry_run=True`。
  QA scenarios (exact tool + invocation):happy:`uv run python -m unittest tests.test_main -v`;failure:dry-run sentinel encoder 若被呼叫則測試失敗(保護契約);Evidence `.omo/evidence/k-task-2-k-retrieval-improvements.log`
  Commit: Y | feat(main): semantic ranking wired into e2e; LIMIT 100 / target 20 / top-k 16

- [ ] 3. 文件同步: README / AGENTS.md / HANDOFF.md / STATE.md — 常數與 ranking 描述一致
  What to do / Must NOT do:
  - `README.md`「Run the full pipeline (M3C)」段(`LIMIT=50...TOP_K_CHUNKS=8...` 描述)→ 100/20/16 + 一句「論文排名改為 bge-small-en-v1.5 語意相似度」。
  - `AGENTS.md`「Current architecture」與 ranking 描述(如有 `3*title+abstract` lexical 文字)→ 更新為 semantic 三成分;commands 段不需改(pipeline CLI 範例 --top-k 8 是範例值非常數,可留)。
  - `HANDOFF.md`:加 K 里程碑紀錄(完成內容、測試數、驗收結果位置)。
  - `.omo/STATE.md`:「下一步」第 2 項候選 K → 標「執行中/已完成待驗收」(執行代理更新;含測試數與 commit 待辦)。
  - grep 驗證:`LIMIT=50`、`TOTAL_TARGET=15`、`TOP_K_CHUNKS=8` 在 `literature_review/` 與 `README.md`/`AGENTS.md` 零殘留(測試檔 `test_main.py` 的 import 除外——那是引用常數,正確)。
  - Must NOT:不更新 `tests/` 內的數值斷言(它們已引用常數);不碰 `.omo/drafts/`、`.omo/evidence/`(執行代理不得寫 drafts)。
  Parallelization: Wave 1 | Blocked by: 2 | Blocks: 4
  References:
  - `README.md`(M3C 段)、`AGENTS.md`(Current architecture / Terminoltogy 附近)、`HANDOFF.md`(里程碑紀錄區)、`.omo/STATE.md`(下一步/候選 K)
  Acceptance criteria (agent-executable):grep 上述常數於 product/docs 零命中(除 test_main import);README 顯示 100/20/16;git diff 僅含預期檔案。
  QA scenarios (exact tool + invocation):happy:grep 指令 + `git status` 對照;failure:grep 命中殘留 → 修正;Evidence `.omo/evidence/k-task-3-k-retrieval-improvements.log`
  Commit: Y | docs: synchronize K parameters and semantic ranking description

- [ ] 4. 真實驗收 run: 同 query「literature review agent」完整 run + 對比 M4-after — 驗證分數分布上移 / include-consider>0 / 報告產出
  What to do / Must NOT do:
  - **前置**:`curl http://localhost:3000/api/public/health` → 非 200 立即停止並回報「請使用者啟動 Langfuse server」,不繼續(使用者 2026-09-07 已提醒尚未開啟);`.env` 需有 `GEMINI_API_KEY` 與 `GEMINI_API_KEY_2`(不得讀取/列印 key 內容)。
  - 跑:`printf 'literature review agent\n' | uv run python -m literature_review.main`(WSL/bash;Windows 用 `cmd /c "echo literature review agent| uv run python -m literature_review.main"`)→ stdout 存 `.omo/evidence/k-after-real.log`。
  - 從 run 輸出產出 `.omo/evidence/k-after-dist.json`:論文層 relevance/quality 分數分布(min/max/中位數)、include/consider/exclude 計數、下載論文 id 集合、inline citation 數。
  - 寫 `.omo/evidence/k-before-after-compare.md`:對比 before = `.omo/evidence/m4-after-dist.json`——五段:(1) 論文集合 overlap(embedding 換血證據);(2) 分數分布變化;(3) include/consider 數量;(4) synthesis 產出與 citations;(5) **人工抽查入選論文「相關但無用」者**(候選 J 的實證);若仍 0 usable → 附閾值校準建議(比照 M4 先例建議 A/C 值,**不實作**)。
  - 記錄 elapsed、failed_extractions、Langfuse 狀態。
  - Must NOT:不 commit;不碰 `data/papers/`(**下載目標 = temp 目錄如 `/tmp/k-real-*`,比照 M3C/M4 真實 run 先例**——執行代理以臨時 script 或覆寫 DEST_DIR 方式達成,`data/papers/` 零變更);不印/抄 API key;不調任何閾值。
  Parallelization: Wave 2 | Blocked by: 2,3 | Blocks: F1-F4
  References:
  - `.omo/evidence/m4-after-dist.json`(before 基準)、`.omo/evidence/m4-before-after-compare.md`(對比格式先例)
  - `literature_review/main.py`(常數已改)、AGENTS.md(Langfuse health check 指令)
  Acceptance criteria (agent-executable):log 存在且含 plan.generated_by="llm"、無 "Pipeline failed"、無 key 洩漏(AIza 命中=0);dist JSON 與 compare md 存在且五段齊全;若 0 usable → 校準建議段存在。
  QA scenarios (exact tool + invocation):happy:全 run 成功 + compare 產出;failure:Langfuse health 非 200 → 停止回報;開 run 中斷 → 重跑;Evidence `.omo/evidence/k-after-real.log` / `k-after-dist.json` / `k-before-after-compare.md`
  Commit: N(執行代理不 commit;等 F 波通過後使用者收)

## Final verification wave
> Runs in parallel after ALL todos. ALL must APPROVE. Surface results and wait for the user's explicit okay before declaring complete.
- [ ] F1. Plan compliance audit
- [ ] F2. Code quality review
- [ ] F3. Real manual QA
- [ ] F4. Scope fidelity

## Commit strategy
- 執行代理一律不 commit(專案慣例);規劃 agent 也不 commit。
- 使用者(驗收通過後)一次收,PowerShell:
```powershell
git add literature_review/ranking.py literature_review/main.py tests/ README.md AGENTS.md HANDOFF.md .omo/plans/k-retrieval-improvements.md .omo/STATE.md
git commit -m "feat: K semantic paper ranking + larger retrieval budget (LIMIT 100, target 20, top-k 16)"
git push
```
- `.omo/drafts/`、`.omo/evidence/` 在 gitignore,不加 `-f`。

## Success criteria
1. 全部測試綠(196 既有 + 新增 embedding/main 測試)。
2. 真實 run:論文集合與 M4-after 不同(embedding 排名真的改變選擇)、論文層分數分布上移或 include/consider>0、synthesis 產出含 inline citations;或若仍 0 usable,已產出「檢索層已改進但不足 → 閾值校準建議」的明確數據。
3. dry-run 零模型承諾由 sentinel 測試證明。
4. Scope fidelity:out-of-scope 檔案零變更。