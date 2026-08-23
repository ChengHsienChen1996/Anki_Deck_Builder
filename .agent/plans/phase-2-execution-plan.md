# Phase 2 執行計畫（待使用者確認）

日期：2026-08-22 ／ 分支：`dev_ai`
依據：[phase-2-ocr.md](phase-2-ocr.md)、[architecture.md](../../docs/architecture.md)、[project-overview.md](../../docs/project-overview.md)、[agent-factory-cheatsheet.md](../notes/agent-factory-cheatsheet.md)

---

## 0. 開工前檢查結果

| 檢查項 | 結果 |
|--------|------|
| 分支 | ✅ `dev_ai`，工作區乾淨（`58c8235`） |
| `.claude/settings.local.json` 備份 | ⚠️ 專案內仍無 `.claude/` 目錄（同 Phase 1），**無檔案可備份** → 略過，待該檔出現後每次開工再快照 |
| `agent_factory` submodule README | ✅ 已讀（`.agent/notes/agent-factory-cheatsheet.md` 可用） |
| 外部介面狀態 | ✅ 本 phase 只依賴 Ollama，VOXCPM2／ComfyUI 未定不影響 |
| Phase 1 交付 | ✅ 驗收通過，`extract` → `pack` 可跑；`BaseStage` 已支援一對多與失敗跳過 |
| 既有 fixtures | ✅ `tests/fixtures/pages/page_0{1..4}.jpg`、`ocr_raw/*.txt`（人工轉寫）、`expected_cards.csv` |

---

## 1. 多模態支援查證結果（2026-08-22 實測，本 phase 最大風險已解除）

phase-2-ocr.md 列為「本 phase 最大風險」的 Gemma 4 多模態支援，以及 Task 2.3 標注待查的影像 content block 格式，**皆已實測確認**。

### 1.1 模型宣告（`POST /api/show`）

| 模型 | capabilities | 視覺塔 |
|------|--------------|--------|
| `gemma4_31b_q4_K_M-optimized` | `completion, vision, tools, thinking` | `gemma4.vision.*`：27 層、embedding 1152、patch 16 |
| `glm-ocr-optimized` | `completion, vision, tools` | `glmocr.vision.*`：24 層、image_size 336、patch 14、max_pixels 9,633,792 |

### 1.2 實測（`AsyncOpenAI` → `http://localhost:11434/v1/chat/completions`）

測試圖為合成的日語教材頁（900×340 PNG、Noto Sans CJK 34px、含拗音與漢字）。

| 測項 | 結果 |
|------|------|
| **影像 content block 格式** | ✅ `{"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}}` 可用。**Task 2.3 規劃的 base64 路徑成立，不需修改 submodule** |
| GLM-OCR 讀圖 | ⚠️ 此列數據**作廢**：當時用了 GLM-OCR 不支援的自擬 prompt，見 §2.1／§2.2 |
| GLM-OCR 重複輸出 | ⚠️ 未設 `max_tokens` 時整段無限重播至 context 耗盡 |
| Gemma 4 讀圖 | ✅ 於**合成簡單圖**上逐字全對（10.5s／218 tok）。⚠️ 真實書頁上表現大幅劣化，見 §2.2 |
| **Gemma 4 影像 + structured output** | ✅ `response_format=json_schema`（`ExtractOutput.model_json_schema()`、`strict: True`）與影像**可併用**，回傳通過 `ExtractOutput.model_validate_json()`；17.0s／162 tok |

> **由此確定**：Task 2.5 `vision_direct` 的兩項硬性前提——模型支援影像、且影像路徑仍能產出與 `ExtractAgent` 相同的 `output_schema`——**皆成立**，可照原文件實作。

### 1.3 實測順帶發現（影響後續設計）

