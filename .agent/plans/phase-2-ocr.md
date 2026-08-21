# Phase 2 — OCR 輸入端

## 目標

補上輸入端，讓 pipeline 能從書本照片或 PDF 起跑。本階段完成後，你能直接餵一疊書頁圖片或一份 PDF，一路跑到可載入記憶引擎的 ZIP（仍無圖無音）。

輸入類型由程式**自動判別**：圖片、圖片目錄、PDF、純文字檔各自走對應處理路徑，純文字直接繞過 OCR。

影像類輸入另有**兩種模式**可選，由 `.env` 的 `INGEST_MODE` 決定——`two_stage`（GLM-OCR 讀取後交 Gemma 4 抽取，預設）與 `vision_direct`（Gemma 4 直接讀圖抽取，跳過 OCR）。設計取捨見 [architecture.md](architecture.md)〈雙路徑輸入模式〉。

> **開工前**：依 [CLAUDE.md](../CLAUDE.md)〈開工前必做〉完成三項準備（確認 `dev_ai` 分支、備份 `.claude/settings.local.json`、確認外部介面狀態），再將本階段的執行計畫寫入 `.agent/plans/`，交使用者確認後才動手。

---

## 完成標準

- [ ] 輸入為單張圖片、圖片目錄、PDF、純文字檔時，皆能正確判別並處理
- [ ] 純文字輸入**完全繞過** OCR，不呼叫任何外部服務
- [ ] 多頁 PDF 的頁序正確保留於 `ocr_source_page`
- [ ] 單頁 OCR 失敗時記錄於 `ocr_error` 並跳過，整批繼續
- [ ] `uv run anki-builder ocr --input <path>` 可執行，`status` 顯示正確的 ocr 階段統計
- [ ] `run-all` 可從圖片一路跑到 ZIP
- [ ] `INGEST_MODE=two_stage` 與 `vision_direct` 皆可正常運作，產出的卡片欄位一致
- [ ] `vision_direct` 模式下 `raw_text` 留空、`ocr_status` 不被觸碰
- [ ] 非付費模組的 pytest 全數通過
- [ ] `tests/clients/test_ocr_client.py` 已撰寫 mock 骨架但未執行，並在改動日誌點名

---

## 子任務拆分

### Task 2.1 — 輸入源判別

**產出**
```
src/anki_deck_builder/stages/input_source.py
tests/stages/test_input_source.py
```

**要求**
1. 提供判別函式，回傳輸入類型與待處理項目清單：
   ```python
   class InputKind(str, Enum):
       IMAGE = "image"
       IMAGE_DIR = "image_dir"
       PDF = "pdf"
       TEXT = "text"

   def detect_input(path: str) -> tuple[InputKind, list[str]]: ...
   ```
2. 判別依據：
   | 條件 | 判定 |
   |------|------|
   | 副檔名為 `.jpg` `.jpeg` `.png` `.webp` `.bmp` | `IMAGE` |
   | 副檔名為 `.pdf` | `PDF` |
   | 副檔名為 `.txt` `.md` | `TEXT` |
   | 為目錄且內含圖片 | `IMAGE_DIR`，依檔名自然排序 |
   | 其他 | 拋 `StageProcessingError`，訊息說明支援的格式 |
3. `IMAGE_DIR` 的排序須為**自然排序**（`page2.jpg` 排在 `page10.jpg` 之前）

**測試**：完整單元測試，執行至通過。涵蓋各類型、空目錄、混合內容目錄、不支援格式、自然排序正確性。

**不要做**：此 task 不做任何實際的 OCR 或 PDF 轉換，只做判別與清單產出。

---

### Task 2.2 — PDF 轉圖

**產出**
```
src/anki_deck_builder/stages/pdf_render.py
tests/stages/test_pdf_render.py
```

**要求**
1. 將 PDF 逐頁渲染為圖片，輸出至 `$WORK_DIR/pdf_pages/`
2. 檔名須含零填充頁碼以保序：`page_0001.png`
3. 回傳 `list[tuple[int, str]]`，即 `(頁碼, 圖片路徑)`
4. 渲染 DPI 可設定，預設 200 *(待確認：需實測 OCR 辨識率與檔案大小的平衡點)*
5. 加入 `pypdfium2` 至 `pyproject.toml` 主要相依

