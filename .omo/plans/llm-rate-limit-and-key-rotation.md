# LLM rate limiting + key rotation（2026-09-29）

本計畫＝F4 掛帳後，先把 Gemini 免費 tier 的額度問題根治，再讓真實 run 可以「用乾淨 key」重跑。動因證據：`.omo/evidence/f4-real-run*.log` 三次 run 都在 key1 卡死——503（高需求）、429「20 requests/day Free Tier 用盡」（run-2/4）；且 log 無 timestamp，無法定量「一天到底燒幾次、失敗有沒有計數」。

## TL;DR

- 把「限流＋錯誤分類＋計時證據」收進 `GeminiJsonClient.generate_json`（所有 LLM 唯一 junction，key1/2/3/4 全吃到），用 **process 內 per-key registry**：RPM pacing（預設間隔 13s）、RPD 滑動 24h 計數（預設 20/day）、429/503 三層錯誤處理、每次 call 打 timestamp 到 stderr。
- `main.py` 新增 `--plan-key/--notes-key/--report-key N` 選 key；screening 遇 quota/server 錯誤 → **跳過 LLM screening，降級走 legacy per-query 下載**（不再整個 run crash）。
- `pipeline.py` 修正錯誤費率 docstring（「20 per minute」→ 正確 RPM=5）；`NOTES_PACING_SECONDS` 預設 4→13（原本 4s 會超過 5 RPM）。
- 本次真實 run：**planner+screening 用 key4（全新未用）**、notes=key2、report=key3——使用者確認三支皆未用過。
- 零額度測試：全部 fake clock + fake client，不燒任何 key。完整 suite 全綠後才排真實 run。

## Scope In

- `literature_review/llm_evidence.py`：rate limiter（RPM/RPD）+ 例外型別（`DailyQuotaExhausted` / `LlmServiceError`）+ 429「retry in Ns」解析重試 ≤`GEMINI_429_RETRIES` + call log（timestamp/間隔/結果/各 key 今日計數）。
- `literature_review/main.py`：`--plan-key/--notes-key/--report-key`；screening 降級（quota/service 錯誤 → legacy 下載，不 crash）；`DailyQuotaExhausted` 由 planner 接住 → rule_based（延續既有 fallback）。
- `literature_review/pipeline.py`：修 docstring；`NOTES_PACING_SECONDS` 預設 4→13。
- `.env.example`：`GEMINI_API_KEY_4=`＋`GEMINI_REQUESTS_PER_MINUTE=5`／`GEMINI_REQUESTS_PER_DAY=20`／`GEMINI_429_RETRIES=1` 註解。
- `tests/test_llm_evidence.py`、`tests/test_main.py`、`tests/test_pipeline.py`：新增測試。
- `AGENTS.md`（Commands 節）＋`HANDOFF.md`：記錄「真實 run 前提醒使用者確認額度」與「503=停、daily-429=不重試」。

## Scope Out

- 不猜測真實 reset 時間（以 server 429 為準；使用者確認為下午 3-4 點）。
- 不實作 RPD 持久化到磁碟（process 內就夠；跨 run 交給 server 429）。
- 不重構 screening block 外部結構；降級只加在現有 if/else 內。
- 不改 key2/key3 的「無降級就退出」政策（報告無降級，維持現狀）。
- 本次不做 F4 重跑以外的新功能。

## Verification

- 完整 test suite 全綠（`uv run python -m unittest discover -s tests -v`）。**baseline 實測 `Ran 478 tests ... OK`（2026-09-29）**，實作後再跑一次比對新增。
- call log 有 timestamp 證據（單測可驗證 fake clock 的間隔與計數）。
- key-leak canonical 檢查無洩漏。
- 真實 run（Todo 6）記錄實際呼叫數回填本檔；screening 若是降級路徑亦記錄。

## Execution strategy

限流器為一個 bounded junction 變更；逐檔改完立刻跑對應單測，最後全量 suite。真實 run 只在使用者確認額度後執行一次；任何 429→降級或停手回報，不自動重試。

## Todos

### Todo 0 — 計畫檔＋plan-review 自審

- [x] 本計畫檔成文
- [x] `plan-review` skill 自審（唯讀）通過（5 點已修入本檔）
- [x] `git status --short` 確認 baseline（工作區僅兩個未追蹤計畫檔；HEAD=`919a2f9`；suite baseline `Ran 478 tests OK`）

Acceptance：plan-review 無 blocking 問題。**已通過**。

### Todo 1 — `llm_evidence.py` 限流器