1. **冷啟動會撞 Ollama 的載入逾時**。第一次直接送影像請求，Ollama 回
   `500 timed out waiting for llama-server to start`——權重從未進 VRAM，與影像能力無關。
   改為「先發純文字請求暖機（79.3s 載入）→ 再送影像」即正常。
   本專案端的 `timeout: 600` 擋不住這個 500（它來自 Ollama 自身）。
2. **two_stage 會讓兩個模型同時佔 VRAM**：Gemma 4 20.3 GB + GLM-OCR 2.2 GB = 22.5 GB／24 GB，
   加上桌面約 1 GB 已逼近上限。Ollama 預設 `keep_alive` 5 分鐘不會主動讓位。
   對應 project-overview.md〈階段間的 VRAM 讓渡〉，本 phase 需要處理（見 Q3）。
3. **Gemma 4 帶 `thinking` 能力**：暖機那次給 `max_tokens=32`，32 token 全被思考佔掉、
   可見內容為空。抽取階段若設過小的 `max_tokens` 會拿到空字串——Task 2.5 訂 vision agent
   參數時要留足額度。
4. `card_id` 由 LLM 自填時會出現雜訊（實測拿到 `"，1"`）。Phase 1 的 `extract.py` 本來就
   自行組 `card_id`，維持現狀即可，但 vision prompt 不要暗示模型自己編號。

---

## 2. 已確認的決定（2026-08-22）

> ⚠️ Q1／Q3 曾一度定為「預設 `vision_direct`、不做 VRAM 讓渡」，該決定建立在一份
> **不對等的比較**上（GLM-OCR 用了它不支援的自擬 prompt vs Gemma 4 只讀簡單合成圖）。
> 以正確 prompt 與真實書頁重測後結論反轉（§2.2），兩項均已改回。

| # | 議題 | 決定 |
|---|------|------|
| **Q1** | 預設輸入模式 | **維持 `INGEST_MODE=two_stage`**（GLM-OCR 讀圖 → Gemma 4 抽取）。真實書頁上 GLM-OCR 的辨識忠實度明顯較高，Gemma 4 直讀會產生幻覺內容（§2.2）。`vision_direct` 仍照 phase-2-ocr.md 完整實作，作為可切換的次要路徑 |
| **Q2** | GLM-OCR prompt | 使用者提供官方規格（§2.1）。`prompts/ocr_extract.md` 用 Document Parsing 的 **`Text Recognition:`**，不自擬指令 |
| **Q3** | 階段間 VRAM 讓渡 | **本 phase 做最小版**：`ocr` 階段結束後，依設定選擇性對 Ollama 送 `keep_alive: 0` 卸載 GLM-OCR（`90b91fb` 已實測可行）。須為**可選、設定驅動**，不寫死進 client（architecture.md 要求） |
| **Q4** | PDF 渲染 DPI | 預設 **200**，驗收時以真實 PDF 比較 150／200／300 的辨識率與檔案大小後定案 |
| **Q5** | OCR 併發上限 | **固定 1**，不套用 `GLOBAL_CONCURRENCY`。本地推理 GPU 序列化，併發只會讓多份 KV cache 同時佔 VRAM |

### 2.1 GLM-OCR 的 prompt 規格（使用者提供）

GLM-OCR 只支援兩種 prompt 場景，**不接受自擬指令**：

1. **Document Parsing**——從文件擷取原始內容，任務前綴固定：
   ```
   text    → "Text Recognition:"
   formula → "Formula Recognition:"
   table   → "Table Recognition:"
   ```
2. **Information Extraction**——擷取結構化資訊，prompt 必須內嵌嚴格的 JSON schema
   （例：`请按下列JSON格式输出图中信息:` 後接 schema 本體），輸出須嚴格符合該 schema。

本專案的 `OCRAgent` 屬第 1 類，用 `Text Recognition:`。**不走第 2 類**——結構化抽取是階段 ②
`ExtractAgent` 的職責，OCR 階段只負責出純文字（與 phase-2-ocr.md「OCR 不設 `output_schema`」一致）。

