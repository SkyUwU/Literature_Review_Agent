# 專案狀態（盤點日期：2026-10-03）

## 2026-10-03：舊測試契約更新與操作文件整理（最新）

- 已依本輪授權更新四個舊測試，不改正式程式／品質政策：screening 429/503 中止且不下載未篩選候選；Gemini 503 首次加三次重試（15/30/60 秒，fake clock）；functional batch_size 預設 4。
- 新增 Git 可追蹤的 `tests/run_offline.py`：清除 provider 設定、封鎖 network／child process／workspace .env、暫存 cwd 與既有 data/ 寫入保護。Pairwise CLI 只注入短假 key；Windows cleanup 先還原 cwd。
- 最新完整離線 suite **621/621 全通過**，process exit 0；沒有排除 tests。四個舊契約失敗已消除。git diff --check／key-pattern scan 通過。未執行真實系統 API、未修改既有 data/ 或提交。證據：`.omo/evidence/maintenance-tests-and-guidance.md`、`maintenance-full-suite.log`。
- 已核對並更新 AGENTS／HANDOFF／README：原 idea 保存、M2 雙 query、M3 PDF recovery、screening 合併 keep/maybe 目標、dry run 連網且預設路徑同樣重建；改用可重現離線測試與新 dest-dir 的 run 命令。下方標示歷史的完成數字／待辦／失敗不代表當前狀態，舊 run 紀錄保留。
- 既有 bounded-plan-review/execution 與 `.omo/skill-drafts/` 副本 SHA256 一致，無需改動。新增官方 `jupyter-notebook` skill 到本機 Codex skills，供研究 notebook／比較實驗使用；未安裝 notebook runtime 或改專案依賴。這是 Codex 工作指引，不改 literature review 系統行為。
- 首次真實 run 可在確認實際 provider 額度／模型權限後使用新的 `--dest-dir`，保留預設年份／venue。M1/M2 語義品質、M3 真實取得率與完整報告品質仍需真實驗證；代理本輪未代跑。

以下內容保留各輪當時的狀態與證據；其中「未完成」「下一步」等以本節最新狀態為準。


## 2026-10-03：M3 有效 PDF 與 OA 補救（歷史）

- 依本輪授權只完成 M3，主代理自審；M1/M2 不重做。新增結構化 fetch metadata、25 MiB raw/gzip 上限、30 秒／五 redirects、有界 HTML/ACL/AAAI PDF link 與 DOI/Unpaywall recovery；僅 screening keep/maybe 啟用。UNPAYWALL_EMAIL 本機設定，缺 DOI/email 明示 skipped；DOI cache 每 run lookup 一次，每篇三個額外文獻 URL，不遞迴。
- 所有下載需 %PDF-／PyMuPDF 可開啟且至少一頁／身分 confirmed 才 atomic write 及進全文；mismatch/unconfirmed 都拒絕。保留 OA location version/host/license/source，provider OA URL 與實際 final URL 分列，URL 移除 secrets，venue 政策不變。
- PapersOutput 新增 optional download_attempts，獨立保存失敗候選與逐次下載 snapshot；library 仍 opt-in persistence，dry run 無新補救請求。成功／recovered／HTTP attempts／shortfall 分列；失敗不再算成功 duplicate，run 內失敗去重避免跨 query 重試 URL budget。
- 新增 36 M3 tests。完整隔離 suite 621 tests：617 通過、四個既有契約失敗（screening 429/503 兩項、Gemini 503 fake script、batch_size=8 預期而 HEAD=4）；未排除 tests。最後細節修正後 70 直接受影響 tests 全通過。git diff --check／key-pattern scan 通過。測試封鎖 network／child processes／workspace .env，暫存 cwd，最後 runner 禁止既有 data/ 寫入；未執行真實 API、未修改既有輸出、未提交。
- 證據：`.omo/evidence/m3-validated-pdf-recovery.md`、`m3-full-suite-final.log`、`m3-final-affected.log`、`run_m3_offline.py`。真實取得率／Unpaywall smoke／LLM 品質未驗證；完整 suite 未全通過。下方「M3 未實作」為歷史快照。
## 2026-10-03：M2 原始任務與子查詢共同 embedding（歷史）

