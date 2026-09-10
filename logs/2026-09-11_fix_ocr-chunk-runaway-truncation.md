# 改動總結 — OCR 分塊退化改為截斷尾巴，不再整塊丟掉

日期：2026-09-11
分支：dev_ai
相關 commit：
- `3243f6c` fix: OCR 分塊退化改為截斷尾巴，不再整塊丟掉
- `<本檔>` docs: 2026-09-11 改動總結

> 前情：[2026-09-10_fix_ocr-chunk-rotation.md](2026-09-10_fix_ocr-chunk-rotation.md)、
> [2026-09-10b_fix_vram-release-standalone-commands.md](2026-09-10b_fix_vram-release-standalone-commands.md)。

---

## 先更正一個錯誤歸因

2026-09-10 的 log 與 CLAUDE.md 都寫著「p28 掉了 `形／型` 與 `刑事`，
是因為 `contains_text()` 會跳過偵測漏掉的塊」。**那是未經驗證的推論，是錯的。**

實際把 p28 切塊後逐塊檢查：

- 12 塊裡**只丟掉一塊**：`(0, 0, 37, 6948)`，37 px 寬的頁緣，確實是空白
- `形／型` 完整落在**塊 0**、`刑事` 完整落在**塊 4**，兩塊**都有送出去**

所以 `contains_text()` 沒有壞，那個函式一行都不必改。
差點讓下一個人去修一個沒有問題的地方。

## 真正的根因

把那兩塊各自單獨送 OCR：

| 塊 | 長度 | `looks_degenerate` | 內容 |
|---|---|---|---|
| p28 塊 0 | 5438 字元 | `True` → 丟棄 | 前 12 行是**完整正確**的 `形／型` 詞條，之後 `` ``` `` 連續重複 1332 次 |
| p28 塊 4 | 8255 字元 | `True` → 丟棄 | 前 8 行是**完整正確**的 `刑事` 詞條，之後 `.....` 連續重複 1327 次 |
| p28 塊 1（健康對照） | 129 字元 | `False` | 9 行、7 行不重複 |

`looks_degenerate()` 的判準（不重複行數比例）本身沒錯——這兩塊的比例分別是
6/1341 與 9/1344，確實是退化。**錯的是處置方式：整塊丟掉，連前面正確的詞條一起丟。**

## 修法

新增 `truncate_runaway()`：砍掉尾端跑掉的重複，保留前面正確的部分。

**判準是「單一行**連續**重複超過 3 次」，而不是整塊重複率。** 兩個理由：

1. 重複率看的是整塊，**無法指出「從哪裡開始壞」**；連續重複可以。
2. 健康輸出**也有大量重複行**——模型常把同一段內容再用 ```markdown 圍欄包一次
   （p28 塊 1 的 9 行裡就有 2 行重複）。但那是**整個區塊的回音**，重複行彼此
   不相鄰，連續長度是 1。而退化是連續 1327～1332 次。兩者相距三個數量級，
   門檻怎麼訂都不敏感。

`looks_degenerate()` 降為第三道防線，只收「從第一行就在重複」、截斷後無可救的塊。

## 變更清單

1. `src/anki_deck_builder/stages/ocr_chunking.py`：新增 `truncate_runaway()` 與
   常數 `_MAX_CONSECUTIVE_REPEATS`（＝3，依據寫在常數的註解裡）。
   `looks_degenerate()` 的 docstring 改寫，說明它現在是第三道而非第二道防線。
2. `src/anki_deck_builder/stages/ocr.py`：`_recognize_chunked()` 改為
   **先截斷再判退化**；截斷時記一則 WARNING（含截斷前後長度），
   仍判定為退化才丟棄（訊息保留）。
3. `tests/stages/test_ocr_chunking.py`：新增五個案例——
   - `test_runaway_tail_is_cut_and_the_good_prefix_survives`：**這次的回歸本身**
   - `test_healthy_output_is_untouched`：```markdown 區塊回音不該被砍
   - `test_blank_lines_neither_break_nor_count_toward_a_run`：空行是排版不是內容
   - `test_a_few_repeats_are_left_alone`：門檻之下不動它
   - `test_truncation_hands_a_hopeless_chunk_to_looks_degenerate`：交棒給第三道防線

## 測試結果

- **pytest：924 passed / 0 failed**（前一輪 919 ＋ 新增 5），31 deselected
  （付費 API 骨架，標記 manual，需人工執行）。
- **ruff check：All checks passed**。
- **無誤砍驗證**：把 `truncate_runaway()` 套用到 41 頁的實產 `raw_text`，
  **零改動**。
- **實地複驗**（重跑 p28 的 OCR）：

  | | 修正前 | 修正後 |
  |---|---|---|
  | `raw_text` 長度 | 1293 | **1458** |
  | 書上 15 條詞目 | 13 條 | **15 條全數在列** |
  | `形／型` | ❌ | ✅ |
  | `刑事` | ❌ | ✅ |

## 備註

### `掲示` 仍是字形誤判，不是漏字

它在 `raw_text` 裡是 `提示`／`揭示`，釋義（牌示，佈告）與例句都正確。
那是字形辨識問題，與本次的截斷無關，`check-card-quality.py` 的 `glyph` 項會報。

### p28 的卡片尚未重新抽取

本輪只修程式並複驗 OCR 輸出。`work/cards.csv` 裡 p28 的 14 張卡仍是
2026-09-10 那次抽取的結果，**缺 `形／型` 與 `刑事` 兩張**。
要補齊需重新抽取 p28（會移除既有 14 列並重生媒體），未獲指示，未做。

### 教訓

**推論要標成推論。** 那條錯誤歸因寫進了 CLAUDE.md 與 log，讀起來像已確認的結論。
下一個人照著去改 `contains_text()` 會白費工，而真正的 bug 還在。
已在 CLAUDE.md 與 2026-09-10 的 log 加上更正註記。

### 仍未處理

- `audio_front` 429 列、`audio_back` 605 列為 pending（2026-09-07 kana 腳本留下的佇列）
- p2、p16、p27 條目數偏低，值得用 `verify` 對照影像查
- `work/media/img/` 有 14 個早於本次作業的孤兒檔
