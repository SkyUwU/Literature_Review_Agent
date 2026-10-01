# 專案狀態（盤點日期：2026-10-01）

## 換對話接續摘要與待評估方向（2026-10-01）

本節為交接與候選方向，不是已核准的完整實作計畫。下一輪先檢查 git status，讀 AGENTS.md、HANDOFF.md 與本檔，再選一個有界里程碑；不可直接全部實作或自動重跑付費 API。

### 已完成與目前限制

- 已修正 FullTextDocument.title 存取、補蒐 query 覆蓋原始問題、短 query／schema 範例不一致、priority enum、零候選 screening 與搜尋計數；已分離 publication_venues／pdf_host，加入 OpenAlex locations 讀取與通用任務錨定 prompt。
- SS publicationVenue 優先、缺名稱時才讀 legacy venue；目前沒有 venue 衝突判定或額外發表位置補查。PDF 在 arXiv 不代表未發表。provider_reported 不等於官方接收核驗。
- 最新通用 prompt 調整後重跑 13 個 follow-up tests 通過；此前 planner 23、screening 17 通過。只有 focused／fake-client 驗證，不代表完整 suite 或真實搜尋品質已驗收。
- 使用者已確認原始輸入是 `LLM-based automated literature review`，可用於後續新 run；舊 JSON 的 query 是覆蓋 bug 結果，不可當作原始問題。錯誤 run 影響 functional scoring／synthesis，需重新評估，不能只改 JSON 字串。
- 舊輸出 `data/outputs/papers_20261001_213412_789889.json` 與對應 `report_20261001_213412_789889.json` 保留供比較；對應錯誤 checkpoint c18bd3dc81f241ec988828b37cbc05bf 已依授權刪除，其他資料保留。

### 建議優先順序（尚未實作）

1. 正確原始 query 的新 run 與品質診斷：先確認 provider 用量／模型權限、下載目的地與篩選政策；對照候選減少階段、任務偏移、claim 支持程度，不只看報告是否生成。
2. 證據可追溯性：目前 quote 固定取 chunk 前 240 字元，可能只截到作者資訊；考慮讓 notes 回傳支持 claim 的原文句子並驗證確實屬於來源 chunk。保留 chunk ID，補實際 PDF 頁碼映射，不推造頁碼。quote 與頁碼可各自界定里程碑。
3. 排除紀錄輸出：記錄年份／venue／screening／重複／OA 缺失／下載或抽取失敗／functional 選取等階段、原因與已有分數；未執行評分者不可填假分數。
4. 發表位置補查與 track：對缺 venue、arXiv-only、無法辨認來源或需要 track 的候選，考慮以 DOI／arXiv ID／標題作者匹配 OpenAlex 或官方 proceedings／OpenReview。分開 provider metadata 與官方核驗；main、workshop、Student Abstract、Findings 等政策待討論，不自動排除。單一 SS 路徑沒有跨來源衝突判定，PDF host 不作接收證據。
5. OA 補齊：考慮 DOI-based Unpaywall／其他 OA locations，保留來源與失敗原因；OA 補齊不等於檢索或正式發表核驗。
6. 檢索擴展：SS／OpenAlex 有界分頁（原始候選數與頁数限制，不湊固定數量的合格論文）、SS bulk search、seed-paper citation／reference 擴展，分別評估成本與適用情況。
7. 搜尋回饋：區分 provider 零結果、年份／venue 後零結果、screening 全拒絕、OA 不足；再決定改寫、分頁或 metadata 補查。避免對所有零結果盲目加詞；保留研究政策與呼叫上限。2–4 words 對完整術語的限制也可再評估，尚未放寬。

使用者準備下一輪整理敘述檔案；建議 HANDOFF.md 保留穩定架構／操作規則，STATE.md 保留目前狀態與待辦，詳細歷史移到既有 evidence／歷史紀錄，避免多處重複或把候選方向寫成已完成。

## 2026-10-01：查詢任務錨定與 repair 上下文

