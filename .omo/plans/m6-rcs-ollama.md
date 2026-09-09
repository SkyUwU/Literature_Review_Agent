# M6: RCS-on-Ollama(本地模型跑 RCS 評分)

- slug:`m6-rcs-ollama`
- date:2026-09-09
- status:待使用者核准(已討論:Claude ①④ + 使用者決定;Ollama 裝於 Windows、WSL 連結已通)
- 前置環境:Ollama 已裝(Windows)+ 已 pull Qwen3 8B + WSL 可連 Windows Ollama 的 URL

## 動機

- **Gemini free-tier 配額連日卡 run**(M5a 6 次真實 run:429 ×3 + 下游中斷 ×3),RCS 是「量最大、任務最窄」的環節(單 call 帶 32 chunks + 隨機 miss 重試),最該移出雲端。
- **Claude ①**:單 call 全部 chunks 易「相對比較」而非「絕對評分」;逐 chunk/小 batch 更客觀(本地呼叫免費,不怕 429)。
- **Claude ④**:量大的窄任務適合本地小模型;配額留給 synthesis(高階推理)。
- 現況架構支援:`JsonGenerationClient`(llm_evidence.py:29)是注入式介面;`pipeline.run_synthesis_pipeline` 目前用**單一 client** 同時服務 RCS + 逐篇筆記 + synthesis(main.py 傳 key2)——M6 只需拆出 RCS 換 Ollama(逐篇筆記與 synthesis 留 Gemini)。

## Scope

- 新增 `literature_review/ollama_client.py`(`OllamaJsonClient` implements `JsonGenerationClient`)。
- `pipeline.run_synthesis_pipeline` 拆 client:新增 `client_rcs` 參數(**只用於 RCS** `summarize_and_rerank`);**逐篇筆記 `summarize_paper_notes` 與 synthesis 維持既有 `client`**(Gemini key2)——逐篇筆記是理解+綜合任務、量小(每篇 include/consider 1 call),品質直接進報告 prompt;搬 Ollama 省不到配額、風險高(使用者 2026-09-09 裁示)。
- `main.py`:`_build_clients` 產第三個 client——`OLLAMA_BASE_URL` 設定存在時 RCS 用 `OllamaJsonClient`;未設定 → fallback 現況(同一 Gemini client 兼 RCS,逃生門)。
- `llm_evidence.summarize_and_rerank`:單 call 全 chunks → **batch 迴圈**(每 call ≤ B 個 chunk,B 預設 8,可配),結果合併。評分契約不變。
- 設定:`.env` 加 `OLLAMA_BASE_URL` + `OLLAMA_MODEL`(Windows 與 WSL 各自填自己的 URL——Windows 填 localhost、WSL 填 Windows IP 連結)。
- **不**:改 `LlmEvidenceAssessment`/收縮平均/閾值/embedding 檢索;不動 planner(Gemini key1)、逐篇筆記與 synthesis(Gemini key2)的 Gemini 路徑(保留逃生門)。

## Todo 0 — 前置驗證(執行代理先做,決定 Todo 1 設計)