- [x] `DailyQuotaExhausted(LlmEvidenceError)`／`LlmServiceError(LlmEvidenceError)` 例外型別
- [x] `_RateTracker`（可注入 `clock`＋`sleeper`，兩者都注入才好在測試零真實 sleep）：`wait_for_slot()`、`can_call()`、`record()`
- [x] 本機RPD 計數用**process 內單調計數器**即可（不建 rolling 24h deque——我們已決議不推測 reset 窗口，跨 run 交給 server 429；用 rolling 反而暗示我們在模擬 reset）
- [x] process 內 `_TRACKERS: dict[str, _RateTracker]` keyed by api_key
- [x] `GeminiJsonClient.generate_json`：call 前 pacing＋RPD 檢查（滿→`DailyQuotaExhausted`）；call 後 record＋印 `[llm] label=... delay_s=... calls=...` 到 stderr
- [x] 錯誤分類（lazy import genai compat errors；**先看 `getattr(error, "code", None)`（429/503），再 fallback 解析 `str(error)`**——純字串比對太脆）：
  - 503 `InternalServerError`／`code==503` → `LlmServiceError`（停，不重試，通知）
  - 429 且 `"requests per day"` in msg → `DailyQuotaExhausted`（不重試）
  - 429 且 `"retry in Ns"`（無 daily）→ sleep N 後重試 ≤`GEMINI_429_RETRIES`（預設 1）
  - `__init__` 增加 `label: str | None`（call log 用，印角色非 key 值；向後相容，其他呼叫端免改）
- [x] env：`GEMINI_REQUESTS_PER_MINUTE`(5)、`GEMINI_REQUESTS_PER_DAY`(20)、`GEMINI_429_RETRIES`(1)；間隔＝`60/rpm + 1`
- [x] 註記：pacing 是 **per-key**，key2 的 functional 分批＋逐篇 notes 也各會隔 13s（一次 run 可能加數分鐘 wall-clock）；key1 約 6 call 幾乎無感。此為預期行為，寫進 docstring。

Acceptance：單測（fake clock/fake client）驗證 13s 間隔、RPD 滿 raise、503 不重試、per-day 不重試、retry-in 重試 1 次、True/False can_call。

### Todo 2 — `main.py` key 選擇＋screening 降級

- [x] argparse：`--plan-key/--notes-key/--report-key`（int，預設 1/2/3；reader `_key_env(n)`：n==1→`GEMINI_API_KEY`，否則 `GEMINI_API_KEY_{n}`）
- [x] `_build_clients(arguments)` 改由 suffix 取 key；**必須用 `getattr(arguments, "plan_key", 1)` 之類的防禦讀取**——現有 8 個 `_build_clients` 單測（`test_main.py:794/806/818/828/850/868/888/911`）傳的 `Namespace` 只有 `rule_based`/`dry_run`，直接 `arguments.plan_key` 會全數 AttributeError；`client_screen=client_plan` 邏輯不變
- [x] screening block：把 `screen_candidates` 主 call＋follow-up call 包 try；catch `(DailyQuotaExhausted, LlmServiceError)`
- [x] **降級不可重跑搜尋**：主 call（`main.py:351`）失敗時 `query_candidates`（336-350）已建好，降級要用**既有 `query_candidates` 的 `sampled.papers`** 直接 `download_and_backfill`，不可呼叫 legacy `_search_and_rank` 再搜一次（會白燒 SS/OpenAlex 額度）。target 用 `math.ceil(TOTAL_TARGET / len(plan.queries))`
- [x] **follow-up（:377）失敗的處理**：若主 screening 已成功、只有追問那輪失敗 → 只跳過追問（log），**保留已成功的 `screening_result`**，不要整段降級丟掉有效篩選
- [x] 降級 helper 需一併填 `paper_queries`/`paper_titles`/`downloaded_papers`（輸出組裝 `_make_papers_output` 需要），並讓 `screening_result` 保持 `None`（`main.py:550` 已容忍 None）
- [x] `_make_plan` 確認現有 `except Exception` fallback 印出 `DailyQuotaExhausted` 訊息（既有 stderr warning 已涵蓋，驗證即可）

Acceptance：**已通過**（`tests.test_main` 56 綠）。另補：`_translate_error` 也要把「retry 預算用完的 per-minute 429」映射成 `DailyQuotaExhausted`，否則它會以一般 `LlmEvidenceError` 逸出、降級 handler 接不到。

### Todo 3 — `pipeline.py` pacing 修正

- [x] `_notes_pacing_seconds` docstring 錯誤「20 requests per minute」→ 正確敘述（RPM=5，最小間隔 12s，預設 13s 含緩衝）
- [x] 預設 `"4"`→`"13"`
- [x] 注意**雙重 pacing**：`pipeline` 的 notes pacing 與新的中央限流器都會隔 13s。兩者一致時無衝突（pipeline 先睡 13s，限流器看到已過 12s 下限就放行），保留為 defense-in-depth；但 `pipeline` pacing 對 Ollama 路徑也會生效（與 Gemini RPM 無關），本次僅改預設值與 docstring，不動機制。

