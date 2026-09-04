# 改動總結 — TTS 語言判定、核對檢查點、全牌組分塊重跑（Phase 9 Task 9.5～9.7）

日期：2026-09-05
分支：dev_ai
相關 commit：`57b13c5`（9.5）、`90e3ae8`（9.6）、`593faae`（9.6 修正）
執行計畫：[.agent/plans/phase-9-execution-plan.md](../.agent/plans/phase-9-execution-plan.md)
前半段（Task 9.1～9.4）：[logs/2026-09-03_feat_ocr-chunking.md](2026-09-03_feat_ocr-chunking.md)

> Phase 9 收尾。9.1～9.4 把 OCR 的源頭修好，這半段處理兩件剩下的事：
> 全漢字文字被 TTS 唸成中文，以及確定性規則做不到、但看著影像可以判斷的殘留。
> 最後以全牌組重跑驗收整條流程。

---

## 一、Task 9.5 — 全漢字文字改送假名讀音

VOXCPM2 沒有語言參數，語言由文字本身決定，線索只有「含漢字 → zh，否則 → en」。
日語的全漢字詞條（`足跡`、`圧縮`）整批被唸成中文——實測 342 張有 78 張。

規則寫成兩張表，**兩者都是引擎的性質，不是日語的性質**：

    換 reading 有意義，只當
      (a) 原文整串落在「引擎會判錯語言」的 script，且
      (b) reading 的 script 在本引擎上「實測唸得出目標語言」

### (b) 為什麼是白名單而不是黑名單

寫成「reading 不含拉丁字母」看似更通用，實際上會把引擎根本不會唸的 script
一律放行：注音標音的中文牌組**本來唸得好好的**（漢字→zh 本來就對），
會被換成 `ㄗㄨˊㄐㄧˋ` 這串符號；韓語漢字詞換成諺文之後連詞本身都丟了。
兩者都不報錯，只產出流利唸錯的音檔。

白名單在實際資料上與黑名單**結果完全相同**（77 = 77，對稱差集為空），
代價為零，換來的是未實測過的 script 一律不動而不是唸出垃圾。

### 三個實測校準出來的細節

- 漢字判定必須含 `々`（IDEOGRAPHIC ITERATION MARK 不是漢字），否則 `云々`、`各々` 掉出規則
- 多讀音只切 `/` `／` 不切空白。空白分隔的是 OCR 標音碎片黏在一起的殘留
- **只有 front 側套用**。back 側的 `reading` 是「條目」的讀音不是例句的讀音，
  換上去是抽換內容不是修發音——實測 8 筆會把 `温室効果` 唸成 `おんしつ`

### 更正一個寫錯的因果

計畫書說問題出在 `text_normalize.py` 的 `contains_chinese()`。那條路徑其實
**預設沒跑**（`VOXCPM2_NORMALIZE` 預設 `False`），真正把全漢字讀成中文的是
**模型本身**——沒有假名線索就沒有別的判準。結論與解法不變。

實跑 77 張重生成 front 音檔，成功 77 失敗 0，平均長度 0.88s → 0.95s，人工對聽確認。

---

## 二、Task 9.6 — 核對檢查點做成獨立子命令

### 骨架探測：9.0 原本設想的作法會靜默失效

計畫書把「位置與粒度」標為待決。實測 9.0 設想的「覆寫 `run()` 建索引、
`process_row` 就地改兄弟卡片列」：

```
處理的來源列 ['p1']；result: succeeded=3, failed=0
p1_001 reading='ORIG'  ← 改動沒進去
p1_002 reading='ORIG'  ← 改動沒進去
```

**沒有例外、沒有警告、succeeded=3**——最壞的一種失敗。原因是 `BaseStage.run()`
一開始就 `rows = await store.read()`，而覆寫的 `run()` 為了建索引也讀了一次，
兩次讀出來是**不同的物件**（實測 `first[0] is second[0]` 為 False）。索引握的是
第一批，骨架寫回的是第二批。

`image` 與 `audio` 也覆寫 `run()` 重讀一次，但它們只拿來數進度條總數、不改內容，
所以這個地雷一直沒被踩到。

因此比照 `pack` 做成獨立子命令。代價是沒有逐列狀態（無續作、Web UI 無單列重跑），
換得不動骨架、不動 `STAGE_FIELDS`、不動 `CardRow` 欄位——加狀態欄位會讓現有
`cards.csv` 因表頭嚴格比對而立刻讀不開，得再寫一支 migration。

### 開工前的閘門更正了自己的估算

先量真實塊數再寫（不呼叫 LLM）：平均一頁 8.8 塊，但**只有 3.3 塊含文字**。
先前估的 420 次呼叫是拿總塊數算的，漏了 `contains_text`——核對這一側同樣要
跳過空白塊。實際 24 頁只要 **160 次**，兩個範圍都做得起，不必砍掉 `example`。

### 全批實測補上三個缺陷

