# C2d — aspect metadata 過濾:作者行 / 期刊前綴標題誤判修復

- 狀態:待核准(2026-09-13 使用者確認做法「可以加上這些檢查」)
- 範圍:`literature_review/synthesis.py`(aspect 判定層)+ 測試;不動 extraction/切 chunk/評分/報告
- 證據:S1-S5 真實 run log(`.omo/evidence/s1s5-real-final.log`)2 類誤判實例

## 背景與證據

`top_level_section`(synthesis.py:415)負責把 chunk 的 markdown heading 路徑翻譯成 claim 的 aspect。S1-S5 真實 run 發現 2 類誤判:

- **誤判 A(作者行,log:191)**:W4401667275 的 claim aspect =「Francisco Bolaños<sup>1</sup> · Angelo Salatino<sup>1</sup> · …」——pymupdf4llm 把 PDF 首頁作者列當成 heading 輸出,單段路徑下 `_is_title_segment` 三重判據全不觸發 → 作者行成為 aspect。
- **誤判 B(期刊前綴標題,log:399)**:W7127589252 的 aspect =「Article Synthesizing scientific literature with retrieval-augmented language models」——標題帶「Article 」前綴,pymupdf4llm 輸出的 heading 比 `paper.title` 多了前綴,`_normalize_title` 後的「等於/開頭等於」比對方向相反 → 標題沒被認出來 → 整行當 aspect。

影響:僅 aspect 分類標籤(notes 群組依據),不影響 claim 事實/引用正確性;發生率 2/62 claims。使用者 2026-09-13 裁定:加入檢查修復。

## 設計

### 1. 期刊標題前綴剝離(修誤判 B)

新增模組常數 + 小函數:

```python
_JOURNAL_TITLE_PREFIXES: Final[tuple[str, ...]] = (
    "article ", "research article ", "journal article ",
    "full length article ", "original article ", "review article ",
)

def _strip_journal_prefix(text: str) -> str:
    lower = text.lower()
    for prefix in _JOURNAL_TITLE_PREFIXES:
        if lower.startswith(prefix):
            return text[len(prefix):]
    return text
```

`_is_title_segment` 的比對輸入改為 `_strip_journal_prefix(first)`(只影響比對,回傳值與其他判據不動):

```python
normalized = _normalize_title(_strip_journal_prefix(first))
```

效果:「Article Synthesizing X」比對 `paper_title="Synthesizing X"` → 剝前綴後 equal → 判定為標題 → drop。單段純「Article Synthesizing X」→ drop 後無第二段 → `None` → caller 給 `other`。

### 2. 作者/metadata 行偵測(修誤判 A)

新增函數:

```python
_METADATA_COPYRIGHT_RE: Final[re.Pattern[str]] = re.compile(r"©\s+the\s+author", re.IGNORECASE)

def _is_metadata_line(text: str) -> bool:
    """首頁 metadata 行(作者列/版權行)判別:這種 heading 不是章節,不應成為 aspect。

    特徵(保守、避免誤殺真章節標題):
    - 含 <sup> 數字標記 ≥2 個(作者 affiliation 標記,如 <sup>1</sup>、<sup>1,2</sup>);或
    - 含 <sup> 且以「·」或「,」分隔多個 token(作者串特徵);或
    - © The Author(...(版權行,絕不可能是章節)
    """
    sup_count = len(re.findall(r"<sup>\d+(?:,\d+)*</sup>", text))
    if sup_count >= 2:
        return True
    if sup_count >= 1 and re.search(r"[·,]", text):
        return True
    return bool(_METADATA_COPYRIGHT_RE.search(text))
```

`top_level_section` 在 `_is_title_segment` 檢查**之前**先檢查第一段:

```python
if _is_metadata_line(parts[0]):
    parts = parts[1:]
if _is_title_segment(parts[0], parts, paper_title):
    parts = parts[1:]
```

