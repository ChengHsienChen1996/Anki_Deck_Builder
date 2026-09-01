# 使用說明

從零跑通一次，以及每個設定項的意思。專案目的與架構見
[project-overview.md](project-overview.md) 與 [architecture.md](architecture.md)；
開發規範（編碼風格、測試策略、版控流程）在 `docs/` 其餘檔案，本文不重複。

---

## 前置需求

四樣東西要先備妥。**沒有全部到位也能跑一部分**——例如只做文字抽取而不生圖，
就不需要 ComfyUI；缺的服務只會讓對應階段擋下，其他階段照常。

### 1. 本專案

```bash
git clone --recurse-submodules <repo-url> anki-deck-builder
cd anki-deck-builder
uv sync --extra dev
```

`--recurse-submodules` 不能漏：LLM 與 OCR 的呼叫全部建立在 `src/agent_factory`
這個 submodule 上。已經 clone 過但忘了帶的話，補 `git submodule update --init`。

`--extra dev` 帶的是 pytest 與 ruff。只想用不想開發的話 `uv sync` 就夠。

### 2. Ollama 與兩個模型（階段 ① ②）

抽取與 OCR 都走本地 Ollama，endpoint 與模型名寫在專案根目錄的 `agents.yaml`
（**不在 `.env`**——那是 agent_factory 的設計，同一份 `.env` 可以搭不同 YAML 切換供應商）。
預設值：

| 用途 | agents.yaml 的 model | 說明 |
|------|----------------------|------|
| 抽取 | `gemma4-e4b-optimized:latest` | 約 11.6 GB。整頁 94 條目約 4～5 分鐘 |
| OCR | `glm-ocr-optimized:latest` | 約 2.2 GB。只認官方前綴 prompt，見 `prompts/ocr_extract.md` |

換模型就改 `agents.yaml` 的 `model:`，不必動程式碼。`agents.yaml` 裡有實測過的候選
與各自的代價（速度、VRAM、分類品質），換之前值得讀一下。

**`num_ctx` 決定成敗**：本地模型若沒有完整載入 VRAM，速度會掉一個數量級、長輸入直接逾時。
Modelfile 的 `num_ctx` 開太大時，KV cache 會把權重擠到 CPU。細節與實測數字見
[project-overview.md](project-overview.md)〈Ollama 模型的 num_ctx 會決定成敗〉。

### 3. ComfyUI 與 workflow（階段 ③）

啟動 ComfyUI（預設 `http://127.0.0.1:8188`），並準備一份 **API 格式**的 workflow JSON。
專案內附三份：

| 檔案 | 模型 | 尺寸 | 熱機每張 | 圖片大小 |
|------|------|------|----------|----------|
| `workflows/card_image_kyoani.json`（預設） | FLUX.2-klein-9B ＋ KyoAni Style LoRA | 1344×768 | 約 15s | 約 1.0 MB |
| `workflows/card_image_xl.json` | fabricatedXL／SDXL | 1344×768 | 約 8s | 約 1.1 MB |
| `workflows/card_image.json` | DreamShaper 8／SD 1.5 | 768×432 | 約 1.6s | 約 0.3 MB |

kyoani 那份需要 ComfyUI 裝好 **KJNodes**（節點 `100` 的 SageAttention）與 Python 的
`sageattention` 套件；XL 那份需要 Impact Pack、rgthree、easy-use、LoraManager。

**換 workflow 是「整組換」**：路徑、觸發詞前綴、尺寸、VRAM 讓渡與全部節點 ID 綁在一起，
只改 `COMFYUI_WORKFLOW_PATH` 一行必定對不上節點 ID。`.env.example` 已把 kyoani 與 SDXL
兩組排成前後兩塊，換的時候一塊註解掉、另一塊解開，再 `anki-builder image --force` 重生
（兩種畫風混在同一副牌組會很明顯）。

**FaceDetailer 預設不跑**：它會依偵測到的臉數做額外細修，實測同一批 3 張卡從 25 秒
變成 95 秒（其中一張獨佔 69 秒）。記憶錨點圖在卡片上顯示得小，臉部細節換不到記憶效果。
要打開的話，把 `card_image_xl.json` 裡節點 `100` 的 `images` 從 `["39", 0]`
改成 `["75", 0]`（該節點的 `_meta.title` 也寫了這件事）。

