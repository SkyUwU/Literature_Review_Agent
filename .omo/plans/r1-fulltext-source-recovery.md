# R1：全文來源整理、PDF 補救與階段診斷實作計畫

日期：2026-10-03。權威上位範圍：`source-recovery-query-and-report-roadmap.md` 的 R1。

狀態：規劃及自審後，依使用者「那可以執行」授權完成 R1（2026-10-04）。最終隔離離線 suite 679 tests 全通過、exit 0；未執行真實 API、未修改既有輸出，R2/R3 未展開。下文保存原設計與規劃時的基線；實作與驗收證據見 `.omo/evidence/r1-source-recovery.md`。

## 1. 目標與界線

讓同篇候選的已知 identifiers／全文位置從搜尋一路保存到實際使用的全文來源；先整理 metadata，screening keep/maybe 後才下載及驗證；失敗時能區分未找到 metadata、沒有位置、transport、格式、身分與預算限制。OA 可取得性不影響 ranking、分桶或 screening 納入。

只展開 R1。排除 R2 query 詞數／prompt、R3 文獻角色／報告組織、搜尋續頁／bulk／seed 擴展、全面標題搜尋、正式接收／track 核驗、quote／頁碼改進、完整續跑及舊輸出回填。年份／venue、embedding、screening repair、scoring 聚合／threshold／quota、2/9 chunk 取樣及 notes policy 保持既有契約。來源解析不用 LLM。不要求達到固定可下載篇數。

## 2. 本輪核對的基線

- Git 初始只有未追蹤 `Summer_Project.pdf`、`data/`；保留。指定 AGENTS、HANDOFF、STATE 與 roadmap 已讀取。
- `models.py:Paper` 為單 DOI、單 provider OA URL；`DownloadedPaperEntry` 保存 Paper/query/path/priority。`PaperSource` 只存 paper ID/path/claim IDs。
- `ss_search.py` 已請求 externalIds，但 adapter 只留下 DOI。`search.py` 已取得 locations，但全文只用 best-OA PDF URL；publication provenance 已另行保存。
- `main.py:backfill_abstracts` 以 DOI 查 OpenAlex，select 只取 abstract，未共用完整來源 cache；初始及 follow-up 皆經 `_search_and_rank`。
- `pdf_fetch.py:recover_pdf` 先 provider、其 HTML links，再 Unpaywall；每篇三個額外 URL、Unpaywall run cache、30 秒、五 redirects、25 MiB raw／decoded 上限。`pdf_identity` 為 DOI／title 的保守核對，DOI 衝突會拒絕。
- `DownloadAttempt` 現有 fetch/unpaywall/complete stages；network 細節不足。下載開始才寫 snapshot，`RunDiagnosticsCollector` 從下載成功者建立 dispositions，不能容納搜尋淘汰者。
- `ranking.py:filter_papers` 已有 normalized title 去重並挑 metadata 較佳者；不等於確認版本關係。R1 不改既有選取政策，但須將此事件記為既有去重，而非合併書目身分。
- STATE 的 621/621 是前輪紀錄，本輪未重跑。舊 run 的 network_error 不能由本計畫還原成 DNS/TLS 等原因。

## 3. 已定案設計

使用者已表示由代理選擇對系統合理且有用的補充方式，確保版本正確或標註不同版本，盡量在可行方法內補充。依此採以下有界方案，無需使用者理解或逐一選擇識別碼細節。

1. **來源邊界**：可靠 arXiv ID／arXiv DOI + 已知 ACL ID；保留 provider 所有已知直接 PDF 位置（含 OpenReview）。本 R1 不增加 OpenReview API lookup，並非所有下載經 OpenReview。
2. **費用／預算**：新增 source metadata lookup 每篇最多一個、每 run 40 個，recovery transport 每 run 160 次，暫時錯誤至多兩次重試。這是初始工程上限，不是最佳取得率結論；任何真實執行仍另行確認服務權限／用量。
3. **版本接受**：優先取得明確目標版本；可確認同一研究的替代版本可納入，清楚標註 actual version 與匹配狀態。版本未知但論文身分已確認時可以使用，明示未知，不冒稱目標版本。研究關係未確認／身分衝突者不納入。