> **待確認**：`pypdfium2` 的實際 API 與我的認知可能有出入。**開工時先查閱其官方文件確認渲染 API 用法**，不要照記憶寫。若渲染品質不符 OCR 需求，停下來與使用者討論替代方案。

**測試**：完整單元測試，執行至通過。以一份簡單的測試 PDF 驗證頁數、頁序、檔名格式。測試用 PDF 可用 pypdfium2 或其他方式在測試中即時產生，不要放入版控。

---

### Task 2.3 — OCR agent 與適配層

**前置**：先讀 `src/agent_factory/README.md`。OCR 與 LLM 走**同一套 agent_factory**，不是另一個獨立套件。

**產出**
```
agents.yaml（修改：新增 OCR agent）
prompts/ocr_extract.md
src/anki_deck_builder/clients/ocr_client.py
tests/clients/test_ocr_client.py
```

**要求**

1. **於 `agents.yaml` 新增 `OCRAgent`**
   - 新增獨立的 model 區塊指向 GLM-OCR 變體的 `base_url` 與模型名（由使用者填入）
   - 靜態 prompt 指向 `${__dir__}/prompts/ocr_extract.md`
   - **不設 `output_schema`**——OCR 回傳純文字，不需 structured output
   - 該模型同樣須登錄 `MODEL_LIMITS`，處理方式沿用 Task 1.7 與使用者確認的結論

2. **`ocr_client.py` 實作 `OCRClientProtocol`**
   - 沿用 Task 1.7 的 `llm_client` 相同模式：`create_agent_factory()` → `get_agent_by_name("OCRAgent")` → `LimitAgentRunner`
   - **影像以 base64 字串傳入**（已定案）。理由：避免路徑中的非 ASCII 字元、空白、跨平台分隔符造成問題
   - 由 client 內部負責讀檔並編碼，呼叫端只給路徑：
     ```python
     async def encode_image_b64(path: str) -> str: ...
     ```
   - 外部錯誤轉為 `ExternalServiceError`

3. **多模態輸入格式待查證**
   - `LimitAgentRunner.run(input_=...)` 接受 `str | list`，影像需以 content block 形式包在 list 中
   - **開工時先查 `openai-agents` 的多模態輸入格式**，或以實際 GLM-OCR endpoint 手動打一次確認
   - 若 agent_factory 目前不支援影像輸入，**停下來與使用者討論**——可能需要在 submodule 補功能，或改走其他路徑

**測試**：**Mock 骨架，撰寫後不執行**。mock 對象為 `LimitAgentRunner.run`。涵蓋正常回應、逾時、認證失敗、`KeyError`（模型未登錄）。於改動日誌點名需人工驗證。

**不要做**
- 不要另外裝 OCR 套件或自己打 HTTP——OCR 一律經 agent_factory
- 不要修改 submodule 內的任何檔案

---

### Task 2.4 — 階段 ①：ocr

**產出**
```
src/anki_deck_builder/stages/ocr.py
tests/stages/test_ocr.py
```

**要求**
1. 繼承 `BaseStage`，註冊為 `"ocr"`
2. 流程：
   1. 呼叫 `detect_input()` 判別輸入類型
   2. `TEXT` → 直接讀檔寫入 `raw_text`，**不呼叫 OCR client**
   3. `PDF` → 先 `pdf_render()` 轉圖，再逐頁 OCR
   4. `IMAGE` / `IMAGE_DIR` → 逐張 OCR
3. 每個來源項目（頁／張）對應中間 CSV 的**一列**，寫入 `raw_text` 與 `ocr_source_page`
4. 中間 CSV 不存在時自動建立
5. 單項失敗記錄於 `ocr_error`、狀態設 `failed`、繼續下一項
6. 併發控制：OCR 併發上限**待確認**，暫用 `GLOBAL_CONCURRENCY`

> **設計說明**：階段 ① 產出的是「每頁一列」的粗粒度資料。階段 ② 的 LLM 負責把一頁文字拆成多張卡片。這代表 `extract` 階段可能需要**一列展開為多列**——此行為已在 Phase 1 的 `BaseStage` 中預留，若發現不支援，停下來討論。

**測試**：輸入判別、頁序、純文字繞過等純邏輯撰寫**可執行**測試；OCR 呼叫部分 mock。