匯出自己的 workflow：在 ComfyUI 介面裡排好流程後，選 **Save (API Format)**——
不是一般的 Save。兩者的 JSON 結構不同：

| 匯出方式 | JSON 頂層 | 本專案 |
|----------|-----------|--------|
| Save (API Format) | 節點 ID 當 key（`{"3": {...}, "6": {...}}`） | ✅ |
| Save（編輯器格式） | `nodes` / `links` 陣列 | ❌ 會被擋下並指名道姓報錯 |

匯出後打開 JSON，**頂層的每個 key 就是節點 ID**，填進 `.env` 的
`COMFYUI_*_NODE_ID`（見下方〈ComfyUI 節點注入點〉）。

### 4. VOXCPM2 權重（階段 ④）

`voxcpm` 是本機 Python 套件（`uv sync` 已裝好），推論跑在本專案行程內的 GPU 上，
**不是 HTTP 服務**。你要準備的只有權重目錄：內含 `config.json`、`model.safetensors`
（約 4.58 GB）與 `audiovae.pth`（約 377 MB），路徑填進 `VOXCPM2_MODEL_PATH`。

---

## `.env` 設定

`cp .env.example .env` 之後逐項調整。以下按區塊說明，**只列需要判斷的項目**；
`.env.example` 每一行都有註解，兩邊搭配著看。

### agent_factory（LLM 與 OCR 共用）

| 變數 | 說明 |
|------|------|
| `OPENAI_API_KEY` | 走本地 Ollama 時不會被驗證，但底層 SDK 要求非空字串，填 `ollama` 即可 |
| `YAML_SETTINGS_FILE` | `agents.yaml` 的路徑 |
| `GLOBAL_CONCURRENCY` | 同時最多幾個 LLM 請求。本地推論的瓶頸是 GPU 序列化，調高不會更快 |

### 輸入模式

| 變數 | 說明 |
|------|------|
| `INGEST_MODE` | `two_stage`（預設）影像先 OCR 再抽取；`vision_direct` 影像直接送抽取模型，跳過 OCR。純文字輸入一律直接進階段 ② |
| `INGEST_PDF_DPI` | PDF 逐頁渲染的解析度。過低傷辨識率，過高則 base64 過大並逼近模型的像素上限（A4 @ 200 DPI 約 3.9M px，GLM-OCR 上限 9.63M px） |
| `INGEST_EXTRACT_CHUNK_LINES` | 抽取階段每次最多送幾行。本地模型面對太多條目不會報錯，而是**退化**（吐壞 JSON 或只回一張卡）；失敗時階段會自動對半再切，此值只是起點 |

### 階段間的 VRAM 讓渡

一張卡上要輪流跑三種模型，誰先佔住不放，下一階段就會排隊或載不滿。

| 變數 | 預設 | 說明 |
|------|------|------|
| `MODEL_UNLOAD_BEFORE_STAGE` | `true` | 階段**開始前**卸載其他常駐的 Ollama 模型。image 與 audio 也適用（它們一個 Ollama 模型都不需要） |
| `MODEL_UNLOAD_ENABLED` | `false` | 階段**結束後**卸載自己用的模型。只有要把 VRAM 讓給非 Ollama 的消費者時才需要，開啟會讓下一階段付冷載入代價 |
| `COMFYUI_FREE_BEFORE_LLM` | `false` | **extract 與 audio 前**請 ComfyUI 釋放 VRAM（變數名的「LLM」是歷史包袱，語意以此為準）。**是不是必要取決於模型大小**，見〈疑難排解〉的 VRAM 段。用 kyoani workflow 時必須設 `true` |

### ComfyUI 節點注入點

程式不猜你的 workflow 長什麼樣，一律照 `.env` 指定的節點與欄位注入：

| 變數 | 必填 | 注入什麼 |
|------|------|----------|
| `COMFYUI_POSITIVE_NODE_ID` / `_FIELD` | ✅ | 該卡的 `image_prompt` |
| `COMFYUI_NEGATIVE_NODE_ID` / `_FIELD` | ✅ | `COMFYUI_NEGATIVE_PROMPT` |
| `COMFYUI_OUTPUT_NODE_ID` | ✅ | 從哪個節點取回圖片 |
| `COMFYUI_SEED_NODE_ID` / `_FIELD` | 選填 | 由 `card_id` 推得的穩定種子——prompt 沒改的話，同一張卡重跑會得到同一張圖 |
| `COMFYUI_LATENT_NODE_ID` / `_WIDTH_FIELD` / `_HEIGHT_FIELD` | 選填 | `COMFYUI_IMAGE_WIDTH` / `_HEIGHT` |
| `COMFYUI_WIDTH_NODE_ID` / `COMFYUI_HEIGHT_NODE_ID` | 選填 | 寬高**不在同一個節點**時改指這兩個，留空則退回 `COMFYUI_LATENT_NODE_ID` |

