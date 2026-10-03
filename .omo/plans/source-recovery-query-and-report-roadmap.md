# 全文來源、搜尋與報告組織：後續里程碑概要

日期：2026-10-03。

狀態：使用者授權保存三個里程碑的大致紀錄；僅討論稿，尚非決策完整的實作計畫，不構成程式修改或真實 API 執行授權。採主代理自審；不使用子代理。

## 背景與共通界線

- 本輪 papers/report_20261003_170622_726392.json 記錄 35 篇下載候選、11 篇有 provider OA 連結、6 篇成功下载、3 篇納入 synthesis；補救成功 0。
- 24 個 Unpaywall 404 全部對應 arXiv DOI；其意義是該 lookup 未找到記錄，不是論文無 OA。5 個替代 arXiv PDF 為 network_error，舊輸出不足以還原具體原因。
- 既有 task-alignment 計畫的 M1/M2/M3 已完成，不重做。本概要使用 R1/R2/R3 區別後續工作。
- 保留既有輸出、使用者變更、原始 provider metadata 與來源資訊。每輪只實作一個另行確認的里程碑。
- 不默默改年份／venue、functional aggregation／threshold／quota；不以 OA 可取得性替代研究相關性。

## R1：全文來源整理、PDF 補救與階段診斷

### 方向

- 搜尋及摘要補齊時保存已知識別碼與全文位置；對符合現有年份／venue 的候選，有限度補缺少的來源資訊，再分桶及 screening。此時不保證已下載，不新增 PDF 可下載的硬門檻。
- screening 前整理來源，screening 後才對 keep/maybe 下載、驗證。換同篇的下載來源不重新 screening；新增論文候選才需要 screening。
- Semantic Scholar 保留 arXiv 等 external IDs；OpenAlex 保存可用 locations，而非只留 best_oa_location.pdf_url。共用 lookup cache，已取得的 metadata 不重查。
- 有可靠 arXiv ID／arXiv DOI 時產生官方 PDF 候選；結合既有 provider／HTML／Unpaywall 和 OpenAlex 來源。已知 ACL／OpenReview 識別碼的官方來源可評估納入，不做全面標題爬搜。
- 原始 DOI 不覆寫；記錄識別碼與經確認的 preprint／出版版本關係。不同 DOI 不自動合併，相似標題不單独構成版本關係。成功下載記錄實際來源與版本；全文證據只歸屬實際使用版本。
- 所有來源照常經過 signature、parser 及身分確認。未確認版本關係造成的 DOI 差異不得直接接受，也不能讓已確認的 preprint／出版對應被舊單 DOI 規則錯誤拒絕。
- 403 不盲目重試；404 lookup 明示 not found；暫時性網路錯誤、429／適當 5xx 可有限重試。細分 DNS／TLS／timeout／connection 等，保存安全錯誤碼、耗時、attempt 與實際失敗步驟，不輸出 secrets。
- 保存各階段數量及可識別候選的淘汰／保留事件：provider normalization、摘要補齊、年份／venue、分桶、screening、下載；provider 未回傳的記錄不虛構逐篇原因。
- 澄清 OA metadata coverage、有效 PDF 成功率、lookup not found、fetch failure 等名稱；新增清楚欄位，舊欄位相容，舊 JSON 不回填。

### 驗收與待展開

- 離線 fake transports 覆蓋 missing OA→其他來源、arXiv DOI、同篇多來源、版本對應／錯配、403／404／429／TLS／timeout、cache、有限重試、診斷持久化、舊 JSON 相容與不重複 screening。
- 不含搜尋續頁、不要求取得 100 篇可下載論文，不改 LLM prompt／評分政策；不執行真實 API。
- 執行前另行定案：來源查找顺序、lookup／URL／重試與 run 上限、跨階段事件 schema、版本確認規則及 OpenReview 支援邊界。保留原 M3 的有界精神，不無限擴張來源嘗試。

## R2：搜尋詞與 planning／follow-up 規則整理

- 初步採 2–6 詞硬界線、通常 3–5 詞；每條 query 只針對一個主要面向，保留原任務或不歧義的學術等價名稱。
- 移除每條 query 強制跨維度組合；方法、benchmark、evaluation 等面向依 idea 選擇，不固定平均分配。
- 初始 planning、follow-up、repair 及詞數 validation 同步採共用規則；purpose 正確不能補救實際 query 失去任務錨點。
- 保留原始 idea 的權威性、現有搜尋與 venue 品質界線；不直接加入搜尋分頁。必要時另議有界分頁，不以 PDF 成功數冒充搜尋回傳數。
- 驗收：字數邊界、repair、follow-up 一致性、跨領域歧義案例；真實 retrieval 效果須另以固定條件比較，不能由 mock tests 宣稱改善。
- 待展開：query 語義驗收與實際搜尋評估方式；2–6 詞為設計假設，尚非效果結論。

## R3：文獻角色、需求導向大綱與報告

- 保留現有 utility score，不新增直接相關性總分或固定核心／輔助篇數比例。
- 增加針對原始 idea 的角色背景：核心、可轉用、僅背景、未判定，以及具體可用階段／限制。角色是報告組織資訊，不取代納入決策、不增加新的事實證據。
- 優先重用既有 functional scoring 的證據與理由形成結構化角色；來源不充分時標未判定，不由 similarity 或 utility 分數直接推定角色。角色形成介面與聚合方式須正式計畫定案。
- 以 paper_id 將角色背景對應到既有 claims，另傳給 outline、章節及 directions；章節保留相同背景且 claims 仍按 allowed IDs 限制。逐篇 notes 忠實原任務，不為角色改寫。
- 大綱依使用者需求而非 claim 數量分配篇幅：核心證據支持主要結論，相鄰方法只展開具體轉用內容；不足明示缺口，不用旁支內容填滿。
- 不要求引用全部 claims；壓縮對目標任務無直接用途的 QA prompt／evaluator 訓練細節，完整逐篇資料仍保存。
- 驗收：核心 claims 少而旁支多、全為相鄰證據、角色未知、同篇跨階段用途、章節背景傳遞、claim 來源與 ID 驗證、舊 notes／輸出相容。
- 小型品質案例由代理先準備約 5 個不同 idea 及判斷理由，使用者不需自行建立大型標記集。案例自審不稱客觀 benchmark；fake flow 不代替真實模型評估，真實執行另行安排。

## 自審結論與接續

概要與目前介面一致：Paper 現為單 DOI／單 OA URL；PDF 補救沒有 OpenAlex lookup；synthesis notes payload 只有 paper_id／claims，未傳 functional rationale／角色背景。

結論：方向已記錄；正式實作前仍需展開 R1 的介面、請求預算、版本確認與驗收。R2/R3 是後續方向，不隨 R1 自動執行。未修改程式或設定，未驗證真實網路錯誤原因或模型品質。