---

### Task 2.5 — vision_direct 模式

**前置**：先查證 **Gemma 4 是否支援影像輸入**。若不支援，**停下來與使用者討論**——換模型或放棄此路徑，不要硬做。

**產出**
```
agents.yaml（修改：新增 VisionExtractAgent）
prompts/extract_cards_vision.md
src/anki_deck_builder/stages/extract.py（修改）
src/anki_deck_builder/clients/llm_client.py（修改）
tests/stages/test_extract.py（補充）
```

**要求**

1. **新增 `VisionExtractAgent`**
   - 模型同 `ExtractAgent`（Gemma 4），但 prompt 改為指示模型直接從影像讀取並抽取
   - `output_schema` **與 `ExtractAgent` 完全相同**——兩條路徑的產出必須一致

2. **`llm_client.py` 支援影像輸入**
   - `run_agent()` 的 `input_` 接受 `str | list`，影像以 content block 形式包在 list 中
   - 沿用 Task 2.3 已實作的 `encode_image_b64()`，不要重寫

3. **`extract.py` 接上分支**
   - 依 `INGEST_MODE` 與該列是否有 `raw_text` 決定走哪條路：
     | 條件 | agent | input |
     |------|-------|-------|
     | 有 `raw_text` | `ExtractAgent` | 文字 |
     | 無 `raw_text` 且有影像來源 | `VisionExtractAgent` | 影像 content block |
   - 分支點是 Phase 1 Task 1.9 預留的切換設計，**只補分支，不重構既有邏輯**

4. **`ocr` 階段在 vision_direct 下的行為**
   - 僅建立 CSV 列並記錄影像來源路徑與 `ocr_source_page`，**不呼叫 OCR client**
   - `raw_text` 留空、`ocr_status` 維持 `pending` 不變動——該欄位為空即代表這張卡走 vision 路徑

**測試**：分支選擇邏輯撰寫**可執行**單元測試，涵蓋兩種 `INGEST_MODE` 與 `raw_text` 有無的組合；LLM 呼叫部分 mock 不執行。

**不要做**
- 不要讓兩條路徑產出不同的欄位集合——`output_schema` 必須共用
- 不要為了 vision 路徑修改 `stages/base.py`
- 不要在 Gemma 4 多模態支援未確認前就實作

---

### Task 2.6 — CLI 接線與 run-all

**產出**
```
src/anki_deck_builder/cli.py（修改）
tests/test_cli.py（補充）
```

**要求**
1. 將 `ocr` 子命令接上 Task 2.4 的實作，移除 Phase 1 的佔位訊息
2. 實作 `run-all`，依序執行 `ocr` → `extract` → `pack`
   - 兩種 `INGEST_MODE` 皆走同一條指令；`vision_direct` 下 `ocr` 階段仍會執行（只建列、不呼叫 OCR）
   - `image` 與 `audio` 尚未實作，本階段的 `run-all` 先跳過這兩步
   - **保留呼叫位置與註解**，標明 Phase 3/4 將接入此處
3. `run-all` 任一階段有 `failed` 列時**不中止**，繼續下一階段
4. 最終 `pack` 若仍有 `failed`，中止並提示改用個別子命令修正

**測試**：完整單元測試，執行至通過。涵蓋 `run-all` 的階段串接順序與失敗不中斷行為。

**不要做**：不要在 `run-all` 中加入 `image` / `audio` 的實際呼叫，只留位置。

---

## 驗收流程

1. **純文字輸入**
   ```bash
   echo "あきらめる 放棄；死心" > work/test.txt
   uv run anki-builder ocr --input work/test.txt --work work/cards.csv
   ```
   確認 CSV 中 `raw_text` 已填入，且**未發出任何 OCR 請求**（可暫時關閉 OCR 服務驗證）。

2. **單張圖片**
   準備一張書頁照片：
   ```bash
   uv run anki-builder ocr --input path/to/page.jpg --work work/cards.csv
   ```
   確認 `raw_text` 內容與圖片文字相符。

3. **多頁 PDF**
   ```bash
   uv run anki-builder ocr --input path/to/book.pdf --work work/cards.csv
   uv run anki-builder status --work work/cards.csv
   ```
   確認列數等於 PDF 頁數，`ocr_source_page` 由 1 遞增且順序正確。

