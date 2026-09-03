---
name: project-context
description: 載入本專案進行中的工作狀態。當使用者說「載入專案狀態」「現況如何」「現在做到哪」「繼續專案」「resume 專案」「project context」時使用。
---

1. 讀 `.omo/STATE.md`（錨點：含目前階段 + 計畫檔路徑 + 最新指令包路徑）
2. 依 STATE.md 路徑讀計畫檔 → 讀最新指令包 → 讀 `.omo/evidence/` 最新 log
3. 回覆三行式：「現在做到哪｜下一步｜卡點」
4. 只回報不執行；衝突以計畫 amendment/行動卡為準；維持 route B 紀律

動態定位：不得寫死階段路徑；以 STATE.md 為準，找不到則掃描 `.omo/plans/` 和 `.omo/start-work/` 下最新 `*.md`。