第一輪跑完 24 頁（160 次呼叫、260 筆提案、套用 84）後檢查稽核檔：

| 缺陷 | 實測 | 修法 |
|------|------|------|
| `reading` 更正沒傳到 `tts_front_text` | **38/38 筆全部如此** | `_propagate_reading()` |
| 書上的括號標註被抄進 `reading` | 3 筆（`あっ` → `あ（つ）`） | prompt ＋ `_READING_NOTATION` 防線 |
| 只抄漢字上方的注音，讀音唸不完整 | `荒い`→`あら`、`女の人`→`ひと` 等 | prompt 新增〈reading 要唸得出整個詞條〉 |

第一個最危險：核對修好了顯示用的讀音，語音卻照著錯的唸——**修了一半比沒修更難發現**。
根因是 `CORRECTABLE_FIELDS` 只有 `reading` 與 `example`，而 `tts_front_text`
是另一個欄位；Task 9.5 的 `speech_text()` 又只在 tts 文字全是漢字時才改用 `reading`，
這些卡的 tts 文字是假名（舊的錯讀音），不會觸發。

第三個的關鍵認識：**模型並沒有說謊**——日文的注音只標在漢字上方，送假名與
接頭語標不到，書上印的就是那幾個字。所以判準要從「書上印了什麼」改成
「照著唸出來是不是這個詞條本身」，prompt 裡定調**注音是線索，不是答案**。

修好後重跑同一批驗收：

| | 第一輪 | 第二輪 |
|---|---|---|
| `tts_front_text` 同步 | 0／38 | **38／38** |
| 套用含括號的 `reading` | 3 筆 | **0 筆** |
| 第一輪出錯的 5 個案例 | — | **4 個修好、1 個部分** |
| 失敗呼叫 | 1（逾時） | **0** |

`notation in reading` 那道程式防線**一次都沒觸發**——模型在新 prompt 下根本不再
提出那種寫法。防線留著當保險，真正解決問題的是 prompt。

僅存的 `粗し → あら` 根因在 `front` 本身抄成「粗し」而非「粗い」，而 `front`
依設計不在核對範圍（9.0 實測那一欄 26 筆提案只有約 3 筆正確），核對修不了它。

---

## 三、變更清單

### 新增

1. `src/anki_deck_builder/stages/verify.py`：核對檢查點。判定管線全部純函式
2. `tests/stages/test_verify.py`：45 條，不載入任何模型
3. `logs/2026-09-05_feat_phase-9-tts-verify-and-rerun.md`：本檔

### 修改

4. `src/anki_deck_builder/stages/audio.py`：`speech_text()` 與兩張 script 表
5. `tests/stages/test_audio.py`：+21 條（Task 9.5）
6. `src/anki_deck_builder/cli.py`：`verify` 子命令
7. `src/anki_deck_builder/stages/factory.py`：`build_verify_client()`
8. `src/anki_deck_builder/clients/image_input.py`：`_crop_b64` 從 `stages/ocr.py`
   移來並改名 `crop_to_b64`——核對必須切出與 OCR **完全相同**的塊
9. `src/anki_deck_builder/stages/ocr.py`：改用 `crop_to_b64`
10. `src/anki_deck_builder/schemas/extract_output.py`：`normalise_example_separator`
    從 pydantic validator 抽成 module function、`TRANSLATION_SEPARATORS` 改為公開
11. `prompts/verify_cards.md`：核對範圍、讀音完整性、括號標註三節
12. `docs/architecture.md`：`LayoutDetectorProtocol`、〈OCR 分塊輸入〉、
    〈核對檢查點〉、CLI 表格、環境變數
13. `docs/usage.md`：`verify` 子命令、OCR 分塊設定與關掉的方法
14. `docs/project-overview.md`：技術棧加 DocLayout-YOLO
15. `CLAUDE.md`：進度表、外部相依、文檔索引

---

## 四、測試結果

- `uv run pytest -q`：**901 passed**、31 deselected（付費 API 測試）
- `uv run ruff check .`：All checks passed
- 需人工執行：無新增付費 API 測試

---

## 五、全牌組重跑（實測）

24 頁重跑整條流程驗收。**七階段全數零失敗**（`image` 有 1 張逾時，重跑一次即過）。

### 資料品質

| | 舊（E4B ＋ 整頁 OCR） | 新（分塊 ＋ 核對） |
|---|---|---|
| 卡片 | 342 | 335 |
| `reading` 含假名 | 322（94%） | **335（100%）** |
| **羅馬字讀音** | **20** | **0** |
| **`reading` 與 `tts_front_text` 不一致** | **183** | **0** |
| `tts_front_text` 全是漢字 | 80 | **0** |
| `raw_text` 總字數 | 13415 | 17666 |

`irai`、`ima ni`、`iyoiyo` 那類羅馬字讀音**全數消失**——那正是 Phase 9 開頭
診斷出的病灶（OCR 沒讀到 furigana，抽取模型只好憑知識生成）。

