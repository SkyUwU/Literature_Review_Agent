---
name: project-context
description: 載入本專案進行中的工作狀態。當使用者說「載入專案狀態」「現況如何」「現在做到哪」「繼續專案」「resume 專案」「project context」時使用。
---

依序讀取 (1) `.omo/STATE.md`（錨點：內含「目前階段 + 對應計畫檔路徑 + 最新指令包路徑」）；(2) 依 STATE.md 記錄的路徑讀計畫檔 → (3) 依 STATE.md 記錄的路徑讀最新指令包 → (4) `.omo/evidence/` 最新 log；以「現在做到哪｜下一步｜卡點」三行式回覆；只回報不執行；衝突以計畫 amendment/行動卡為準；維持 route B 紀律。**動態定位：不得寫死特定階段的路徑；一律以 STATE.md 記載的路徑為準，找不到則掃描 `.omo/plans/` 與 `.omo/start-work/` 下修改時間最新的 `*.md`。**