預算與來源上限仍限制補救；「盡量補充」不表示新增搜尋候選、無限爬搜或繞過 screening。

## 4. 資料契約與保存責任

在 `models.py` 增加 Pydantic 模型；所有既有 JSON 新欄位均有空集合／None 預設，不重寫舊檔。

| 模型／欄位 | 必要資訊與責任 |
| --- | --- |
| PaperIdentifier；Paper.identifiers | scheme（doi/arxiv/acl/openreview/openalex/semantic_scholar）、value、provider、metadata_path、record_id。arXiv base ID 與明示 revision 分開保存；原 Paper.doi 不改 |
| FullTextLocation；Paper.fulltext_locations | location_id、source、pdf_url/landing_url、metadata_path、source_record_id、identifiers、version、host_type、license；provider 原欄位照留，未知值 None |
| VersionRelation；Paper.version_relations | 兩端 identifier、relation（same_work_version）、status（confirmed/unconfirmed/conflicting）、evidence source/path/record_id、title/author check 結果。書目配對不是 PDF 身分確認 |
| RetrievedFullText | actual source/location ID、sanitized original/final URL、actual identifiers、version（unknown 可用）、version_evidence、relation ID、identity evidence、local path。DownloadedPaperEntry.fulltext 與 PaperSource.fulltext optional |
| CandidateEvent；PapersOutput.candidate_events | event_id、query_id/query、round（initial/follow_up）、provider、record_key、paper_id optional、stage、action、reason_codes、related_record_key、已知分數／bucket／screening priority、sequence。不可識別原始 record 使用 request ID + record ordinal，不虛構 paper_id |
| StageSummary；PapersOutput.stage_summaries | 每 query/round/provider 的 received、normalized、abstract usable、after year、after venue、ranked、sampled、screened keep/maybe/reject、download selected/attempted/valid。全部是實收／實際狀態；未執行 None，實際零為 0 |
| SourceLookupAttempt；PapersOutput.source_lookup_attempts | lookup_id、paper/record key、provider、identifier、phase、status/reason、cache_hit、transport attempt、safe HTTP/error code、elapsed_ms、budget scope；來源整理及摘要查找共用 |
| DownloadAttempt additive fields | attempt_id/location_id、transport attempt index、failure_step、error_kind/code、elapsed_ms、cache_hit、retry wait、budget reason、actual identifiers/relation/identity evidence。舊 stage 值仍可解析 |
| SourceRecoveryPolicy | 明確受驗證的上限、重試及 source order；run snapshot 保存 effective policy 與 counters |

候選 registry 保存 provider 原始可用 identifiers／locations 的白名單欄位與 provenance，以及事件用的最小 title/year/venue。不要保存完整原始 HTTP payload、headers、token、email 或例外訊息。Paper 是可用 metadata 的嚴格模型；不要用 placeholder 製造通過資格。Adapter 在建立 Paper 前即可發 normalization events；沒有 abstract 的 OpenAlex record 仍被排除，但其真實 record key／原因可保存。

URL 執行值只在記憶體；持久化沿用 `sanitize_url` 的 allowlist，去 credentials/未知 query/fragment。request URL 去 secrets 不代表可直接重播，禁止拿 sanitize 後的 URL 代替記憶體下載 URL。錯誤只存型別與白名單分類碼，不存 str(exception)。

## 5. 跨階段流程與查找順序

### 5.1 搜尋、摘要及 screening 前來源整理

