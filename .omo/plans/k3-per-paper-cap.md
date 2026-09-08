# K3:per-paper cap 遞補 + shrinkage_strength 4→3(固定下載集驗收)

- slug:`k3-per-paper-cap`
- date:2026-09-08
- status:approved(使用者 2026-09-08 確認:固定下載集、遞補方式、cap=6、shrinkage 4→3)
- milestone:K2 的後繼(檢索分配公平化 + 聚合微調)

## 動機與背景

- **K2 教訓**:跨論文 top-32 被 W4401667275 單篇吃 17/32(53%),分配集中,「更多論文拿到評分券」的假設未成立。
- **K2 歸因勘誤**(使用者 2026-09-08 發現):compare 表格「K 獨有(被擠出前 32)」是錯誤歸因——真正原因是兩輪下載集不同(LLM plan 隨機 + OpenAlex 浮動),W4402901320/W4416209823 在 K2 根本沒被下載。同 pool 下 16→32 全是「拿更多」。**結論:跨 run 對比必須固定下載集。**
- **設計**(使用者定案):cross-paper top-k 加 **per-paper quota(遞補式)**,單篇最多 6 chunks,超過的名額遞補給後面的論文;`shrinkage_strength` 4→3(少 chunks 論文分數減少被 prior 5.5 壓低;配合 cap 後 n≤6,風險可控)。

## Scope

- 改:models.py(`EvidenceRetrievalPolicy.max_chunks_per_paper` 新欄位、`EvidenceAggregationPolicy.shrinkage_strength` default 4→3)、embedding_retriever.py(遞補邏輯)、main.py(傳 policy cap=6)、tests、docs。
- 不:動 include/consider 閾值、prior_score、top_k 值(維持 32)、ranking 邏輯、LLM prompt。

## Todo 1 — 欄位與常數

- `models.py`:
  - `EvidenceRetrievalPolicy` 加 `max_chunks_per_paper: int | None = Field(default=None, ge=1, le=50)`(None = 無 cap,舊行為完全保留;le=50 對齊 `max_chunks_per_paper` 既有上限)。
  - `EvidenceAggregationPolicy.shrinkage_strength: int = Field(default=4, ...)` → `default=3`。
- 檢查既有測試是否 assert 這兩個預設值(有則同步)。

## Todo 2 — 遞補實作 + 測試

- `embedding_retriever.py` `_score_ranked`:在 `scored[: policy.top_k]` 截斷前加遞補邏輯:
  ```
  selected = []
  per_paper = {}   # paper_id -> count
  for rank, (chunk, score) in enumerate(sorted_scored, 1):
      if policy.max_chunks_per_paper is None:
          selected.append(...); (維持原 path:直接取前 top_k,行為不變)
      else:
          if per_paper.get(chunk.paper_id, 0) >= max_chunks_per_paper: continue
          selected.append(...); per_paper[chunk.paper_id] += 1
          if len(selected) == policy.top_k: break
  ```
  - chunk 的 paper_id 從 `chunk.paper_id`(EvidenceChunk 已有該欄位,id 格式 `{paper_id}-...`)取得。
  - rank 編號與現況一致(選中者依序 1..N)。
- 測試(`tests/test_embedding_retriever.py` 或既有檔):
  1. `max_chunks_per_paper=None` 行為與舊完全一致(現有測試全數保留即證明)。
  2. fake encoder 固定 similarity 下:單篇論文 chunks 超過 cap → 只留前 N,名額遞補給下一篇論文(斷言兩篇論文各自的 chunk 數與總數)。
  3. cap 後不足 top_k(論文數×N < top_k)→ 全收,不 error。
  4. policy `max_chunks_per_paper=6`、`top_k=32` 時 32 名額分配給 ≥5 篇論文(合成資料)。
  5. shrinkage_strength default 3 的聚合測試(before/after 數值斷言同步)。
- 全套件 `uv run python -m unittest discover -s tests -v` 全綠(log → .omo/evidence/k3-task-*.log)。

## Todo 3 — 固定下載集驗收(retrieval 層,無 LLM)

- **固定集**:K2 的 18 個 PDF(現於 `/tmp/k2-after-downloads/`;執行代理複製到 `/tmp/k3-fixture/` 避免動原檔;若檔案缺失,由 data/papers 原始備份或其他證據還原,並在 compare 註記)。`data/papers/` 本身零變更。
- 用 pipeline 的多 PDF 抽取 → `retrieve_evidence_embedding`(encoder 真實 bge,本地免 key)對比兩設定:
  - A:`EvidenceRetrievalPolicy(top_k=32, max_chunks_per_paper=None)` — 再現 K2 分配
  - B:`EvidenceRetrievalPolicy(top_k=32, max_chunks_per_paper=6)` — cap 版
- 驗證點:
  1. **A 的 chunk 分配 sanity**:對照 K2 實際分配(17/6/3/6 給 W4401667275/W7140287209/W4393065402/W4391006361),確認固定集重現 K2 的 retrieval 結果(證明 fixture 有效;若不完全一致,記錄差異來源:抽取差異/PDF 版本)。
  2. **B vs A 的差異表**:每篇論文 chunk 數(≤6?)、進評分論文數、60 名額外的論文得到第 1 個評分券。
  3. 期望:進評分論文數 > 4(無 cap 的 K2 值),分配不再被單篇壟斷。
- 產出 `.omo/evidence/k3-retrieval-compare.md`(表格 + 結論;若 A 無法重現 K2,仍照實記錄並分析)。

## Todo 4 — 真實 run sanity(LLM 完整流程)

- 條件:前置 Langfuse health 200;`uv run --env-file .env`(既有環境問題先例)。
- 跑一次完整真實 run(query「literature review agent」,cap=6、shrinkage=3、top_k=32):重點看 **進評分論文數、usable 數、分數分布、報告產出**(inline citations、future directions)、AIza 0 命中、failed_extractions。
- 下載仍會浮動(main.py 重新下載)— 接受,但 compare 需註記「真實 run 下載集與 fixture 可能不同」,retrieval 層結論以 Todo 3 固定集為準;真實 run 只當 cap+shrinkage 的 sanity。
- 產出 `.omo/evidence/k3-after-real.log` + `k3-after-dist.json`(同 K2 格式)。
- 結論規則:usable 增加 / 持平(附分數分析);shrinkage 3 + cap 6 的組合效果明確記錄。

## Must NOT

- 不調 include/consider 閾值、prior_score、top_k(維持 32)。
- 不 commit;不碰 `.env`(不讀不印 key)、`data/papers/`(零變更)、`.omo/drafts/`。
- 不 spawn 子代理;不跑額外真實 run。
- 不改 LLM prompt / synthesis 邏輯。

## Success criteria

1. tests 全綠(含新遞補/聚合測試)。
2. Todo 3 固定集對比產出:cap 後單篇 ≤6、進評分論文數 > 4(或如實記錄反例)。
3. Todo 4 真實 run 完成,產物齊全、無 AIza、`generated_by=llm`(sanity)。
4. compare 文件含歸因(固定集 vs 真實 run 差異)。

## Commit strategy(使用者親做)

```powershell
git add literature_review/models.py literature_review/embedding_retriever.py literature_review/main.py tests/ README.md HANDOFF.md .omo/plans/k3-per-paper-cap.md .omo/STATE.md
git commit -m "feat: K3 per-paper cap (6) with backfill in cross-paper top-k; shrinkage strength 4->3"
git push
```