### 2.2 真實書頁的模型比對（`tests/fixtures/pages/page_01.jpg`）

輸入：原圖 4000×3000 = 12,000,000 px，**超過 GLM-OCR 宣告的 `max_pixels` 9,633,792**，
等比縮至 3200×2400 = 7,680,000 px（base64 2.37 MB）後送出。比對基準為
`tests/fixtures/ocr_raw/page_01.txt`。

| 面向 | GLM-OCR `Text Recognition:`（9.1s） | Gemma 4 直讀（關思考後 17.4s／542 tok） |
|------|--------------------------------------|------------------------------------------|
| 標題詞 | 13 條中 12 條正確（`増大` 誤讀為 `そうだい`） | **幻覺**：生出頁面上不存在的 `壮大`、把 `予算が多そうだ` 當標題 |
| 讀音 | 多數正確，但振假名與本文黏連（`だん匯う そうち`、`のうりくそうとう能力相当の地位`） | `送別`→`そうび`、`属する`→`らくする`，錯得離譜 |
| 釋義 | 忠實照抄頁面上的中文 | 自行改寫為 `[意味]／[例文]` 格式，內容為模型自編的日文 |
| 其他缺陷 | 掉字（`体力を測定する` 少「体力」）、句尾偶有重複 | 混入韓文（`纷纷 입장`）、誤譯（`虎はネコ科に属する`→「屬於老虎科」） |

**結論**：Gemma 4 在密集排版的真實書頁上不是在 OCR，而是看圖說話；GLM-OCR 雖有振假名黏連
與偶發掉字，但忠實度遠高。→ Q1 維持 `two_stage`。

### 2.3 Gemma 4 的 thinking 必須關閉（新發現，影響 agents.yaml）

`gemma4_31b_q4_K_M-optimized` 的 capabilities 含 `thinking`，預設開啟。實測讀整頁影像時
**4096 個 completion token 全被思考吃光**，`finish_reason=length`、`content` 為空字串，
思考內容落在 `message.reasoning` 欄位。

關閉方式（兩者實測皆有效）：

| 方式 | 端點 | 結果 |
|------|------|------|
| `reasoning_effort="none"` | OpenAI 相容 `/v1` ✅ **本專案採用** | `reasoning` 歸零、`content` 正常 |
| `"think": false` | Ollama 原生 `/api/chat` | 17.4s／542 tok、`done_reason=stop` |

agent_factory 走的是 `/v1`，因此用 `reasoning_effort`。`VisionExtractAgent` 的
`model_params.params` 必須帶此設定，否則影像路徑會拿到空輸出。

> ⚠️ **需一併查證**：Phase 1 的 `ExtractAgent` 同樣未設此參數。文字抽取在 Phase 1 驗收時
> 通過（structured output 有解析出結果），但長輸入是否可能同樣耗盡預算未經驗證。
> Task 2.5 動 `agents.yaml` 時一併確認並回報，不擅自更動已驗收的設定。

### 2.4 影像尺寸上限（新發現，影響 Task 2.2／2.3）

真實書頁 4000×3000 超過 GLM-OCR 的 `max_pixels`，**縮圖是必要步驟而非最佳化**。
`ocr_client.py` 的 `encode_image_b64()` 需在編碼前依上限等比縮圖，上限值設定化
（預設對齊 9,633,792 px）。Task 2.2 的 DPI 200 也須回頭核算：A4 @ 200 DPI 約
1654×2339 = 3.9M px，在上限內；300 DPI 為 8.7M px，接近上限。

### 2.5 已評估並否決：抽取階段同時送入影像（grounded extraction）

**提案**（使用者，2026-08-22）：既然 GLM-OCR 有掉字與振假名黏連，抽取時把原始影像一併送給
Gemma 4 當參考校正，是否能改善？

**實測 A／B**：同一份 GLM-OCR 文字（`page_01`），A 組純文字、B 組文字＋影像並明示
「衝突時以影像為準」，兩組皆用 `ExtractOutput` schema 與 `reasoning_effort=none`。