1. 在 `run_end_to_end` 開始建立 run-owned SourceLookupContext + candidate diagnostics collector；初始及 follow-up 共用。Adapter normalization 保存所有已知 ID/locations，即使已有 OA URL 也保留其他位置，不新增請求。
2. SS 缺摘要的既有 DOI OpenAlex lookup 改為一次取 abstract + identifiers + locations + 最小配對 title/authors。同 endpoint/normalized DOI 的完整結果在 run cache 保存，成功、not_found、耗盡失敗均 cache；摘要更新沿用現有 usable abstract 判準。來源欄位只在配對安全時附加，不改 year/venue/citations/title。
3. 按既有 abstract、年份及 venue 篩選與 title 去重產生事件；使用與現有 filter 同一判準，避免為診斷重寫另一套規則。排名前對 survivors 整理已有位置並產生可靠 arXiv/ACL URL。
4. 仍沒有可用直接 PDF candidate 且有精確 DOI 者，按原 provider 回傳順序做一次 OpenAlex source lookup；優先 cache，缺 key 時不讀出秘密，也不假設服務可用。只處理同篇 record，不採用搜尋結果擴增候選。無 DOI 不做標題搜尋或 arXiv search。
5. lookup budget 耗盡、404、身分不符或無來源都留下狀態，但候選仍依原規則排名／分桶／screening；OA 信息不加入 LLM prompt，不改分桶比例。已提供 OpenReview PDF URL 可作 provider location；不從未知字串猜 ID。

摘要原有 lookup 不因新增 source budget 被取消；新增 source lookup 40 是獨立計數。摘要 lookup 的結果供 cache 重用不算新增 source lookup。摘要 lookup 同樣最多一次 logical lookup/key，transport 最多首次 + 兩次重試；原有 provider search retry/fallback 不改。分別保存既有 abstract transport、新增 source transport，避免聲稱整輪 HTTP 都受 160 上限。

### 5.2 screening 後下載

只對既有 keep/maybe priority groups 依原規則下載。來源優先順序固定為：

1. 原 `open_access_pdf_url` 作 baseline（若同 URL 另有較完整 location metadata 則附加 provenance）。
2. 已知直接 PDF locations：先 published、accepted、submitted、unknown；同版本先官方 ACL/出版位置、官方 arXiv、其他 provider 位置，最後按首次出現順序。保留完整候選列表，但受每篇三個額外 URL 限制。
3. 直接來源都未成功且還有 URL budget 時，嘗試 baseline 回傳 HTML 的明確 PDF links；沒有 baseline HTML 時可用一個已知文獻 landing page（也占三個額外 URL 之一），其 links 只再走一次，不遞迴。沿用 ACL/AAAI bounded parser，不全面爬網站。
4. 尚有 URL budget 才做 Unpaywall；arXiv DOI `10.48550/arXiv.*` 不做 Unpaywall lookup，記 `skipped_arxiv_doi`，直接用 reliable arXiv ID。其他 DOI 缺 email/DOI 維持 skipped；每 DOI/run 一次 logical lookup；依 published/accepted/submitted/unknown 嘗試其直接 PDF。不讀其他不同 DOI 作 Unpaywall 擴張。

每篇三個額外文獻 URL 包含 landing 及其子 PDF，provider baseline 不占；同 canonical URL 不重算、不重 fetch。retry 是同 URL 的 transport attempt，不占新 URL slot。候選同 URL 的所有 metadata provenance 合併保存，衝突版本標 unknown/conflicting，不能以高優先宣告已出版。

不用 source availability 重新 screening；不同 DOI 的 lookup record 未確認關聯不得成為新候選。既有 normalized-title 去重只記 `duplicate_title_policy` 及 representative ID，不建立 VersionRelation、不移轉不同 DOI 的 locations；同精確 ID 的 metadata 才能共用。

## 6. 預算、cache、重試與失敗處理

建議 effective policy：new_source_lookups_per_paper=1、new_source_lookups_per_run=40、extra_document_urls_per_paper=3、recovery_transports_per_run=160、max_retries=2。Library 以 optional policy/context keyword 注入；CLI 採相同預設，本輪不另加一批 CLI flags；變更上限需另行核准並保存 effective policy。

