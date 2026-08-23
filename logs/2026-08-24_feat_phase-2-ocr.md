# 改動總結 — Phase 2：OCR 輸入端

日期：2026-08-24
分支：dev_ai
相關 commit：`1e200b1` … `9fcfaf0`（15 筆，`b6d779e` 之後全部）

| commit | Task／主題 | 內容 |
|--------|-----------|------|
| `1e200b1` | 2.1 | 輸入源判別（圖片／目錄／PDF／純文字，自然排序） |
| `f5ef525` | 2.2 | PDF 轉圖（pypdfium2、零填充頁碼、`INGEST_PDF_DPI`） |
| `ec39048` | 2.3 | OCRAgent 與適配層（`Text Recognition:`、編碼前縮圖） |
| `e33346d` | 2.4 | 階段 ① ocr、階段間 VRAM 讓渡（`keep_alive=0`） |
| `9d8461c` | 2.5 | vision_direct 影像直送路徑 |
| `d7ef5d8` | 2.6 | CLI 接線與 run-all |
| `9152a16` | — | 驗收修正：舊列誤判、併發自撞逾時、模型不讓位 |
| `f4f99c6` | — | 驗收修正：切段、決定性編號、抽取模型改用 E4B |
| `55198a1` | — | 釋義語言參數化；31B 的三個 num_ctx 變體 |
| `b69e005` | — | 分類語言參數化；評估並否決 Qwen |
| `37a835c` | — | 分類選項參數化；無聲產出不足的偵測 |
| `2d47363` | — | 抽取模型完整評估（E4B／a4b／Qwen） |
| `68b0d0c` | — | 索引式教材的補釋義前置步驟 |
| `80a6584` | — | vision_direct 的 max_tokens 截斷；補釋義自動判斷 |
| `9fcfaf0` | — | 必填欄位為空卻回報成功 |

---

## 變更清單

### 新增程式

1. `src/anki_deck_builder/stages/input_source.py`：`InputKind`、`detect_input()`、
   `list_images()`。目錄內圖片依**自然排序**（字典序會把 page10 排在 page2 前面，
   頁序一錯 `ocr_source_page` 就跟著錯）；不遞迴子目錄
2. `src/anki_deck_builder/stages/pdf_render.py`：`render_pdf()` 逐頁渲染為 PNG，
   檔名零填充保序；以 `asyncio.to_thread` 包住（pdfium 是同步 C 擴充）
3. `src/anki_deck_builder/stages/ocr.py`：`OCRStage`，`prepare()`／`run()` 兩步——
   OCR 是唯一從外部路徑產生列的階段。純文字在 `prepare()` 就讀進 `raw_text` 並標
   `done`，`run()` 因此完全不發 OCR 請求。併發固定 1
4. `src/anki_deck_builder/clients/ocr_client.py`：`OCRClient` 實作
   `OCRClientProtocol`；`model_endpoint()` 供 VRAM 讓渡取得 endpoint
5. `src/anki_deck_builder/clients/image_input.py`：`encode_image_b64()`、
   `build_image_input()`。**編碼前依像素上限等比縮圖**——DPI 框不住像素數，
   同樣 DPI 200，真 A4 為 3.87M px、手機照片轉的 PDF 卻是 12M px
6. `src/anki_deck_builder/clients/model_unload.py`：`unload_model()`、
   `ensure_room()`、`loaded_models()`。卸載相對於回應是非同步的，須輪詢 `/api/ps`
7. `src/anki_deck_builder/clients/agent_endpoint.py`：從 agent 物件推導
   `(base_url, 模型名)`，兩個 client 共用

### 修改程式