- 依使用者授權只完成 M2，由主代理自審；M1 不重做，M3 未實作。候選 ranking 與正式 scoring/notes chunk sampling 使用 idea/source query 雙相似度，預設 idea_weight=0.5，可由 library 設定 0–1。Run 內快取 query/title+abstract vectors；既有章節政策、2/9 上限、citation/recency、functional 聚合、threshold/quota 不變。
- RankedPaper 新增 optional Pydantic 分項及兩種 query provenance，papers.run.embedding_ranking 保存新 run 的候選排名。Notes checkpoint fingerprint 增加取樣政策、權重及來源 query；舊政策／不同權重／來源不符拒絕重用，原檔保留。
- 最後離線回歸 120 focused tests 全通過（含新增 5 tests 與 checkpoint/provenance 回歸）；封鎖網路、dotenv，使用 fake clients/encoders 與 temporary directories。git diff --check 通過，tracked key-pattern scan 無命中。未跑完整 suite、未呼叫真實 API、未修改既有 data/輸出，未提交。
- 本地 cached BGE 比較完成：7 篇舊已下載論文及 MoE／醫學影像 fixtures，α=0/0.5/1。醫學 fixture 的核心文獻順位改善，但舊 peer-review 論文仍前二，citation/recency 能主導總分；不能宣稱歧義已解決或 0.5 最佳。完整原搜尋池未保存，此比較不等於重播原 run。
- 證據：`.omo/evidence/m2-dual-query-embedding.md`、`m2-tests-final.log`、`m2-alpha-comparison.json`；離線 runner 與 encoder 比較腳本也在該 evidence 目錄。M3 PDF/HTML/Unpaywall、完整 suite 與真實模型品質驗證仍未完成。

## 2026-10-03：M1 任務理解與跨階段歧義處理（歷史）

- 使用者授權只實作 `.omo/plans/task-alignment-and-validated-pdf-recovery.md` 的 M1，已完成並由主代理自審。新增同次 planning 的 TaskInterpretation、跨領域歧義對照例、允許任務錨點重複，以及 screening/scoring/report/directions 與 repair 的原文／摘要傳遞。papers.run 保存完整 validated plan；舊資料及 library optional 介面可讀。
- Notes 不接收 idea，只忠實保留原論文任務與排除 boilerplate；notes prompt policy version 更新，舊 manifest 不再沿用（保留原檔）。聚合公式、quota、threshold 不變，輸出改為 Aggregated 並列分項。M2 embedding、M3 PDF/HTML/Unpaywall、quote/頁碼未實作。
- 離線回歸 335 tests：332 通過、3 個既有失敗（screening 429/503 tests 期待降級、model defaults 期待 batch size 8 而 HEAD 為 4）。前兩者另以 HEAD 原始 main.py 重現；defaults 以 HEAD source 核對。最後 schema 順序及格式整理後，受影響的 169 tests 全通過，含新增 M1 12 tests。未執行完整 suite 或真實 API，不能據此宣稱 review 歧義已經真實模型驗證解決。
- 證據：`.omo/evidence/m1-task-alignment.md`、`m1-offline-tests-final.log`、`m1-final-affected-tests.log`、`m1-baseline-failures.log`。測試封鎖 network/child processes/專案 .env，使用暫存 cwd、fake clients/encoders；既有 data/ 與 Summer_Project.pdf 未修改。git diff --check 與 tracked key-pattern scan 通過。未由代理提交。

## 歷史接續摘要（2026-10-02，不作為目前待辦）

