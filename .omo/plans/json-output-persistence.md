# JSON 輸出存檔（小里程碑）— Rev 2

> **Rev 2（2026-09-16 使用者裁示）**：只把 `result["report"]` 存成 JSON 檔；其餘欄位（plan/downloads/stats/failed_extractions/screening/follow_ups）改印到 cmd 供檢視，不存檔。

## 目標

真實 run 結束後，把 report（`SynthesisResponse`）序列化成 JSON 檔存到 `data/outputs/`，方便使用者事後核對結構化內容（paper_summaries / claim_chunks / paper_sources / future_directions）；其餘操作資訊印到 stdout。

## 範圍

- **存檔**：`result["report"]`（`SynthesisResponse` → `model_dump(mode="json")`）
- **印 cmd**：`query` / `plan` / `downloads` / `stats_per_query` / `failed_extractions` / `screening` / `follow_ups` / `dry_run`（`json.dumps`）
- **Out**：raw PDF content、chunk-level 全文（太大）、Langfuse trace（另有 dashboard）

**現況落差**：`main()` 目前只 `print(result["report"].model_dump_json(indent=2))`（:464），plan/downloads/stats/failed_extractions/screening/follow_ups 全部只在記憶體、run 結束即消失——本里程碑補「report 落檔 + 其餘欄位印出」。

## 設計決策

1. **輸出目錄**：`data/outputs/`（相對專案根；`os.makedirs(exist_ok=True)` 自動建）
2. **檔名**：`report_YYYYMMDD_HHMMSS_ffffff.json`（含微秒避免碰撞）
3. **觸發時機**：`main()` 非 dry-run 分支——先印其餘欄位到 cmd，再存 report 檔，接著 Langfuse flush；dry-run 路徑不動（無 synthesis output）
4. **格式**：`json.dump(payload, f, ensure_ascii=False, indent=2)`（人類可讀，方便核對）
5. **錯誤處理**：寫檔失敗只 `print(..., file=sys.stderr)` 警告，**不 crash pipeline**（report 仍會印到 cmd）
6. **Git 追蹤**：`.gitignore` 加 `data/outputs/`（與 `data/papers/` 同邏輯）

## 實作步驟

### Todo 1：`main.py` 新增 `save_report_output`

```python
def save_report_output(
    report: SynthesisResponse,
    *,
    output_dir: str | Path = "data/outputs",
    timestamp: datetime | None = None,
) -> str | None:
    """Serialize *report* to a timestamped JSON file under *output_dir*.

    Returns the written file path on success, or ``None`` when the write
    fails (a warning is printed to stderr; the pipeline never crashes).
    """
    # payload = report.model_dump(mode="json")
    # Path(output_dir).mkdir(parents=True, exist_ok=True)
    # filename = f"report_{ts.strftime('%Y%m%d_%H%M%S_%f')}.json"
    # json.dump(payload, f, ensure_ascii=False, indent=2)
    # except OSError -> print(..., file=sys.stderr) + return None
```

需要新增 import：`SynthesisResponse`（從 `literature_review.synthesis` 或 `models`）、`datetime`。`main.py` 無 logger——一律用 `print(..., file=sys.stderr)`（與現況風格一致）。

**測試**（`tests/test_main.py`）：
- `test_save_report_output_creates_file`：tmpdir + fake `SynthesisResponse`（沿用測試既有 fixture）→ file exists → `json.load` 回讀 === `fake.model_dump(mode="json")`
- `test_save_report_output_returns_none_on_error`：注入不可寫路徑 → return None → no raise

QA：`uv run python -m unittest tests.test_main -v 2>&1 | tee .omo/evidence/json-output-t1.log`

### Todo 2：`main()` 接線

非 dry-run 分支（:463-465）改為：

```python
else:
    print(result["report"].model_dump_json(indent=2))           # 維持現況：report 印 cmd
    print(json.dumps({                                          # 新增：其餘欄位印 cmd
        "query": query,
        "plan": result["plan"].model_dump(mode="json"),
        "downloads": result["downloads"],
        "stats_per_query": result["stats_per_query"],
        "failed_extractions": result["failed_extractions"],
        "screening": result["screening"],
        "follow_ups": result["follow_ups"],
        "dry_run": False,
    }, ensure_ascii=True, indent=2))
    saved_path = save_report_output(result["report"])           # 新增：report 落檔
    if saved_path:
        print(f"Report saved to: {saved_path}")
    pipeline._flush_langfuse()
```

dry-run 分支（:449-462）不動。

**測試**：既有 fake e2e 測試（`test_full_run_produces_report` 等）補断言——`unittest.mock.patch` 把 `output_dir` 導向 tmpdir（或 patch `save_report_output` 的 `output_dir` 參數），確認：① JSON 檔被建立且可 `json.load` 含 `report` 欄位；② stdout 出現 `Report saved to:`。

QA：`uv run python -m unittest tests.test_main -v 2>&1 | tee .omo/evidence/json-output-t2.log`

### Todo 3：`__main__.py`（使用者裁示：選方案 3）

新增 `literature_review/__main__.py`（3 行），讓 `python -m literature_review` 可直接執行：

```python
"""Entry point: ``python -m literature_review`` runs the end-to-end CLI."""
from literature_review.main import main

if __name__ == "__main__":
    main()
```