- 後續依使用者要求改為通用核心任務／研究對象／領域錨定；移除文獻綜述專用指示，跨領域例子明示非必用關鍵字。測試涵蓋文獻綜述與 MoE 輸入的初始／補蒐 prompt。

- 使用者授權依討論選取建議實作；本輪限定共用 query prompt 的任務錨定與縮寫／多義詞消解，保留 2–4 words 與既有 repair 呼叫預算。
- Planner schema repair、screening repair 與 Groq gap repair 保留原始研究問題；不以字面詞彙硬篩語義，不新增零命中自動搜尋、分頁、quote 或正式發表紀錄補查。
- 本環境無 plan-review skill；未新增計畫檔，未宣稱完成該流程。驗證使用 fake clients，不執行真實外部 API。
- 驗證：test_search_follow_up 13、test_planning 23、test_screening 17，共 53 tests 通過；git diff --check 通過。未執行完整套件或真實模型檢索，不能由 prompt 測試推定語義品質已達標。

## 2026-10-01：發表位置與 PDF host 分離（provider metadata 階段）

- `Paper` 新增 `publication_venues` provenance（provider、metadata path、source ID/type/link）；computed `pdf_host` 記錄配置 URL hostname，`venue_verification` 區分 `provider_reported` 與 `unconfirmed`。舊 JSON／手動 Paper 與 legacy venue 保持可讀。
- SS 讀取 structured `publicationVenue`；OpenAlex 請求 `locations` 並檢查所有非 repository／非 submittedVersion 來源。本地 whitelist 比對所有發表來源；不從 PDF URL 推斷會議，arXiv-only 不推定頂會。
- 測試：89 個 focused tests 通過，涵蓋 publication metadata、search adapters、ranking、query regression 與 SS routing；未執行完整 suite 或真實 API/run。`git diff --check` 通過。
- 僅完成 provider metadata 分離，不代表已確認正式會議接收；官方 proceedings／OpenReview 補查、track（Student Abstract／workshop／Findings）政策、分頁、bulk search、seed 擴展、quote／頁碼仍未實作。先前 query 覆蓋修正保留。

## 2026-10-01：補蒐迴圈覆蓋原始 query 修正

- 合併補蒐 decisions 的迴圈變數由 `query` 改為 `follow_up_query`，避免覆蓋原始研究問題並影響 functional scoring、synthesis、notes manifest 與 `papers.run.query`。
- Regression tests 核對全空／混合補蒐完成後，synthesis 仍收到原始 query、papers run 保存原始 query，逐篇搜尋來源分組則保持原本子查詢。12 個 focused tests 通過，未執行完整 suite 或真實 API/run。
- 使用者授權刪除錯誤輸出對應的 checkpoint `c18bd3dc81f241ec988828b37cbc05bf`；刪除前比對 manifest 的錯誤 query 與 report paper IDs。保留舊 report／papers JSON、PDF 及其他 checkpoint，供診斷比較。
- 使用者後續已確認原始 research query 為 `LLM-based automated literature review`；錯誤 JSON／manifest 本身無法還原原始文字。重新評分／生成尚未執行；OA 補齊、排除紀錄、頁碼與 quote 改善仍為後續工作。

## 2026-10-01：補蒐 query、零候選 screening 與搜尋計數

- 初始 LLM planner 與補蒐共用 2–4 詞 query 規則及本地驗證；超長輸出沿用一次 repair，不直接截斷。Rule-based planner 保持舊行為。
- Screening prompt 的 follow-up 範例改為 `query`／`target_gap`／`reason` 物件，範例可通過 schema；Groq gap aggregation／repair 同樣套用短 query 規則。Decision priority 改為 Literal enum。
- 零候選 pool 不呼叫 LLM，回傳空 decisions 與未解決缺口；全空補蒐保留初始 screening decisions，混合空／非空 query 只評估存在的候選。有候選而驗證失敗仍停止。
- OpenAlex／SS 統一 `total_candidates` 為實收 records，新增 optional `total_matches`；log 改為 `returned_candidates` 與 `total_matches`，不再將 OpenAlex 全部命中數誤標為實收候選。
- 驗證：五組 focused suite 81 tests 通過；其後含新增 prompt-schema 測試、SS routing 與端到端 fake flow 的 22 tests 通過，合計 93 個不同測試。`git diff --check` 通過；沒有完整 suite 或真實 API/run。
- Venue metadata 補查與 chunk 頁碼追溯尚未實作，搜尋來源與 venue whitelist 政策保持不變。