- 新 source lookup 的首次與 retries 同時計入 recovery transport；screening 後額外 PDF/landing、Unpaywall lookup/retries 也計入。baseline 的首次 provider fetch 不計新增 recovery，上面的 baseline retry 計入。搜尋與既有 abstract lookup 不在 160 範圍，另列真實總 transport 數。
- 每項 logical lookup 預約一次 logical slot；每次 transport 前扣 slot。cache hit、URL 推導、parser/identity/write、skip events 均不扣。40 logical lookup 不包含 retries，160 包含 retries。redirects 仍為單次 fetch，由五 redirects/30 秒另設邊界。
- 耗盡時記 `budget_exhausted` 與 scope，繼續可用 baseline／既有候選及後續研究流程，不放寬身分檢查，不自動升額或改模型。每次重試前先檢查剩餘 budget。
- Cache key = provider + endpoint operation + normalized identifier；摘要/來源用同一完整 OpenAlex work operation。cache 只存 run 記憶體，不新增跨 run 磁碟 cache。403/404/invalid payload/身分衝突與已耗盡暫時失敗均 cache terminal result；同 run 不再啟新 logical lookup。
- 403/404、憑證驗證失敗、invalid URL、format/parser/identity/storage failure 不重試。DNS 暫時解析失敗、timeout、connection reset、429、500/502/503/504 可首次 + 最多兩次；永久 DNS 不重試。其他 4xx/5xx 不泛化重試。
- injectable clock/sleeper；backoff 1、2 秒。Retry-After 支援 seconds/date；需要等待超過 30 秒則記 `retry_deferred` 並停止該 lookup/URL，不能 cap 成 30 秒後提早打服務；沒有 header 使用 backoff。未知 network error 不猜細分類、不自動重試。
- `default_fetcher` 分析 URLError.reason 的真實 exception chain，分類 dns/tls_certificate/tls_transport/timeout/connection/unknown；SSL 不停用 verification。Transport exception metadata 與 FetchResult additive fields 保留 `bytes | FetchResult` fake fetcher 相容；已有 search fetcher 的內部 retry 不重複套外層，lookup transport 採專用單次 injectable fetch。
- 每次 transport 30 秒／五 redirects／25 MiB raw 和 decoded 照留；lookup JSON 同樣有大小/timeout/shape 驗證。錯誤記 failed step（lookup/fetch/decode/signature/parser/identity/write）和 monotonic elapsed_ms，attempt 從 1 開始。

## 7. 身分與版本接受

可靠 identifier 僅來自 typed provider field、可信官方 URL 的完整路徑或 canonical arXiv DOI；驗證 arXiv 新舊格式/revision、ACL ID 路徑；不在任意 title/abstract 搜尋 ID。arXiv ID 缺 revision 時 `/pdf/<base>` 可能取得最新版本，version 不得捏造；PDF 有 revision 才記實際 revision。

VersionRelation 的 confirmed 門檻：同一 SS externalIds work 明列 DOI/arXiv，或官方 metadata 明示出版 DOI ↔ preprint；OpenAlex DOI endpoint 回傳相同 DOI 且 locations 含 canonical arXiv/ACL ID，也可作該 work 的明示來源關係。title normalized exact match，並至少一位 normalized author full name exact overlap；缺 title/authors、衝突、只相似標題／作者則 unconfirmed/conflicting。不使用模糊分數或 LLM 裁決；不向其他 work 自動擴張，不建立不同 DOI 的全域 dedup equivalence。此 confirmed 僅確認同研究關係，不證明同一修訂版本或相同內容。

新增 VersionTarget 與 VersionMatch：搜尋目標保存 work identifiers、明示 revision、version kind、證據來源；未指定 revision 不猜搜尋摘要對應版本。RetrievedFullText 分別保存 actual revision、version kind 及 match（exact/alternative/unknown），每個版本 assertion 另列 evidence level（official/pdf_observed/provider_reported/unknown）。完整 arXiv vN 官方 URL + PDF 標記一致才 exact；出版 DOI 官方稿核對 DOI/title/authors/出版資訊可作對應出版版本。OpenAlex publishedVersion 等只是類別與 provider 報告，不能單獨證明 exact。相同 SHA-256 僅證明同檔，且至少一份須已核對身分／版本；hash 不同不推定不同學術版本。

下載 queue 在 baseline 前優先明確目標 revision 的可靠官方 URL（仍占額外 URL slot）；其他 locations 先明確目標版本，再版本未知但有目標版本 metadata，再已確認同研究的替代版本，類別內才按第 5 節次序。只下載一份成功確認的全文，不為比對額外下載全部版本。成功後記下載 UTC 時間、SHA-256、target/actual/match/evidence；arXiv base ID 下載後可辨認 vN 就固定 actual revision，無法辨認留 unknown。報告結構化 PaperSource 保存相同資訊；材料來源呈現標籤為「指定版本已確認」「使用替代版本：…」「版本未確認」，不改 R3 報告正文生成。版本不同但已確認同研究可使用，claim 僅歸屬 actual source；無法確認同研究不得使用。

