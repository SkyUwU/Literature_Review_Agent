# venues-by-name — `--venues` 按會議名對應別名表

## TL;DR (For humans)

`--venues` 目前吃的是 **raw 子字串 token**（必須是 paper venue 正規化字串的子字串），使用者手打別名（如 `nips`、`ICLR 2024`）容易 miss／拼錯。本里程碑把它升級為**「會議名 → 自動展開別名表」**：系統維護 alias→canonical 索引，輸入**命中任一會（key 或其別名，大小寫無所謂）**就判斷指識那一會，並用**該會完整別名組**去篩選；**未命中**的詞警告後照 raw 子字串使用；**全部未命中**→警告＋停用過濾（`()`）。新增特權值 **`none`**＝不濾（比空字串直觀）。**無 `--venues` 維持現狀＝整張 17 會別名表（全開）**。

本里程碑＝純 code/config/test + 文件，**不燒 Gemini 額度**。`data/run` / 三年窗改動不重做（前一里程碑已完成）。主要行為變更：`--venues "nips"` 由「只指 raw `nips`」變成「NeurIPS 全別名展開」——此為本次改動目的。

**不會做**：新增 `all` 特權值（使用者裁示：預設即 17 會全開，不需要）、alter 預設別名表內容、provider 行為變更、`FilterPolicy.venues` 語意變更（仍是正規化 token、包含式 match）、任何 `resolve_venues` 之外的介面擴張。

---

## Scope

**In**
- `literature_review/ranking.py`：新增 alias→canonical 索引 + `resolve_venues(raw: str | None) -> tuple[str, ...]`。
- `literature_review/main.py`：`_resolve_venues`（:77）改委派 `resolve_venues`；`--venues` help（:634-635）更新。
- `literature_review/search.py`：inline resolver（:189-197）改用 `resolve_venues`；import（:21）清理（`normalize_venue`／如不再使用則 `default_venues`）。
- 測試：`tests/test_ranking.py`（新增 `resolve_venues` 全態測試）、`tests/test_main.py`（:1135-1139、:1261 預期值更新）、`tests/test_search.py`（:156-201 預期值更新）。
- 文件：README（`--venues` 語意）、AGENTS.md（Commands/政策段落）、HANDOFF（Latest milestone 補記）、`.omo/STATE.md`、本計畫檔。

**Out**
- 新增 `all` 特權值／修改預設別名表內容
- `FilterPolicy.venues` 語意或 `filter_papers` 匹配邏輯變更
- 任何 provider / 下載 / 評分層行為變更
- Unpaywall / F4 real run

---

## 定案決策（使用者已確認，2026-09-22）

| 輸入 | 行為 |
|---|---|
| 無 `--venues`（raw=None） | `default_venues()`＝整張 17 會別名表（全開） |
| `--venues ""` | `()`（不濾） |
| `--venues none` | `()`（不濾，新增特權值；`none` 在別名解析**之前**攔截） |
| 詞**命中**索引（key 或別名，相等比對、大小寫無所謂、雙方差異僅正規化） | 展開該會 **key＋全部別名**（各別正規化）進篩選 token |
| 詞**未命中** | `stderr` 警告「未識別頂會 'X'，以 raw 子字串過濾」＋該詞正規化後當 raw token |
| **全部**未命中 | `stderr` 警告「所有輸入均未識別，停用頂會過濾」＋回傳 `()` |
| 混合 | token＝展開的別名組 ∪ raw 詞（去重、維持首現順序）；`filter_papers` 既有 OR 語意（任一 token 包含即保留） |
| 空 token（空白段） | 跳過（沿用現行為） |

匹配語意：輸入側**相等**比對（未知詞比對正規化後的別名/key）；篩選側**包含**比對（沿用 `_matches_venues`，ranking.py:106）。`nips` 與 `NeurIPS` 輸入結果**完全一樣**。

已知限制（寫入文件）：未命中詞必須是 paper venue 正規化字串的子字串才有效（沿用現行 raw 語意）；警告只在 CLI（stderr），非 CLI 嵌入使用時不會被吞（本專案僅 CLI 呼叫，可接受）。

---

## Verification strategy

每 Todo＝改檔 + 對照檢查 + 對應測試。最終：完整 suite 全綠（430 基準＋新增）、`--venues` 語意以測試驗證、`git status` 只含預期檔、無 key 洩漏。測試數以實測為準，寫回文件。

---

## Execution strategy

```
Todo 0（預檢）→ Todo 1（ranking.py：索引 + resolve_venues）→ Todo 2（main.py 委派 + help）
→ Todo 3（search.py 改用 + import 清理）→ Todo 4（測試更新/新增 + 全 suite）
→ Todo 5（文件同步 + 驗收 + commit 指令）
```

---

## Todos（evidence log 存 `.omo/evidence/venues-by-name-*.log`，gitignored）