「`reading` 與 `tts_front_text` 不一致」從 183 降到 0 是意外收穫：舊資料有超過
一半的卡，顯示的讀音與實際唸出來的文字不同步。

> **Task 9.5 的規則現在一次都不會觸發**（`tts_front_text` 全是漢字的卡從 80 變成 0）。
> 那不是白做——源頭修好之後它退化成安全網，等的是抽取沒把讀音填進
> `tts_front_text`、退路落到 `front` 的情況。規則的價值在於**那時不會安靜地唸錯**。

### 核對成效（第二輪）

24 頁、160 次呼叫、零失敗、269 筆提案、**套用 80**（`reading` 38、`example` 42）。

`reading` 修的是串錯行與截斷（`しいがき→いしがき`、`いけくじゅう→いしょくじゅう`、
`おうじしょ→おうじょ`）；`example` 修的是規則做不到的東西——簡繁誤植（`爱情→愛情`）、
形近字（`石牘→石牆`、`嚎得→嚇得`）、片假名（`フイル→ファイル`），以及
**把混進例句的 furigana 碎片清掉**（`あわ泡が立つ → 泡が立つ`），那是 9.4 明列的殘留。

### 耗時，以及一個過期的基準

| 階段 | 耗時 | 備註 |
|------|------|------|
| `ocr`（分塊，24 頁） | 約 5 分 | 丟棄 5 個退化塊 |
| `extract`（24 頁） | **約 5 小時** | 見下 |
| `verify`（160 次呼叫） | 約 25 分 | |
| `scene` | 9:49 | 1.8 秒/張 |
| `prompt` | 15:27 | 2.8 秒/張 |
| `image` | **2:50:29** | 見下 |
| `audio`（front ＋ back） | 18:00 | |
| `pack` | 數秒 | 335 張卡、1005 個媒體檔 → `deck.zip` **34.5 MB** |

**兩個基準過期了，記下來免得下次又拿去做規劃：**

1. **`image` 不再是 15 秒/張，是 30.6 秒/張。** Phase 7 實測 311 張 1:17:59，
   現在 335 張要 2:50:29。差額對應 2026-09-02 加掛的 `klein_fixer_slider`
   （修四肢的第二組取樣），`COMFYUI_BATCH_SIZE` 沒變

2. **`extract` 慢了一個數量級。** 單頁 15 分鐘、每次呼叫 145～199 秒。原因是
   `agents.yaml` 的抽取模型在 `cf5262e`（2026-09-02，一個 **docs commit**）
   從 `gemma4-e4b-optimized`（11.64 GB）換成 `gemma4_26b-a4b-it-q8_0-128K`
   （**29.26 GB**）。顯卡只有 24 GB，**只有 70% 上卡**，30% 的權重跑在 CPU 上。
   這不是 ComfyUI 佔用造成的（跑之前已釋放），`COMFYUI_FREE_BEFORE_LLM` 救不了。

   > `agents.yaml` 的註解與設定**互相矛盾**：註解仍寫著「抽取模型選 E4B」，
   > 並把現行的 26B 一族標為「慢 3.4 倍、28.1 GB 與 ComfyUI 無法共存」。
   > 現有牌組的品質是用 26B 驗證的（`reading` 13/14），所以**沒有動設定**，
   > 但那段註解該更新——這是 CLAUDE.md 警告的「同一件事有兩個說法」的變體。

   附帶一提：`scene` 與 `prompt` 也繼承 `<<: *model_extract`，但它們只有 1.8～2.8
   秒/張。145 秒是抽取特有的（整頁輸入、結構化 JSON 輸出），不是模型的固定成本。

---

## 六、備註

### 已知限制

1. **`example` 偶發亂碼**：1 筆 `不分軒<0xE8><0xBC><0x8A>`（模型輸出的無效位元組），
   程式端沒有偵測。兩輪都出現同一筆
2. **`front` 的錯誤核對修不了**：`粗し`（應為 `粗い`）、`荒し`、`ある或いは` 這類
   抽取階段的錯誤會連帶讓 `reading` 修不完整。`front` 依設計不在核對範圍——
   9.0 實測那一欄 26 筆提案只有約 3 筆正確
3. **`verify` 沒有接進 `run-all`**：它沒有逐列狀態，接進去會讓每次執行都重跑
   全部核對呼叫。目前需手動執行
4. **舊媒體檔成了孤兒**：`work/media/img` 有 621 個檔但只有 335 個被引用。
   `pack` 只收卡片引用到的檔案，所以不影響產出

### 備份

- `work/backup-before-chunking-rerun/`：重跑前的 342 張卡 ＋ 全部媒體（66 MB）
- `work/cards.csv.bak-before-verify`：核對前的 335 張
- `work/verify-round1/`：核對第一輪的稽核檔，可與第二輪逐筆對照

### 後續

`agents.yaml` 的抽取模型註解與設定不一致，待使用者決定要改註解還是改模型。