- 協作引導已改為目前 Codex 可遵循的白話規則：預設主代理自審與驗收，其他代理須明確授權；只使用本輪實際可用工具。歷史段落的 route B／plan-review 不作為目前能力或安裝狀態的證據。
- bounded-plan-review／bounded-plan-execution 已完成修訂及主代理情境自審，uv 執行格式 validator 與 UI metadata 檢查通過，已安裝至本機 `C:/Users/User/.codex/skills/`，並比對檔案 hash。來源副本在 `.omo/skill-drafts/`；自審證據在 `.omo/evidence/skill-workflow-review.md`。未使用獨立代理行為實測，未跑專案測試或真實 API；全域 skills 不隨專案 Git 自動同步。
- 已依使用者本輪授權完成 downloaded-paper-dispositions 里程碑：成功下載者的唯一 disposition、結構化 threshold/quota 原因、真實分數與 scoring batch 保存、extraction/notes/synthesis 狀態，以及 CLI 的 atomic papers snapshots。Library 寫檔須明確 opt-in；舊介面與政策維持。
- 最新離線驗收 223 focused tests 通過（dispositions 16、functional/pipeline 70、main 57、synthesis 80）；main 另有兩個既有 screening 429/503 降級預期衝突，未修正／未計入通過數。驗收封鎖網路、fake encoder、暫存 cwd；未跑完整 suite 或真實 API。一般自審，未使用 plan-review。
- 本輪測試事故：隔離前既有 main tests 觸發預設 `data/run/` 重建。已修正 MainEntryTests 的 cwd 隔離，且 bad-PDF fixture 注入 fake encoder。從 `data/run_20261002_000823/` 同名副本恢復舊 papers 清單中的 3 份 PDF；另 5 份未找到本機副本，無法確認完整恢復。最新 12 份 PDF、report/papers JSON 與 notes checkpoints 仍在；詳見 `.omo/evidence/downloaded-paper-dispositions.md`。本輪沒有下載補回或刪除其他資料。
- 實作與文件尚未由代理提交；本輪明列 stage source/tests/docs，勿 stage Summer_Project.pdf 或 data/。下方「尚未開始」為本輪之前的歷史接續資訊。

- 本對話已完成分節報告里程碑，109 個 focused tests 通過；未跑完整 suite 或真實 API。新對話不要重做已完成工作，也不要自行跑 API。
- 下一步計畫：`.omo/plans/downloaded-paper-dispositions.md`，使用者已要求先寫計畫，尚未開始實作。範圍限成功下載論文的處理紀錄、實際評分／排除原因，以及失敗 run 仍保存 papers 診斷；搜尋階段 selection_events 暫緩。
- 先 git status，讀 AGENTS.md、HANDOFF.md、本檔與新計畫，再說明並確認實作範圍。route B 不使用子代理；Codex 未使用另一環境 plan-review。
- 本輪末次 Git 檢查：分節報告 source/tests/docs 已無待提交變更；僅本輪 STATE 更新與新計畫待提交。以新對話實際 Git 狀態為準，保留既有變更、不重置。`Summer_Project.pdf`、`data/` 為未追蹤資料，不提交。
- 實際案例：`data/outputs/papers_20261002_001952_120309.json` 與 companion report，12 篇下載、7 篇 notes。不能由這些舊 JSON 還原另外 5 篇未保存的具體排除分數或原因；不補造診斷。
- Notes 短代號已完成；PDF 驗證、完整 checkpoint 續跑與 report checkpoint 尚未實作。原始 research query 為 `LLM-based automated literature review`。

## 2026-10-02：依大綱分節生成報告

- 已依使用者「接續執行」實作 `.omo/plans/section-scoped-report-generation.md` 第一個里程碑：每次只提供本節 claims／paper attribution、原始 query、全局大綱 title/purpose；節內引用範圍與 schema／缺引用／額外標題共用一次 repair，仍無效即停止，不生成完整 report。
- 程式依序組裝全文；report JSON 保存 outline 與 report_sections（index/title/body/allowed/cited IDs），舊 JSON 與 deterministic 路徑保持可讀，notes checkpoint 格式不變。Directions 繼續獨立使用全局 claims。K 節正常 synthesis calls 為 K+2，未新增報告 checkpoint。
- 已完成一般唯讀自審，未使用另一環境的 plan-review。離線驗收：synthesis 80、section report 6、pipeline 22、main 正式入口 fake flow 1，共 109 tests 通過；git diff --check 通過、key-pattern scan 無命中。未跑完整 suite 或真實 API，不改選文政策、不刪資料。首次 pipeline focused run 的 2 個錯誤為舊 fake report 移除標題後低於正文 100 字元下限，修正 fixture 後重跑通過，未放寬正式 schema。
- 下一個里程碑為已下載論文的 paper_dispositions；搜尋階段 selection_events、PDF 驗證與完整 checkpoint 續跑尚未實作。