4. **圖片目錄自然排序**
   準備 `page1.jpg` `page2.jpg` `page10.jpg` 的目錄，確認處理順序為 1 → 2 → 10，而非 1 → 10 → 2。

5. **失敗跳過**
   在圖片目錄中放入一個損毀的圖片檔，確認該項標為 `failed`、記錄錯誤，其餘項目正常完成。

6. **全流程（two_stage）**
   ```bash
   uv run anki-builder run-all --input path/to/book.pdf --output output/deck.zip
   ```
   確認產出的 ZIP 可載入記憶引擎。

7. **vision_direct 模式**
   ```bash
   # .env 設 INGEST_MODE=vision_direct
   uv run anki-builder run-all --input path/to/book.pdf --output output/deck_vision.zip
   ```
   確認：
   - `raw_text` 全部留空
   - 產出的卡片欄位集合與 two_stage 一致
   - 未對 OCR endpoint 發出任何請求（可暫時關閉 GLM-OCR 服務驗證）

8. **兩模式品質比對**
   以 `tests/fixtures/pages/` 的四張書頁照片為輸入，分別跑兩種模式，並與 `tests/fixtures/expected_cards.csv` 比對——特別是讀音、例句、專有名詞。記錄哪種模式表現較好，回報使用者作為預設值的依據。

---

## 已知風險與注意

### 風險

| 風險 | 說明與因應 |
|------|-----------|
| pypdfium2 API 與認知不符 | **開工時先查官方文件**。若渲染 API 用法不同或品質不足，停下來討論 |
| OCR 辨識率不足 | 若書頁照片辨識結果雜亂，可能需調整 DPI 或加入前處理（去歪斜、二值化）。**但前處理不在本 phase 範圍**，發現問題先報告 |
| 一頁對多卡的展開 | 階段 ② 需將一列 `raw_text` 展開為多列卡片。若 Phase 1 的 `BaseStage` 不支援一對多，停下來討論骨架調整 |
| Gemma 4 多模態支援未知 | **本 phase 最大風險**。agent_factory 本身已確認支援影像輸入，但 Gemma 4 是否為多模態模型待查證。不支援則 `vision_direct` 無法實作 |
| 影像 content block 格式 | `openai-agents` 的多模態輸入格式需查證，不要照記憶寫 |
| 兩路徑產出不一致 | 兩個 agent 共用 `output_schema` 是硬性要求。若 vision 路徑漏抽欄位，優先調 prompt，不要改 schema |
| base64 大小上限 | 高 DPI 大圖編碼後可能達數 MB，若請求被拒，回報討論降 DPI 或改用壓縮格式 |

### 不要做

- **不要**實作影像前處理（去歪斜、二值化、裁切）——不在本 phase 範圍，如有需要另行討論
- **不要**在 `run-all` 中呼叫尚未實作的 `image` / `audio`
- **不要**為了提高辨識率而自行更換 OCR 服務或加裝本地 OCR 套件——OCR 一律經 agent_factory
- **不要**修改 `src/agent_factory/` 內的任何檔案；需要該 submodule 補功能時停下來討論
- **不要**在 Gemma 4 多模態支援未確認前實作 `vision_direct`
- **不要**讓兩條路徑產出不同欄位——共用 `output_schema` 是硬性要求
- **不要**引入 Gradio、FastAPI 等 Phase 5 才需要的相依
- **不要**修改 Phase 1 已完成的 `CardRow` 欄位定義。若確實需要新欄位，停下來討論

---

## 完成後

**暫停，等使用者完成驗收流程並確認**，才進入 Phase 3。

驗收通過後：
1. 依 [change-log-guide.md](change-log-guide.md) 於 `logs/` 產出改動日誌
2. 點名需人工執行的測試：`tests/clients/test_ocr_client.py`
3. 更新 [CLAUDE.md](../CLAUDE.md) 的進度追蹤表

> `logs/` 與 `CLAUDE.md` 皆屬 Zone 2（本地版控、不進 `main`），照常在 `dev_ai` commit 即可，不需另做處理。合併回 `main` 由人工執行 `scripts/merge-to-main.sh dev_ai`，AI 不主動 merge。
