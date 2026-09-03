---
name: review-progress
description: 總結並審核專案進度。當使用者說「總結並審核」「審核進度」「review progress」「gate」時使用。
---

讀 `.omo/STATE.md`（錨點）+ 依 STATE.md 路徑讀計畫檔 + 最新指令包 + `.omo/evidence/` 最新 log；對照計畫「下一步行動卡」檢查目前 Todo 是否完成、判準是否滿足；逐項給 verdict：✅ APPROVE / ❌ FAIL / ⚠️ 需補充；總結「已完成｜下一步｜風險」。**動態定位：同 project-context，以 STATE.md 為錨、不寫死階段路徑。**

步驟1: 對每個 [x] todo，比對 .omo/evidence/ 對應 log 與計畫檔該 todo 的 acceptance criteria；逐 todo 給 APPROVE/FAIL（FAIL 需附具體缺失）。
步驟2: 讀 .omo/STATE.md（若與計畫 amendment 衝突以 amendment 為準）；輸出三行式摘要：現況（哪些 todo 完成/卡點）｜下一步（下一個待執行指令包或其指定動作）｜待決策。
步驟3: 不修改任何檔案，只回報；維持 route B 紀律（不 spawn subagent、不 git、不印 key）。