| | A 純文字（45.9s／1225 tok） | B 文字＋影像（38.8s／1215 tok） |
|---|---|---|
| 卡片數 | 13（正確） | 13（正確） |
| `増大`／`ぞうだい`（GLM 誤讀為 `□そうだい`） | ✅ 還原正確 | ✅ 同 |
| 振假名黏連 | ✅ 全部修正 | ✅ 同 |
| 幻覺／重複卡 | 無 | 無 |

**逐欄位 diff 後兩組完全相同**（僅 `card_id` 雜訊差異，該欄由 `extract.py` 覆寫）。

**結論：不做。** GLM-OCR 的兩種失誤都已被階段 ② 的上下文推理解決——標題誤讀可由例句中的
漢字還原（`予算が増大する`），黏連的讀音字串本身仍在文字流中。影像進來沒有新資訊可補，
卻要付出設定開關、額外測試路徑與每列的 vision token。

> 保留條件：若日後遇到「例句中也不含該漢字」的頁面，影像校正才有價值。屆時形式應為
> `two_stage` 底下的開關（如 `TWO_STAGE_GROUNDING`），**不新增 `INGEST_MODE` 的第三個值**，
> 以免動到驗收標準。`llm_client` 屆時已支援影像輸入（Task 2.5），補上不難。
>
> 附帶結論：B 組**未引入幻覺**。Gemma 4 有文字錨點時是穩的，§2.2 的亂編發生在無錨點的
> `vision_direct` 情境——這也佐證 Q1 的選擇。

---

### 2.6 驗收回報的 extract 全流程失敗：診斷與修正（2026-08-23）

使用者以**真實英文單字表**（一頁 94 個條目）驗收 `run-all`，extract 全數
`APITimeoutError`。診斷過程推翻了三個假設，最終發現是五個問題疊加。

#### 根因一：ComfyUI 佔住 VRAM（環境）

```
GPU 24576 MiB
├─ ComfyUI (python3 ./ComfyUI/main.py)   2424 MiB   ← 常駐 7 小時
└─ Ollama llama-server                  20198 MiB
```

Gemma 4 需 21.5 GB，扣掉 ComfyUI 後只能載入 **85~88%**，2.6~3.2 GB 溢出到 RAM，
速度由 34 tok/s 崩至 4~6 tok/s。呼叫 ComfyUI 的 `POST /free` 釋放後即回到 100%，
同一段輸入從 844s 降到 189s。

> ⚠️ **診斷時的誤判**：一度歸咎於 `num_ctx`，請使用者由 16384 改為 8192，
> 反而讓失敗從「逾時」變成「JSON 截斷」（8192 裝不下 system prompt 2844 +
> thinking 3000 + 輸出 3200）。`num_ctx` 只是次要因素，主因是 ComfyUI。

#### 根因二：模型能力邊界（設計）

| 條件 | 結果 |
|------|------|
| 31B、關 thinking、10 或 25 條目 | 都只回 **1 張卡**（約 10~20s） |
| 31B、開 thinking、25 條目 | 844s（VRAM 88%）／189s（100%）但吐壞 JSON |
| 31B、開 thinking、整頁 94 條目 | 逾時；可靠處理量約 **5 條目／次** |

**本地模型面對太多條目不會報錯，而是退化**——壞 JSON 或只回一張卡。因此
`reasoning_effort: none` 這條先前記在 §2.3 的建議**只適用於影像路徑**，
文字抽取關掉思考會直接廢掉。

#### 根因三～五：本專案的三個 bug

1. `extract` 併發取自 `GLOBAL_CONCURRENCY=6`，本地模型序列化執行，
   **逾時計時器在排隊時照樣在跑** → 併發固定 1
2. 長頁面一次送出 → 加入切段（`INGEST_EXTRACT_CHUNK_LINES`，預設 20）
   與**失敗自動對半重試**（下限 4 行）