## 2026-10-01：附錄準備階段 title 介面修正

- `_prepare_documents()` 改由 optional `paper_titles` mapping 取得標題；正式 synthesis 傳入既有 mapping，不再存取 `FullTextDocument` 不存在的 `title` 欄位。
- 新增 regression tests：附錄在有／無標題時的保留與排除、論文標題以 Evaluation 開頭時不誤保留未知附錄，以及 synthesis 的標題傳遞。
- 驗證：pipeline 22 tests、section stats 11 tests 共 33 tests 通過；未執行完整 suite 或真實 API/run。
- 後續分開處理發表位置證據整合與 chunk 頁碼追溯；本里程碑未修改搜尋、venue 政策或頁碼處理。

## 2026-10-01：OpenAI provider 路由

- 已新增明確的 `LLM_PROVIDER=openai` 路由；OpenAI 官方 SDK 可供 plan、screening、functional scoring、per-paper notes 與 report 使用，預設模型 `gpt-5.6-luna`，支援全域及階段模型覆寫。
- 未設定 provider selector 時維持舊路由；只放 `OPENAI_API_KEY` 不會改變既有 Groq/Gemini 選擇。OpenAI requests 使用 Chat Completions JSON Schema、SDK 有界 retry 與本地 Pydantic 驗證；程式只測 mock，沒有用實驗室 key 發 API request。
- GPT-5.6 Luna 不接受 `temperature=0.2`；OpenAI client 現省略該參數，採用模型預設值。
- 驗證：OpenAI provider、notes、functional scoring 與 section stats focused tests 共 140 tests 通過；後續 temperature 修正的 provider mock test 通過。`git diff --check` 通過，key-pattern scan 無命中。未執行完整 suite 或真實 API/run。

## 2026-10-01：章節分類與逐篇證據取樣

- 正式 pipeline 現依 section path 分成 context、method、evaluation_setup、results、limitations_future 五類；References／acknowledgments 排除，Appendix 僅保留能歸入上述類別的子章節。
- Functional scoring 優先取 Method 與 Results 各一個 chunk；notes 取 Abstract/context 一個，再從 Method、Evaluation Setup、Results、Limitations/Future Work 各至多兩個，最多九個。每篇候選只做一次 embedding，向量共用於兩種取樣。
- Focused 驗證 78 tests 通過。全套測試因 `test_main` 載入本機 `.env` 後發出真實 Semantic Scholar 與 Groq 請求而中止；沒有繼續重試。這些請求不是本次變更的預期驗證步驟。
- 未執行端到端真實 literature review run；未追蹤的 `Summer_Project.pdf` 保持原狀。

## 2026-10-01：逐篇 notes checkpoint 與 Semantic Scholar 篩選