- [x] 0. **預檢** — 確立執行前底線

  **References**
  - `.omo/evidence/run-policy-final.log`（430 tests 基準、2026-09-22）
  - `git status --short` 底線（預期乾淨；run-policy 兩筆 commit 已由使用者提交）
  - `git log --oneline -3`

  **Implementation**
  1. `git status --short` 記錄底線，確認工作區無未預期變動（應乾淨；新計畫檔自身除外）。
  2. 記錄基準測試數：`uv run python -m unittest discover -s tests` 至 `.omo/evidence/venues-by-name-todo0.log`。

  **Acceptance**
  - 底線無未預期變動；測試數記錄於 log（預期 430，以實測為準）。

- [x] 1. **ranking.py：alias→canonical 索引 + `resolve_venues`**

  **References**
  - `ranking.py:17-63` `TOP_VENUE_ALIASES`（17 會；values 含空格全名，使用前須 `normalize_venue`）
  - `ranking.py:63-66` `normalize_venue`（lower + 去非 alnum）
  - `ranking.py:68-76` `default_venues()`（keys∪values 正規化、去空）
  - `ranking.py:98` `filter_papers` 的 venues 硬濾呼叫點、`ranking.py:106` `_matches_venues`（OR 語意）——**不改**

  **Implementation**
  1. 新增 `_VENUE_BY_ALIAS: dict[str, str]`：`{normalize_venue(key): canonical, *(normalize_venue(a): canonical for a in aliases)}` 的合併對映；以 module-level 函式（如 `_build_venue_index()`）建構或直接字面組合，**建構時斷言無碰撞**（同一正規化 token 不得映射到兩個不同 canonical）。
  2. 新增 `resolve_venues(raw: str | None) -> tuple[str, ...]`：
     - `raw is None` → `default_venues()`。
     - `raw == ""` 或 `raw.strip().lower() == "none"` → `()`（先攔截，`none` 不落入 raw 路徑）。
     - 其餘：`raw.split(",")` 逐段 `normalize_venue`；空 token 跳過。
       - 命中 `_VENUE_BY_ALIAS` → 記 matched；把該 canonical 的 **key＋所有 aliases 各別 `normalize_venue`** 加入結果。
       - 未命中 → 記 unknown（`(原始段, 正規化詞)`）；加入 raw token。
     - 收尾：matched 為空、但 unknown 非空 → `stderr` 印「所有輸入均未識別頂會，停用頂會過濾（輸入：…）」→ 回傳 `()`。
     - matched 非空 → 對每個 unknown 印「未識別頂會 'X'（→'y'），以 raw 子字串過濾」；回傳「展開別名組 ∪ raw token」去重、維持首現順序的 tuple。
     - matched 與 unknown 皆空（全部空白段）→ 不警告、回傳 `()`。
  3. `default_venues()` 不動（其語意與 `resolve_venues(None)` 共用）。

  **Acceptance**
  - 新測試（`tests/test_ranking.py` 新增類，含 `contextlib.redirect_stderr` 捕捉）：
    - `None→default_venues()`；`""→()`；`"  "→()`；`"none"→()`。
    - `"NeurIPS"` 與 `"nips"` 產出**完全相同**，且＝NeurIPS 的 key＋aliases 正規化、去重、首現順序的 tuple（`("neurips","nips","annualconferenceonneuralinformationprocessingsystems")`）。
    - `"neurips, icml"` → NeurIPS 組 ∪ ICML 組、順序＝首現順序。
    - 未知單詞 `"tyop"`（全未知）→ `()`＋stderr 警告。
    - 混合 `"nips,cvprw"` → NeurIPS 組 ∪ `("cvprw",)`＋stderr 警告該未知詞。
    - 去重：`"neurips,nips"` 結果無重複 token。
    - 索引建構無碰撞（遍歷 `_VENUE_BY_ALIAS` 值無重複 canonical 衝突）。
  - 既有 `VenueWhitelistTests` / `default_venues` / `filter_papers` 測試**不改可過**。

  **QA**
  - happy：`nips`→NeurIPS 全別名；failure：全未知不得誤殺（`()`）；raw 詞維持身分（不被當別名誤展開）；順序穩定（測試斷言固定 tuple）。已知限制：混用時 unknown 詞若拼錯仍會 0 命中該詞——靠 stderr 警告自救。