選填的留空就不注入，workflow 裡原本的值照用。

寬高為什麼有兩套變數：SDXL 那類 workflow 的寬高是單一 latent 節點的兩個欄位
（`COMFYUI_LATENT_NODE_ID` ＋ `_WIDTH_FIELD` / `_HEIGHT_FIELD`），而 FLUX.2 把它們放在
`88`／`89` 兩個獨立的 `PrimitiveInt`，同一組值還餵給 `Flux2Scheduler`。後者就把
`COMFYUI_LATENT_NODE_ID` 留空、改用 `COMFYUI_WIDTH_NODE_ID` / `_HEIGHT_NODE_ID`
分別指過去（欄位名兩者都叫 `value`）。錯誤訊息會指出實際生效的是哪個變數。

### 語法層 profile

| 變數 | 預設 | 說明 |
|------|------|------|
| `IMAGE_PROMPT_AGENT` | `ImagePromptFluxAgent` | `prompt` 階段用哪個 agent 把 `image_scene` 改寫成該模型的語法 |

**這一行就是 profile 的開關。** 模型專屬的三件事——目標語法（自然語言敘述
vs 逗號 tag）、LoRA 觸發詞、風格後綴——全部寫在該 agent 的 prompt 檔裡，
不散在 `.env`：

| 值 | prompt 檔 | 給誰用 |
|----|-----------|--------|
| `ImagePromptFluxAgent` | `prompts/image_prompt_flux.md` | FLUX.2-klein-9B ＋ KyoAni LoRA。自然語言敘述式，觸發詞 `Anime. ` |
| `ImagePromptSDXLAgent` | `prompts/image_prompt_sdxl.md` | fabricatedXL／SDXL 回頭路。場景原樣照抄 ＋ 統一風格後綴 |

換模型 = 改這一行加 workflow 那組節點 ID → `anki-builder prompt --force`
→ `anki-builder image --force`。**語義層（`image_scene`）完全不動**，
人工編修過的場景也保留。

> Phase 7 曾有一個 `COMFYUI_PROMPT_PREFIX` 專放觸發詞，Phase 8 **移除**了它：
> 同一件事有兩個機制，換模型時忘了清哪一邊就會出事。
> 現在 CSV 裡的 `image_prompt` **就是**送進 ComfyUI 的完整字串，中間沒有任何加工。

### 牌組媒體格式

外部服務給的是 PNG 與 WAV，兩者都壓不動也壓得少，而記憶引擎
（`engines/anki_engine.html`）用 JSZip 把 ZIP 內**每個媒體檔解成 Blob 常駐記憶體**——
體積直接決定它在手機上會不會被系統殺掉。

| 變數 | 預設 | 說明 |
|------|------|------|
| `MEDIA_IMAGE_FORMAT` | `webp` | `png` 為原樣輸出、不轉檔；`jpeg` 的副檔名是 `.jpg` |
| `MEDIA_IMAGE_QUALITY` | `92` | 平塗插畫在 92 幾乎無損，體積只有 PNG 的 1/17 |
| `MEDIA_AUDIO_FORMAT` | `mp3` | `wav` 為原樣輸出、不轉檔 |
| `MEDIA_AUDIO_COMPRESSION` | `0.4` | 0 最好、1 最小 |

實測 308 張卡：**PNG + WAV 約 443 MB → WebP + MP3 約 35 MB**。
轉檔由既有相依完成（Pillow、soundfile），**不需要 ffmpeg 或 pydub**。

轉檔功能上線前做好的牌組，用腳本補轉即可，不必重跑 image／audio：

```bash
uv run python scripts/convert-media.py work/cards.csv --dry-run   # 先看會轉幾個、省多少
uv run python scripts/convert-media.py work/cards.csv             # 轉檔並改寫 CSV 的路徑欄位
```

### VOXCPM2 語音