所有下載先 signature/parser；只有 actual identifier 符合原 DOI／可靠同 arXiv ID，或符合 confirmed relation 的允許端點，且無明確 title/author 衝突，才身分 confirmed。保留原 DOI match 與既有 conservative title fallback；但 DOI 不同時不得靠 title fallback 覆蓋衝突。對跨版本例外，須有 PDF metadata／首頁對應 identifier + title／author checks，只有「URL 看似 arXiv」不足。

現有首頁 DOI 掃描可能誤抓首頁引用：改為明確區分 observed identifier 與確認 identifier；不能把任何 match 都當唯一書目真值。無法定位或多個互相衝突 ID 時 unconfirmed，不使用後頁 bibliography 裁決。短標題仍需原有 exact metadata/first-line 判準。新增 relation 接受測試及原來 DOI mismatch 回歸，不能藉關係表接受任意 PDF。

成功才 atomic write。`RetrievedFullText` 建立一次，跟隨下載 entry → main paper_id mapping → `run_synthesis_pipeline(fulltext_sources=None)` → `PaperSource.fulltext`。Claims/chunks 的 paper_id 仍是原 candidate ID，搭配 actual fulltext source 確認版本；不把兩版本 chunks 混在一起、不宣稱使用出版版本全文。未知版本保持 unknown。Checkpoint 既有內容 fingerprint 不弱化；source-only metadata 新增不強迫 notes policy migration，同內容可重用，內容改變照原 manifest 拒絕。

## 8. 診斷生命週期及相容性

新增 candidate collector 與既有 downloaded-only dispositions 分工；`ProcessingStage` 在 run failed_stage 支援 search/abstract_backfill/source_resolution/ranking/sampling_candidates/screening，但不要把未下載者塞進 PaperDisposition。

CLI/library opt-in snapshot 在搜尋前建立同一 timestamp/run ID 的 papers JSON，search/normalization/abstract/filter/rank/sample/screen/lookup/download 完成或失敗時 flush；download transport 每次 attempt 可 flush。前段任何例外保存 partial state、failed_stage、safe error，重新拋原錯誤。後段 RunDiagnosticsCollector 採同一 run state，不能重建 run ID 或清空 candidate events。持久化使用既有 atomic save；寫檔失败明示，不假稱已保存。

initial/follow-up 各有 query instance ID；同篇跨 query 不丟事件，各 query stage totals 不等於全 run unique papers。總數另列唯一 record/paper 的計數與去重 key 定義。retain/reject 與處理 failure 分开；未送 screening 為 `not_sampled`，keep/maybe/reject 為真實結果，maybe target satisfied 不算下載失敗。provider total_matches 只當全庫 metadata，不生成未回傳的逐篇事件。

新增清楚 stats：provider_oa_metadata_coverage = 原 provider OA link unique candidates / unique download-eligible candidates；resolved_pdf_location_coverage 另列；validated_pdf_success_rate = 本輪有效 confirmed PDFs / unique attempted candidates；0 denominator 為 None。lookup_not_found/fetch_failed/identity_rejected/budget_skipped 各列計數及基數。保留既有 with_oa_link/oa_ratio_candidates/oa_ratio_attempted 的值與意義，只在說明中標 legacy alias，不悄悄重定義舊欄位。recovered 指 baseline 之外來源成功，不把 cache hit/metadata lookup 算補救成功。

Dry run/legacy 只被動保存 adapter 已給 ID/locations、既有 abstract lookup metadata/cache；不啟新增 source lookup、衍生來源下載、HTML/Unpaywall/retry recovery，不寫 papers/report JSON。保持 return type 與 legacy下載行為；新增正式 recovery 限 screen_client 存在且 keep/maybe。Library diagnostics_output_dir 未設定時只有回傳/記憶體診斷，不意外寫檔。

## 9. 實作順序與預計檔案