8. `src/anki_deck_builder/stages/extract.py`：本 phase 改動最大的檔案
   - `vision_direct` 分支（`_is_vision_row()`、`_vision_input()`）
   - **切段**（`split_into_chunks()`）與**失敗對半重試**
   - **`card_id` 決定性編號**（`_renumber()`）——切段後序號各自從 001 起算，
     唯一性押在模型服從度上會出事
   - **產出檢查**（`estimate_entries()`、`_check_yield()`）——數量明顯不足時重試
   - **必填欄位檢查**（`_check_card_quality()`）——數量對但 `back`／`front` 空時重試
   - **補釋義**（`_enrich()`）與**自動判斷**（`_needs_enrichment()`）
   - **影像路徑重試**（`_extract_image()`，`MAX_IMAGE_ATTEMPTS = 2`）
   - 併發由 `GLOBAL_CONCURRENCY` 改為**固定 1**
   - 任務參數新增 `釋義語言`、`分類語言`、`分類選項`
9. `src/anki_deck_builder/cli.py`：`ocr` 接上實作、`run-all` 串接三階段；
   新增 `--card-language`、`--deck-categories`、`--enrich`／`--no-enrich`；
   階段開始前騰出 VRAM；關閉 openai-agents 的 trace 上傳
10. `src/anki_deck_builder/clients/llm_client.py`：`run_agent()` 回傳型別放寬為
    `BaseModel | str`——未宣告 `output_schema` 的 agent（補釋義、OCR）回傳純文字，
    型別檢查移至呼叫端
11. `src/anki_deck_builder/clients/protocols.py`：同上，Protocol 照實反映兩種模式
12. `src/anki_deck_builder/config.py`：新增 `IngestSettings.pdf_dpi`、
    `extract_chunk_lines`、`ModelUnloadSettings`（`enabled`／`before_stage`／`timeout`）
13. `src/anki_deck_builder/stages/__init__.py`：匯出新增的公開名稱

### 設定與 prompt

14. `agents.yaml`：新增 `OCRAgent`、`VisionExtractAgent`、`EnrichAgent`、
    `MaterialTypeAgent`；抽取模型改為 `gemma4-e4b-optimized`；補上三個候選模型
    （31B 三種 num_ctx、a4b、Qwen）的評估註解與切換方式
15. `prompts/ocr_extract.md`：新增，只有一行官方前綴 `Text Recognition:`
16. `prompts/extract_cards_vision.md`：新增，〈欄位規則〉以下與 `extract_cards.md`
    逐字一致（兩條路徑共用 `output_schema`）
17. `prompts/enrich_entries.md`：新增，索引式教材的補釋義
18. `prompts/detect_material.md`：新增，只輸出 `HAS_DEFINITIONS`／`NO_DEFINITIONS`
19. `prompts/extract_cards.md`：deck 規則重寫（分類語言、分類選項、禁泛稱、
    領域範例表）；`back` 新增語言規則；〈補全規則〉新增「釋義可補全、`back` 不可留空」
20. `.env.example`：新增 `INGEST_PDF_DPI`、`INGEST_EXTRACT_CHUNK_LINES`、
    `MODEL_UNLOAD_BEFORE_STAGE`、`MODEL_UNLOAD_ENABLED`、`MODEL_UNLOAD_TIMEOUT`
21. `pyproject.toml`／`uv.lock`：新增 `pypdfium2`

### 測試

22. `tests/stages/test_input_source.py`（24）、`tests/stages/test_pdf_render.py`（16）、
    `tests/stages/test_ocr.py`（30）、`tests/clients/test_image_encoding.py`（10）、
    `tests/clients/test_model_unload.py`（12）：新增
23. `tests/stages/test_extract.py`：由 22 項擴充至 66 項（vision 分支、切段、
    決定性編號、產出檢查、必填欄位檢查、補釋義、自動判斷）
24. `tests/test_cli.py`：由 34 項擴充至 57 項（ocr 續作、純文字繞過、vision_direct、
    VRAM 騰位、run-all 串接與失敗不中斷、新參數）
