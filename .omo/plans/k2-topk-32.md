# K2:TOP_K_CHUNKS 16 → 32 驗收 run

- slug:`k2-topk-32`
- date:2026-09-08
- status:approved(使用者 2026-09-08 裁示「先嘗試 top k 調整,先調成 32 試試看」;閾值後議)
- milestone:K 的後繼小調(純參數 + 驗收 run)

## 動機

K 驗收發現:**19 篇下載,只有 5 篇的 chunks 擠進「跨論文 top-16」(RCS 評分券)**,其餘 14 篇完全沒被評到;最終 1 consider(6.2/6.6)、0 include,報告只有 1 source,偏少。`TOP_K_CHUNKS=16` 是早期保守值(1M context 下無技術壓力;llm_input_cap=40 已是筆記側寬裕上限)。

**假設**:TOP_K 32 讓更多論文拿到評分券 → 進評分論文數與 usable 數可能上升。若仍只有 1 usable,則問題在閾值/檢索品質,附建議交使用者決策(本計畫**不**調閾值)。

## Scope

- 只改:`main.py` 一行常數 + 文件同步 + 一次真實 run 對比。
- **不**動:ranking 邏輯、評估邏輯、`EvidenceAggregationPolicy` / `FilterPolicy` 任何數值、測試邏輯。

## Todo 1 — 常數調整

- `literature_review/main.py`:`TOP_K_CHUNKS = 16` → `32`。其餘常數(`LIMIT=100`、`TOTAL_TARGET=20`、`MIN_YEAR=2021`)不動。
- tests 斷言 import 常數,自動跟上;跑全套件確認全綠(預期 204+ OK)。
- grep `TOP_K_CHUNKS|top-k|16` 於 `literature_review/` 與 docs,確認只有歷史紀錄(如 HANDOFF M3C/M4 段落屬計畫範圍外)與已同步處殘留。

## Todo 2 — 文件同步

- `README.md`「Run the full pipeline (M3C)」段:`TOP_K_CHUNKS=16` → `32`。
- `AGENTS.md` / `HANDOFF.md` / `.omo/STATE.md`:K2 里程碑紀錄(一句話 + 驗收結果待 Todo 3 回填)。

## Todo 3 — 真實驗收 run(同 query 對比)

- 前置:`curl http://localhost:3000/api/public/health` → 非 200 停止回報(使用者已開,預期 200)。
- 紀錄 run 前 `data/papers/` 檔案清單(現 8 檔;main.py 硬編碼 `DEST_DIR=data/papers`,無法 CLI 覆寫 temp——比照 K 先例:run 後把**新增的** W*.pdf 移至 `/tmp/k2-after-downloads/` 並還原原本 8 檔。K 驗收已記候選 M:之後讓 main.py 支援 temp 覆寫,本次不實作)。
- 指令:`printf 'literature review agent\n' | uv run --env-file .env python -m literature_review.main`(fresh shell 讀不到 .env 的既有環境問題,比照 K 用 `--env-file`;不得讀/印 key 值)。
- 產出三件(存放 .omo/evidence/):
  1. `k2-after-real.log` — 完整 stdout
  2. `k2-after-dist.json` — 結構化摘要:入評分論文數(assessments 數)、每篇 rel/qual/recommendation、分布(min/max/median)、include/consider/exclude 計數、downloaded_paper_ids、inline_citations、generated_by、aiza_hits
  3. `k2-vs-k-compare.md` — 對比 `.omo/evidence/k-after-dist.json`,五段:①進評分論文集合變化(16 vs 32 時誰進誰出)②分數分布變化 ③include/consider 數量 ④synthesis 產出(來源數/citations/報告尾端中文瑕疵是否仍在——已知 A5 待辦)⑤人工抽查「相關但無用」者(候選 J 實證延續)。
- 結論規則:usable > 1 → 涵蓋改善成立;仍 1 usable → 附閾值建議(候選 C:consider rel 6→4.5 + qual 卡 5.5)供下次討論,**不實作**。

## Must NOT

- 不調 `EvidenceAggregationPolicy` / `FilterPolicy` 任何數值(閾值後議)。
- 不 commit;不碰 `.env`(不讀不印 key);`data/papers/` 最終狀態零變更;不動 `.omo/drafts/`。
- 不 spawn 子代理、不跑額外真實 run。

## Success criteria

1. tests 全綠(log 存 .omo/evidence/k2-task-*.log)。
2. 真實 run 完成,三產物齊全、五段 compare 完整。
3. 無 AIza 洩漏、無 `Pipeline failed`、`generated_by=llm`。
4. `data/papers/` 還原 8 檔。
5. compare 結論明確(usable 增/不變 + 建議),供使用者決策下一步(閾值 or C2 or L)。

## Commit strategy(使用者親做)

```powershell
git add literature_review/main.py README.md HANDOFF.md .omo/plans/k2-topk-32.md .omo/STATE.md
git commit -m "feat: K2 raise TOP_K_CHUNKS from 16 to 32 for wider RCS scoring coverage"
git push
```