## 2026-10-02：逐篇 notes 短引用代號

- 已依授權實作 request／batch 內 `C1`、`C2` 代號；驗證 claims 與 coverage 引用後映射回完整 chunk ID，保留原來源物件、quote／頁碼與 checkpoint 輸出格式。未知代號維持一次 repair，失敗則保留既有 notes 未完成處理。
- Synthesis 離線 fake-client tests 80 個通過，涵蓋長 ID 映射、未知 coverage 代號拒絕及 Groq 分批代號作用域；pipeline focused tests 22 個通過（exit 0），涵蓋 checkpoint 重用與不符拒絕。git diff --check 通過，key-pattern scan 無命中。未執行完整 suite 或真實 API，未刪除既有資料。同步 main fake notes fixture，未重跑 test_main suite。
- 本里程碑不含 PDF 下載驗證與完整 checkpoint 續跑；PDF 檢查為下一個里程碑。Codex 未使用另一環境的 `plan-review`，未新增計畫檔。

## 換對話接續摘要與待評估方向（2026-10-01）

本節為交接與候選方向，不是已核准的完整實作計畫。下一輪先檢查 git status，讀 AGENTS.md、HANDOFF.md 與本檔，再選一個有界里程碑；不可直接全部實作或自動重跑付費 API。

### 已完成與目前限制

- 已修正 FullTextDocument.title 存取、補蒐 query 覆蓋原始問題、短 query／schema 範例不一致、priority enum、零候選 screening 與搜尋計數；已分離 publication_venues／pdf_host，加入 OpenAlex locations 讀取與通用任務錨定 prompt。
- SS publicationVenue 優先、缺名稱時才讀 legacy venue；目前沒有 venue 衝突判定或額外發表位置補查。PDF 在 arXiv 不代表未發表。provider_reported 不等於官方接收核驗。
- 最新通用 prompt 調整後重跑 13 個 follow-up tests 通過；此前 planner 23、screening 17 通過。只有 focused／fake-client 驗證，不代表完整 suite 或真實搜尋品質已驗收。
- 使用者已確認原始輸入是 `LLM-based automated literature review`，可用於後續新 run；舊 JSON 的 query 是覆蓋 bug 結果，不可當作原始問題。錯誤 run 影響 functional scoring／synthesis，需重新評估，不能只改 JSON 字串。
- 舊輸出 `data/outputs/papers_20261001_213412_789889.json` 與對應 `report_20261001_213412_789889.json` 保留供比較；對應錯誤 checkpoint c18bd3dc81f241ec988828b37cbc05bf 已依授權刪除，其他資料保留。

### 建議優先順序（尚未實作）

