# R2：搜尋詞與 planning／follow-up 規則實作計畫

日期：2026-10-04。上位範圍：`source-recovery-query-and-report-roadmap.md` 的 R2。

狀態：設計已由使用者釐清、主代理自審可執行；只有規劃與保存授權，尚未授權實作。R1 已完成，不重做；R3 留待後續。本輪沒有執行測試、真實 API 或修改既有輸出。

## 1. 目標與範圍

讓新生成的 LLM 初始查詢、screening follow-up、Groq 全局 gap 彙整及各自 repair 使用一致的短查詢規則：2–6 個空白分隔詞為硬界線，通常 3–5 詞；每條針對一個主要資訊需求，實際 query 保留原任務或不歧義的學術等價名稱。purpose／target_gap／reason 的正確說明不能補救 query 本身的歧義。

初始仍為 3–4 條 query；follow-up 上限仍為 3 條，不增加搜尋輪數。方法、benchmark、evaluation 等面向依原 idea 選擇，不要求每條跨維度組合、不固定平均分配面向。原始 idea 最高權威，task_interpretation 僅為輔助。

排除：R1 來源／版本／transport budgets、R3 角色與報告組織、搜尋分頁／bulk／seed 擴展、零結果自動補搜尋、新 runtime 語義 judge／關鍵字硬拒絕、真實 API／模型評估、舊輸出回填。年份／venue、ranking／embedding、分桶、screening 決策、scoring 聚合／threshold／quota、chunk 2/9 上限、notes policy、provider retries 保持現行契約。Rule-based／dry-run 原查詢延伸及舊 SearchPlan JSON 相容，不加新詞數門檻。

## 2. 本輪核對的基線

- 已讀 AGENTS、HANDOFF、STATE、roadmap；Git 初始只有未追蹤 `Summer_Project.pdf`、`data/`，保留。
- STATE 及 R1 計畫記錄 R1 完成、679 項離線測試通過；這是前輪紀錄，本輪未重跑。
- `query_policy.py`：SHORT_QUERY_GUIDANCE 與 validate_short_query 共用 2–4 詞；後者使用 split／join 正規化，不檢查語義。
- `planning.py`：build_llm_plan_prompt 額外強制三維度 keyword pools 與每條跨維度組合，另重複 2–4 詞規則；_parse_plan 在新 LLM generation 後逐條驗證。
- create_llm_plan 預設 enable_overlap_repair=True；正式 `main.py:_build_search_plan` 未覆寫。Jaccard 門檻 0.5，重寫低於門檻或僅比原案低皆可被採用；失敗保留原有效 plan。schema 與 overlap 共用最多兩次應用層 generation calls。
- 詞數不合法走 _build_plan_repair_prompt：帶驗證錯誤、原始問題、SHORT_QUERY_GUIDANCE、TASK_INTERPRETATION_GUIDANCE 及前次輸出；不要求降低 overlap。repair 用掉第二次 call 後不再作 overlap repair。
- `screening.py:FollowUpQuery` 欄位描述及 validator 使用同規則；一般 screening、repair、Groq batches 及全局 gap／repair 已接收 SHORT_QUERY_GUIDANCE。GapAnalysis 上限三條。
- `models.py:PlannedQuery` 只要求字元下限，SearchPlan 保持舊 JSON 可讀；不應在其公共 schema 加詞數 validator。
- `tests/run_offline.py` 已提供清除 provider 環境、封鎖網路／子程序／workspace .env、暫存 cwd、既有 data 寫入保護，可重用。

## 3. 使用者已確認的設計

### 3.1 詞數與語義邊界

硬界線改為 2–6 個 whitespace-separated words，通常 3–5 僅為 guidance。連字號、縮寫或數字以既有空白分詞方式計數，不新增 tokenizer、不切斷／截短／自行改寫不合格 query。搜尋用英文學術 keyword phrases；不新增 ASCII 或語言拒絕規則，非空白語言的詞數限制屬已知限制。

在 query_policy.py 集中詞數常數與文字，保留 SHORT_QUERY_GUIDANCE、validate_short_query 公共名稱。screening query 欄位 schema description 同步；planner 原始與 repair prompt 皆取相同規則。Validator 只驗詞數與正規化，不宣稱證明語義品質。