**音色有三種來源，彼此不互斥**，換路線只要改設定，不必動程式碼：

| 想要的效果 | 怎麼設 |
|-----------|--------|
| 以文字描述指定音色（預設走這條） | 只填 `VOXCPM2_VOICE_DESCRIPTION`，例如 `(A young woman, clear and steady voice, neutral American accent, calm pace)` |
| 聲音複製 | 描述留空，`VOXCPM2_REFERENCE_WAV` 指向音檔 |
| 複製 ＋ 風格控制 | 兩者都給，描述退為風格控制 |
| 最高保真複製 | `REFERENCE_WAV` 與 `PROMPT_WAV` 指向**同一個檔**，再加 `PROMPT_TEXT` 逐字稿 |

**三者全空會每張卡都是隨機音色**（整副牌組聲音不一致），首次生成時會記一則 warning。

參考音檔的條件：16 kHz 以上、單聲道、無損、**只有目標語者的乾淨人聲**（無 BGM／他人聲音／
混響）、平穩朗讀、5～15 秒、語言與要唸的內容一致。

其餘常調的參數：

| 變數 | 說明 |
|------|------|
| `VOXCPM2_INFERENCE_TIMESTEPS` | 預設 `10`。**短單字容易生出幾乎無聲的音檔**，實測 10 時約 40%、提到 30 後降到 1% 以下 |
| `VOXCPM2_CFG_VALUE` | 越高越貼合文字，代價是自然度 |
| `VOXCPM2_SPEAK_TRANSLATION` | 背面要不要連譯文一起唸。關閉唸 `tts_back_text`（只有原文），開啟改唸 `example`（原文＋譯文）——選的是**欄位**，不是切字串 |
| `VOXCPM2_LOUDNESS_*` | 輸出響度統一。模型逐段音量本來就飄（實測全距 83 dB），預設會把每段對齊 −20 dBFS 並把峰值封在 −1 dBFS。與 `VOXCPM2_NORMALIZE`（文字正規化）無關 |

---

## CLI

十個子命令。`--work` 指定中間 CSV（預設 `$WORK_DIR/cards.csv`），
它是整條流程的狀態機——每一列的每個階段各有 `pending` / `done` / `failed`。

| 指令 | 做什麼 |
|------|--------|
| `ocr --input <路徑>` | ① 建立來源列並辨識文字。四種輸入見下表 |
| `extract` | ② LLM 抽取整理，一列原始文字切成多張卡 |
| `scene` | ③ 聯想圖場景（語義層）。產出 `image_scene`，**模型無關** |
| `prompt` | ④ 聯想圖 prompt（語法層）。依 `IMAGE_PROMPT_AGENT` 把場景改寫成該模型的語法 |
| `image` | ⑤ 聯想圖生成 |
| `audio [--side front\|back\|both]` | ⑥ 語音生成，正反兩側是獨立階段 |
| `pack --output <ZIP>` | ⑦ 打包。任一列有 `failed` 就中止（除非 `--allow-failed`） |
| `run-all [--fresh]` | ①–⑦ 依序跑完。`--fresh` 開跑前清空工作檔（見下） |
| `status` | 各階段統計與失敗明細 |
| `serve [--host --port]` | 啟動本地 Web UI（預設 `127.0.0.1:7860`） |
| `reset` | 重置階段狀態，或清空工作檔（見〈重置工作檔〉） |

### 換一批教材：`run-all --fresh`

工作檔是**單一份**狀態機。換教材時若不先清空，新舊卡片會混在同一份檔案裡，
而且同一頁重抽會撞 `card_id`。`--fresh` 就是省掉手動刪檔這一步：

```bash
anki-builder run-all --input ~/scans/新教材 --fresh
```

- **一律先備份**成 `cards.csv.bak-<時間>`，不需要再加 `--yes`
- **讀不進來的舊工作檔也照樣清掉**（Phase 8 之前的 39 欄格式、或內容損壞）
  ——那正是最需要重來的情形
- **不碰 `media/`**。`pack` 只收卡片欄位引用到的檔案，殘留的舊圖與舊語音
  不會混進新牌組，只是佔磁碟。要一起刪用 `reset --clear all --yes`

> **預設不清空是刻意的。** `run-all` 同時是**中斷續作**的路徑（已完成的階段會跳過），
> 無條件覆寫會讓「圖生成跑到一半斷掉、重跑一次」變成毀掉前面所有成果。
> 要清空就明確說。