- `curl http://localhost:11434/api/tags` 確認模型存在(WSL 端用 .env 的 URL)。
- **測試 `response_format` 行為**:`curl` 打 `/v1/chat/completions`,body 含 2 個簡短 chunk 的迷你 prompt + `response_format={"type":"json_object"}`——觀察:(a) 是否被接受(非 4xx);(b) 輸出是否為裸 JSON(無 ```json fence、無前綴說明)。
- **若 response_format 無效** → Todo 1 設計改為「不加 response_format(prompt 已明示裸 JSON)+ 依賴既有 strip/repair」,並在回報中註明。
- 記錄 Ollama 版本(`ollama --version`)。

## Todo 1 — `OllamaJsonClient`(ollama_client.py)

- `__init__(model=None, base_url=None)`:`load_local_env()` 後讀 `OLLAMA_MODEL`(預設 `qwen3:8b`)/`OLLAMA_BASE_URL`(預設 `http://localhost:11434/v1`);缺 model → `LlmEvidenceError`。
- **建構時輕量 ping**(GET `{base_url}/models`,timeout 5s):失敗 → `LlmEvidenceError` 早退——比照 K 的 D1 先例(真實 run 失敗=中止不 fallback),避免 run 走到 RCS 才炸。
- `generate_json(prompt, schema)`:
  - POST `{base_url}/chat/completions`(用標準庫 `urllib.request`,零新相依);
  - body:`{"model": ..., "messages": [{"role": "user", "content": prompt}], "response_format": {"type": "json_object"}, "temperature": 0.2}`(response_format 依 Todo 0 結果調整);
  - **`timeout=120`(8B 回應 8 chunks 可能 30-60s+;無 timeout 會 hang)**;
  - 回傳 `choices[0]["message"]["content"]`(空 → `LlmEvidenceError`);
  - HTTP/網路失敗 → `LlmEvidenceError`(訊息含狀態碼,不含 body/secret)。
- 測試:monkeypatch urllib 的開瓶器(fake https 回應)——正常 JSON、空內容、HTTP 500、連線錯誤四例。
- Langfuse `@observe(name="llm_call")` 比照 Gemini client(可選,保持一致)。

## Todo 2 — pipeline/main 接線

- `pipeline.run_synthesis_pipeline(..., client, client_rcs=None)` → **RCS(line 123)改用 `client_rcs or client`**;`summarize_paper_notes`(line 136)與 synthesis 維持 `client`。
- `main.py` `_build_clients`:`OLLAMA_BASE_URL` 存在 → 第三 client `OllamaJsonClient()`(RCS);缺 → None(現況行為)。
- 既有測試:`summarize_and_rerank`/pipeline 測試的 fake client 不受影響(契約不變);新增「client_rcs 注入」測試。

## Todo 3 — RCS batch 迴圈(summarize_and_rerank)

- `build_evidence_prompt` 支援部分 chunks(保留現況簽名或加參數 `chunk_ids`/上限 B)——每 batch 產 `LlmEvidenceAssessmentBatch`,迴圈合併。
- `summarize_and_rerank` 迴圈:每 call ≤ B 個 chunk(B = 8,模組常數可配);每個 batch 失敗走既有 repair(每 batch 一次 repair 上限);合併後走既有「每個 chunk 恰一次」完整性檢查。**單 batch 漏 chunk(模型漏答)→ repair 一次 → 仍漏則 `LlmEvidenceError`(與現況單 call 行為一致,不新增重試)**。
- **測試同步**:現有「single corpus-wide RCS call」斷言將改為「≤ ceil(n/B) calls + 合併結果一致」;**fake client 需改為「依請求 prompt 中的 chunk_ids 回對應子集」(多批次模擬,不是只改斷言)**;新增 batch 邊界測試(B 整除/不整除 chunk 數)與漏 chunk 測試(repair 補回 / 仍漏則 error)。
- 註:`LlmEvidenceAssessmentBatch` 是逐回契約,合併後輸出仍是它的實例——下游零改動。

## Todo 4 — 校準對比(強制,免費)

- **資料集優先序**:① `/tmp/k3-fixture/`(K3 固定集,有 Gemini 歷史分數可對照——先確認還存在);② 不存在 → `data/papers/` 基準 21 檔(只有分布描述,無歷史對照,結論要標註)。
- 跑 retrieval + RCS(Ollama,零 Gemini 消耗)→ 產分數分布;**跑前先 curl 熱身一次(載入模型,避免計時污染 / 首 call 超時)**。
- 對照 Gemini 歷史(P3 的 K3 run:K3 論文層 rel 分布 2.1-6.5、天花板 rel 6.3):分布位置/形狀對照表。
- **使用者抽查 3-5 個 chunk**(比照 K3c):Ollama rel/qual vs 使用者判斷,分歧記錄。
- 產物 `.omo/evidence/m6-calibration-*.md/.json`。
- 結論記錄「跨 run 比較須標註 RCS 模型;閾值是否需隨 Ollama 尺度重校(→ M5b 決策)」。

## Todo 5 — 真實 run 驗收

- 完整 run(`printf 'literature review agent\n' | uv run --env-file .env python -m literature_review.main`)——planner Gemini key1、RCS Ollama、逐篇筆記 + synthesis Gemini key2。
- 記錄:Gemini calls 數(應只剩 plan 1 + notes N(每篇 include/consider 論文 1)+ synthesis 1)、Ollama calls 數(batch 數)、0 個 429、進評分池組成、usable、天花板 rel(vs K3:9 篇 / 1 / 6.3)、failed_extractions、Langfuse health 前置。
- `data/papers/` 還原(新 W*.pdf 移出);log `.omo/evidence/m6-real-*.log`。

## Must NOT

- 不改評分契約(LlmEvidenceAssessment / EvidenceSummary / 收縮平均 / 閾值 / embedding 檢索 policy)。
- 不刪 Gemini RCS 路徑(fallback 逃生門保留)。
- 不 commit;不碰 `.env`(不讀不印 key)、`data/papers/`、`.omo/drafts/`;不 spawn。
- 不跑額外 run(同一個驗收 run 至多重試一次,比照 K3 先例)。

## Success criteria

1. tests 全綠(新增 ollama_client + batch 測試;既有斷言同步);Ollama client 4 例測試過。
2. Todo 4 校準對比完成(分布對照表 + 使用者抽查 + 結論)。
3. Todo 5 真實 run 完整成功(report JSON 產出、0 個 Gemini 429、Gemini calls 只剩 plan+notes+synthesis)。
4. `data/papers/` 還原基準。

## Commit strategy(使用者親做)

```powershell
git add literature_review/ollama_client.py literature_review/llm_evidence.py literature_review/pipeline.py literature_review/main.py tests/test_ollama_client.py tests/test_llm_evidence.py tests/test_pipeline.py HANDOFF.md
git commit -m "feat: M6 run RCS scoring on local Ollama model with batched chunk assessment"
git push
```

(第二筆 docs commit 視 HANDOFF/STATE/README 更新量而定,比照 K 系列先例)

## 風險自審

| # | 風險 | 對策 | 殘餘 |
|---|---|---|---|
| P1 | 8B JSON/schema 遵從度不足(repair 率高) | `response_format=json_object` + temperature 0.2 + 既有 repair loop;校準抽查;若 repair 率過高 → 降 batch 或換模型 | 中 |
| P2 | Ollama 分數尺度 ≠ Gemini,K3c 校準不通用 | Todo 4 強制;跨 run 比較標註「Ollama RCS」;閾值重校 → M5b 決策 | 中 |
| P3 | batch 迴圈改壞現有 RCS 行為 | 測試同步(呼叫次數斷言);契約零改動;「每 chunk 恰一次」檢查保留 | 低 |
| P4 | 8B 速度慢(未確認 GPU) | Todo 4 前先小樣本測速(8 chunks 計時);慢則降 batch 或試 qwen3:4b | 中低 |
| P5 | WSL/Windows URL 差異 | `.env` 各自填(已確認連結);Ollama 需常駐(Windows 端) | 低 |
| P6 | Ollama `/v1` 不支援 `response_format` → 輸出含 fence/前綴 | Todo 0 前置驗證決定設計;既有 strip/repair 兜底 | 低 |
| P7 | 連線無 timeout → hang | Todo 1 `timeout=120` 明確寫入 | 低 |
| P8 | `/tmp/k3-fixture` 已被清理 → 無歷史對照 | Todo 4 資料集優先序 + 結論標註 | 低 |
| P9 | WSL 連不到 Windows Ollama → run 中途才炸 | Todo 1 建構 ping 早退(D1 先例) | 低 |