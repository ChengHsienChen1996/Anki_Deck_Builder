# 改動總結 — OCR 分塊轉正評分修正與 28 頁重跑

日期：2026-09-10
分支：dev_ai
相關 commit：
- `c48fe61` chore: dev 相依改用 dependency-group，安裝指示統一為 --all-extras
- `8077db9` fix: 轉正評分改用文字框數，page_rotation 預設改為 none
- `309642b` chore: 五個 agent 的 temperature 由 0.0 調高

---

## 起因

使用者反映 p2 的卡片錯誤率過高。對照影像發現 72 列中只有 16 條是書上真有的詞條，
其餘是切一半的碎片（`七時に開`、`散／就地角`），末段 8 列甚至整組變成中文拼音卡
（`通過驗票口` / `tōng guò yàn piào kǒu`）。p28、p35 同樣症狀，p35 更達 44 列對 15 條。

## 根因

`clients/layout_detector.py` 的 `_best_rotation` 用「文字框**聯集面積**」決定頁面轉正方向。
偵測器方向不對時**不是**回傳零個框，而是吐一個低信心的兜底大框
（p35 轉 cw 後只剩 3 個框，其中 `table` conf=0.117 佔全頁 53%），
於是**偵測失敗的分數贏過偵測成功**：

| | 文字框數 | 聯集面積 |
|---|---|---|
| 方向正確（0°） | 33 – 67 | 0.373 – 0.611 |
| 方向錯誤（cw） | 0 – 8 | 0.000 – 0.741 |

41 頁全掃，舊評分只對 13 頁（**32%**；原 docstring 記的「一致率約 60%」是低估）。

選錯之後 `detect_axis` 拿一個框推不出欄縫，`split_to_budget` 把 30.1M px 的頁面
沿次軸等分成 8 塊 → **628 px 寬、穿過每一行文字的直條**（單欄寬約 2199 px，
等於每欄被剖成四條）。左右半截各自送 OCR 再依塊序串接，於是
`構造を分析する` 變成 `構造を分`；只含中文的碎片（書的譯文欄）沒有日文語境，
抽取模型依〈領域不預設〉判成中文教材，再依〈補全規則〉補上拼音與例句 → 拼音卡。

**兩道既有防線都攔不到**：`_check_yield` 抓「卡太少」、`_check_card_quality` 抓
「欄位全空」，而這個失效產出的是**又多又飽滿**的卡。

損害與根因完全對應：有拼音卡的頁 100% 落在選錯的 28 頁裡，
正確的 13 頁一張都沒有；卡片密度 20.8 vs 13.8 張/頁。

## 變更清單

1. `src/anki_deck_builder/clients/layout_detector.py`：`_best_rotation` 評分改為
   `(文字框數, 聯集面積)`；新增純函式 `_beats()` 與常數 `_COUNT_TIE_RATIO`
   承載「框數差距顯著時只看框數，接近時才比面積」的優先順序。
   docstring 記下實測數據與**兩道實測否決的候選防線**。模組 docstring 同步更新。
2. `src/anki_deck_builder/config.py`：`OCRChunkSettings.page_rotation` 預設
   `auto` → `none`，自動判斷改為 opt-in；docstring 換掉過期的 60% 說法。
3. `tests/clients/test_layout_detector.py`：**新增檔案**，8 個測試。
   以假偵測器覆蓋決策本身，不載入 DocLayout-YOLO 權重。
   含 `_beats` 的三個優先順序案例、本次回歸案例、反向案例
   （真的該選橫式時仍要選橫式），以及 1/3/8 框的參數化案例。
4. `pyproject.toml`：`dev` 從 `[project.optional-dependencies]` 搬到
   `[dependency-groups]`（PEP 735，預設就會裝）。`layout`／`kana` 維持 extra。
5. `uv.lock`：隨上一項更新。
6. `.env.example`：轉正說明改寫並附實測數字；安裝指示改 `--all-extras`。
7. `README.md`、`docs/usage.md`、`docs/architecture.md`：安裝指示改
   `uv sync --all-extras`，`usage.md` 補上「為何不能單點某一個 extra」。
8. `scripts/check-card-quality.py`、`scripts/kana-tts-back.py`、
   `src/anki_deck_builder/cli.py`、`stages/ocr.py`、`stages/factory.py`：
   錯誤訊息與 docstring 內的安裝指示同步改為 `--all-extras`。
9. `CLAUDE.md`：⚠️ 區塊新增本次結論、兩道實測否決的防線、兩個未修缺口。
10. `agents.yaml`：**使用者手動調整**，五個 agent 的 temperature 由 0.0 調高
    （MaterialType／Scene／ImagePromptFlux／ImagePromptSDXL 為 0.2，Verify 為 0.15）。
    原樣提交，未經 AI 修改。

## 實測否決的兩道候選防線

**不要再試**，理由與數據見 `_best_rotation` 的 docstring：

- **「切線切穿文字框的比例」**：壞計畫算出 100%，但那是拿**僅有的 1 個框**算的，
  沒有解析度；正確計畫在 p8 是 52%、p40 是 32%（`split_to_budget` 的
  「寧可切在不理想的位置」本來就會切穿）。分不開。
- **塊形**（最窄塊短邊 ÷ 頁面短邊、最大長寬比）：正確 0.06–0.28／1.7–7.6，
  錯誤 0.14–1.00／1.6–8.8，區間重疊；p1 的正確計畫比多數錯誤計畫還扁。

真正有鑑別度的訊號只有偵測品質本身，也就是修正後的評分所看的東西。

