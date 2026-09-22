# plan-write-skill — 建立 route-B 相容的 plan-write skill

## TL;DR (For humans)

本專案（2026-09-22 討論第 1 點，已授權未做）目前只有 read-only 的 `plan-review` / `project-context` / `review-progress`（route-B 相容）；openCode 全域的 `plan-write` 類 skill 會 spawn 子代理，不符合 route B 鐵則。本計畫＝建立**本專案自己的 `plan-write` skill**：主代理直接寫 `.omo/plans/<slug>.md`、不 spawn 子代理、寫前先與使用者確認內容、寫完自動用 `plan-review` 自審。零 code/test 變更。

**不會做**：改任何 code / config / test、建立子代理型 skill、變更 AGENTS.md 分工鐵則。

---

## Scope

**In**
- 新增 `.opencode/skills/plan-write/SKILL.md`（與既有 skill 同層、進 git）
- 本計畫檔 `.omo/plans/plan-write-skill.md`

**Out**
- code / test / `.opencode/` 以外任何變更
- AGENTS.md / HANDOFF.md / STATE.md / README / `.env*` 變更
- 任何子代理（route B）

---

## Verification strategy

每 Todo＝建檔 + 對照既有 skill 格式檢查。最終：`git status --short` 只含預期檔案、skill 檔 YAML frontmatter 可用 Python `yaml`/`ruamel` 解析且 name/description 與觸發語一致、確認 `.opencode/skills/` 內無同名 skill 重複。

---

## Execution strategy

```
Todo 0（預檢）→ Todo 1（建 SKILL.md）→ Todo 2（驗證）→ Todo 3（收尾 + commit 指令）
```

---

## Todos（evidence log 存 `.omo/evidence/plan-write-skill-*.log`，gitignored）

- [ ] 0. **預檢** — 確立執行前底線

  **References**
  - `.opencode/skills/plan-review/SKILL.md`、`project-context/SKILL.md`、`review-progress/SKILL.md`（既有格式：frontmatter `name`/`description` + 編號短步驟 + 動態定位）
  - `.omo/plans/m5c-doc-wrapup.md`（現行計畫檔格式範例：TL;DR / Scope / Verification / Execution / Todos / Commit strategy / Success criteria）

  **Implementation**
  1. `ls .opencode/skills/` 確認無 `plan-write`、無同名重複。
  2. `git status --short` 記錄底線（預期：` M .omo/plans/m5c-doc-wrapup.md` 未 commit）。

  **Acceptance**
  - `plan-write` 不存在；底線 git status 無未預期變動。

- [ ] 1. **建立 `.opencode/skills/plan-write/SKILL.md`**

  **References**
  - 既有 skill frontmatter/步驟格式（Todo 0）
  - 使用者已確認的草案（見本案 TL;DR 下方：skill 內容）

  **Implementation**
  1. 建 `.opencode/skills/plan-write/` 目錄與 `SKILL.md`，內容＝使用者確認之草案全文。
  2. 內容要點（照確認稿）：
     - frontmatter：`name: plan-write`、`description` 含觸發語「寫計畫/規劃/分解步驟/計畫」。
     - 步驟：判定語意（只計畫語才寫檔）→ 收集 inputs（STATE/AGENTS/HANDOFF/既有計畫/實際 code）→ 起草大綱向使用者確認、未確認不得寫檔 → 產生 `.omo/plans/<slug>.md`（格式與既有計畫一致，Todos 含 References=查證行號、Implementation、Acceptance=可執行判準、QA）→ 寫完用 `plan-review` 自審（唯讀）→ 只產 `.omo/plans/*.md`，執行/commit 交使用者決定。
     - 動態定位句（比照既有 skill 結尾）。

  **Acceptance**
  - 檔案存在且僅含該檔；frontmatter 可解析；內容與確認稿一致。

- [ ] 2. **驗證**

  **Implementation**
  1. Python 解析 frontmatter（`yaml` 或 `ruamel`）確認 `name`/`description` 字串存在。
  2. `git status --short`、`git diff --stat` 確認只動 skill 檔 + 計畫檔。
  3. 註記：skill 是否被 opencode 載入需下一 session 驗證（`available_skills` 清單），本 session 內無法自我確認 → 列入回報供使用者確認。

  **Acceptance**
  - frontmatter 解析成功；git status 乾淨（只含預期檔）。

- [ ] 3. **收尾 + commit 指令**

  **Implementation**
  1. 跑完整 suite 確認零回歸（`uv run python -m unittest discover -s tests`，輸出記錄測試數）——skill 檔不影響 code，但維持「commit 前全綠」慣例。
  2. `git status --short`、`git grep` 確認無 key 洩漏。
  3. 提供使用者 commit 指令（連同未 commit 的 `.omo/plans/m5c-doc-wrapup.md` 補勾可一事或分事）。

  **Acceptance**
  - suite 全綠、無 key 洩漏、commit 指令一次列齊。

---

## Commit strategy（使用者親做）

```bash
git add .opencode/skills/plan-write/SKILL.md .omo/plans/plan-write-skill.md
git commit -m "feat(skill): add route-B plan-write skill for authoring .omo/plans"
# 補勾（尚未 commit 者）可併：
git add .omo/plans/m5c-doc-wrapup.md
git commit -m "docs(plan): tick m5c-doc-wrapup todos"
git push
```

---

## Success criteria

- `.opencode/skills/plan-write/SKILL.md` 存在、格式與既有 skill 一致、貼合確認稿。
- 流程符合 route B：主代理寫檔、無子代理；寫前確認、寫後自審。
- git 追蹤檔無 key 洩漏；commit 指令一次列齊。