### `--input` 吃什麼

| 給的東西 | 行為 |
|----------|------|
| 單一圖片（`.jpg` `.jpeg` `.png` `.webp` `.bmp`） | 該檔案 |
| 內含圖片的目錄 | 目錄下第一層的圖片，**依檔名自然排序**（`page2` 排在 `page10` 前面，順序錯了 `ocr_source_page` 就跟著錯）。**不遞迴**——子目錄的圖不會被吃進來；非圖片檔（PDF、`.DS_Store` 等）自動略過 |
| PDF | 逐頁渲染成圖再辨識，解析度由 `INGEST_PDF_DPI` 決定 |
| `.txt` / `.md` | **繞過 OCR**，內容直接成為待抽取的原始文字 |

同一個來源重跑 `ocr --input` 不會產生重複列（已在工作檔裡的會被略過）；
真要重新辨識既有的列，用 `--force`。

### 選列規則（`ocr` / `extract` / `scene` / `prompt` / `image` / `audio` / `run-all` 共用）

| 參數 | 處理哪些列 |
|------|-----------|
| 預設 | `pending` ＋ `failed` |
| `--only-failed` | 只有 `failed` |
| `--force` | 全部，含 `done`（兩者互斥） |

**不加參數重跑是安全的**：已完成的列會被跳過，這就是「中斷後續作」的做法。

### 常用組合

```bash
# 一頁單字書 → 牌組
uv run anki-builder run-all --input ~/scans/p333.jpg --output output/deck.zip

# 整個資料夾（整本書的掃描頁）→ 牌組。不必先壓成 ZIP，ZIP 只是輸出格式
uv run anki-builder run-all --input ~/scans/n2_book/ --output output/deck.zip

# 整本 PDF → 牌組（逐頁渲染成圖再辨識）
uv run anki-builder run-all --input ~/books/n2.pdf --output output/deck.zip

# 分次累加：不同來源可以陸續進同一個工作檔，不會互相覆蓋
uv run anki-builder ocr --input ~/scans/ch1/
uv run anki-builder ocr --input ~/scans/ch2/

# 純文字輸入：.txt／.md 由 ocr 階段收進工作檔（不會呼叫 OCR 模型），再抽取
uv run anki-builder ocr --input ~/notes/n2_vocab.txt
uv run anki-builder extract --deck-name 日語::N2 --domain "日語 N2 單字" \
    --card-language 繁體中文 --deck-categories 名詞／動詞／形容詞

# 索引頁、純單字表（原文沒有釋義）：強制先補釋義再抽取
uv run anki-builder extract --enrich

# 只補正面語音，完全不碰背面的狀態
uv run anki-builder audio --side front

# 只重跑失敗的
uv run anki-builder image --only-failed

# 改完 prompt 後強制重生全部的圖
uv run anki-builder image --force
```

`extract` 的幾個選項值得一提：`--deck-name` 是牌組名稱前綴（例 `日語::N2`）、
`--card-language` 是釋義要用哪種語言寫（原文書或純單字表需要它）、
`--deck-categories` 給 deck 最後一層的**封閉分類清單**、`--card-id-prefix` 決定 `card_id` 前綴。
不給的話模型自行判斷，多數情況可用，但整批一致性會差一些。

---

## Web UI

```bash
uv run anki-builder serve --port 8080
```

預設綁 `127.0.0.1`——這是本機工具，沒有帳號也沒有權限控管，暴露到區網等於把工作檔開放給人改。
瀏覽器打開後是五個分頁，其中三個是 CLI 做不到的事：

### 匯入：把書頁收進工作檔

輸入**這台機器上**的路徑（單張圖、資料夾、PDF、`.txt`／`.md`），與 CLI 的
`ocr --input` 是同一件事。**沒有做瀏覽器上傳**——伺服器與瀏覽器是同一台機器，
上傳等於把檔案複製一份再讀回來。

- **檢查**：只判別會抓到哪些檔案（含順序），**不寫任何東西**
- **匯入**：建立來源列。**不會跑 OCR**——建好列之後到「狀態總覽」按 ① OCR

重複匯入同一個路徑不會產生重複列。

### 抽取結果：逐欄檢視與修正

表格直接編輯，按「儲存變更」寫回。存檔後的**狀態連動**是重點：