1. 使用原始問題重新執行與品質診斷：原始 query 是 `LLM-based automated literature review`。舊 run 的 query 覆蓋 bug 影響 scoring／synthesis，不能只改 JSON 字串；重新執行仍需另行確認 provider 用量／模型權限、下載目的地與篩選政策，並非本輪文件整理的執行範圍。
2. 證據可追溯性：目前 quote 固定取 chunk 前 240 字元，用於來源辨識即可，不必強制是重點句；它不保證直接支持 claim，核對主張需閱讀完整來源 chunk。若未來需要直接展示 claim 支持證據，可另評估擷取原文支持句並驗證屬於來源 chunk。正式章節切分尚未保留 PDF 頁碼映射；保留 chunk ID，不推造頁碼。支持句與頁碼各為可選獨立里程碑。
3. 排除紀錄輸出：記錄年份／venue／screening／重複／OA 缺失／下載或抽取失敗／functional 選取等階段、原因與已有分數；未執行評分者不可填假分數。
4. 發表位置補查與 track：對缺 venue、arXiv-only、無法辨認來源或需要 track 的候選，考慮以 DOI／arXiv ID／標題作者匹配 OpenAlex 或官方 proceedings／OpenReview。分開 provider metadata 與官方核驗；main、workshop、Student Abstract、Findings 等政策待討論，不自動排除。單一 SS 路徑沒有跨來源衝突判定，PDF host 不作接收證據。
5. OA 補齊：考慮 DOI-based Unpaywall／其他 OA locations，保留來源與失敗原因；OA 補齊不等於檢索或正式發表核驗。
6. 檢索擴展：分頁指向 provider 取得第二頁以後的搜尋結果，例如總命中 216 筆但首頁只收到 100 筆。SS／OpenAlex 有界分頁需限制原始候選數與頁數，不湊固定數量的合格論文；SS bulk search、seed-paper citation／reference 擴展另行評估成本與適用情況，尚未實作。
7. 搜尋回饋：指依候選不足的階段與原因，評估是否改寫 query、取下一頁或補 metadata；目前已有候選統計與 LLM gap follow-up，尚未加入這類依原因自動選策略的流程。需區分 provider 零結果、年份／venue 後零結果、screening 全拒絕、OA 不足，保留研究政策與呼叫上限。2–4 words 的限制尚未放寬。

使用者準備下一輪整理敘述檔案；建議 HANDOFF.md 保留穩定架構／操作規則，STATE.md 保留目前狀態與待辦，詳細歷史移到既有 evidence／歷史紀錄，避免多處重複或把候選方向寫成已完成。

### 本輪文件整理與驗證界線

- 2026-10-01 唯讀核對程式與文件後整理敘述；未改程式、未跑測試、未執行真實 API。下方測試數皆為各里程碑歷史紀錄，最新完整 suite 尚未重新驗證通過。
- 本機 `data/run/`、`data/outputs/` 與上述舊 papers JSON 存在；同時間戳 report JSON 本輪未找到，不能把先前「保留」紀錄當成當前檔案存在證據。`.omo/evidence/` 與已授權刪除的錯誤 checkpoint 不存在；不據此推論 WSL 資料狀態。
- `plan-review` 屬另一執行環境，Codex 本輪未使用該 skill；未新增計畫檔或宣稱完成該流程。

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
- 較早盤點曾記錄本機沒有 .omo/evidence/、data/run/ 或 data/outputs/；這是歷史快照，已由頂部本輪盤點更新。WSL 未追蹤資料不會由 Git 同步。
- 使用者回報 Windows dry run 先遇 OpenAlex anonymous-search 503，加入 OpenAlex key 後又遇 query_timeout 504。dry run 不載入 Gemini，但需 OpenAlex key 時應載入 .env；目前 checkout 未取得完整終端 log，504 對應的實際子查詢仍需從 Search plan 確認。
- HANDOFF 記載 2026-09-29 rate limiting/key rotation 里程碑有 504 tests 通過。這是舊執行紀錄，非本次重新測試；舊版 462 數字已過時。

## 目前工作基線

- 正式入口為 literature_review.main：規劃 → 多 query 搜尋與排名 → screening → OA PDF 下載 → 全文抽取與分段 → per-paper functional scoring / notes → synthesis → report 與 papers JSON。
- 正式證據路徑逐篇章節取樣：scoring 最多 2 chunks，notes 最多 9 chunks；corpus-wide retrieval、RCS 等為 CLI／比較／legacy 路徑。詳見 HANDOFF.md。
- 預設研究政策為最近三年與 top-venue whitelist；後者是硬篩選，已知可能使某些 query 零候選。一次性探索可明確使用 --venues none。
- Gemini key 分工、503/429 的 stage-specific 行為、額度限制及真實 run 命令見 HANDOFF.md。

## 歷史下一步（當時 run 診斷建議）

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