任務錨點可以重複；主要面向須不同，不能只換同義詞冒充不同需求。選擇 idea 實際支持的面向，不為填滿 query 數臆造模型、資料集、方法或 benchmark。跨任務可轉用機制若需搜尋，也要在實際 query 中保留可辨認的目標任務脈絡；不能僅在 purpose 寫目標任務、query 卻完全移到旁支。

語義約束維持 prompt guidance＋小型人工案例自審，不加 runtime judge、額外 API、固定領域黑名單或 literal anchor validator。已知限制：合法詞數的歧義 query 仍可能通過；這不等於允許 prompt 放棄錨定，screening 仍負責既有相關性判斷。

### 3.2 Overlap 與 repair

create_llm_plan 的 enable_overlap_repair 預設改為 False，正式 main 既有呼叫因此停用。保留參數、query_overlap、max_query_overlap、門檻與顯式 True 的比較行為，不新增 CLI flag。這是已確認的 library 預設行為變更；顯式 True 仍可能觸發詞彙分散，其侷限需在 docstring 說明，不推薦作正式品質 gate。

一般路徑有效 plan 一次 generation 即返回，即使任務錨點高度重疊。schema／詞數／必要 task_interpretation 不合法仍共用一次 repair，第二次不合法仍依原例外與 main fallback 契約處理；不改 provider-specific failure 行為。詞數 repair 不新增降低重疊度要求。最多兩次是應用層 generation budget，不包含既有 provider transport retries。

顯式 True 的 schema／overlap 共享兩次預算與保留原有效 plan 行為不改，相關既有測試改為明確傳 True；新增預設 False 的回歸測試。不以新的語義 heuristic 更換舊 overlap 分數。

### 3.3 相容與資料流

PlannedQuery／SearchPlan 公共模型不加詞數硬驗證，保持 rule-based 長 query／舊 JSON 可讀。新 LLM plan 在 _parse_plan 驗 2–6；FollowUpQuery 現有 validator 放寬至 2–6，舊合法 2–4 值仍可讀。沒有欄位新增、舊 JSON 改寫或 notes policy version 變更。

main 接到正規化 query 後按原路徑搜尋、保存 plan、query provenance、candidate diagnostics 及 embedding source_query。原始 idea 保持不被 follow-up 覆寫。Follow-up 新候選照常篩選，零候選不呼叫 screening；repair 不得繞過 decisions 完整性及 priority enum。

query 改變可能使既有 notes manifest 的 paper_queries 不相符：沿用現行拒絕重用與保留原 checkpoint 行為，不為 R2 放寬 fingerprint。

## 4. 預計修改介面與檔案

| 檔案 | 預計工作 |
| --- | --- |
| literature_review/query_policy.py | 集中 2–6 界線與通常 3–5 guidance；單一主要面向、任務錨定、purpose 不可補救歧義；維持原始 idea 權威及通用例子 |
| literature_review/planning.py | 移除固定三維度／跨維度組合與重複詞數說明；預設停用 overlap；同步 docstrings、repair 規則；保留顯式 opt-in |
| literature_review/screening.py | 同步 FollowUpQuery schema 描述；核對 initial／batch／全局 gap／repair 的共用規則，必要時修正互相矛盾的文字與例子 |
| tests/test_planning.py | 新預設、5／6 詞、非法與 repair、面向規則；原 overlap tests 明確 opt-in |
| tests/test_search_follow_up.py | 邊界、一般／Groq gap repair 一致性、實際 query 傳遞 |
| tests/test_screening.py、tests/test_task_alignment.py | 相應 prompt／schema、語義案例及決策完整性回歸；不改 unrelated fixtures |
| tests/test_main.py、tests/test_models.py、tests/test_pipeline.py | 優先重用現有測試，僅必要時增補 formal entry／legacy plan／checkpoint 回歸 |
| HANDOFF.md、AGENTS.md、.omo/STATE.md | 實作完成時同步現行詞數、overlap 預設與驗證限制；歷史紀錄保留 |
| .omo/evidence/r2-query-planning-policy.md | 未來執行者保存自審、案例與實測結果；本輪不虛構執行證據 |

