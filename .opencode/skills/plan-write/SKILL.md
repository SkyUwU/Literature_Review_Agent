---
name: plan-write
description: 為本專案撰寫工作計畫文件。當使用者說「寫計畫」「規劃」「分解步驟」「計畫」時使用。
---

1. 判定語意：只計畫語 → 本 skill；含「執行/開始」語 → 不寫計畫，先請使用者切換到可執行模式。
2. 收集 inputs：使用者目標 + 讀 `.omo/STATE.md`、`AGENTS.md`、HANDOFF 相關段、既有 `.omo/plans/`、計畫會動到的實際 code。
3. 起草大綱（TL;DR / Scope In·Out / Verification / Execution strategy / Todos / Commit strategy / Success criteria）→ 向使用者確認內容，未確認不得寫檔（嚴禁同時建立/編輯任何 code/config）。
4. 產生 `.omo/plans/<slug>.md`（snake-case、格式與既有計畫一致；Todos 須含 References=查證過的真實行號、Implementation、Acceptance=可執行判準、QA）。
5. 寫完立即用 plan-review skill 自審（唯讀），輸出問題清單；通過才交執行。
6. 只產 `.omo/plans/*.md`，絕不碰 code/config/test；執行與 commit 交由使用者決定。

動態定位：計畫檔為權威；以 `.omo/plans/` 最新檔為輸入，不寫死階段路徑。