- [x] 2. **main.py：`_resolve_venues` 委派 + help 更新**

  **References**
  - `main.py:77-88` `_resolve_venues`（現行 None/空/token 三個分支）
  - `main.py:634-635` `--venues` argparse help
  - `main.py:677` `venues=_resolve_venues(arguments.venues)` 呼叫點

  **Implementation**
  1. `_resolve_venues` body 改為 `return resolve_venues(raw)`（import 自 ranking；保留薄包裝以最小化測試/呼叫面變動）。
  2. help 改為（意思一致）：`"Comma-separated top venues by name (match the built-in list and expand its aliases; unknown names filter as raw substrings); 'none' disables the filter; empty string disables the filter; default is all built-in top venues"`。
  3. import：`from literature_review.ranking import resolve_venues, ...`（維持現有 `default_venues` 需要、視情況上述沿用）。

  **Acceptance**
  - `tests/test_main.py:1136-1139` 預期值更新：
    - `_resolve_venues("NeurIPS, icml,  ")` → `("neurips", "nips", "annualconferenceonneuralinformationprocessingsystems", "icml", "internationalconferenceonmachinelearning")`（依 `TOP_VENUE_ALIASES` 順序；以實測 API 為準）。
    - 加 `_resolve_venues("none") == ()`；加 `_resolve_venues("nips") == _resolve_venues("NeurIPS")`。
  - `tests/test_main.py:1261`（`--venues "neurips,icml"` 傳遞）之斷言同步改為展開組。
  - 既有 None／`""` ／預設傳遞測試（:1170）不動。

  **QA**
  - failure：`--venues "nips"` 若展開不完整 → 測試抓；None/空/`none` 三重語意互斥無誤。

- [x] 3. **search.py：改用 `resolve_venues` + import 清理**

  **References**
  - `search.py:21` import（`default_venues, filter_and_rank, normalize_venue`）
  - `search.py:164-165` `--venues` help
  - `search.py:189-197` inline resolver
  - `search.py:199-205` `filter_and_rank`（`min_year=arguments.year_from, venues=venues`）

  **Implementation**
  1. `search.py:189-197` 改為 `venues = resolve_venues(arguments.venues)`。
  2. help 同步（與 main.py:634-635 一致）。
  3. import 清理：`normalize_venue` 若於他處無使用則移除；`default_venues` 若僅 inline resolver 使用則移除（改用 `resolve_venues(None)`），否則保留（grep 確認）。

  **Acceptance**
  - `tests/test_search.py:201`（`--venues "neurips,icml"` → `("neurips","icml")`）預期改為展開組（與 main 相同）。
  - `tests/test_search.py:203-230`（預設 → `default_venues()`）不動。
  - `grep -rn "normalize_venue" literature_review/search.py` 經清理後無殘留非必要引用。

  **QA**
  - happy：CLI 傳 `--venues "nips,aaai"` → `resolve_venues` 語意；failure：import 清理後不留死 import（flake8/語法檢查或手動 grep）。

- [x] 4. **全 suite + 驗證**

  **References**
  - `tests/test_ranking.py`（新增 `ResolveVenuesTests`）
  - `tests/test_main.py` / `tests/test_search.py` 更新項
  - `.omo/evidence/venues-by-name-todo4.log`

  **Implementation**
  1. `uv run python -m unittest discover -s tests > .omo/evidence/venues-by-name-todo4.log`。
  2. `git status --short` 對照預期變動檔。

  **Acceptance**
  - 全 suite 全綠（430＋新增約 10-12）；log 留存。

- [x] 5. **文件同步 + 最終驗收 + commit 指令**

  **References**
  - `AGENTS.md`（Commands / run-folder-and-policy 段落中的 `--venues` 描述）
  - `README.md`（flags 說明）
  - `HANDOFF.md`（Latest milestone 節）
  - `.omo/STATE.md`（現況快照 430、里程碑表、候選）

  **Implementation**
  1. AGENTS.md：`--venues` 描述改「會議名 → 自動對應別名表；'none' 不濾；未識別詞警告＋raw 過濾；全部未識別→警告＋不濾」。
  2. README.md：同語意更新（含 `none`、混用、全未知行為）。
  3. HANDOFF.md：Latest milestone 補一段「venues-by-name（2026-09-22）」。
  4. `.omo/STATE.md`：最後更新日期、測試數（430＋新增）、里程碑表加列（待 commit 狀態）。
  5. 本計畫檔 Todo 全勾。
  6. 最終驗收：suite 全綠（以 todo4 log 引用）、`git status --short`、`git grep -n` key 名稱洩漏檢查。

  **Acceptance**
  - 四份文件與實際行為一致；`git status` 僅預期檔；無 key 洩漏；commit 指令一次列齊（code+tests 一筆、docs 一筆，同 `feature/plan-doc`，ahead 17 → 19）。

---

## Commit strategy

使用者親做（承接 AGENTS.md）：code+tests 一筆（`literature_review/ranking.py main.py search.py tests/test_ranking.py tests/test_main.py tests/test_search.py`）、docs 一筆（`README.md AGENTS.md HANDOFF.md .omo/STATE.md .omo/plans/venues-by-name.md`），`--venues` 描述於 commit message 標題帶出，最後 `git push`。

---

## Success criteria

- `--venues "nips"` 與 `--venues "NeurIPS"` 語義相同＝NeurIPS 全別名展開篩選。
- `--venues "nips,cvprw"`＝NeurIPS 組 ∪ raw `cvprw`（OR 語意），並對 `cvprw` 發 stderr 警告。
- `--venues "tyop"`（全未知）＝警告＋`()`。
- `--venues none`／`--venues ""`＝`()`；無 `--venues`＝`default_venues()` 17 會全開。
- 全 suite 全綠；文件與行為一致；無 key 洩漏。