25. `tests/clients/test_ocr_client.py`：新增，14 項 mock 骨架，標記 `manual`
26. `tests/clients/test_llm_client.py`：更新純文字 agent 的斷言（manual）
27. `tests/test_config.py`：新增 6 項（新設定項）

### 文檔

28. `.agent/plans/phase-2-execution-plan.md`：新增。執行計畫與 §1～§2.11 的
    完整診斷紀錄（含被推翻的假設）

---

## 測試結果

- `uv run pytest`：**360 passed / 28 deselected**，`ruff check` 無錯誤
- 需人工執行的測試（**已於 2026-08-24 由使用者執行，全部通過**）：
  `tests/clients/test_ocr_client.py`（14 項 mock 骨架，mock 對象為
  `LimitAgentRunner.run`，不觸及真實推理）

  ```bash
  uv run pytest -m manual tests/clients/test_ocr_client.py -v
  ```

### 驗收流程（使用者執行，`two_stage` 全數通過）

真實教材涵蓋四種形態：日文詞條頁（有釋義）、英文單字表（無釋義、一頁 94 條目）、
英文詞條頁（有釋義）、藥理學（非語言領域）。

---

## 備註

### 驗收過程發現並修正的問題

驗收揪出的問題比開發階段多，且多數是 fixtures 涵蓋不到的：

| # | 問題 | 性質 |
|---|------|------|
| 1 | ComfyUI 常駐佔 2.4 GB → 模型只載入 88%，速度剩 1/6 | 環境 |
| 2 | Phase 1 舊工作檔的列被 ocr 誤判為待辦 | 程式 |
| 3 | extract 併發 6 → 排隊時燒掉逾時預算 | 程式 |
| 4 | 前一階段的模型不讓位 → 下一階段卡到逾時 | 程式 |
| 5 | 長頁面一次送 → 模型退化成 1 張卡或壞 JSON | 程式 |
| 6 | 切段引入的 `card_id` 撞號風險 | 程式 |
| 7 | **無聲產出不足**：呼叫成功、JSON 合法，7 個條目只回 1 張卡 | 程式 |
| 8 | **必填欄位為空卻回報成功**：59 張卡全空 `back` | 程式 |
| 9 | vision_direct 的 `max_tokens: 4096` 截斷 JSON | 程式 |

第 7、8 項最危險——它們**不會拋錯**，缺漏會安靜寫進工作檔。

### 兩條可複用的經驗

1. **參數比 prompt 規則有效得多**（對 8B 模型）：同一條要求寫在 180 行 prompt
   的中段幾乎無效，提升為任務參數就守得住。釋義語言 0／98 → 95／95，
   分類語言 60／98 → 98／98
2. **只要設小的 `max_tokens`，就必須同時關思考**：本輪四次踩同一個坑
   （Gemma 31B 讀影像、Qwen、a4b、MaterialTypeAgent），症狀都是
   `finish=length`、`content` 空字串

### 已知限制

- **`vision_direct` 對密集頁面產出不足**：影像無法切段，實測 74 張／94 條目。
  `two_stage` 為預設且品質較佳（同一份教材 109 張、例句 109／109）
- **索引式教材需要補釋義**：自動判斷已可辨識（四種教材 4／4），代價是每頁多一次
  短呼叫與補釋義呼叫（整頁 6m10s → 9m05s）
- **抽取速度受限於本地模型**：E4B 在 94 條目的密集頁面約 6～9 分鐘

### 後續待辦

- Phase 3 的 VRAM 策略：ComfyUI 不是 Ollama，`ensure_room()` 看不到它；
  需在 extract → image 之間呼叫 ComfyUI 的 `POST /free`，或啟用
  `MODEL_UNLOAD_ENABLED` 卸載 Ollama 模型
- ~~`tests/fixtures/` 只有日文詞條頁一種形態~~ → 已補上 `materials/` 三種形態
  （索引式、非語言領域、有釋義的非日文），並加上啟發式規則的回歸測試