- 動了任何內容欄位 → 該列 `extract_status` 回 `pending`
- 動 `image_prompt` → 連帶 `image_status`
- 動 `tts_front_text` / `tts_back_text` → 連帶**對應那一側**的 `audio_*_status`，另一側不動

也就是說，修完文字後再跑一次對應階段，就只會重做被你改過的那些。
`card_id` 是主鍵，鎖住不可改。儲存只作用於**當前頁**，換頁前記得先存。

### 聯想圖：看圖、改 prompt、只重生一張

縮圖牆列出所有卡片（**含還沒生成的**，顯示為灰底佔位圖）。點一張看大圖與 prompt，
改完按「生成／重生此圖」——只跑那一張，其他圖的檔案不會被動到。單張約數秒。

### 失敗清單：集中處理壞掉的列

所有階段的 `failed` 列列在一起，附完整錯誤訊息。一列若在多個階段失敗會出現多筆
（`audio_front` 壞了不代表 `audio_back` 也壞，各自重跑）。

- 「重跑此列」只跑那張卡的那個階段，跑完才回來
- 「重跑全部失敗」等同 `--only-failed`，**在背景**依序跑每個有失敗的階段，進度看「狀態總覽」

### 重置工作檔（設定分頁底部）

換一批教材、或想整批重跑某個階段時用。**每一種都會先備份**
`cards.csv.bak-<時間戳>`：

| 動作 | 做什麼 | 破壞性 |
|------|--------|--------|
| 重置選定階段 | 勾選的階段設回 `pending`，**內容與既有的圖／語音都留著** | 否 |
| 清空所有列（保留媒體） | 刪掉所有列，媒體檔還在 | 是 |
| 清空所有列並刪除媒體 | 連 `media/img`、`media/audio` 第一層的檔案一起刪 | 是 |

破壞性的兩顆要**先在輸入框打出工作檔名稱**（`cards.csv`）才會啟用——
「確定嗎？」按下去只需要一次手滑。有階段正在跑時一律拒絕，清空會與
階段的 checkpoint 寫入打架。

CLI 有對等的命令：

```bash
uv run anki-builder reset --stages image,audio_back    # 非破壞性，直接執行
uv run anki-builder reset --clear rows                 # 只告訴你會刪什麼，不動手
uv run anki-builder reset --clear rows --yes           # 真的清空，媒體保留
uv run anki-builder reset --clear all  --yes           # 連媒體一起
```

> 只刪媒體目錄**第一層的檔案**，不遞迴、不刪目錄本身——你若把原稿放在
> `media/` 底下的子目錄，不會被順手掃掉。

### 兩個限制

**同時只允許一個階段在跑。** GPU 只有一張，並行只會互相搶 VRAM，而且兩個階段會
同時寫同一份中間 CSV。第二個請求會被回絕並告訴你正在跑什麼。

**serve 期間不要另開 CLI 跑同一份工作檔。** 上面那道鎖只擋得住 UI 自己，
兩個行程同時寫同一個 CSV 會互相覆蓋。

---

## 疑難排解

### ComfyUI 節點 ID 找不到

多半是匯出方式錯了。JSON 頂層若是 `nodes` / `links` 陣列，那是編輯器格式，
要改用 **Save (API Format)** 重新匯出（見〈前置需求〉）。程式會直接指出這件事，
不會讓你在半小時的進度條中間才發現。

節點 ID 填錯（例如 workflow 換了但 `.env` 沒跟著改）也會在**第一張送出前**被擋下，
錯誤訊息會指名是哪個節點、哪個欄位。

### VRAM 不足

> `run-all` **開跑前會先把 GPU 清乾淨**（ComfyUI、Ollama、VOXCPM2），
> 而且不看 `COMFYUI_FREE_BEFORE_LLM` 與 `MODEL_UNLOAD_BEFORE_STAGE`——
> 那兩個開關管的是「階段之間要不要付重載成本換讓渡」，開跑前沒有這個取捨：
> 此刻 GPU 上的東西沒有一樣是這趟流程需要的。
>
> 個別子命令（`image`、`audio` 等）沒有這一步，因為它們是接在別的階段後面跑的。
> 手動跑單一階段前若剛用過 ComfyUI，可以先
> `curl -X POST http://127.0.0.1:8188/free -H 'Content-Type: application/json' -d '{"unload_models":true,"free_memory":true}'`
> ——實測 17.6 GB 在兩秒內釋放乾淨。

