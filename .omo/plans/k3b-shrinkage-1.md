# K3b:shrinkage_strength 3 → 1(離線重算驗證)

- slug:`k3b-shrinkage-1`
- date:2026-09-08
- status:approved(使用者 2026-09-08 裁示:m=1——「主要只是限制平均分過度占比,多了 1 就往 5.5 拉許多」;前案 m=2 被取代)
- milestone:K3 的後繼修正(純聚合參數 + 離線驗證,不重跑 LLM 完整 run)
- 取代:`.omo/plans/k3b-shrinkage-2.md`(m=2 草案,未執行)

## 動機

K3 驗收後使用者觀察:cap=6 已限制每篇論文最多 6 chunks(n≤6),而 `shrinkage_strength` 把論文層分數往 prior(5.5)拉,「收縮平均與實測平均差異過大」。**m 3→1**:n=1 時 prior 佔 50%(m=3:75%)、n=6 時 14%(m=3:33%)——收縮僅保留「單一/極少 chunks 不獨裁」的最小干預,分數更貼近實測、鑑別度更高。**聚合層是確定性公式**,可用既有 RCS chunk scores 離線重算,不需重跑 LLM。

## Scope

- 只改:`models.py` 一行 default(3→1)+ 同步測試斷言(`tests/test_models.py`、`tests/test_assessment.py` 中聚合預期值隨 3→1 更新)。
- 不:調其他 policy 值、不改 retrieval/ranking/prompt/synthesis 邏輯、不重跑完整真實 run(除非 Todo 2 備援觸發)。

## Todo 1 — 常數與測試同步

- `models.py`:`EvidenceAggregationPolicy.shrinkage_strength: int = Field(default=3, ge=0)` → `default=1`。
- 同步測試:找到所有依賴 shrinkage 預期值的斷言(test_models/test_assessment)並用公式 `(n*mean + m*prior)/(n+m)`(prior=5.5、m=1)手算更新;若手算與既有斷言語意(consider/exclude 維持)衝突,調整輸入分數維持語意不變。
- 全套件 `uv run python -m unittest discover -s tests -v` 全綠 → log `.omo/evidence/k3b-task-1-shrinkage1.log`。

## Todo 2 — 離線重算對比(m=3 vs m=1)

- **資料來源**:`.omo/evidence/k3-after-real.log` 的區塊 A(每 chunk 的 `relevance_score` / `evidence_quality_score`;若該 log 缺 chunk-level scores → 備援:對固定集 `/tmp/k3-fixture/`(18 PDF)跑一次 retrieval + RCS(LLM)產生新 chunk scores,並在 compare 註記資料來源差異)。
- 寫一次性 script(存 `.omo/evidence/` 或 temp,不入 git):
  - 抽出每篇論文的 chunk scores(與 K3 聚合輸入一致)
  - 以 `_shrunk_mean`(m=1, prior=5.5)重算每篇論文層 rel/qual(**直接 import 或複製公式**)
  - 依現行門檻重判 recommendation(include: rel≥8 且 qual≥6;consider: rel≥6)
  - 對比 `k3-after-dist.json`(m=3 結果):每篇分數變化、usable 數變化、recommendation 變化
- 產出 `.omo/evidence/k3b-offline-m1.md`(表格:論文 / n / m=3 rel·qual / m=1 rel·qual / 等級變化)+ `.omo/evidence/k3b-offline-m1.json`。
- 預期:分數普遍微升(prior 5.5 低於多數 mean)、usable 數變化如實記錄;若仍 1 usable 維持「閾值後議」。

## Todo 3 — 收尾

- 全套件復跑確認;`git status` 核對變更集 = models.py + 2 個測試檔 + HANDOFF/STATE 文件;HANDOFF.md 加 K3b 段。
- data/papers 零變更;不 commit。

## Must NOT

- 不調 include/consider/prior/top_k/`max_chunks_per_paper` 等任何其他 policy 值。
- 不 commit;不碰 `.env`、`data/papers/`、`.omo/drafts/`。
- 不 spawn 子代理;不跑額外完整真實 run(Todo 2 備援除外)。
- 不改 LLM prompt / synthesis 邏輯。

## Success criteria

1. tests 全綠(log 備查)。
2. Todo 2 離線重算產出齊全;m=1 vs m=3 的每篇分數/等級變化表完整;結論明確。
3. 變更集僅限計畫內檔案;無洩漏、無 `Pipeline failed`(若觸發備援 RCS 則檢查)。

## Commit strategy(使用者親做,建議順序)

先 commit K3 主體(執行 K3b 前):見 K3 計畫 Commit strategy。K3b 完成後:

```powershell
git add literature_review/models.py tests/test_models.py tests/test_assessment.py HANDOFF.md .omo/plans/k3b-shrinkage-1.md .omo/STATE.md
git commit -m "perf: K3b shrink shrinkage_strength from 3 to 1 (cap bounds n<=6; minimal pull toward prior)"
git push
```