main 正式入口原則上不需修改；以 fake entry flow 驗證已採新預設。models 原則上不需修改。README 只在存在相關舊規則時同步；synthesis 的「2–4 個章節」與 R2 無關，不搜尋取代。Roadmap 保留歷史概要，R2 決策以本計畫為準。

## 5. 執行順序與局部驗收

1. 實作授權後重新確認 Git 與相關指引。修改共用規則、validator／schema descriptions；先驗 1／2／3／4／5／6／7 詞及空白正規化、舊 plan 相容。
2. 改 planner prompt 與 overlap 預設；驗有效高重疊 plan 一次返回、schema／詞數 repair 最多兩次、顯式 True 保留舊選擇與失敗行為。消除初始與 repair 的規則衝突。
3. 核對一般 screening、Groq batch／global gap／repair，驗 5／6 詞正常、7 詞 repair、仍非法停止；decisions、priority 與 follow-up 上限不放寬。
4. fake 正式 flow 追蹤 initial／follow-up 的實際 query 至 search 與保存的 plan/provenance，確認原始 idea、年份／venue、零候選、R1 診斷與 checkpoint 契約未變。
5. 完成下列案例自審與受影響 tests，修正必要問題後跑一次完整隔離 suite。再同步操作文件、保存證據、git diff --check 與最終 status。若有新錯誤／變更，僅重跑受影響範圍及必要整體驗收。

## 6. 驗收矩陣

| 需求 | 可觀察結果／測試 |
| --- | --- |
| 2–6 硬界線 | initial 與 FollowUpQuery 接受 2、5、6；拒絕 0、1、7；tabs／換行／多空白正規化；不截斷 |
| Repair 一致 | planner、一般 screening、Groq gap 的非法詞數各一次 repair；第二次非法依原契約失敗；prompt 帶原始問題與共用規則 |
| 錨點重複 | 高 Jaccard、不同面向的 valid plan 在預設模式只呼叫一次；不要求換詞降 overlap |
| opt-in 相容 | 原 overlap tests 明確 True，重寫較佳／無改善／失敗／已用 schema repair 等分支不變 |
| 面向按 idea 選擇 | prompt 不再強制三維度 keyword pools、跨維度組合或固定平均分配；保留單一需求、原始 idea 與 interpretation 邊界 |
| 下游實際收到 query | fake initial／follow-up 搜尋收到正規化 5／6 詞，plan／candidate query provenance 相符；原始 idea 不被 query 迴圈覆寫 |
| 品質門檻 | 有候選的 invalid screening 不改成略過；零候選維持既有處理；priority／完整 decisions／follow-up 上限回歸 |
| 相容與保存 | 舊 JSON 與 rule-based 長 query 可讀；不改 notes policy／manifest gate；tests 暫存輸出，既有 data 不寫入 |

未來驗收命令（專案 PowerShell cwd；本輪不執行）：

```powershell
uv run --offline --no-sync python tests/run_offline.py test_planning test_search_follow_up test_screening test_task_alignment test_models
uv run --offline --no-sync python tests/run_offline.py test_main test_pipeline
uv run --offline --no-sync python tests/run_offline.py
git diff --check
git status --short
```

不使用 dry-run 當離線驗收：它仍會搜尋及下載。Runner 使用 fake clients／encoders、暫存 cwd 並封鎖 network／workspace .env／既有 data 寫入。若測試嘗試連線就修正隔離或 fixture，不能解除封鎖、排除必要測試或擅自真實重跑。若實作觸及 key 相關內容，依 AGENTS 執行前後 key-pattern scan；不讀出 .env。

## 7. 小型語義案例與另行真實評估

代理準備五個跨領域案例，每個含原 idea、3–4 條候選 query、purpose、歧義反例及判斷理由。以下為規劃時的代表對照，不是模型實測結果：