先確認一件事：**VRAM 約束是模型大小的函數，不是固定事實。**

以 24 GB 卡的實測為例（RTX 3090）：

| 佔用者 | 實測 |
|--------|------|
| 抽取模型 E4B | 約 8.6 GB |
| ComfyUI 常駐（SD 1.5／SDXL） | 約 2.3／7.2 GB |
| **ComfyUI 常駐（kyoani／FLUX.2-klein-9B）** | **約 17.4 GB** |
| VOXCPM2 推論峰值 | 約 7.5 GB（權重載入後 6.0 GB） |

用 SD 1.5 或 SDXL 時三者同時 ＋ 桌面約 18 GB／24 GB，**綽綽有餘**，預設不需要任何讓渡。

**kyoani workflow 改變了這個結論**：17.4 GB 常駐加上 VOXCPM2 的 7.5 GB 峰值就是
24.9 GB，24 GB 卡放不下，所以那組設定**必須開 `COMFYUI_FREE_BEFORE_LLM=true`**
（它會在 extract 與 audio 之前各請 ComfyUI 讓位一次）。同理，若把抽取模型換成
31B q4（19.87 GB），`MODEL_UNLOAD_BEFORE_STAGE` 與 `COMFYUI_FREE_BEFORE_LLM`
也都變成必要。

症狀對照：

| 現象 | 多半是 |
|------|--------|
| 抽取速度掉到 1/6、`still_waiting` 一路累積 | 模型沒完整載入 VRAM。關掉 ComfyUI 或開 `COMFYUI_FREE_BEFORE_LLM`，並檢查 Modelfile 的 `num_ctx` |
| 語音階段 OOM | 前一階段的模型沒讓位。確認 `MODEL_UNLOAD_BEFORE_STAGE=true`；用 kyoani workflow 時還要 `COMFYUI_FREE_BEFORE_LLM=true`，否則 ComfyUI 的 17.4 GB 不會退場 |
| ComfyUI 生成中途失敗 | 抽取模型還佔著。先跑完 extract 再跑 image，不要並行 |

### 圖的畫風不對

畫出來不是預期的風格（例如換上 kyoani workflow 後仍是一般寫實動漫），
多半是 **LoRA 觸發詞沒出現在 `image_prompt` 裡**：沒觸發詞的話 LoRA 掛了等於沒掛，
模型會照常出圖、不會報任何錯——**這是「安靜地錯」，不是失敗**。

依序檢查：

1. 直接看 CSV 的 `image_prompt` 欄：kyoani profile 的每一列都該以 `Anime. ` 開頭。
   沒有就是 `prompt` 階段的產出有問題，重跑 `anki-builder prompt --force`
   （觸發詞刻意不做程式檢查——檢查它就得把它變回一個系統參數）
2. `IMAGE_PROMPT_AGENT` 指到的是不是對的 profile（見〈語法層 profile〉）
3. `COMFYUI_WORKFLOW_PATH` 與整組節點 ID 是不是同一組的（見〈ComfyUI 節點注入點〉）
4. workflow 裡 LoRA 節點的 `strength_model` 不是 0

改完要 `anki-builder image --force` 重生，只補未完成的會讓兩種畫風混在同一副牌組。

**最常見的一種**：換了 `IMAGE_PROMPT_AGENT` 卻**只跑 `image --force`**。
`image` 只讀 `image_prompt`，而那一欄是上一個 profile 產生的——換 profile 後
必須先 `anki-builder prompt --force` 把語法層重生，再跑 `image --force`。
`image_scene` 不必動。

### OCR 辨識率低

1. **提高 `INGEST_PDF_DPI`**（PDF 輸入）。200 是堪用起點，但別無限往上加——
   影像會逼近模型的像素上限。
2. **改走 `INGEST_MODE=vision_direct`**：影像直接送抽取模型，跳過 OCR。
   版面複雜（多欄、表格）時反而常常更好。
3. 檢查 `prompts/ocr_extract.md`——GLM-OCR 只吃官方前綴，**往裡面加說明文字會讓它退化成
   無限重複輸出**。那個檔案只有一行是刻意的。

### 生成圖含文字

`COMFYUI_NEGATIVE_PROMPT` 預設已涵蓋 text／letters／caption／watermark 等詞。