3. 切段後每段序號各自從 001 起算，`card_id` 唯一性押在模型服從度上會出事
   （實測模型把段落標籤當成 id）→ 改由程式決定性編號

#### 決定：抽取模型改用 E4B

| | 31B | **E4B（採用）** |
|---|---|---|
| 日文 13 條目 | 157s | **88s** |
| 英文 25 條目 | 吐壞 JSON | **63.7s／18 張卡** |
| 英文整頁 94 條目 | 40 分 45 秒／93 張卡 | **6 分 15 秒／98 張卡** |
| 穩定度 | 時好時壞、尾段 `back` 空白 | 每次成功 |

31B 的宣告保留在 `agents.yaml` 註解中，改一行即可切回。

#### prompt 的五處加強（皆由實測打出來，非預想）

1. deck 不得填 `Vocabulary` 這類泛稱 —— E4B 原本全部歸成一類
2. deck 只填**單一中文詞性** —— 原本出現 `名詞/動詞`、`noun_verb`
3. deck 前綴照抄**並保留分類層** —— 修正 1、2 後它把 `英語::基礎` 當成完整 deck
4. 釋義可由知識補全、**`back` 不可留空** —— 純單字表原文沒有釋義，
   模型照〈補全規則〉留空，而 `back` 是 `pack` 的必填欄位
5. 釋義語言規則 —— 原 prompt 從未規定，日文教材照抄原文中文所以沒暴露

#### 釋義語言：已改為顯式任務參數（2026-08-23）

prompt 裡的語言規則對 E4B 無效（輸入全英文時它就跟著用英文）。改為與 `領域`、
`牌組前綴` 同級的**任務參數** `釋義語言`，由 CLI `--card-language` 傳入——
參數區塊就在輸入開頭、緊鄰教材本文，比埋在 180 行 prompt 裡的一條規則有效得多。

實測（英文 94 條目頁）：**釋義含中文 95／95**（改動前 0／98）。

#### 仍未解決

英文教材的 **deck 詞性中英混雜**（noun 26／名詞 14／verb 10／副詞 10…）。
`back` 的語言已由參數解決，但分類名稱走的是另一條規則。可比照辦理——
讓分類名稱跟隨 `釋義語言`。**日文教材（本專案主要目標）不受影響**，
其 deck 一律為中文詞性。

---

## 3. 任務執行順序

一次一個 task，完成即停下交付。

| 順序 | Task | 產出 | 測試 |
|------|------|------|------|
| 1 | **2.1 輸入源判別** | `stages/input_source.py` | 可執行單元測試（各類型／空目錄／混合／不支援格式／自然排序） |
| 2 | **2.2 PDF 轉圖** | `stages/pdf_render.py`、`pyproject.toml` 加 `pypdfium2` | 可執行單元測試（測試用 PDF 即時產生，不進版控）。**動手前先查 pypdfium2 官方文件，不照記憶寫** |
| 3 | **2.3 OCR agent 與適配層** | `agents.yaml`（`OCRAgent`）、`prompts/ocr_extract.md`（`Text Recognition:`）、`clients/ocr_client.py`（含 §2.4 的編碼前縮圖） | 縮圖邏輯為**可執行**測試；OCR 呼叫為 mock 骨架，**撰寫後不執行**，於日誌點名 |
| 4 | **2.4 階段 ① ocr** | `stages/ocr.py`、模型卸載小模組（§Q3，`keep_alive: 0`，可選且設定驅動） | 判別／頁序／純文字繞過／卸載開關為可執行測試；OCR 呼叫與 HTTP 卸載 mock |
| 5 | **2.5 vision_direct** | `agents.yaml`（`VisionExtractAgent`，含 `reasoning_effort: none`）、`prompts/extract_cards_vision.md`、`extract.py`／`llm_client.py` 補分支 | 分支選擇邏輯可執行測試；LLM 呼叫 mock。另查證 `ExtractAgent` 的 thinking 情形並回報（§2.3） |
| 6 | **2.6 CLI 接線與 run-all** | `cli.py` | 可執行單元測試（串接順序、失敗不中斷） |