| Idea／主要需求 | 可接受 query 例 | 應避免的 query／理由 |
| --- | --- | --- |
| LLM-based automated literature review：生成綜述的 citation 評估 | automated literature review citation evaluation | manuscript peer review evaluation：變成投稿審查；purpose 寫綜述也無法補救 |
| mixture-of-experts token assignment：負載平衡 | mixture-of-experts token routing load balancing | routing optimization：可指網路路由，未保留研究對象 |
| medical image segmentation：泛化評估 | medical image segmentation domain generalization | customer segmentation evaluation：不同研究對象 |
| software vulnerability detection：benchmark | software vulnerability detection benchmarks | biological vulnerability assessment：詞彙相近但任務不同 |
| retrieval-augmented question answering：引用可靠性 | retrieval augmented question answering citation evaluation | survey generation citation evaluation：雖共享 citation，但換成綜述生成 |

完整案例另核對：query 的錨點是否明確、單一主要面向、是否忠實 input、不因同義詞變化臆稱不同需求、詞數合格及面向合理。特別加入同任務 methods／evaluation 共用高重疊錨點、低重疊卻跑題，以及 query 跑題但 purpose 正確的反例。程式測試檢查規則／payload 到達，不把 fake client 預填答案當模型已學會消歧。

真實品質比較另待使用者授權，不是 R2 離線完成的必要條件。建議在固定五個 idea、相同 provider／model／年份／venue／每 query page size 與搜尋輪數下，對舊／新 policy 保存 plan 和實際第一頁回傳；人工作 task fidelity、主要面向多樣性、有效獨特候選與抽樣 relevance 判斷，列 query 長度與 overlap 為描述量。按 query 與 run 的 unique candidates 分列；下載成功數不是 retrieval 品質或回傳量指標。

記錄時間、條件、原始回傳與不確定性；結果量大不等於較相關，零結果不證明領域無文獻。固定規則與模型條件仍無法消除 generation 隨機性及 provider index 漂移，因此小樣本結果是定性比較，不稱客觀 benchmark／因果證明。實際 API 次數、樣本量與費用須在另次授權前定案；確認各 provider 用量／權限後才執行，使用隔離新輸出位置，不重播整個下載與 synthesis。

## 8. 自審結論與未決問題

**結論：可執行，但尚未獲實作授權。** 使用者確認 2–6 硬界線／通常 3–5、initial 3–4、rule-based／legacy 相容；語義 guidance＋小型自審；overlap 預設停用、保留明確 opt-in。

自審發現及處理：

1. planner 強制跨維度與單一主要需求可能衝突：移除強制組合，面向依 input 選擇。
2. Jaccard 會把必要任務錨點重複視為問題：預設停用，顯式 opt-in 只保留比較用途；不改成另一個未驗證 hard gate。
3. prompt 放寬但 FollowUpQuery schema 仍寫 2–4 會造成矛盾：共用 validator、描述及所有 repair 同步；以一般／Groq 分支測試。
4. 在 PlannedQuery 加全域 validator 會破壞 rule-based／舊 plan：只在現有 LLM parse 與 FollowUpQuery 位置更新，不更動公共 plan schema。
5. purpose 正確、query 模糊會讓實際 search 偏離：規則明示 query 本身需錨定；加入反例並承認未設 runtime 語義 gate。
6. 初始生成正確但下游只接 purpose 不算完成：fake flow 核對真正傳給 search 及保存 provenance 的 query。
7. 詞數 repair 與 overlap repair 不能混為一談：前者不要求降低 overlap，保留一次 budget；opt-in 分支明確測試 schema repair 用完後不再改寫。
8. mock／小型案例不足以证明真實 retrieval 改善：離線契約、人工自審與另行真實比較分開報告；不以 OA coverage、PDF 數或低 overlap 代替任務品質。

阻擋實作的未決設計：無。例行常數名稱、測試 fixture 拆分與證據 log 檔名由實作者定案。

仍需另行確認的事項：真實模型／retrieval 評估的授權、provider/model、執行規模與費用；不阻擋 R2 離線實作。2–6 與通常 3–5 是已同意的設計，不是最佳長度效果結論；語義品質尚未真實驗證。

本輪交付只新增此計畫。下一輪取得使用者實作授權後，依本計畫完成 R2 及隔離驗收；不連帶實作 R3、不改舊輸出、不自動呼叫真實 API、不自行 commit。