⚠ **但那串對 kyoani workflow 完全無效**：它的 cfg 是 1、負向條件接 `ConditioningZeroOut`，
負向根本不參與取樣，`.env` 裡的負向注入點打的是一個刻意的孤兒節點（`999`），
ComfyUI 連執行都不會執行它。用那組時**調負向詞是白調**。

> **判準是「壓低出現率」，不是「零出現」。** 零出現做不到——三種手段的實測結果
> 記在 `prompts/image_prompt_template.md`〈已實測的天花板〉。2026-09-01 實測
> 隨機 24 張中 4 張（17%）含假文字，**這是目前的正常水準**。

真正有效的只有一件事：**不要在場景裡要求會帶字的道具**（規則寫在
`prompts/image_scene.md`）。實測 308 張的場景違規率因此從 19% 降到 8%。

某一張出字而你在意時：

1. 先看那列的 `image_scene`——多半是它要了螢幕、日曆、招牌、票券之類的東西。
   改掉場景 → `prompt_status` 與 `image_status` 設回 `pending` → 重跑 `prompt`、`image`
2. 場景乾淨卻還是出字，那是模型自己加的道具（實測看過「舞台上的喜劇演員」
   被自動配上一面寫滿亂碼的投影幕）。**這種改 prompt 沒用**，換 seed 或換場景題材
3. 條目本身就是帶字的東西（`advertisement`、`boarding pass`、`menu`）：
   `image_scene.md` 允許直接畫該物件並接受亂碼——比畫出一張看不懂的圖好
4. **不要靠調高 CFG 解決**。實測 cfg 13 不但無效，還會突破防文字約束

另有一項已知限制：**多元素構圖不一定畫得齊**（「狗＋蘋果＋木桌」可能只出現其中兩樣）。
SD 1.5 與 SDXL 之間只是互有勝負；2026-08-28 換上 kyoani（FLUX.2-klein-9B）後
**確有改善但仍未根治**——同一批 prompt 下人臉、人群、桌上物件比 SDXL 齊得多
（`p3_084` 烤肉、`p3_098` 離場都是明顯例子），但仍會有整張改走另一種解讀的情形。
真的要那張圖對，就到 Web UI 的「聯想圖」分頁改 prompt 重生一張。

**不要試圖用風格標籤救**：加動漫品質標籤（`masterpiece, best quality, absurdres`）
會把畫面拉成角色特寫，加廣角指令（`wide establishing shot`）會讓主體整個消失。
四種嘗試的實測結果記在 `prompts/image_prompt_template.md`〈實測否決的調整〉。

### 短單字的語音幾乎無聲

模型對很短的詞是弱項。把 `VOXCPM2_INFERENCE_TIMESTEPS` 從 10 提到 30，
實測失敗率從約 40% 降到 1% 以下。剩下的重跑那幾張即可——
響度正規化會在放大到上限仍偏小聲時記一則 warning，那通常就是生成失敗的那些。

**正面唸出來是一串怪音**：檢查該列的 `tts_front_text` 是不是音標（如 `ˈæktɪv`）。
音標是給人看的符號，模型唸不出來，改填單字本身即可。

### 不同批次的教材混在一起了

工作檔是**單一份**狀態機，沒有「專案」的概念。要分開處理不同教材有兩種做法：

1. **做完一批就清空**：`pack` 出 ZIP 之後，到設定分頁「清空所有列」（或
   `reset --clear rows --yes`），再匯入下一批。媒體檔要不要一起刪由你決定
2. **分成不同工作目錄**：`WORK_DIR=./work/n2 uv run anki-builder serve`——
   一個服務綁一份工作檔，要換就重啟指向另一個目錄

第 2 種比較適合會來回切換的情況；第 1 種適合做完就不再回頭的。

### 中斷後如何續作

直接重跑同一個指令就好，不必加任何參數：

```bash
uv run anki-builder image        # 已 done 的列自動跳過
```

中間 CSV 每完成幾列就原子寫回一次（image 與 audio 是每一列），
所以 Ctrl-C 最多只損失最後一列的工作。想知道還剩什麼：

```bash
uv run anki-builder status
```

### 打包時被擋下

`pack` 會在任一列有 `failed`、或媒體檔缺失／為 0 位元組時中止，並指名是哪張卡的哪個欄位。
修好後重跑那個階段即可；真的想帶著失敗打包，用 `--allow-failed`。