Acceptance：單測驗證預設 13.0。

### Todo 4 — `.env.example`

- [x] 加 `GEMINI_API_KEY_4=`＋`GEMINI_REQUESTS_PER_MINUTE=5`／`GEMINI_REQUESTS_PER_DAY=20`／`GEMINI_429_RETRIES=1`（含註解：免費 tier RPM=5/RPD=20、per-day 429 不重試）

Acceptance：檔內容與實作常數一致。

### Todo 5 — 測試＋全量 suite

- [x] `tests/test_llm_evidence.py`：`RateTracker` 單測（fake clock）+ 例外分類（fake 訊息字串）
- [x] `tests/test_main.py`：key suffix 選擇、screening 降級（mock screen_candidates 拋 quota/service → legacy 下載執行、無 crash）
- [x] `tests/test_pipeline.py`：pacing 預設 13
- [x] `uv run python -m unittest discover -s tests -v` 全綠
- [x] key-leak canonical：`git grep -nE "AIza[A-Za-z0-9_-]{20,}|AQ\.[A-Za-z0-9_-]{20,}"`（只允許測試假 key `AIza000`）

Acceptance：**已通過** — `Ran 504 tests ... OK`（baseline 478 → +26）、key-leak 無命中。

實測紀錄：預設 notes pacing 從 4s 改 13s 後，未 mock sleep 的全 run 單測會真的睡（suite 106s → 216s），故在 `MainEntryTests.setUp` 加 `mock.patch("literature_review.pipeline._notes_pacing_seconds", return_value=0.0)`，suite 回到 124s。

### Todo 6 — 真實 run（使用者確認額度後，耗 key4/key2/key3）

> **阻塞（新發現，2026-09-29）**：以預設 top-venue 白名單跑 `--dry-run`，四個計畫 query 全部 `candidates_available=0`（實際檢索各有 86-99 筆候選）。抽樣 venue 分布是 IEEE Access / Heliyon / arXiv / npj Digital Medicine 等，白名單 17 conference 40 alias 全不命中——這是**研究政策結果，不是 bug**，但會讓真實 run 下載 0 篇並在「All downloaded PDFs failed text extraction」中止。
> 待使用者決策：(a) 用既有逃生門 `--venues none` 跑（偏離研究政策）、(b) 擴充 venue 清單（政策變更，超出本里程碑範圍）、(c) 換一個會命中頂會的 query。

- [ ] **提醒使用者**：開 ai.dev/rate-limit 確認 key4、key2、key3 三支餘額
- [ ] 上面三個 venue 選項由使用者決定
- [ ] `printf 'literature review agent\n' | uv run python -m literature_review.main --plan-key 4 2>&1 | tee .omo/evidence/f4-real-run-5.log`
- [ ] exit 0、SD 表、`report_*.json`／`papers_*.json` 產出；`[llm]` call log 數實際呼叫次數＋間隔；記錄 `--plan-key` 是否真的用 key4
- [ ] 期間任何 429→依降級；不自動重試；失敗即回報

Acceptance：成功或明確失敗原因；實際呼叫數回填本檔與 HANDOFF。

### Todo 7 — 文件回填＋回報

- [ ] `AGENTS.md` Commands 節：加「真實 run 前提醒確認額度」＋「503=停、daily-429=不重試」
- [ ] `HANDOFF.md`：里程碑表＋Follow-up（限流器、降級、key4、實證數字）
- [ ] 本計畫檔勾 todos、紀錄執行偏差
- [ ] 驗收表＋commit 指令交使用者

Acceptance：文件一致、可 commit。

## Commit strategy

| Todo | Commit message（建議） | 受影響檔案 |
|---|---|---|
| 1-5 | `feat(llm): add per-key rate limiting, error taxonomy, and key rotation flags` | `llm_evidence.py`、`main.py`、`pipeline.py`、`.env.example`、3 個 test 檔 |
| 6-7 | `docs: rate-limit/key-rotation milestone + F4 real-run evidence` | `AGENTS.md`、`HANDOFF.md`、`.omo/plans/`、`.omo/evidence/f4-real-run-5.log`（若非 gitignored） |

commit/push 由使用者親做；`.env` / `data/` 不入 Git。

## Success criteria

- 一個 run 內不會因自己亂打而超 RPM/RPD；真實額度由 key4 cleansheet 實證一次。
- 429/503 不再把 key1/4 相關階段拖垮整個 run（screening 降級）。
- 有可量化的 call log（次數＋間隔），證實「6 次左右的 run 不會耗盡 20/day」或修正該假設。
- 全程零 key 洩漏、零額度浪費（不做無腦重試）。