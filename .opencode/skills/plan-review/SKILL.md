---
name: plan-review
description: 審核工作計畫的潛在問題。當使用者說「審核計畫」「檢查計畫」「計畫有沒有問題」「plan review」，或計畫剛寫完要進執行前使用。
---

1. 讀目標計畫檔（`.omo/plans/*.md`，由使用者指定或取最新）→ 同時讀 `AGENTS.md`、`.omo/STATE.md`、以及計畫會動到的實際程式碼與測試。
2. 逐 Todo 檢查四項：
   - 明確性：有 References（真實行號）、Implementation、Acceptance、QA（happy + failure）、Commit 訊息與檔案清單？
   - 可驗證性：Acceptance 是可執行/可觀察的判準（指令、exit code、數字門檻），不是形容詞？
   - 完整性：依賴順序、既有測試是否被打壞、rollback 方式、evidence log 命名，是否都交代？
   - 與現況一致：引用的行號/函式/檔名逐一查證與現有 code 相符，不接受推測。
3. 交叉比對計畫的「設計描述」與實際 code 行為（例：dry-run 落點、env 讀取位置、參數預設值）→ 標出措辭與實作不符處。
4. 輸出：問題清單（嚴重/中等/輕微）＋ 每項具體修正建議 ＋「執行者若不問就會做錯」的三個必答問題。
5. 唯讀：不改任何檔案、不 spawn 子代理；只回報，維持 route B 紀律。

動態定位：計畫檔為權威，行號以查證當下為準。