1. `models.py`：上述 optional 契約、policy 驗證、run stages；新增 `source_resolution.py`：identifier/locations normalization、版本關係、run cache/budget/source ordering，與 lookup transport 注入。
2. `ss_search.py`、`search.py`：保存 identifiers/locations 與 normalization events；lookup select 擴充只作用 exact lookup。`main.py:backfill_abstracts/_search_candidates/_search_and_rank`：共用 context、來源整理 gate、原始 metadata 保護。
3. `ranking.py`：optional observer 記錄實際 filter/duplicate 決策，return/排序不變；main 在 sample_candidates 與 screening 邊界記錄事件，不修改 screening prompts/比例。
4. `pdf_fetch.py`：source queue、typed transport error、bounded retry、arXiv/ACL、Unpaywall arXiv skip、version-aware identity。`pdf_downloader.py`：policy/context/fulltext mapping、stats、run-wide dedup/counters、進度回呼；Path 返回介面維持。
5. `diagnostics.py`、`main.py:_make_papers_output/run_end_to_end`：前段 atomic snapshot、整輪 exception 保存與 collector handoff。`pipeline.py`、`models.py:PaperSource`：actual source mapping 到 report 結構，不改 synthesis prompt/正文。
6. 相關 tests + 新 `test_source_resolution.py`、`test_candidate_diagnostics.py`；最後更新 HANDOFF/STATE（執行後只記實際證據），必要 README 操作說明。

本輪保存計畫不代表上列檔案已改。下一輪依核准版先做契約與 shared context，再串 initial/follow-up 與 downloader，最後串前後段保存和 provenance，避免局部實作把關鍵欄位留在上游。

## 10. 驗收矩陣

| 需求 | 離線 fixture 與應觀察結果 |
| --- | --- |
| metadata 保留 | SS externalIds、OA 多 locations、unknown/null/malformed 值；原 DOI/OA URL 不覆寫、provenance 可辨來源；旧 Paper/PapersOutput/Report JSON 可讀 |
| 缺 OA 同篇補齊 | survivors exact DOI lookup→alternative PDF→keep/maybe→confirmed；ranking/sample/LLM payload 無 availability gate；來源失敗仍送原 screening |
| arXiv/ACL | new/old ID、revision、10.48550 DOI、錯誤路徑；可靠 ID 產生官方候選，arXiv DOI 不呼叫 Unpaywall；未知 ID 不猜 |
| 多來源／排序 | baseline 失敗、直接來源、HTML、Unpaywall；相同 URL provenance 保留、只 fetch 一次；版本優先序穩定，三 URL 上限含 landing |
| 版本安全 | 原 DOI、confirmed DOI↔arXiv、未確認不同 DOI、衝突 title/authors、僅相似標題、首頁另一篇 DOI、無 revision；未確認拒絕、不合併不同 DOI registry；正確 actual version 到 PaperSource |
| transport | DNS temporary/permanent、TLS cert/transport、timeout/reset/unknown、403、404 lookup、404 PDF、429 Retry-After seconds/date/過長、適用 5xx；fake clock 檢查首次+重試與細分類，不存 exception secrets |
| cache／上限 | 同 DOI 多 query、摘要 lookup→來源 cache hit、negative/failed cache、不同 operation、40/160 精確邊界、retry 最後一 slot、三 URL；cache 不扣 transport，budget exhausted 可診斷、不再連線 |
| 階段事件 | malformed identifiable record、missing abstract、year/venue、既有 title duplicate、bucket not sampled、screen reject/keep/maybe、target satisfied、download failure；query totals/unique totals 分開，未執行 None，實際零 0 |
| partial persistence | search/screening/source/download/extraction/notes/report 各失敗；同 run ID snapshot 留成功與失敗候選，不重建 collector；storage failure 明示且保留原例外 |
| 下游資料 | 成功換同篇 source 不增加 screening calls；actual identifiers/version/path 從 download→pipeline→report JSON 可追溯；同內容 checkpoint 可重用、變更內容不重用 |
| 邊界回歸 | dry/legacy 無新增 recovery requests/JSON；library opt-in；短 query、venue/year、threshold/quota、notes 2/9 與 prompt/repair 不變；既有 data/Summer_Project 不可寫 |