- 已完成 Groq per-paper notes：按 section 和 `GROQ_NOTES_BATCH_TOKENS`（預設 2500）切批，逐批 Pydantic/chunk ID 驗證後本地合併。Groq notes 與其他 Groq stages 共用 TPM tracker；Gemini-only 分工不變。
- Notes 每篇成功後寫入 `data/outputs/notes_checkpoints/<run-id>/`；`state.json` 留下 pending/completed paper IDs。失敗項會讓流程繼續處理其他論文並停止最終報告；用 `--resume-notes <run-id>` 重跑，會驗證 manifest、重用成功項並補缺漏項。續跑仍會重做搜尋、下載、抽取與 scoring。
- Semantic Scholar request 現傳年份與 venue 名稱；本地年份上／下界及 venue whitelist 仍是最後把關。`[search]` log 現列 provider 回傳、abstract 可用、年份後、venue 後與最終候選數。
- 驗證：受影響的 notes/checkpoint/search/provider-routing focused tests 通過（最新 focused run exit 0；先前同組合計 111 tests 通過）。完整 suite `Ran 511 tests`，有 1 failure、3 errors：`test_functional_scoring_policy_defaults` 期待 batch size 8、程式為 4；兩個 screening tests 預期 429/503 退回未篩選候選、目前正式程式明確中止；Gemini 503 fake SDK 測試的互動腳本耗盡。這些位置未由本次變更修改。
- 測試需清空 `GROQ_API_KEY`，避免 `load_local_env()` 從本機 `.env` 載入 key。第一次完整 suite 未清空時，舊測試實際呼叫一次 Groq planner，發現後立即中止；無 key 值輸出。後續 suite 在 Groq disabled 環境中執行。
- 本次沒有執行真實 literature review run；未追蹤 `Summer_Project.pdf` 保持原狀。

## 本次 checkout 可確認的狀態

- 本次文件整理前，Windows working copy 的 Git status 僅有預期中的未追蹤 Summer_Project.pdf；未見 tracked file 變更。
- 本機沒有 .omo/evidence/、data/run/ 或 data/outputs/。這些可能是 WSL OpenCode checkout 的未追蹤執行資料，Git 不會同步；請在原 WSL 環境查證，不代表紀錄遺失。
- 使用者回報 Windows dry run 先遇 OpenAlex anonymous-search 503，加入 OpenAlex key 後又遇 query_timeout 504。dry run 不載入 Gemini，但需 OpenAlex key 時應載入 .env；目前 checkout 未取得完整終端 log，504 對應的實際子查詢仍需從 Search plan 確認。
- HANDOFF 記載 2026-09-29 rate limiting/key rotation 里程碑有 504 tests 通過。這是舊執行紀錄，非本次重新測試；舊版 462 數字已過時。

## 目前工作基線

- 正式入口為 literature_review.main：規劃 → 多 query 搜尋與排名 → screening → OA PDF 下載 → 全文抽取與分段 → per-paper functional scoring / notes → synthesis → report 與 papers JSON。
- 正式證據路徑每篇論文內取樣 top-2 chunks；corpus-wide retrieval、RCS 等為 CLI／比較／legacy 路徑。詳見 HANDOFF.md。
- 預設研究政策為最近三年與 top-venue whitelist；後者是硬篩選，已知可能使某些 query 零候選。一次性探索可明確使用 --venues none。
- Gemini key 分工、503/429 的 stage-specific 行為、額度限制及真實 run 命令見 HANDOFF.md。

## 下一步

1. 若要評估剛才那輪，先從 WSL/OpenCode 找回原始終端 log、Langfuse trace（若有）、data/outputs/report_*.json 與 papers_*.json；不要只憑 503 重跑。
2. 由 log 的 [llm] label/status 找出 503 發生的階段。若流程中止，檢查候選數、venue 篩選、下載／抽取狀態及每把 key 的實際額度紀錄。
3. 若決定重新跑真實流程，先確認使用到的所有 Gemini key 剩餘額度、venue policy，並備份或指定下載目錄；預設 data/run/ 會被清空重建。
4. 完整 run 後再用逐篇 evidence、claim-to-chunk traceability 與 section-distribution 統計評估輸出；schema 通過本身不等於學術品質已驗證。

## 近期完成項目索引

- Planner prompt tightening：.omo/plans/planner-prompt-tightening.md
- Section stats / references heading filter：.omo/plans/section-stats-and-ref-filter.md
- Gemini rate limiting / key rotation：.omo/plans/llm-rate-limit-and-key-rotation.md
- 更早里程碑：見 .omo/plans/；詳細測試 log 若未在此 checkout，需回原執行環境查找。

本檔僅記錄有來源的狀態；新的測試、run 或 commit 完成後再更新日期與證據位置。不要從未追蹤檔案缺席推論其在另一個 working copy 不存在。