## 測試結果

- **pytest：915 passed / 0 failed**（原 907 ＋ 新增 8），31 deselected（付費 API 骨架，
  標記 manual，需人工執行）。
- **ruff check：All checks passed**。（`ruff format --check` 有 45 檔待格式化，
  但那是專案既有狀態，本次三個新／改檔案皆為 format-clean。）
- **真實影像複驗**：修正後 `auto` 在先前選錯的 p2/p3/p28/p35/p41 全部選對，
  塊寬回到 2100–2500 px（一欄的寬度）；原本就選對的 p1/p40 未受影響。

## 資料重跑（28 頁）

`extract.py` 的已知限制「重跑同一頁會撞 `card_id` 唯一性檢查」仍在，
因此先手動移除 27 頁的 561 張舊卡片列（p2 除外，見〈備註〉），
1680 個媒體檔移出 `work/media/`。

| 階段 | 結果 |
|---|---|
| ocr | 28/28 成功（首次 28/28 CUDA OOM，見〈備註〉） |
| extract | 27/27 成功，新增 410 張卡 |
| scene | 410/410 成功 |
| prompt | 410/410 成功 |
| image | 410/410 成功 |

**成效**：

| 指標 | 重跑前 | 重跑後 |
|---|---|---|
| `raw_text` 總字元 | 14509 | **31335（2.2x）** |
| 卡片數 | 756 / 41 頁 | **605 / 41 頁** |
| 平均卡數 | 18.4（正確頁 13.8、錯誤頁 20.8） | **14.8**（書上一頁 15–16 條） |
| 拼音 `reading` | 80 | **0** |
| `front` 含標點 | 15 | 4 |

必填欄位零缺漏（`back`／`reading`／`example`／`image_prompt`／`image_front` 皆為 0 空），
`card_id` 零重複，CSV 路徑與媒體檔零不一致。
`scripts/check-card-quality.py`：110 筆待確認（重跑前 123）。

## 備註

### 首次 OCR 全滅：單獨子命令沒有 VRAM 讓渡

28 列全部 CUDA OOM。ComfyUI 從先前的 `image` 階段留下來抓著 **17.97 GB / 24 GB**，
剩下的 6 GB 塞不下 GLM-OCR 加版面偵測器。

`stages/vram.py` 的 `release_all_gpu()` 就是為這件事寫的，但**只接在 `run-all`**
（`cli.py:526`）；`extract`／`audio` 等子命令有 `release_comfyui()`，
**`ocr` 一個讓渡都沒有**。`release_comfyui()` 自己的 docstring 還寫著
「不在 OCR 前呼叫：OCR 模型只佔 2.2 GB，沒有同樣的壓力」——那句話在
Phase 9 加了版面偵測器、Phase 7 換上 17.4 GB 的 kyoani workflow 之後已不成立。

本次以手動呼叫 `free_memory()` 繞過（18 GB → 0.9 GB），`ocr --only-failed` 即全數成功。
**這個缺口尚未修**，是後續待辦。

### ~~尚未修的缺口：`contains_text()` 會讓偵測漏掉的整塊消失~~（歸因錯誤，已更正）

> **2026-09-11 更正：本節的歸因是錯的。** 實證後發現 p28 只丟掉 37 px 寬的頁緣，
> `形／型` 與 `刑事` 都落在**有送出去**的塊裡；真正的原因是 `looks_degenerate()`
> 把整塊丟掉，而退化的塊前面是正確的。修正見
> [2026-09-11_fix_ocr-chunk-runaway-truncation.md](2026-09-11_fix_ocr-chunk-runaway-truncation.md)。
> 以下保留原文以存查。

### 原文：`contains_text()` 會讓偵測漏掉的整塊消失

`ocr_chunking.py` 開頭宣稱「聯集必然覆蓋全頁，涵蓋率天然 100%，
不依賴偵測的召回率」，但後來為了避免空白塊讓 OCR 退化而加的 `contains_text()`
會跳過「沒有偵測框落在裡面」的塊——偵測漏掉的區域整塊消失。
p28 重跑後仍掉了 `形／型`（頁首第一條）與 `刑事` 兩條詞目（14/16）。
這與轉正無關，是另一條路徑，量級也小得多。

以例句分隔符粗估每頁條目數，四頁偏低值得之後用 `verify` 對照影像查：
p2(11)、p16(11)、p27(9)、p41(7)（p41 是 か行 最後一頁，可能本來就不滿頁）。

### p2 未納入重新抽取

p2 的 16 張卡是本次稍早**對照影像手工建立**並已生成聯想圖的，品質高於模型抽取，
因此只重跑它的 OCR（讓 `raw_text` 與其他頁一致），不重新抽取。

### 未執行的階段

`audio_front` 429 列、`audio_back` 605 列仍為 pending。那是 2026-09-07
`kana-tts-back.py --apply` 留下的重生佇列，本次未獲指示執行，未動。

### 殘留的孤兒媒體

`work/media/img/` 有 14 個未被 CSV 引用的孤兒檔（p1/p4/p7/p15/p19/p22），
經比對**早於本次作業**即已存在，未處理。本次產生的孤兒已全部移出。

### 一併發現：`kana-tts-back.py` 的三筆建議是錯的

`二日 ふつか→ふたか`、`七時 しちじ→ななじ`（書上注音是 しちじ）、
`抽象絵画 ちゅうしょうかいが→まくがあく`（SudachiPy 斷詞斷歪）。
本次只跑預覽未寫檔。**不要無條件 `--apply`**。