- 多段路徑「作者行 > Abstract」→ drop 作者行 → aspect = Abstract ✅
- 單段「作者行」→ drop 後無第二段 → `None` → caller 給 `other` ✅
- 真實章節標題含 sup(罕見)→ 誤判風險低;若發生頂多 aspect 變 `other`,比作者行汙染好

## 檔案

| 檔案 | 變更 |
|---|---|
| `literature_review/synthesis.py` | +`_JOURNAL_TITLE_PREFIXES`、`_strip_journal_prefix`、`_is_metadata_line`;`_is_title_segment` 比對改用剝前綴版;`top_level_section` 加 metadata 檢查 |
| `tests/test_synthesis.py` | `TopLevelSectionTests` 新增 ~6 測試(前綴剝離 ×2 情境、作者行 ×2、版權行 ×1、回歸 ×1) |

## 測試計畫(tests/test_synthesis.py,`TopLevelSectionTests` 內)

1. `test_journal_prefix_title_is_skipped`:
   - `top_level_section("Article Synthesizing X > 2 Related Work", paper_title="Synthesizing X")` == `"2 Related Work"`
   - `top_level_section("Article Synthesizing X", paper_title="Synthesizing X")` is `None`
   - `top_level_section("Research Article X > 1 Intro", paper_title="X")` == `"1 Intro"`
2. `test_author_line_is_skipped`:
   - `top_level_section("Fran B<sup>1</sup> · Ana S<sup>1</sup> > Abstract")` == `"Abstract"`
   - `top_level_section("Fran B<sup>1</sup> · Ana S<sup>1</sup>")` is `None`
3. `test_copyright_line_is_skipped`:
   - `top_level_section("© The Author(s) 2024 > Abstract")` == `"Abstract"`
   - 單段 `"© The Author(s) 2024"` is `None`
4. 既有測試回歸:前綴剝離僅作用於比對、metadata 檢查僅作用於第一段 → 既有 `TopLevelSectionTests` 8 測試與全文 346 tests 應全綠。

## 驗收標準

1. 全部測試跑綠(346 + 新增),指令:
   ```powershell
   uv run python -m unittest discover -s tests -v
   ```
2. 新測試涵蓋:前綴剝離(多段 drop、單段 None)、作者行(多段 drop、單段 None)、版權行。
3. fake e2e 仍過(`s1s5-fake-e2e.log` 同指令重跑一次或跳過——aspect 路徑無其他下游契約;執行代理視情況跑一次確認)。
4. 不動:extraction/切 chunk、functional 評分、notes、報告、models 契約。
5. 真實 run **不必**跑(低風險、證據已足);下次真實 run 掛 aspect 清單觀察點。

## Must NOT

- 不改 `merge_numbered_section`、`_group_chunks_by_section` 的行為(介面不變)。
- 不做 LLM 歸類後備(S5 保留後續觀察)。
- 不納入 DOI 行偵測(無證據顯示 DOI 行曾成為 heading;保持有據原則)。
- 不 commit、不 push(使用者親做);不碰 `.env` / `data/papers/`。
- 執行期間不改本計畫檔;偏離記回報。

## Commit strategy(執行完成後由使用者執行)

```powershell
git add literature_review/synthesis.py tests/test_synthesis.py
git commit -m "feat: C2d filter author/copyright metadata lines and journal title prefixes from aspects"
```

docs 筆(執行代理驗收後、規劃 agent 更新 STATE 後再一起 commit):
```powershell
git add .omo/STATE.md
git commit -m "docs: record C2d aspect metadata filter acceptance"
```

## 下一步行動卡

1. 執行代理:讀本計畫 → Todo 1-2 改 code → 加測試 → 跑全測試 → 存 log `.omo/evidence/c2d-tests.log` → 回報(tests 數、log 路徑、偏離)。
2. 規劃 agent:驗收(log + code 核對)→ 更新 STATE.md。
3. 使用者:commit + push。