**測試/smoke**：`uv run python -m literature_review --help` → 顯示 argparse help、無 key 需求、無 crash。f1 驗收點另列。

### Todo 4：`.gitignore` 更新

加一行 `data/outputs/`（緊鄰 `data/papers/`，:5 下方）。

### Todo 5：全量測試

`uv run python -m unittest discover -s tests -v 2>&1 | tee .omo/evidence/json-output-tests.log`

期望全綠；回報確切測試總數。

### Final verification

- F1. grep `save_report_output` 於 `main.py` → 定義 + 呼叫各 1 處
- F2. grep `data/outputs` 於 `.gitignore` → 1 行
- F3. fake e2e log 顯示 `Report saved to:` 行
- F4. `uv run python -m literature_review --help` → argparse help 正常、無 crash（`__main__.py` smoke）

## 審計同步（docs commit 一併）

執行 JSON 里程碑的同時，把文件審計發現修正（三份文件 ~19 處）併入 **docs commit**。修正內容：

### STATE.md
1. 標題日期 `2026-09-13` → `2026-09-16`
2. 現況區 Output Traceability「待使用者 commit」→「已 commit + push ✅」（與 0b0 一致）
3. C2e 重複段落刪除一行
4. M4 條目「Todo 8 待辦」→ 標註已被 C2b 功能性評分取代、Todo 8 關閉
5. 候選區清理：S1-S5「待使用者 commit」→ 已 commit；L. SearchPlan「下個里程碑 M5a」→ 已完成；A9/A10 → 標 M5b 已實作；notes prompt page removal → 標 LLM Input Hygiene 已完成；C 條目「與 T1 同屬候選」→ T1 已完結、僅留 JSON 輸出（本里程碑）

### HANDOFF.md
1. Line 39 `74 tests` → `371 tests`
2. Line 62 `[chunk_id]` → `[claim-N]`
3. Line 61「anchored in explicit limitations」→ 獨立 call 不限 limitation
4. 實施里程碑表補：M4/M5a/M5b/M5e/M6/C2a-C2e/S1-S5/LLM Input Hygiene/Output Traceability
5. 刪除/更新過時的「Suggested sequence」與 handoff prompt 段落

### AGENTS.md
1. Line 39 `74 tests` → `371 tests`
2. Line 73 收縮平均公式 → `round((n*mean + m*prior)/(n+m), 1)`（m=1）
3. 架構段補 screening / functional scoring / claim tagging
4. Terminology 段更新（覆蓋包現況、補新術語）

## Commit strategy（使用者親做，分開提交）

1. **code 1 筆**：`literature_review/main.py`、`tests/test_main.py`、`literature_review/__main__.py`、`.gitignore`
   - message：`feat: save synthesis report as JSON, print run metadata to stdout, add -m entry point`
2. **docs 1 筆**：`.omo/STATE.md`、`.omo/plans/json-output-persistence.md`、`HANDOFF.md`、`AGENTS.md`
   - message：`docs: record JSON output milestone and sync stale docs`
3. push

## 自我審核：潛在問題

| # | 問題 | 風險 | 處理 |
|---|---|---|---|
| 1 | `data/outputs/` 不存在 | 首次 run 會 error | `makedirs(exist_ok=True)` |
| 2 | 檔名碰撞（同一秒兩次 run） | 極低機率 | 含微秒 `%f`，機率 ≈0 |
| 3 | JSON 大小（53 claims × ~200 chars + 全文 prose） | ~50-300 KB/次，可接受 | indent=2 人類可讀優先 |
| 4 | `.gitignore` 遺漏 | `data/outputs/` 被 commit | Todo 3 處理 |
| 5 | 權限/磁碟滿 | 寫檔失敗 | warn only，不 crash |
| 6 | dry-run 觸發 | 無 synthesis output | 只在非 dry-run 分支呼叫 |
| 7 | 多 run 累積目錄 | 磁碟空間緩慢增長 | 先不做清理（低風險） |
| 8 | Windows/WSL 路徑差異 | 兩端都可建 | `Path` 處理跨平台 |
| 9 | `SynthesisResponse` 未 import | NameError | Todo 1 新增 import |
| 10 | 測試汙染正式 `data/outputs/` | 測試 run 留下檔案 | 測試用 tmpdir 注入，不碰真實目錄 |
| 11 | cmd 輸出變長（report + 其餘欄位兩段） | stdout 落落長 | 使用者裁示「其餘用印 cmd」，可接受；必要時之後再瘦身 |
| 12 | 「其餘欄位」的層級順序 | 閱讀順序 | 依 result dict 原順序（plan→downloads→stats→failed→screening→follow_ups） |

## 驗收

- 真實 run：`printf 'query\n' | uv run --env-file .env python -m literature_review.main` → `data/outputs/` 出現 `report_*.json` → `json.load` 可解析 → 含 `report`/`paper_summaries`/`claim_chunks`/`paper_sources`/`future_directions`/`generated_by`
- stdout 依序出現：report JSON → 其餘欄位 JSON → `Report saved to: data/outputs/report_....json`
- `--dry-run` → `data/outputs/` 無新增（正確）、stdout 維持現況