測試重用既有 `test_pdf_recovery`（多來源、大小、gzip、atomic、dry、fake flow）、`test_pdf_downloader`、`test_search/test_ss_search`、`test_search_follow_up`、`test_main`、`test_models`、`test_pipeline`、checkpoint suite；名稱以實作時實際新增 module 核對。

下一輪命令（本輪未執行）：

```powershell
uv run --offline --no-sync python tests/run_offline.py test_source_resolution test_candidate_diagnostics test_pdf_recovery test_pdf_downloader test_search test_ss_search test_search_follow_up test_models test_main test_pipeline
uv run --offline --no-sync python tests/run_offline.py
git diff --check
git grep -nE "AIza[A-Za-z0-9_-]{20,}|AQ\.[A-Za-z0-9_-]{20,}"
```

runner 已核對：清 provider env、封 network/child processes/workspace .env、temporary cwd、禁止既有 data 與 Summer_Project 寫入。用 fake transports/clock/encoder/clients + temporary output，測試中不得實際 API 或下載模型。Focused 合約先驗證，再完整 suite 一次；失敗不排除 tests、不放寬政策。實作 evidence 保存 module/result/exit code/驗證限制，不把歷史 621 當本輪測試結果。真實 provider 格式、取得率、版本配對正確率與研究品質需另行授權、額度/權限確認與新 dest-dir 後評估。

## 11. 自審、修正與接續條件

審核方式：主代理唯讀程式核對及設計對照，自審；沒有子代理或獨立審核。

| 發現與依據 | 影響 | 計畫修正 |
| --- | --- | --- |
| main 在 download 才保存、collector 只有成功下载者 | 前段 reject/失敗消失 | 新 candidate collector、搜尋前 snapshot、同 run handoff；不擴張 dispositions 含義 |
| 摘要 lookup select 僅 abstract | 同 DOI 多查、下游無位置 | 完整 exact-work cache 共用、摘要與新增 source 預算分列 |
| filter_papers 同 title 去重已存在 | 「不同 DOI 不合併」易被誤述為改既有選取 | 保留現政策、記 representative event；禁止把此選取推定 VersionRelation 或轉移不同 DOI 位置 |
| DOI mismatch 一律拒絕、首頁任意 DOI match 可誤認 | 正確 preprint 被拒或首頁引用造成誤認 | 明示 relation 與 actual identity 分離；confirmed relation + actual evidence 才例外，不放寬無關 mismatch |
| PaperSource 只有 path | 成功源/版本只在 log | RetrievedFullText 同時保存 entry 與 report sources；claim 仍連实际 source |
| metadata/URL/HTTP 混合預算 | 上限可被 retry/cache/follow-up 绕过 | run-owned context、每次 transport 扣、摘要/search另計、cache/redirect 定義明确 |

對照情境：大量旁支論文有 PDF、少量直接論文缺 OA→不依來源改 ranking/screening；同 title 不同 DOI→選取政策事件不等於版本確認；Unpaywall 404 但 arXiv 可得→lookup_not_found 不等於無 OA；出版候選實際取得 preprint→報告 provenance 明記 actual version、研究效果不冒稱出版版本；暫時 timeout→有限重試不作研究排除；40/160 用完→budget skipped 與 fetch failure 分開。

結論：**可執行**。使用者已授權代理定案合理補充方式；本計畫採有界來源、目標優先及替代版本明示。追加自審：同研究 relation confirmed 不等於 exact version；兩位置同為 submittedVersion 不等於同 arXiv vN；搜尋未指定版本時不能宣稱 PDF 與摘要精確對應；版本未知與身分未知分開，只有後者阻擋全文納入。驗收矩陣追加 target exact/alternative/unknown、provider-only version 不標 exact、SHA-256 相同／不同的正確含義、target revision 優先與來源標籤持久化。其餘例行名稱／函式拆分可由執行者按契約處理。R2/R3 不展開，實作需另行授權。未決服務契約若離線 fixture/現有 metadata 無法核對，保守不啟用該 lookup，不用猜測宣稱支援；真實驗證仍不屬本次授權。