### 全程遵守的紅線（取自 phase-2-ocr.md〈不要做〉）

- 不改 `src/agent_factory/` 任何檔案；需要它補功能就停下來討論
- 不裝本地 OCR 套件、不自己打 OCR 的 HTTP——一律經 agent_factory
- 不做影像前處理（去歪斜／二值化／裁切）
- 不改 `CardRow` 欄位定義；需要新欄位先停下來
- 兩條路徑共用 `output_schema`，vision 漏欄位只調 prompt
- `run-all` 只留 `image`／`audio` 的位置與註解，不實際呼叫
- 不引入 Gradio／FastAPI

---

## 4. 風險與因應

| 風險 | 因應 |
|------|------|
| ~~Gemma 4 多模態支援未知~~ | ✅ 已解除（§1.2） |
| ~~影像 content block 格式未知~~ | ✅ 已解除（§1.2） |
| Ollama 冷啟動 500 | Task 2.4／2.6 的實際執行會遇到。先在文件記錄，若驗收時重現，建議調 `OLLAMA_LOAD_TIMEOUT`（屬環境設定，非本專案程式碼） |
| two_stage VRAM 相加 22.5 GB | Q3 已定：`ocr` 結束後選擇性 `keep_alive: 0` 卸載 GLM-OCR |
| **兩個模型無法共存**（驗收實測） | 24 GB 卡上 Gemma 18.3 GB + 桌面約 4.8 GB = 23.2 GB，只剩 1.4 GB，GLM-OCR 的 2.2 GB 進不去。Ollama 預設 `keep_alive` 5 分鐘不讓位，下一階段的請求會卡在排隊、`still_waiting` 累積到逾時。→ 新增 `ensure_room()`，**階段開始前**卸載其他常駐模型，`MODEL_UNLOAD_BEFORE_STAGE` **預設開啟**（這是必要條件，不是最佳化） |
| **卸載與逾時的互動**（Task 2.6 實測發現） | 開啟 `MODEL_UNLOAD_ENABLED` 後，`run-all` 的 extract 會緊接著付出 Gemma 4 的冷載入代價。實測單頁 extract 在模型已駐留時 157s，冷載入情況下超過 `agents.yaml` 的 `timeout: 600` 而失敗。**因此 `MODEL_UNLOAD_ENABLED` 預設關閉是對的**；要開啟得同時評估拉長 timeout。驗收第 6 步請以預設值（關閉）執行 |
| GLM-OCR 振假名黏連／掉字 | 已於真實書頁實測（§2.2）。**交由階段 ② 的 LLM 從上下文還原**——此因應已於 §2.5 實測驗證有效（13/13 正確），不做影像前處理、不換 OCR 服務。驗收第 8 步仍需檢查讀音欄位 |
| Gemma 4 thinking 耗盡 token | §2.3 已定 `reasoning_effort: none`；`ExtractAgent` 待查證 |
| pypdfium2 API 與認知不符 | Task 2.2 開工先查官方文件；品質不足則停下討論 |
| 影像超過 `max_pixels` | 已實測會發生（真實 fixtures 12M px > 9.63M）。§2.4 定為編碼前必做的等比縮圖 |

---

## 5. Phase 2 結束時的交付

1. 六個 task 全部完成，非付費測試全綠、`ruff check` 無錯
2. `tests/clients/test_ocr_client.py` 為未執行的 mock 骨架，於日誌點名待人工驗證
3. 驗收流程八步交使用者執行；第 8 步以四張真書頁做兩模式比對，覆核 §2.2 的單頁結論是否成立
4. 驗收通過後：`logs/` 產出改動日誌、更新 CLAUDE.md 進度追蹤表
